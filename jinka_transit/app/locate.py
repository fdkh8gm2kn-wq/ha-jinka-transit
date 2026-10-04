"""Localisation de secours pour les annonces sans coordonnées GPS.

Ordre : 1) coordonnées de la fiche complète jinka.fr, 2) « stations proches » indiquées par Jinka,
3) station citée dans le texte de l'annonce (« gare de La Garenne-Colombes », « métro Nation »…).
Les stations sont localisées via le référentiel IDFM et validées par la distance au centre de la commune."""

import json
import logging
import math
import re
import time
from html import unescape

from http_util import HttpError, get_json, request

log = logging.getLogger("locate")

STATION_RE = re.compile(
    r"(?i:\b(gare|station|m[ée]tro|rer(?:\s+[a-e])?|tram(?:way)?(?:\s+t\d+[a-c]?)?)\b)\s*[:\-–]?\s*"
    r"(?:(de|du|des|d['’])\s*)?"
    r"((?:(?:la|le|les|l['’])\s*)?[A-ZÉÈÊÀÂÎÔÛÇ][\w'’\-]*(?:[\s\-]+(?:(?:de|du|des|la|le|les|sur|sous|en|et|d['’])[\s\-]*)?"
    r"[A-ZÉÈÊÀÂÎÔÛÇ][\w'’\-]*){0,3})")
NOT_STATIONS = {"ligne", "rer", "métro", "metro", "gare", "paris", "proche", "idéal", "ideal"}


def fetch_ad_detail(uuid):
    """Lit la fiche publique jinka.fr/ad/<uuid> : coordonnées, stations proches, description complète."""
    try:
        _, text = request(f"https://www.jinka.fr/ad/{uuid}", headers={"Accept": "text/html"}, timeout=30)
    except (HttpError, OSError) as e:
        log.warning("Fiche Jinka %s illisible : %s", uuid, e)
        return {}
    h = text.replace('\\"', '"')
    out = {}
    m = re.search(r'"lat":([-0-9.]+|null),"lng":([-0-9.]+|null)', h)
    if m and m.group(1) != "null":
        out["lat"], out["lng"] = float(m.group(1)), float(m.group(2))
    m = re.search(r'"stops":(\[.*?\]),"', h)
    if m:
        try:
            out["stops"] = json.loads(m.group(1))
        except ValueError:
            pass
    m = re.search(r'"description":"(.*?)","description_is_truncated"', h, re.S)
    if m:
        try:
            out["description"] = json.loads(f'"{m.group(1)}"')
        except ValueError:
            out["description"] = m.group(1)
    if not out.get("description") or re.fullmatch(r"\$\w+", out["description"]):
        # description rendue ailleurs dans la page : on prend la balise meta (début du texte)
        m = re.search(r'<meta name="description" content="([^"]*)"', text)
        out["description"] = unescape(m.group(1)) if m else ""
    return out


def stations_in_text(text):
    """Stations citées dans le texte, dans l'ordre d'apparition (requêtes à essayer)."""
    found = []
    for m in STATION_RE.finditer(text or ""):
        kind, prep, name = m.group(1), m.group(2), m.group(3).strip(" -–'’")
        if not name or name.lower() in NOT_STATIONS or len(name) < 3 or re.fullmatch(r"[TtMmAaBbCcDdEe]?\d+[a-cA-C]?", name):
            continue
        queries = []
        if kind.lower() == "gare" and prep:
            queries.append(f"Gare {prep} {name}".replace("d' ", "d'"))
        queries.append(name)
        if queries not in [q for q, _ in found]:
            found.append((queries, f"{kind} {prep + ' ' if prep else ''}{name}"))
    return found


def distance_km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def commune_centre(postal_code, city, cache):
    key = f"cp:{postal_code}|{city}"
    if key in cache:
        return cache[key]
    centre = None
    try:
        rows = get_json("https://geo.api.gouv.fr/communes",
                        params={"codePostal": postal_code, "fields": "nom,centre"}) or []
        norm = lambda s: re.sub(r"[^a-z]", "", (s or "").lower().replace("saint", "st"))
        row = next((r for r in rows if norm(r["nom"]) == norm(city)), rows[0] if rows else None)
        if row and row.get("centre"):
            lon, lat = row["centre"]["coordinates"]
            centre = {"lat": lat, "lon": lon}
    except (HttpError, OSError, ValueError) as e:
        log.warning("Centre de la commune %s introuvable : %s", postal_code, e)
    cache[key] = centre
    return centre


def find_station(transit, queries, centre, cache, max_km=6):
    """Cherche la station dans le référentiel IDFM, près de la commune de l'annonce."""
    for q in queries:
        key = f"stop:{q.lower()}"
        places = cache.get(key)
        if places is None:
            data = transit._prim("/places", {"q": q, "type[]": ["stop_area"], "count": 5}) or {}
            places = [{"name": p.get("name"), "lat": float(p["stop_area"]["coord"]["lat"]),
                       "lon": float(p["stop_area"]["coord"]["lon"])}
                      for p in data.get("places") or [] if p.get("stop_area", {}).get("coord")]
            cache[key] = places
        for p in places:
            if centre is None or distance_km(p["lat"], p["lon"], centre["lat"], centre["lon"]) <= max_km:
                return p
    return None


def locate(listing, transit, cache):
    """Complète une annonce sans GPS. Renvoie (lat, lon, note) ou None ; note = texte affiché."""
    detail = fetch_ad_detail(listing["uuid"]) if listing.get("uuid") else {}
    time.sleep(0.3)
    if detail.get("lat") is not None:
        return detail["lat"], detail["lng"], None
    centre = commune_centre(listing.get("postal_code"), listing.get("city"), cache) \
        if listing.get("postal_code") else None
    for stop in detail.get("stops") or listing.get("stops") or []:
        lines = ", ".join(stop.get("lines") or [])
        st = find_station(transit, [stop.get("name", "")], centre, cache)
        if st:
            return st["lat"], st["lon"], f"près de la station {st['name']}{f' ({lines})' if lines else ''}, indiquée par Jinka"
    text = detail.get("description") or listing.get("description") or ""
    for queries, said in stations_in_text(text):
        st = find_station(transit, queries, centre, cache)
        if st:
            return st["lat"], st["lon"], f"près de {st['name']} (« {said} » dans l'annonce)"
    return None
