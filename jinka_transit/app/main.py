"""Jinka Transit : surveille tes alertes Jinka et ne t'envoie que les annonces d'où tes
adresses (bureau, école…) sont joignables en métro/RER (sans bus) dans le temps voulu."""

import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime

from fmt import body_of, title_of
from jinka import Jinka, JinkaAuthError
from notify import Notifier
from transit import CACHE_TTL, Transit, TransitError
import web

OPTIONS_PATH = os.environ.get("OPTIONS_PATH", "/data/options.json")
STATE_PATH = os.environ.get("STATE_PATH", "/data/state.json")
MAX_DESTINATIONS = 5
MAX_STATE_ENTRIES = 3000

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
log = logging.getLogger("main")


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
                          o.get("max_walk_minutes"), o.get("max_rent", 0)], sort_keys=True)
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def load_state(self):
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"listings": {}, "geocode": {}, "first_run_done": False, "auth_alert_sent": False}

    def save_state(self):
        items = self.state["listings"]
        if len(items) > MAX_STATE_ENTRIES:
            keep = sorted(items.items(), key=lambda kv: kv[1].get("ts", 0), reverse=True)
            self.state["listings"] = dict(keep[:MAX_STATE_ENTRIES])
        cutoff = time.time() - CACHE_TTL
        self.state["journeys"] = {k: v for k, v in self.state.get("journeys", {}).items() if v["ts"] > cutoff}
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

    def evaluate(self, listing, dests, transit):
        max_rent = int(self.opts.get("max_rent") or 0)
        if max_rent and listing.get("rent") and listing["rent"] > max_rent:
            return "rejected", [], f"loyer {int(listing['rent'])} € > {max_rent} €"
        if listing["lat"] is None or listing["lng"] is None:
            return "rejected", [], "pas de coordonnées GPS dans l'annonce"
        cache = self.state.setdefault("journeys", {})
        results = []
        # D'abord les adresses du filtre ; les adresses « pour info » ne sont calculées
        # que pour les annonces retenues (économise les appels IDFM)
        for d in [d for d in dests if not d["info_only"]]:
            r = transit.journey(listing["lat"], listing["lng"], d["lat"], d["lon"],
                                d["arrival_time"], d["max_minutes"], cache=cache)
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
        jinka = Jinka(o.get("jinka_email"), o.get("jinka_password"), token)
        transit = Transit(o["prim_api_key"], o.get("allowed_modes"), o.get("max_walk_minutes", 15))
        notifier = Notifier(o.get("whatsapp_phone"), o.get("whatsapp_callmebot_apikey"),
                            o.get("ha_notify_service"))
        dests = self.resolve_destinations(transit)
        crit = self.criteria_hash()

        done = {k for k, v in self.state["listings"].items()
                if v["status"] in ("notified", "silent") or v.get("crit") == crit}
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

        stats = {"annonces": len(listings), "évaluées": 0, "ok": 0, "refusées": 0}
        known = self.state["listings"]
        for l in listings:
            prev = known.get(l["id"])
            if prev and prev["status"] in ("notified", "silent"):
                continue
            if prev and prev["status"] in ("rejected", "match") and prev.get("crit") == crit:
                continue
            if l["expired"] or l["deleted"]:
                continue
            try:
                status, results, reason = self.evaluate(l, dests, transit)
            except TransitError as e:
                log.error("Calcul d'itinéraire impossible (%s) : on réessaiera au prochain scan.", e)
                break
            stats["évaluées"] += 1
            stats["ok" if status == "match" else "refusées"] += 1
            known[l["id"]] = {"status": status, "crit": crit, "listing": l, "results": results,
                              "reason": reason, "ts": time.time()}
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
            if notifier.send(title_of(v["listing"]), body_of(v), v["listing"]["link"], v["listing"]["image"]):
                v["status"] = "notified"
                v["notified_at"] = time.time()
                sent += 1
        if len(pending) > limit:
            log.info("%d annonces en attente, envoyées aux prochains scans.", len(pending) - limit)
        self.state["first_run_done"] = True
        self.save_state()
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
            except Exception as e:  # noqa: BLE001 - on veut que la boucle survive
                if isinstance(e, (RuntimeError, JinkaAuthError, TransitError, ValueError)):
                    log.error("Scan échoué : %s", e)
                else:
                    log.exception("Scan échoué : %s", e)
                self.last_scan = {"at": started.isoformat(timespec="seconds"), "error": str(e),
                                  "status": "erreur"}

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

    def explore_start(self, radius_km):
        if self.explore_progress.get("running"):
            return
        o = self.opts
        if not o.get("prim_api_key"):
            raise RuntimeError("Renseigne d'abord prim_api_key dans la configuration.")
        if not [d for d in o["destinations"] if not d["info_only"]]:
            raise RuntimeError("Aucune adresse de filtre configurée.")
        self.explore_progress = {"running": True, "done": 0, "total": 0, "error": None}
        threading.Thread(target=self._explore, args=(radius_km,), daemon=True).start()

    def _explore(self, radius_km):
        import explore
        try:
            transit = Transit(self.opts["prim_api_key"], self.opts.get("allowed_modes"),
                              self.opts.get("max_walk_minutes", 15))
            with self.lock:
                dests = self.resolve_destinations(transit)
            result = explore.run(transit, dests, radius_km, self.explore_progress)
            with self.lock:
                self.state["explore"] = result
                self.save_state()
            ok = sum(r["ok"] for r in result["results"])
            log.info("Recherche élargie terminée : %d communes compatibles sur %d.", ok, len(result["results"]))
        except Exception as e:  # noqa: BLE001
            log.error("Recherche élargie échouée : %s", e)
            self.explore_progress["error"] = str(e)
        finally:
            self.explore_progress["running"] = False

    # ---------- reconnexion automatique (code lu dans la boîte mail dédiée) ----------

    def auto_login_possible(self):
        o = self.opts
        return bool(o.get("jinka_email") and o.get("mail_password") and not o.get("jinka_token")
                    and time.time() - self.state.get("auto_login_at", 0) > 20 * 60)

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

    def loop(self):
        while True:
            self.run_once()
            interval = max(5, int(self.opts.get("scan_interval_minutes", 15))) * 60
            self.scan_now.wait(interval)
            self.scan_now.clear()


if __name__ == "__main__":
    app = App()
    web.start(app, port=int(os.environ.get("WEB_PORT", "8099")))
    app.loop()
