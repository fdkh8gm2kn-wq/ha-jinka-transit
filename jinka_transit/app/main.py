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
from transit import Transit, TransitError
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
        opts["destinations"] = dests
        return opts

    def criteria_hash(self):
        """Empreinte des critères : si tu changes une durée/adresse/mode, les annonces
        refusées auparavant sont réévaluées."""
        o = self.opts
        key = json.dumps([o["destinations"], sorted(o.get("allowed_modes") or []),
                          o.get("max_walk_minutes")], sort_keys=True)
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
        if listing["lat"] is None or listing["lng"] is None:
            return "rejected", [], "pas de coordonnées GPS dans l'annonce"
        results = []
        for d in dests:
            r = transit.journey(listing["lat"], listing["lng"], d["lat"], d["lon"],
                                d["arrival_time"], d["max_minutes"])
            results.append({"name": d["name"], "max": d["max_minutes"], **r})
            if not r["ok"]:
                return "rejected", results, f"{d['name']} : {r['reason']}"
        return "match", results, ""

    def scan(self):
        o = self.opts
        if not o["destinations"]:
            raise RuntimeError("Aucune adresse configurée (destinations).")
        if not o.get("prim_api_key"):
            raise RuntimeError("prim_api_key manquante (clé gratuite sur prim.iledefrance-mobilites.fr).")

        jinka = Jinka(o.get("jinka_email"), o.get("jinka_password"), o.get("jinka_token"))
        transit = Transit(o["prim_api_key"], o.get("allowed_modes"), o.get("max_walk_minutes", 15))
        notifier = Notifier(o.get("whatsapp_phone"), o.get("whatsapp_callmebot_apikey"),
                            o.get("ha_notify_service"))
        dests = self.resolve_destinations(transit)
        crit = self.criteria_hash()

        try:
            listings = jinka.listings(o.get("jinka_alerts"), int(o.get("max_pages_per_alert", 3)))
            self.state["auth_alert_sent"] = False
        except JinkaAuthError as e:
            if not self.state.get("auth_alert_sent"):
                notifier.send("Jinka Transit : connexion Jinka impossible", str(e))
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
