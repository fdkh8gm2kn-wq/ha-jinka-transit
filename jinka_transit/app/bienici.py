"""Source Bien'ici : mêmes critères que les alertes Jinka, via l'API JSON publique du site.

Les zones (communes des alertes Jinka) sont dans bienici_zones.json : {nom de commune: [zoneIds]}.
Les annonces sont converties au même format que celles de Jinka (voir jinka.normalize)."""

import json
import logging
import os
import re
import time
import urllib.parse

from http_util import HttpError, get_json

API = "https://www.bienici.com/realEstateAds.json"
SUGGEST = "https://res.bienici.com/suggest.json"
ZONES_FILE = os.path.join(os.path.dirname(__file__), "bienici_zones.json")
PRECISE_RADIUS_M = 150  # au-delà, la position est floutée : on la considère comme inconnue

log = logging.getLogger("bienici")


def default_zones():
    with open(ZONES_FILE, encoding="utf-8") as f:
        return json.load(f)


def zones_for(communes, cache):
    """Zones Bien'ici pour une liste de communes (noms), via la recherche du site, mise en cache."""
    known = default_zones()
    out = []
    for name in communes:
        if name in known:
            out += known[name]
            continue
        key = f"bienici:{name.lower()}"
        if key not in cache:
            try:
                s = get_json(SUGGEST, params={"q": name}) or []
                s = [x for x in s if x.get("type") in ("city", "district")]
                cache[key] = s[0]["zoneIds"] if s else []
            except (HttpError, OSError, ValueError) as e:
                log.warning("Zone Bien'ici introuvable pour %s : %s", name, e)
                cache[key] = []
        out += cache[key]
    return out


def listings(zone_ids, max_rent=0, min_area=0, furnished=True, max_ads=300):
    """Annonces de location, les plus récentes d'abord, au format commun."""
    filters = {"size": 100, "from": 0, "filterType": "rent", "propertyType": ["flat", "house"],
               "sortBy": "publicationDate", "sortOrder": "desc", "onTheMarket": [True],
               "zoneIdsByTypes": {"zoneIds": zone_ids}}
    if max_rent:
        filters["maxPrice"] = max_rent
    if min_area:
        filters["minArea"] = min_area
    if furnished:
        filters["isFurnished"] = True
    out = []
    while len(out) < max_ads:
        data = get_json(API, params={"filters": json.dumps(filters)}) or {}
        ads = data.get("realEstateAds") or []
        out += [normalize(a) for a in ads]
        filters["from"] += len(ads)
        if not ads or filters["from"] >= int(data.get("total") or 0):
            break
        time.sleep(0.5)
    return out


def normalize(ad):
    blur = ad.get("blurInfo") or {}
    pos = blur.get("position") or {}
    precise = blur.get("type") == "point" or (blur.get("radius") or 10 ** 6) <= PRECISE_RADIUS_M
    photos = ad.get("photos") or []
    safety = [label for key, label in (("hasCaretaker", "gardien"), ("hasDoorCode", "digicode"),
                                       ("hasIntercom", "interphone")) if ad.get(key)]
    return {
        "id": f"bienici:{ad.get('id')}",
        "alert_id": "bienici",
        "alert_name": "Bien'ici",
        "rent": ad.get("price"),
        "area": ad.get("surfaceArea"),
        "rooms": ad.get("roomsQuantity"),
        "bedrooms": ad.get("bedroomsQuantity"),
        "type": ad.get("propertyType"),
        "city": ad.get("city"),
        "postal_code": ad.get("postalCode"),
        "lat": pos.get("lat") if precise else None,
        "lng": pos.get("lon") if precise else None,
        "source": f"Bien'ici · {ad.get('accountDisplayName')}" if ad.get("accountDisplayName") else "Bien'ici",
        "created_at": ad.get("publicationDate"),
        "expired": False,
        "deleted": False,
        "image": photos[0].get("url") if photos and isinstance(photos[0], dict) else None,
        "link": f"https://www.bienici.com/annonce/{urllib.parse.quote(str(ad.get('id')))}",
        "uuid": None,
        "stops": [],
        "description": (ad.get("description") or "").replace("<br>", "\n")[:2000],
        "floor": ad.get("floor"),
        "safety": safety,
        "coliving": is_coliving(ad),
        "furnished": ad.get("isFurnished"),
    }


COLIVING_RE = re.compile(
    r"^\s*(chambre|colocation|co-?living)\b"
    r"|chambre[^.]{0,40}\b(en|dans une?)\s+(coloc|co-?living)"
    r"|\bcolocation\s+(meubl|neuve|à|de \d|baux|en bail)"
    r"|\bbaux individuels\b|\bbail individuel\b|\bco-?living\b|\bcolocataires?\b"
    r"|\b(\d+|une)\s+chambres?\s+(est\s+|sont\s+)?(disponibles?|libres?|à louer)\b",
    re.I | re.M)
# « pas de colocation », « colocation non acceptée »… : un logement entier, à ne pas exclure
NOT_COLIVING_RE = re.compile(r"\b(pas de|sans|hors|non)\s+colocation\b"
                             r"|\bcolocation\s+(non|pas|interdite|refusée|exclue|impossible)\b[^.]*", re.I)


def is_coliving(ad):
    """Chambre en colocation / coliving (exclue, comme sur les alertes Jinka) ?"""
    if ad.get("flatSharing"):
        return True
    text = f"{ad.get('title') or ''}\n{(ad.get('description') or '')[:600]}"
    return bool(COLIVING_RE.search(NOT_COLIVING_RE.sub(" ", text)))


def same_flat(a, b):
    """Deux annonces décrivent-elles le même logement ? (même code postal, loyer et surface proches)"""
    if not (a.get("postal_code") and a.get("postal_code") == b.get("postal_code")):
        return False
    if not (a.get("rent") and b.get("rent") and abs(float(a["rent"]) - float(b["rent"])) <= 15):
        return False
    if a.get("area") and b.get("area"):
        return abs(float(a["area"]) - float(b["area"])) <= 1.5
    return False
