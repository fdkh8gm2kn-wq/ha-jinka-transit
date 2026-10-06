"""Source SeLoger, via les emails d'alerte reçus dans la boîte dédiée (SeLoger n'a pas d'API et son site
bloque les robots : on n'ouvre jamais les liens).

Un email contient, par annonce : loyer, type (« Studio à louer », « Colocation à louer »…), pièces,
surface, parfois l'étage, le quartier, la ville et le code postal, et un lien de suivi vers l'annonce.
Pas d'adresse : la position est estimée par le quartier (souvent nommé d'après des stations,
« La Fourche-Guy Môquet ») puis, à défaut, par le centre de la commune."""

import hashlib
import re
from html import unescape

PRICE_RE = re.compile(r"(\d[\d\s\u202f\u00a0.,]*?)\s*(?:€|&euro;|&#8364;|&#x20ac;)\s*/\s*mois", re.I)
LOC_RE = re.compile(r"^(?:(?P<quartier>.+?),\s*)?(?P<city>[^,()]+?)\s*\((?P<cp>\d{5})\)\s*$")
BLOCK_TAGS = re.compile(r"(?i)<\s*(br|/p|/div|/td|/tr|/li|/h\d|/table)\b[^>]*>")


def html_to_lines(html):
    html = re.sub(r"(?is)<(style|script|head).*?</\1>", " ", html)
    text = BLOCK_TAGS.sub("\n", html)
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return [re.sub(r"[ \t  ]+", " ", x).strip() for x in text.split("\n") if x.strip()]


def _num(s):
    s = re.sub(r"[\s  ]", "", s or "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def city_name(city):
    """« Paris 17ème arrondissement » → « Paris 17e »."""
    m = re.match(r"(?i)paris\s+(\d+)\s*(?:e|è|ème|er)\b", city)
    if m:
        return f"Paris {int(m.group(1))}{'er' if m.group(1) == '1' else 'e'}"
    return city.strip()


def parse_email(html):
    """Annonces d'un email d'alerte SeLoger, au format commun (voir jinka.normalize)."""
    hrefs = re.findall(r'(?is)<a\b[^>]*href="([^"]+)"', html)
    out, seen = [], set()
    prices = list(PRICE_RE.finditer(html))
    for i, m in enumerate(prices):
        prev_end = prices[i - 1].end() if i else 0
        nxt = prices[i + 1].start() if i + 1 < len(prices) else len(html)
        start = html.rfind("<a ", prev_end, m.start())
        chunk = html[start if start >= 0 else m.start():nxt]
        lines = html_to_lines(chunk)
        card = parse_card(lines)
        if not card:
            continue
        link = next((h for h in re.findall(r'(?is)<a\b[^>]*href="([^"]+)"', chunk)
                     if "seloger" in h.lower()), None)
        key = "|".join(str(card.get(k)) for k in ("postal_code", "rent", "area", "quartier", "type"))
        lid = "seloger:" + hashlib.sha1(key.encode()).hexdigest()[:12]
        if lid in seen:
            continue
        seen.add(lid)
        out.append({
            "id": lid, "alert_id": "seloger", "alert_name": "SeLoger", "source": "SeLoger",
            "rent": card["rent"], "area": card.get("area"), "rooms": card.get("rooms"),
            "bedrooms": card.get("bedrooms"), "type": card.get("type"), "city": card["city"],
            "postal_code": card["cp"], "quartier": card.get("quartier"), "floor": card.get("floor"),
            "lat": None, "lng": None, "created_at": None, "expired": False, "deleted": False,
            "image": None, "link": unescape(link) if link else "https://www.seloger.com/mes-alertes",
            "uuid": None, "stops": [], "description": card.get("type_line", ""),
            "coliving": bool(re.search(r"(?i)coloc|chambre", card.get("type_line", ""))),
            "furnished": True if re.search(r"(?i)meubl", card.get("type_line", "")) else None,
            "dpe": card.get("dpe"),
        })
    return out


def parse_card(lines):
    """Lignes d'une carte : « 689 €/mois », « Colocation à louer », « 4 pièces · 39 m² »,
    « La Fourche-Guy Môquet, », « Paris 17ème arrondissement », « (75017) »."""
    if not lines:
        return None
    pm = None
    for k, line in enumerate(lines):
        pm = PRICE_RE.search(line)
        if pm:
            lines = lines[k:]
            break
    if not pm:
        return None
    card = {"rent": _num(pm.group(1))}
    rest = lines[1:12]
    loc_parts = []
    for line in rest:
        if re.match(r"(?i)voir l", line):
            break
        if re.search(r"(?i)\bà louer\b", line) and "type_line" not in card:
            card["type_line"] = line
            card["type"] = re.sub(r"(?i)\s*à louer.*", "", line).strip()
            continue
        if "m²" in line or re.search(r"(?i)\bpièces?\b", line):
            if (a := re.search(r"([\d.,]+)\s*m²", line)):
                card["area"] = _num(a.group(1))
            if (r := re.search(r"(?i)(\d+)\s*pièces?", line)):
                card["rooms"] = int(r.group(1))
            if (b := re.search(r"(?i)(\d+)\s*chambres?", line)):
                card["bedrooms"] = int(b.group(1))
            if re.search(r"(?i)\bRDC\b|rez-de-chauss", line):
                card["floor"] = 0
            elif (f := re.search(r"(?i)étage\s*(\d+)|(\d+)\s*(?:er|ère|e|ème)\s*étage", line)):
                card["floor"] = int(f.group(1) or f.group(2))
            continue
        if re.fullmatch(r"[A-G]", line):
            card["dpe"] = line
            continue
        loc_parts.append(line)
        lm = LOC_RE.match(" ".join(loc_parts).replace(", (", " ("))
        if lm:
            card["quartier"] = (lm.group("quartier") or "").strip(" ,") or None
            card["city"] = city_name(lm.group("city"))
            card["cp"] = lm.group("cp")
            break
    if not card.get("cp") or not card.get("rent"):
        return None
    return card


def quartier_queries(quartier):
    """Noms de stations possibles dans un nom de quartier SeLoger (« La Fourche-Guy Môquet »
    → « La Fourche », « Guy Môquet »)."""
    if not quartier:
        return []
    parts = [p.strip() for p in re.split(r"\s*[-–/]\s*|,", quartier) if len(p.strip()) >= 4]
    return [quartier] + parts
