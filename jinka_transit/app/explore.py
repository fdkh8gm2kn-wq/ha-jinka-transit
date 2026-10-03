"""Recherche élargie : quelles communes (et arrondissements de Paris) respectent déjà les critères
de trajet, indépendamment des annonces Jinka. Sert à choisir le secteur des alertes Jinka.

Chaque commune est évaluée depuis son centre (marche jusqu'à la station comprise)."""

import logging
import math
import time

from http_util import get_json

GEO = "https://geo.api.gouv.fr"
DEPARTEMENTS = ["92", "93", "94", "91", "78", "95", "77"]
FIELDS = "nom,code,centre,population,codesPostaux"

log = logging.getLogger("explore")


def distance_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def candidates(center_lat, center_lon, radius_km, min_population=1000):
    """Arrondissements de Paris + communes d'Île-de-France dans le rayon donné."""
    raw = get_json(f"{GEO}/communes", params={"codeDepartement": "75", "type": "arrondissement-municipal",
                                              "fields": FIELDS}) or []
    for dep in DEPARTEMENTS:
        raw += get_json(f"{GEO}/departements/{dep}/communes", params={"fields": FIELDS}) or []
    out = []
    for c in raw:
        if not c.get("centre") or c.get("code") == "75056":  # « Paris » entier : on a les arrondissements
            continue
        lon, lat = c["centre"]["coordinates"]
        dist = distance_km(center_lat, center_lon, lat, lon)
        if dist <= radius_km and (c.get("population") or 0) >= min_population:
            out.append({"nom": c["nom"].replace(" Arrondissement", ""), "code": c["code"],
                        "cp": ", ".join(c.get("codesPostaux") or [])[:40], "lat": lat, "lon": lon,
                        "population": c.get("population") or 0, "distance_km": round(dist, 1)})
    out.sort(key=lambda c: c["distance_km"])
    return out


def run(transit, dests, radius_km, progress, pause=0.25):
    """Évalue chaque commune vers toutes les adresses du filtre. `progress` est un dict mis à jour
    au fil de l'eau (affiché dans la page)."""
    filt = [d for d in dests if not d["info_only"]]
    lat0 = sum(d["lat"] for d in filt) / len(filt)
    lon0 = sum(d["lon"] for d in filt) / len(filt)
    communes = candidates(lat0, lon0, radius_km)
    progress.update(total=len(communes), done=0)
    cache, results = {}, []
    for c in communes:
        trips, ok = [], True
        for d in filt:
            before = transit.api_calls
            r = transit.journey(c["lat"], c["lon"], d["lat"], d["lon"], d["arrival_time"],
                                d["max_minutes"], cache=cache)
            if transit.api_calls > before and pause:
                time.sleep(pause)  # reste sous les limites de débit de l'API IDFM
            trips.append({"name": d["name"], "max": d["max_minutes"], "ok": r["ok"],
                          "minutes": r.get("minutes"), "summary": r.get("summary") or r.get("reason", ""),
                          "walk_minutes": r.get("walk_minutes")})
            ok = ok and r["ok"]
        results.append({**c, "ok": ok, "trips": trips,
                        "worst": max((t["minutes"] for t in trips if t["minutes"] is not None), default=None)})
        progress["done"] += 1
    results.sort(key=lambda c: (not c["ok"], c["worst"] if c["worst"] is not None else 999))
    return {"at": time.time(), "radius_km": radius_km, "results": results, "api_calls": transit.api_calls,
            "dests": [{"name": d["name"], "max": d["max_minutes"], "lat": d["lat"], "lon": d["lon"]} for d in filt]}

