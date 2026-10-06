"""Jinka Transit : surveille tes alertes Jinka et ne t'envoie que les annonces d'où tes
adresses (bureau, école…) sont joignables en métro/RER (sans bus) dans le temps voulu."""

import hashlib
import json
import logging
import os
import random
import re
import threading
import time
from datetime import datetime, timedelta

from fmt import body_of, html_of, short_of, title_of
from jinka import Jinka, JinkaAuthError
from notify import Notifier
from http_util import HttpError
from transit import CACHE_TTL, QuotaError, Transit, TransitError
import web

OPTIONS_PATH = os.environ.get("OPTIONS_PATH", "/data/options.json")
STATE_PATH = os.environ.get("STATE_PATH", "/data/state.json")
MAX_DESTINATIONS = 5
MAX_STATE_ENTRIES = 3000
LISTING_TTL = 30 * 86400     # annonce retirée de la liste 30 jours après sa détection
FORGOTTEN_TTL = 180 * 86400  # … mais son identifiant reste mémorisé 6 mois pour ne pas la renvoyer
ERROR_EMAIL_EVERY = 6 * 3600  # même type d'erreur : au plus un email toutes les 6 h

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("main")


FLOOR_RE = re.compile(r"\b(\d{1,2})\s*(?:e|è|ème|eme|ieme|ième|er|ere|ère)\s+(?:et\s+dernier\s+)?étage", re.I)


def floor_from_text(text):
    """Étage cité dans l'annonce (« au 5e étage », « 3ème étage »), 0 pour le rez-de-chaussée, sinon None."""
    text = text or ""
    m = FLOOR_RE.search(text)
    if m:
        return int(m.group(1))
    if re.search(r"\brez[- ]de[- ]chauss[ée]e\b|\bRDC\b", text, re.I):
        return 0
    return None


NO_ELEVATOR_RE = re.compile(r"\b(sans|pas d['’]|pas de|aucun)\s*(ascenseur|asc\b\.?)", re.I)
ELEVATOR_RE = re.compile(r"\bascenseur\b|\bavec asc\b\.?", re.I)


def has_elevator(listing):
    """True / False si l'annonce le dit (texte prioritaire s'il dit « sans ascenseur »), None si inconnu."""
    text = listing.get("description") or ""
    if NO_ELEVATOR_RE.search(text):
        return False
    if listing.get("elevator") is True or ELEVATOR_RE.search(text):
        return True
    return listing.get("elevator")


class App:
    def __init__(self):
        self.lock = threading.Lock()
        self.scan_now = threading.Event()
        self.opts = self.load_options()
        self.state = self.load_state()
        self.last_scan = {"at": None, "status": "jamais lancé", "error": None}
        self.login = None          # connexion Jinka par code en cours
        self.login_email = ""
        self.flash = ""            # message affiché une fois dans la page
        self.explore_progress = {}  # recherche élargie en cours
        self.auto_test_running = False

    # ---------- config / état ----------

    def load_options(self):
        with open(OPTIONS_PATH, encoding="utf-8") as f:
            opts = json.load(f)
        dests = [d for d in opts.get("destinations") or [] if (d.get("address") or "").strip()]
        if len(dests) > MAX_DESTINATIONS:
            log.warning("%d adresses configurées : seules les %d premières sont prises en compte.",
                        len(dests), MAX_DESTINATIONS)
            dests = dests[:MAX_DESTINATIONS]
        for i, d in enumerate(dests):
            d["name"] = (d.get("name") or f"Adresse {i + 1}").strip()
            d["max_minutes"] = int(d.get("max_minutes") or 45)
            d["arrival_time"] = d.get("arrival_time") or "09:00"
            d["info_only"] = bool(d.get("info_only"))
        opts["destinations"] = dests
        return opts

    def criteria_hash(self):
        """Empreinte des critères : si tu changes une durée/adresse/mode, les annonces
        refusées auparavant sont réévaluées."""
        o = self.opts
        key = json.dumps([o["destinations"], sorted(o.get("allowed_modes") or []),
                          o.get("max_walk_minutes"), o.get("max_rent", 0), o.get("max_walk_home_minutes", 5),
                          "localisation-v2", "marche-destination-30", o.get("min_area", 0),
                          o.get("transfer_penalty_minutes", 3), o.get("max_transfers", 2),
                          bool(o.get("furnished_only")), int(o.get("max_floor") or 0), "ascenseur-v2"],
                         sort_keys=True)
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def load_state(self):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"listings": {}, "geocode": {}, "first_run_done": False, "auth_alert_sent": False}

    def purge_old_listings(self, now=None):
        """Supprime les annonces détectées il y a plus de 30 jours (seul l'identifiant est gardé)."""
        now = now or time.time()
        forgotten = self.state.setdefault("forgotten", {})
        for k, v in list(self.state["listings"].items()):
            if now - v.get("first_seen", v.get("ts", now)) > LISTING_TTL:
                forgotten[k] = now
                del self.state["listings"][k]
        self.state["forgotten"] = {k: t for k, t in forgotten.items() if now - t < FORGOTTEN_TTL}

    def save_state(self):
        self.purge_old_listings()
        items = self.state["listings"]
        if len(items) > MAX_STATE_ENTRIES:
            keep = sorted(items.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
            self.state["listings"] = dict(keep[:MAX_STATE_ENTRIES])
        cutoff = time.time() - CACHE_TTL
        for key in ("journeys", "explore_cache"):
            self.state[key] = {k: v for k, v in self.state.get(key, {}).items() if v["ts"] > cutoff}
        tmp = STATE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False)
        os.replace(tmp, STATE_PATH)

    # ---------- scan ----------

    def resolve_destinations(self, transit):
        cache = self.state.setdefault("geocode", {})
        out = []
        for d in self.opts["destinations"]:
            addr = d["address"].strip()
            if addr not in cache:
                g = transit.geocode(addr)
                cache[addr] = g
                log.info("Adresse « %s » (%s) localisée : %s (%.5f, %.5f)",
                         d["name"], addr, g["label"], g["lat"], g["lon"])
            out.append({**d, **cache[addr]})
        return out

    def make_transit(self):
        o = self.opts
        return Transit(o["prim_api_key"], o.get("allowed_modes"), o.get("max_walk_minutes", 15),
                       transfer_penalty=o.get("transfer_penalty_minutes", 3), max_transfers=o.get("max_transfers", 2),
                       quota=self.state.setdefault("idfm_quota", {}))

    def transfer_reject(self, results):
        """Vérifie des trajets déjà calculés avec la règle des correspondances. Renvoie la raison ou None."""
        o = self.opts
        pen, mx = int(o.get("transfer_penalty_minutes", 3) or 0), o.get("max_transfers", 2)
        maxes = {d["name"]: int(d.get("max_minutes") or 45) for d in o["destinations"] if not d.get("info_only")}
        for r in results or []:
            if r.get("info_only") or r.get("minutes") is None or r["name"] not in maxes:
                continue
            n = r.get("transfers", 0)
            if mx is not None and n > int(mx):
                return f"{r['name']} : {n} correspondances (max {mx})"
            if r["minutes"] + pen * n > maxes[r["name"]]:
                return (f"{r['name']} : {r['minutes']} min + {pen * n} min de correspondances "
                        f"> {maxes[r['name']]} min")
        return None

    def basic_reject(self, listing):
        """Critères simples (sans calcul de trajet) : colocation, loyer, surface. Renvoie la raison ou None."""
        import bienici
        if listing.get("coliving") or bienici.is_coliving({"description": listing.get("description")}):
            return "chambre en colocation"
        if self.opts.get("furnished_only") and listing.get("furnished") is False:
            return "non meublé"
        max_floor = int(self.opts.get("max_floor") or 0)
        if max_floor:
            floor = listing.get("floor")
            if floor is None:
                floor = floor_from_text(listing.get("description"))
            if floor is not None and floor > max_floor:
                elevator = has_elevator(listing)
                if not elevator:  # au-delà du max : gardée seulement avec un ascenseur confirmé
                    why = "sans ascenseur" if elevator is False else "ascenseur non précisé"
                    return f"{int(floor)}e étage {why} (max {max_floor}e sans ascenseur)"
        max_rent = int(self.opts.get("max_rent") or 0)
        if max_rent and listing.get("rent") and listing["rent"] > max_rent:
            return f"loyer {int(listing['rent'])} € > {max_rent} €"
        min_area = int(self.opts.get("min_area") or 0)
        if min_area and listing.get("area") and listing["area"] < min_area:
            return f"surface {listing['area']:g} m² < {min_area} m²"
        return None

    def complete_building(self, listing):
        """Étage élevé sans info d'ascenseur : on lit la fiche complète jinka.fr (champ ascenseur, texte entier)."""
        max_floor = int(self.opts.get("max_floor") or 0)
        floor = listing.get("floor")
        if floor is None:
            floor = floor_from_text(listing.get("description"))
        if not (max_floor and listing.get("uuid") and floor is not None and floor > max_floor
                and has_elevator(listing) is None and not listing.get("detail_checked")):
            return
        import locate
        d = locate.fetch_ad_detail(listing["uuid"])
        listing["detail_checked"] = True
        if d.get("lift") is not None:
            listing["elevator"] = d["lift"]
        if d.get("floor") is not None and listing.get("floor") is None:
            listing["floor"] = d["floor"]
        if len(d.get("description") or "") > len(listing.get("description") or ""):
            listing["description"] = d["description"][:2000]

    def evaluate(self, listing, dests, transit):
        self.complete_building(listing)
        reason = self.basic_reject(listing)
        if reason:
            return "rejected", [], reason
        home_walk_max = int(self.opts.get("max_walk_home_minutes", 5))
        if listing["lat"] is None or listing["lng"] is None:
            import locate
            found = locate.locate(listing, transit, self.state.setdefault("geocode", {}))
            if not found:
                return "rejected", [], "pas de position (ni GPS, ni station dans l'annonce)"
            listing["lat"], listing["lng"], listing["approx"] = found
            if listing["approx"]:
                home_walk_max = None  # on part de la station elle-même
        cache = self.state.setdefault("journeys", {})
        results = []
        # D'abord les adresses du filtre ; les adresses « pour info » ne sont calculées
        # que pour les annonces retenues (économise les appels IDFM)
        for d in [d for d in dests if not d["info_only"]]:
            r = transit.journey(listing["lat"], listing["lng"], d["lat"], d["lon"],
                                d["arrival_time"], d["max_minutes"], cache=cache,
                                home_walk_max=home_walk_max)
            results.append({"name": d["name"], "max": d["max_minutes"], **r})
            if not r["ok"]:
                return "rejected", results, f"{d['name']} : {r['reason']}"
        for d in [d for d in dests if d["info_only"]]:
            r = transit.journey(listing["lat"], listing["lng"], d["lat"], d["lon"],
                                d["arrival_time"], 10 ** 6, cache=cache)
            results.append({"name": d["name"], "info_only": True, **r, "ok": True})
        return "match", results, ""

    def scan(self):
        o = self.opts
        if not o["destinations"]:
            raise RuntimeError("Aucune adresse configurée (destinations).")
        if not o.get("prim_api_key"):
            raise RuntimeError("prim_api_key manquante (clé gratuite sur prim.iledefrance-mobilites.fr).")

        token = o.get("jinka_token") or (self.state.get("jinka_auth") or {}).get("token")
        password = "" if o.get("mail_password") else o.get("jinka_password")
        jinka = Jinka(o.get("jinka_email"), password, token)
        transit = self.make_transit()
        notifier = self.make_notifier()
        dests = self.resolve_destinations(transit)
        crit = self.criteria_hash()

        self.recheck_retained()
        done = {k for k, v in self.state["listings"].items()
                if v["status"] in ("notified", "silent") or v.get("crit") == crit}
        done |= set(self.state.get("forgotten", {}))
        try:
            try:
                listings = jinka.listings(o.get("jinka_alerts"), int(o.get("max_pages_per_alert", 3)), known=done)
            except JinkaAuthError:
                if not self.auto_login_possible():
                    raise
                jinka = Jinka(token=self.auto_login())
                listings = jinka.listings(o.get("jinka_alerts"), int(o.get("max_pages_per_alert", 3)), known=done)
            self.state["auth_alert_sent"] = False
        except JinkaAuthError as e:
            if not self.state.get("auth_alert_sent"):
                notifier.send("Jinka Transit : connexion Jinka impossible",
                              f"{e}\nVérifie la boîte mail dédiée dans la configuration, ou reconnecte-toi "
                              "depuis la page de l'add-on (code par email).")
                self.state["auth_alert_sent"] = True
                self.save_state()
            raise

        if o.get("bienici", True):
            import bienici
            try:
                zones = bienici.zones_for(o["bienici_communes"], self.state.setdefault("geocode", {})) \
                    if o.get("bienici_communes") else [z for v in bienici.default_zones().values() for z in v]
                bi = bienici.listings(zones, int(o.get("max_rent") or 0), int(o.get("min_area") or 0),
                                      furnished=bool(o.get("furnished_only")))
                log.info("Bien'ici : %d annonces.", len(bi))
                listings += bi
            except (HttpError, OSError, ValueError) as e:
                log.warning("Bien'ici indisponible (%s) : on continue avec Jinka seul.", e)
                self.report_error("bienici", f"Bien'ici indisponible : {e}")

        stats = {"annonces": len(listings), "évaluées": 0, "ok": 0, "refusées": 0, "doublons": 0}
        known = self.state["listings"]
        forgotten = self.state.get("forgotten", {})
        for l in listings:
            if l["id"] in forgotten:  # vue il y a plus de 30 jours : on ne la retraite pas
                continue
            prev = known.get(l["id"])
            if prev:  # complète les annonces déjà connues (champs ajoutés depuis : DPE, meublé)
                prev["listing"].update({k: l[k] for k in ("dpe", "furnished", "floor", "elevator")
                                        if l.get(k) is not None})
            first_seen = (prev or {}).get("first_seen") or (prev or {}).get("ts") or time.time()
            if prev and prev["status"] in ("notified", "silent", "duplicate"):
                continue
            if prev and prev["status"] in ("rejected", "match") and prev.get("crit") == crit:
                continue
            if l["expired"] or l["deleted"]:
                continue
            twin = self.find_twin(l)
            if twin:
                known[l["id"]] = {"status": "duplicate", "crit": crit, "listing": l, "results": [],
                                  "reason": f"doublon de {twin['listing'].get('source') or twin['listing']['link']}",
                                  "ts": time.time(), "first_seen": first_seen}
                stats["doublons"] += 1
                self.count_daily(l, "doublons")
                continue
            try:
                status, results, reason = self.evaluate(l, dests, transit)
            except QuotaError as e:
                # pas d'email : les annonces en attente seront calculées dès que le quota est renouvelé
                stats["reportées"] = stats.get("reportées", 0) + 1
                stats["quota IDFM"] = str(e).split("(429), ")[-1]
                # gardée « en attente » (crit vide : recalculée au prochain scan possible)
                known[l["id"]] = {"status": "pending", "crit": None, "listing": l, "results": [],
                                  "reason": f"calcul du trajet en attente : quota IDFM atteint, {stats['quota IDFM']}",
                                  "ts": time.time(), "first_seen": first_seen,
                                  **({"notified_at": prev["notified_at"]} if prev and prev.get("notified_at") else {})}
                continue  # sans appel réseau : on continue pour les annonces déjà en cache
            except TransitError as e:
                log.error("Calcul d'itinéraire impossible (%s) : on réessaiera au prochain scan.", e)
                self.report_error("itinéraires", f"Calcul d'itinéraire impossible (API IDFM) : {e}")
                break
            if status == "match" and prev and prev.get("notified_at"):
                status = "notified"  # déjà envoyée auparavant : pas de second envoi
            stats["évaluées"] += 1
            stats["ok" if status in ("match", "notified") else "refusées"] += 1
            self.count_daily(l, "testées")
            if status == "match":
                self.count_daily(l, "retenues")
            known[l["id"]] = {"status": status, "crit": crit, "listing": l, "results": results,
                              "reason": reason, "ts": time.time(), "first_seen": first_seen,
                              **({"notified_at": prev["notified_at"]} if prev and prev.get("notified_at") else {})}
            log.info("%s %s — %s", "✅" if status == "match" else "❌", title_of(l),
                     reason or " / ".join(f"{r['name']} {r['minutes']} min" for r in results))

        # Notifications (les plus récentes d'abord, avec un plafond par scan)
        pending = sorted((v for v in known.values() if v["status"] == "match"),
                         key=lambda v: v["listing"].get("created_at") or "", reverse=True)
        first_run = not self.state.get("first_run_done")
        if first_run and not o.get("notify_existing_on_first_run", True):
            for v in pending:
                v["status"] = "silent"
            pending = []
        limit = int(o.get("max_notifications_per_run", 10))
        sent = 0
        for v in pending[:limit]:
            if notifier.send(title_of(v["listing"]), body_of(v), v["listing"]["link"], v["listing"]["image"],
                             short=short_of(v), html=html_of(v)):
                v["status"] = "notified"
                v["notified_at"] = time.time()
                sent += 1
            else:
                self.report_error("notification", f"Échec d'envoi de l'annonce {v['listing']['link']} "
                                  "(email et SMS en échec, voir le journal de l'add-on).")
        if len(pending) > limit:
            log.info("%d annonces en attente, envoyées aux prochains scans.", len(pending) - limit)
        self.state["first_run_done"] = True
        self.save_state()
        if stats.get("reportées"):
            log.info("Quota IDFM atteint : %d annonce(s) reportée(s), %s.", stats["reportées"], stats["quota IDFM"])
        stats["envoyées"] = sent
        stats["appels IDFM"] = transit.api_calls
        stats["trajets en cache"] = transit.cache_hits
        return stats

    def run_once(self):
        with self.lock:
            self.opts = self.load_options()
            started = datetime.now()
            try:
                stats = self.scan()
                self.last_scan = {"at": started.isoformat(timespec="seconds"), "error": None,
                                  "status": ", ".join(f"{k} : {v}" for k, v in stats.items())}
                log.info("Scan terminé — %s", self.last_scan["status"])
                self.report_recovery()
            except Exception as e:  # noqa: BLE001 - on veut que la boucle survive
                if isinstance(e, (RuntimeError, JinkaAuthError, TransitError, ValueError)):
                    log.error("Scan échoué : %s", e)
                else:
                    log.exception("Scan échoué : %s", e)
                self.last_scan = {"at": started.isoformat(timespec="seconds"), "error": str(e),
                                  "status": "erreur"}
                self.report_error("scan", f"Le scan a échoué : {type(e).__name__} : {e}")

    # ---------- alertes d'erreur par email ----------

    # ---------- rapport quotidien ----------

    def count_daily(self, listing, kind):
        site = "Bien'ici" if listing.get("alert_id") == "bienici" else "Jinka"
        d = self.state.setdefault("daily", {"since": time.time(), "sites": {}})
        c = d["sites"].setdefault(site, {"testées": 0, "retenues": 0, "doublons": 0, "liens": []})
        c[kind] += 1
        if kind == "retenues":
            c["liens"].append(f"{title_of(listing)} — {listing['link']}")

    def daily_report_due(self, now=None):
        now = now or datetime.now()
        at = (self.opts.get("daily_report_time") or "").strip()
        if not at or not (self.opts.get("daily_report_email") or "").strip():
            return False
        try:
            h, m = map(int, at.split(":"))
        except ValueError:
            return False
        return now.hour * 60 + now.minute >= h * 60 + m and self.state.get("daily_sent") != now.strftime("%Y-%m-%d")

    def send_daily_report(self, now=None):
        """Email du matin : annonces testées / retenues par site depuis le dernier rapport."""
        now = now or datetime.now()
        d = self.state.get("daily") or {"since": time.time(), "sites": {}}
        since = datetime.fromtimestamp(d["since"])
        o = self.opts
        n = Notifier(email_to=o.get("daily_report_email"), smtp_user=o.get("mail_user") or o.get("jinka_email"),
                     smtp_password=o.get("mail_password"), smtp_server=o.get("smtp_server"))
        if not n.email_ok:
            return False
        sites = {k: d["sites"].get(k) or {"testées": 0, "retenues": 0, "doublons": 0, "liens": []}
                 for k in ("Jinka", "Bien'ici")}
        tot_t = sum(c["testées"] for c in sites.values())
        tot_r = sum(c["retenues"] for c in sites.values())
        lines = [f"Depuis le {since:%d/%m à %H:%M} :", ""]
        rows = ""
        for site, c in sites.items():
            dup = f", {c['doublons']} doublons écartés" if c["doublons"] else ""
            lines.append(f"{site} : {c['testées']} annonces testées, {c['retenues']} retenues{dup}")
            lines += [f"   ✅ {x}" for x in c["liens"]]
            rows += (f"<tr><td>{site}</td><td align=right>{c['testées']}</td><td align=right><b>{c['retenues']}</b></td>"
                     f"<td align=right>{c['doublons']}</td></tr>")
        lines += ["", f"Total : {tot_t} testées, {tot_r} retenues.",
                  f"Dernier scan : {self.last_scan.get('at') or '—'} ({self.last_scan.get('error') or 'ok'})"]
        links = "".join(f"<li>{x.rsplit(' — ', 1)[0]} — <a href='{x.rsplit(' — ', 1)[1]}'>voir</a> ({site})</li>"
                        for site, c in sites.items() for x in c["liens"])
        html = (f"<p>Depuis le {since:%d/%m à %H:%M} :</p>"
                "<table border=1 cellpadding=6 style='border-collapse:collapse'>"
                "<tr><th>Site</th><th>Testées</th><th>Retenues</th><th>Doublons</th></tr>"
                f"{rows}<tr><td><b>Total</b></td><td align=right>{tot_t}</td><td align=right><b>{tot_r}</b></td><td></td></tr></table>"
                + (f"<p>Annonces retenues :</p><ul>{links}</ul>" if links else "")
                + f"<p style='color:#888'>Dernier scan : {self.last_scan.get('at') or '—'} ({self.last_scan.get('error') or 'ok'})</p>")
        if n._email(f"📊 Jinka Transit : {tot_t} annonces testées, {tot_r} retenues", "\n".join(lines), html):
            self.state["daily"] = {"since": time.time(), "sites": {}}
            self.state["daily_sent"] = now.strftime("%Y-%m-%d")
            self.save_state()
            return True
        return False

    def error_notifier(self):
        o = self.opts
        to = (o.get("error_email") or "").strip()
        n = Notifier(email_to=to, smtp_user=o.get("mail_user") or o.get("jinka_email"),
                     smtp_password=o.get("mail_password"), smtp_server=o.get("smtp_server"))
        return n if n.email_ok else None

    def report_error(self, kind, message):
        """Envoie l'erreur par email (au plus une fois toutes les 6 h pour la même erreur)."""
        try:
            errors = self.state.setdefault("errors_reported", {})
            prev = errors.get(kind)
            if prev and time.time() - prev["ts"] < ERROR_EMAIL_EVERY:
                return
            n = self.error_notifier()
            if not n:
                return
            text = (f"{message}\n\nType : {kind}\nDate : {datetime.now():%d/%m/%Y %H:%M}\n"
                    "Un email de rétablissement sera envoyé quand tout refonctionnera.")
            if n._email(f"⚠️ Jinka Transit : erreur ({kind})", text):
                errors[kind] = {"message": message, "ts": time.time()}
                self.save_state()
        except Exception:  # noqa: BLE001 - l'alerte ne doit jamais casser le scan
            log.exception("Impossible d'envoyer l'email d'erreur")

    def report_recovery(self):
        """Scan réussi : si des erreurs avaient été signalées, on prévient que c'est rétabli."""
        errors = self.state.get("errors_reported") or {}
        if "scan" not in errors:
            return
        try:
            n = self.error_notifier()
            if n and n._email("✅ Jinka Transit : rétabli",
                              f"Le scan refonctionne ({self.last_scan['status']}).\n"
                              f"Dernière erreur : {errors['scan']['message']}"):
                errors.pop("scan")
                self.save_state()
        except Exception:  # noqa: BLE001
            log.exception("Impossible d'envoyer l'email de rétablissement")

    def recheck_retained(self):
        """Annonces déjà retenues (même déjà envoyées) qui ne passent plus les critères simples — filtre
        ajouté ou durci après coup (colocation, loyer, surface) : passées en « refusées »."""
        n = 0
        for v in self.state["listings"].values():
            if v["status"] in ("match", "notified", "silent"):
                reason = self.basic_reject(v["listing"])
                if reason:
                    v["status"], v["reason"], v["results"] = "rejected", f"{reason} (revérifiée)", []
                    # ascenseur inconnu : recalculée si elle réapparaît (la fiche complète peut le préciser)
                    v["crit"] = None if "non précisé" in reason else self.criteria_hash()
                    n += 1
                    continue
                reason = self.transfer_reject(v.get("results"))
                if reason:
                    # le trajet retenu ne passe plus ; s'il réapparaît, il sera recalculé (un autre trajet
                    # avec moins de correspondances peut convenir) sans être renvoyé une 2e fois
                    v["status"], v["reason"], v["crit"] = "rejected", f"{reason} (revérifiée)", None
                    n += 1
        if n:
            log.info("%d annonce(s) retenue(s) auparavant écartée(s) après revérification des critères.", n)
            self.save_state()

    def find_twin(self, listing):
        """Même logement déjà vu sur une autre source (Jinka ↔ Bien'ici) ?"""
        import bienici
        src = listing.get("alert_id") == "bienici"
        for v in self.state["listings"].values():
            other = v["listing"]
            if other["id"] == listing["id"] or (other.get("alert_id") == "bienici") == src:
                continue
            if v["status"] in ("notified", "match", "silent", "rejected") and bienici.same_flat(listing, other):
                return v
        return None

    def make_notifier(self):
        o = self.opts
        return Notifier(o.get("whatsapp_phone"), o.get("whatsapp_callmebot_apikey"),
                        o.get("ha_notify_service"), email_to=o.get("email_to"),
                        smtp_user=o.get("mail_user") or o.get("jinka_email"),
                        smtp_password=o.get("mail_password"), smtp_server=o.get("smtp_server"),
                        free_sms_user=o.get("free_sms_user"), free_sms_key=o.get("free_sms_key"))

    def send_test(self):
        """Bouton « notification de test » : envoie un message sur chaque canal et dit lequel marche."""
        self.opts = self.load_options()
        n = self.make_notifier()
        title = "🧪 Jinka Transit : test de notification"
        text = "Si tu lis ce message, ce canal fonctionne."
        results = []
        if n.email_ok:
            results.append(("email", n._email(title, text, f"<p>{text}</p>")))
        if n.sms_ok:
            results.append(("SMS Free", n._free_sms(f"{title}\n{text}")))
        if n.phone and n.apikey:
            results.append(("WhatsApp", n._whatsapp(f"*{title}*\n{text}")))
        if n.ha_service:
            results.append(("Home Assistant", n._home_assistant(title, text, None, None)))
        if not results:
            raise RuntimeError("Aucun canal de notification n'est configuré.")
        return results

    # ---------- connexion Jinka par code email ----------

    def jinka_send_code(self, email):
        from jinka_login import JinkaCodeLogin
        self.login = JinkaCodeLogin()
        self.login_email = email.strip().lower()
        self.login.send_code(self.login_email)

    def jinka_verify_code(self, code):
        if not self.login:
            raise JinkaAuthError("Demande d'abord un code.")
        token = self.login.verify_code(self.login_email, code)
        with self.lock:
            self.state["jinka_auth"] = {"token": token, "email": self.login_email, "at": time.time()}
            self.state["auth_alert_sent"] = False
            self.save_state()
        self.login = None
        self.scan_now.set()

    # ---------- recherche élargie (communes compatibles) ----------

    def explore_start(self, radius_km, max_minutes=None):
        if self.explore_progress.get("running"):
            return
        o = self.opts
        if not o.get("prim_api_key"):
            raise RuntimeError("Renseigne d'abord prim_api_key dans la configuration.")
        if not [d for d in o["destinations"] if not d["info_only"]]:
            raise RuntimeError("Aucune adresse de filtre configurée.")
        self.explore_progress = {"running": True, "done": 0, "total": 0, "error": None}
        threading.Thread(target=self._explore, args=(radius_km, max_minutes), daemon=True).start()

    def _explore(self, radius_km, max_minutes=None, from_retry=False):
        import explore
        try:
            transit = self.make_transit()
            with self.lock:
                dests = self.resolve_destinations(transit)
            cache = dict(self.state.get("explore_cache", {}))  # copie : le scan peut sauver en parallèle
            try:
                result = explore.run(transit, dests, radius_km, self.explore_progress, max_minutes, cache)
            finally:
                with self.lock:  # trajets déjà calculés gardés même en cas d'échec (quota…)
                    self.state["explore_cache"] = cache
                    self.save_state()
            with self.lock:
                self.state["explore"] = result
                self.state.pop("explore_retry", None)
                self.save_state()
            ok = sum(r["ok"] for r in result["results"])
            log.info("Recherche élargie terminée : %d communes compatibles sur %d.", ok, len(result["results"]))
            if from_retry:
                self.send_explore_report(result)
        except Exception as e:  # noqa: BLE001
            log.error("Recherche élargie échouée : %s", e)
            self.explore_progress["error"] = str(e)
            if isinstance(e, (HttpError, TransitError)) and "429" in str(e):
                with self.lock:
                    self.state["explore_retry"] = {"radius_km": radius_km, "max_minutes": max_minutes,
                                                   "day": datetime.now().strftime("%Y-%m-%d")}
                    self.save_state()
                self.explore_progress["error"] = ("Quota IDFM du jour atteint : la recherche sera relancée "
                                                  "automatiquement cette nuit, résultat envoyé par email.")
        finally:
            self.explore_progress["running"] = False

    def explore_retry_due(self, now=None):
        r = self.state.get("explore_retry")
        return bool(r) and (now or datetime.now()).strftime("%Y-%m-%d") != r["day"]

    def send_explore_report(self, result):
        """Email du résultat d'une recherche élargie relancée la nuit : communes par tranche de temps compté."""
        o = self.opts
        n = Notifier(email_to=o.get("daily_report_email") or o.get("error_email"),
                     smtp_user=o.get("mail_user") or o.get("jinka_email"),
                     smtp_password=o.get("mail_password"), smtp_server=o.get("smtp_server"))
        if not n.email_ok:
            return
        bands = [("≤ 45 min", 0, 45), ("45 à 50 min", 46, 49), ("50 à 60 min", 50, 60), ("60 à 75 min", 61, 75)]
        lines = [f"Recherche élargie (rayon {result['radius_km']} km, max {result['max_minutes']} min, "
                 "correspondances comptées) — pire des deux trajets, depuis le centre de la commune :", ""]
        for label, lo, hi in bands:
            names = [f"{r['nom']} ({r['worst']})" for r in result["results"]
                     if r["ok"] and r["worst"] is not None and lo <= r["worst"] <= hi]
            lines += [f"{label} — {len(names)} communes :", ", ".join(names) or "—", ""]
        n._email("🗺️ Jinka Transit : recherche élargie terminée", "\n".join(lines))

    # ---------- reconnexion automatique (code lu dans la boîte mail dédiée) ----------

    def auto_login_possible(self, force=False):
        o = self.opts
        return bool(o.get("jinka_email") and o.get("mail_password") and not o.get("jinka_token")
                    and (force or time.time() - self.state.get("auto_login_at", 0) > 20 * 60))

    def auto_login_now(self):
        """Bouton « Tester la connexion automatique » : sans attendre le délai de 20 min."""
        self.opts = self.load_options()
        if not self.auto_login_possible(force=True):
            raise JinkaAuthError("Renseigne d'abord « Email Jinka » et la clé d'application Google dans la configuration.")
        if self.auto_test_running:
            return

        def run():
            self.auto_test_running = True
            try:
                with self.lock:
                    self.auto_login()
                self.flash = "<span class='ok'>✅ Connexion automatique réussie : code lu dans la boîte mail. Scan lancé.</span>"
                self.scan_now.set()
            except Exception as e:  # noqa: BLE001
                log.error("Test de connexion automatique échoué : %s", e)
                import html
                self.flash = f"<span class='ko'>❌ {html.escape(str(e))}</span>"
            finally:
                self.auto_test_running = False

        threading.Thread(target=run, daemon=True).start()

    def auto_login(self):
        """Demande un code à Jinka, le lit dans la boîte mail et se connecte. Une tentative / 20 min."""
        from jinka_login import JinkaCodeLogin
        import mailbox
        o = self.opts
        email_addr = o["jinka_email"].strip().lower()
        self.state["auto_login_at"] = time.time()
        log.info("Connexion automatique à Jinka : demande d'un code pour %s.", email_addr)
        login = JinkaCodeLogin()
        since = time.time()
        login.send_code(email_addr)
        user = o.get("mail_user") or email_addr
        code = mailbox.wait_for_code(mailbox.imap_host(user, o.get("mail_imap_server")), user,
                                     o["mail_password"], since)
        token = login.verify_code(email_addr, code)
        self.state["jinka_auth"] = {"token": token, "email": email_addr, "at": time.time(), "auto": True}
        self.save_state()
        log.info("Connexion automatique à Jinka réussie.")
        return token

    @staticmethod
    def window_left(spec, now):
        """Secondes restantes dans la plage "HH:MM-HH:MM" (0 si on est en dehors ou si la plage est vide/invalide)."""
        spec = (spec or "").strip()
        if not spec:
            return 0
        try:
            start, end = [datetime.strptime(x.strip(), "%H:%M").time() for x in spec.split("-")]
        except ValueError:
            log.warning("Plage horaire invalide : %r (attendu par ex. 00:00-07:00)", spec)
            return 0
        t = now.time()
        inside = start <= t < end if start <= end else (t >= start or t < end)
        if not inside:
            return 0
        end_dt = now.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
        if end_dt <= now:
            end_dt += timedelta(days=1)
        return int((end_dt - now).total_seconds())

    def quiet_seconds_left(self, now=None):
        """Secondes restantes de la pause nocturne (0 hors pause)."""
        return self.window_left(self.opts.get("quiet_hours"), now or datetime.now())

    def scan_interval(self, now=None):
        """Intervalle (s) jusqu'au prochain scan : court en journée (peak_hours), normal sinon."""
        now = now or datetime.now()
        normal = max(5, int(self.opts.get("scan_interval_minutes", 15))) * 60
        peak_left = self.window_left(self.opts.get("peak_hours"), now)
        jitter = random.uniform(0.85, 1.15)  # rythme irrégulier, moins « robot »
        if peak_left:
            return int(min(max(5, int(self.opts.get("peak_interval_minutes") or 5)) * 60, normal) * jitter)
        normal = int(normal * jitter)
        # on ne déborde pas sur le début de la plage de journée ni de la nuit
        nxt = [normal]
        for spec in (self.opts.get("peak_hours"), self.opts.get("quiet_hours"), self.opts.get("daily_report_time")):
            try:
                start = datetime.strptime((spec or "").split("-")[0].strip(), "%H:%M")
            except ValueError:
                continue
            start_dt = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
            if start_dt <= now:
                start_dt += timedelta(days=1)
            nxt.append(max(60, int((start_dt - now).total_seconds())))
        return min(nxt)

    def loop(self):
        manual = False
        while True:
            self.opts = self.load_options()
            quiet = self.quiet_seconds_left()
            if quiet and not manual and self.explore_retry_due() and not self.explore_progress.get("running"):
                r = self.state.pop("explore_retry")  # remis par _explore si le quota est encore atteint
                log.info("Pause nocturne : relance de la recherche élargie (quota IDFM renouvelé).")
                self.explore_progress = {"running": True, "done": 0, "total": 0, "error": None}
                self._explore(r["radius_km"], r["max_minutes"], from_retry=True)
                continue
            if quiet and not manual:
                log.info("Pause nocturne (%s) : reprise des scans dans %d min.",
                         self.opts.get("quiet_hours"), quiet // 60 + 1)
                manual = self.scan_now.wait(quiet + 5)
                self.scan_now.clear()
                continue
            self.run_once()
            if self.daily_report_due():
                try:
                    with self.lock:
                        self.send_daily_report()
                except Exception:  # noqa: BLE001
                    log.exception("Rapport quotidien non envoyé")
            manual = self.scan_now.wait(self.scan_interval())
            self.scan_now.clear()


if __name__ == "__main__":
    app = App()
    web.start(app, port=int(os.environ.get("WEB_PORT", "8099")))
    app.loop()
