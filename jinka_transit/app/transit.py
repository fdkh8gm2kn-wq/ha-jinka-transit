"""Géocodage des adresses et calcul d'itinéraires via l'API PRIM d'Île-de-France Mobilités (Navitia)."""

import datetime as dt
import json
import logging
import re
import time

from http_util import HttpError, get_json

PRIM = "https://prim.iledefrance-mobilites.fr/marketplace/v2/navitia"
BAN = "https://data.geopf.fr/geocodage/search"
BAN_FALLBACK = "https://api-adresse.data.gouv.fr/search/"

# Modes autorisables -> "physical modes" Navitia d'IDFM
MODE_MAP = {
    "metro": {"Metro", "Funicular"},
    "rer": {"RapidTransit"},
    "transilien": {"LocalTrain", "Train"},
    "tram": {"Tramway"},
}
# Modes existants qu'on interdit explicitement s'ils ne sont pas autorisés (le bus l'est toujours)
KNOWN_MODES = {"Bus", "Metro", "Funicular", "RapidTransit", "LocalTrain", "Train", "Tramway"}

# Durée de validité d'un trajet en cache (les horaires bougent peu)
CACHE_TTL = 30 * 24 * 3600

log = logging.getLogger("transit")


class TransitError(Exception):
    """Erreur temporaire (réseau, quota…) : l'annonce sera réévaluée au prochain scan."""


def next_weekday(hhmm, now=None):
    """Prochain jour ouvré (à partir de demain) à l'heure donnée, au format Navitia."""
    now = now or dt.datetime.now()
    day = now.date() + dt.timedelta(days=1)
    while day.weekday() >= 5:
        day += dt.timedelta(days=1)
    h, m = (int(x) for x in hhmm.split(":"))
    return dt.datetime.combine(day, dt.time(h, m)).strftime("%Y%m%dT%H%M%S")


class Transit:
    def __init__(self, api_key, allowed_modes, max_walk_minutes=15):
        self.api_key = api_key
        self.allowed = set()
        for m in allowed_modes or ["metro", "rer"]:
            self.allowed |= MODE_MAP.get(m, set())
        self.forbidden = sorted(KNOWN_MODES - self.allowed)
        self.max_walk = int(max_walk_minutes)
        self.api_calls = 0
        self.cache_hits = 0

    def _prim(self, path, params):
        try:
            return get_json(f"{PRIM}{path}", params=params, headers={"apikey": self.api_key})
        except HttpError as e:
            if e.status in (401, 403):
                raise TransitError("Clé API PRIM refusée : vérifie prim_api_key.") from None
            if e.status == 404:
                # Navitia renvoie 404 + {"error": {"id": "no_solution"}} quand aucun trajet n'existe
                try:
                    return json.loads(e.body)
                except ValueError:
                    pass
            raise TransitError(str(e)) from None
        except OSError as e:
            raise TransitError(f"Réseau : {e}") from None

    # ---------- géocodage ----------

    def geocode(self, address):
        """Renvoie {"lat", "lon", "label"} pour une adresse, une gare ou "lat,lon"."""
        m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*[,;]\s*(-?\d+(?:\.\d+)?)\s*$", address)
        if m:
            return {"lat": float(m.group(1)), "lon": float(m.group(2)), "label": address.strip()}

        looks_like_stop = re.match(r"^\s*(gare|station|métro|metro|rer)\b", address, re.I)
        ban = None
        if not looks_like_stop:
            data = {}
            for url in (BAN, BAN, BAN_FALLBACK):  # le service IGN répond parfois en 504
                try:
                    data = get_json(url, params={"q": address, "limit": 1}) or {}
                    break
                except (HttpError, OSError) as e:
                    log.warning("Géocodage de %r : %s, nouvel essai", address, e)
                    time.sleep(2)
            feats = data.get("features") or []
            if feats:
                f = feats[0]
                lon, lat = f["geometry"]["coordinates"]
                ban = {"lat": lat, "lon": lon, "label": f["properties"].get("label"),
                       "score": f["properties"].get("score", 0)}
                if ban["score"] >= 0.6:
                    return ban

        # Gare / station / lieu : recherche dans le référentiel IDFM
        data = self._prim("/places", {"q": address, "type[]": ["stop_area", "address", "poi"], "count": 1})
        places = (data or {}).get("places") or []
        if places:
            p = places[0]
            coord = (p.get(p.get("embedded_type")) or {}).get("coord") or {}
            if coord:
                return {"lat": float(coord["lat"]), "lon": float(coord["lon"]), "label": p.get("name")}
        if ban:
            return ban
        raise ValueError(f"Adresse introuvable : {address!r}")

    # ---------- itinéraires ----------

    def journey(self, from_lat, from_lon, to_lat, to_lon, arrival_hhmm, max_minutes, cache=None):
        """Meilleur trajet en transports lourds uniquement.

        Renvoie {"ok", "minutes", "summary", "steps", "reason"}. "ok" vaut True si un trajet sans bus
        existe et dure au plus max_minutes (marche comprise). Le trajet brut est mis en cache : changer
        une durée max ne relance aucun appel à l'API."""
        key = (f"{from_lat:.4f},{from_lon:.4f}>{to_lat:.5f},{to_lon:.5f}@{arrival_hhmm}"
               f"|{','.join(sorted(self.allowed))}|{self.max_walk}")
        hit = cache.get(key) if cache is not None else None
        if hit and time.time() - hit["ts"] < CACHE_TTL:
            best, self.cache_hits = hit["best"], self.cache_hits + 1
        else:
            best = self._fetch_best(from_lat, from_lon, to_lat, to_lon, arrival_hhmm)
            if cache is not None:
                cache[key] = {"best": best, "ts": time.time()}
        if best.get("none"):
            err = best.get("error")
            return {"ok": False, "minutes": None, "summary": "", "steps": [],
                    "reason": "aucun trajet métro/RER" + (f" ({err})" if err else "")}
        r = dict(best)
        r["ok"] = r["minutes"] <= max_minutes
        r["reason"] = "" if r["ok"] else f"{r['minutes']} min > {max_minutes} min"
        return r

    def _fetch_best(self, from_lat, from_lon, to_lat, to_lon, arrival_hhmm):
        params = [
            ("from", f"{from_lon:.6f};{from_lat:.6f}"),
            ("to", f"{to_lon:.6f};{to_lat:.6f}"),
            ("datetime", next_weekday(arrival_hhmm)),
            ("datetime_represents", "arrival"),
            ("first_section_mode[]", "walking"),
            ("last_section_mode[]", "walking"),
            ("max_walking_duration_to_pt", self.max_walk * 60),
            ("count", 5),
        ]
        params += [("forbidden_uris[]", f"physical_mode:{m}") for m in self.forbidden]
        self.api_calls += 1
        data = self._prim("/journeys", params) or {}
        best = None
        for j in data.get("journeys") or []:
            check = self.check_journey(j)
            if check["valid"] and (best is None or check["minutes"] < best["minutes"]):
                best = check
        if best is None:
            return {"none": True, "error": (data.get("error") or {}).get("message")}
        best.pop("valid")
        return best

    def check_journey(self, j):
        """Valide un trajet (aucun mode interdit) et le détaille étape par étape."""
        lines, steps, walk = [], [], 0
        for s in j.get("sections") or []:
            t = s.get("type")
            dur = s.get("duration", 0)
            if t == "public_transport":
                pm = next((l.get("id", "") for l in s.get("links") or []
                           if l.get("type") == "physical_mode"), "")
                pm = pm.split(":")[-1]
                if pm not in self.allowed:
                    return {"valid": False}
                di = s.get("display_informations") or {}
                label = f"{di.get('commercial_mode', pm)} {di.get('code') or ''}".strip()
                if not lines or lines[-1] != label:
                    lines.append(label)
                steps.append({"kind": "ride", "mode": pm, "line": label, "direction": di.get("direction"),
                              "from": place_name(s.get("from")), "to": place_name(s.get("to")),
                              "stops": max(0, len(s.get("stop_date_times") or []) - 1),
                              "minutes": round(dur / 60)})
            elif t in ("street_network", "crow_fly"):
                if s.get("mode", "walking") != "walking":
                    return {"valid": False}
                walk += dur
                if dur > 0:
                    steps.append({"kind": "walk", "minutes": max(1, round(dur / 60)),
                                  "meters": walk_length(s), "from": place_name(s.get("from")),
                                  "to": place_name(s.get("to"))})
            elif t == "transfer":
                walk += dur
                steps.append({"kind": "transfer", "minutes": round(dur / 60), "meters": walk_length(s),
                              "at": place_name(s.get("to")) or place_name(s.get("from"))})
            elif t == "waiting":
                if steps and steps[-1]["kind"] == "transfer":
                    steps[-1]["wait"] = round(dur / 60)
                else:
                    steps.append({"kind": "transfer", "minutes": 0, "wait": round(dur / 60),
                                  "at": steps[-1].get("to") if steps else ""})
            else:  # transport à la demande, vélo, etc.
                return {"valid": False}
        # La 1re et la dernière marche désignent le logement / la destination
        walks = [st for st in steps if st["kind"] == "walk"]
        if walks and steps[0] is walks[0] and len(steps) > 1:
            steps[0]["role"] = "start"
        if walks and steps[-1] is walks[-1] and len(steps) > 1:
            steps[-1]["role"] = "end"
        minutes = round(j.get("duration", 0) / 60)
        summary = " → ".join(lines) if lines else "à pied"
        return {"valid": True, "minutes": minutes, "walk_minutes": round(walk / 60),
                "transfers": j.get("nb_transfers", 0), "summary": summary, "steps": steps}


def place_name(p):
    """Nom lisible d'un point Navitia (on préfère le nom de la station au nom de l'arrêt)."""
    if not p:
        return ""
    for key in ("stop_point", "stop_area"):
        obj = p.get(key)
        if obj:
            return (obj.get("stop_area") or {}).get("name") or obj.get("name") or p.get("name", "")
    return p.get("name", "")


def walk_length(s):
    """Distance à pied en mètres (fournie par Navitia, sinon estimée à 4,5 km/h)."""
    path = s.get("path") or []
    if path and all("length" in p for p in path):
        return int(sum(p["length"] for p in path))
    props = (s.get("geojson") or {}).get("properties") or []
    if props and props[0].get("length"):
        return int(props[0]["length"])
    return int(s.get("duration", 0) * 1.25)
