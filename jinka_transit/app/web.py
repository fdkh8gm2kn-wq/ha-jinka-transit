"""Mini interface web (onglet dans Home Assistant via Ingress, ou http://localhost:8099 en local)."""

import base64
import hashlib
import hmac
import html
import ipaddress
import json
import logging
import os
import re
import threading
import time
from string import Template
from urllib.parse import parse_qs, urlencode
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fmt import dest_icon as _dest_icon, steps_of, title_of

log = logging.getLogger("web")

STATUS_LABEL = {"duplicate": "↔️ doublon", "notified": "✅ envoyée", "match": "⏳ OK, envoi en attente",
                "silent": "✅ OK (1er scan, non envoyée)", "rejected": "❌ refusée",
                "pending": "⏳ calcul en attente"}

def safe_json(obj):
    """JSON utilisable dans une balise <script> (impossible d'en sortir avec « </script> »)."""
    return json.dumps(obj).replace("<", "\\u003c")


PAGE_SIZE = 50


def short_counted(r):
    """« (compté 46) » quand la pénalité de correspondances s'applique."""
    c = r.get("counted")
    return f" (compté {c})" if c and c != r.get("minutes") and not r.get("info_only") else ""


FILTERS = {  # clé d'URL : (libellé, statuts affichés)
    "ok": ("OK", lambda s: s in ("match", "notified", "silent")),
    "wait": ("en attente", lambda s: s in ("match", "pending")),
    "dup": ("doublons", lambda s: s == "duplicate"),
    "ko": ("refusées", lambda s: s == "rejected"),
    "all": ("toutes", lambda s: True),
}
DEFAULT_FILTER = "ok"


CSS = """
:root{--bg:#f6f7f9;--fg:#16181d;--muted:#667085;--line:#e4e7ec;--card:#fff;--ok:#12805c;--okbg:#e7f6ef;
--ko:#b42318;--kobg:#fdecea;--warn:#a15c07;--warnbg:#fef4e2;--accent:#3a5bdc;--accentbg:#eef2ff;--chip:#f2f4f7}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e9ebef;--muted:#98a2b3;--line:#262a33;--card:#171a20;
--ok:#4ade9b;--okbg:#10291f;--ko:#f97066;--kobg:#2d1414;--warn:#f5b546;--warnbg:#2b2110;--accent:#8ea2ff;--accentbg:#1b2140;--chip:#20242c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:16px}a{color:var(--accent)}.muted{color:var(--muted)}.ok{color:var(--ok)}.ko{color:var(--ko)}
header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}
h1{font-size:20px;margin:0;letter-spacing:-.01em}
nav.tabs{display:flex;gap:4px;flex-wrap:wrap;margin:12px 0 10px;border-bottom:1px solid var(--line)}
nav.tabs a{padding:8px 12px;text-decoration:none;color:var(--muted);border-bottom:2px solid transparent;margin-bottom:-1px}
nav.tabs a.on{color:var(--fg);border-color:var(--accent);font-weight:600}
.status{display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:var(--card);border:1px solid var(--line);
border-radius:12px;padding:10px 14px}.status .sep{color:var(--line)}
button,.btn{font:inherit;padding:7px 14px;border-radius:9px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer;text-decoration:none}
.btn.ghost,button.ghost{background:transparent;color:var(--accent)}
.toolbar{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin:14px 0 6px;align-items:center}
.pills{display:flex;gap:6px;flex-wrap:wrap}.pills a{padding:5px 11px;border-radius:999px;background:var(--chip);color:var(--fg);text-decoration:none;font-size:13px}
.pills a.on{background:var(--accent);color:#fff}
.sort{font-size:13px}.sort a{margin-left:8px;text-decoration:none}.sort a.on{font-weight:700;text-decoration:underline}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.ad{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;display:flex;flex-direction:column}
.ad.t-fav{outline:2px solid #f5b546}.ad.t-visit{outline:2px solid var(--accent)}.ad.t-contact{outline:2px solid var(--ok)}
.ad.t-drop{opacity:.55}
.ph{display:block;height:170px;background:var(--chip);position:relative}
.ph img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;display:block}
.ph .none{display:flex;height:100%;align-items:center;justify-content:center;font-size:42px;color:var(--muted)}
.ph .tag{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.65);color:#fff;font-size:12px;padding:2px 8px;border-radius:999px}
.ph .src{position:absolute;bottom:8px;left:8px;background:rgba(0,0,0,.55);color:#fff;font-size:11px;padding:2px 7px;border-radius:6px}
.body{padding:10px 12px 12px;display:flex;flex-direction:column;gap:7px;flex:1}
.price{font-size:20px;font-weight:700}.top{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap}
.where{font-weight:600}.where .q{font-weight:400;color:var(--muted)}
.badges{display:flex;gap:5px;flex-wrap:wrap}.b{font-size:12px;padding:2px 7px;border-radius:6px;background:var(--chip)}
.b.warn{background:var(--warnbg);color:var(--warn)}.b.nm{background:var(--accentbg);color:var(--accent);font-weight:600}
.dpe{font-weight:800;color:#111;border-radius:4px 10px 10px 4px;padding:2px 11px 2px 8px;font-size:13px}.dpe.A{background:#009c6d;color:#fff}.dpe.B{background:#52b153;color:#fff}.dpe.C{background:#a5cc74}
.dpe.D{background:#f4e70f}.dpe.E{background:#f0b40f}.dpe.F{background:#eb8235;color:#fff}.dpe.G{background:#d7221f;color:#fff}
.trips{display:flex;gap:6px;flex-wrap:wrap}.trip{font-size:15px;font-weight:600;padding:4px 10px 4px 6px;border-radius:10px;background:var(--okbg);color:var(--ok);display:inline-flex;align-items:center;gap:5px}
.trip .ic{font-size:22px;line-height:1}
.trip.ko{background:var(--kobg);color:var(--ko)}.trip.info{background:var(--chip);color:var(--muted)}.trip small{opacity:.8}
.why{font-size:13px;color:var(--ko)}
details{font-size:13px}summary{cursor:pointer;color:var(--muted)}details .it{white-space:pre-line;color:var(--muted);margin:4px 0 8px}
.foot{display:flex;justify-content:space-between;align-items:center;gap:6px;margin-top:auto;padding-top:4px;flex-wrap:wrap}
.acts{display:flex;gap:4px}.acts form{margin:0}.acts button{padding:6px 9px;background:var(--chip);border:1px solid var(--line);color:var(--fg);font-size:20px;line-height:1}
.acts button.on{background:var(--accentbg);border-color:var(--accent)}
.st{font-size:12px}.pager{margin:14px 0;display:flex;gap:12px;align-items:center;justify-content:center}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:12px 0}
.card table{width:100%;border-collapse:collapse}.card th,.card td{text-align:left;padding:7px 6px;border-top:1px solid var(--line);vertical-align:top}
.card th{color:var(--muted);border-top:0;font-weight:600}.wrap{overflow-x:auto}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.row input{font:inherit;padding:7px 10px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg);flex:1 1 200px;min-width:0}
.flash{padding:10px 14px;border-radius:10px;margin:12px 0;border:1px solid var(--line);background:var(--card)}
h2{font-size:16px;margin:18px 0 8px}#map{height:70vh;min-height:380px;border-radius:12px}
.empty{padding:30px;text-align:center;color:var(--muted);background:var(--card);border:1px dashed var(--line);border-radius:12px}
@media (max-width:600px){main{padding:10px}.ph{height:150px}.price{font-size:18px}}
"""

PAGE = Template("""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>${brand}</title>${head}
<style>${css}</style></head><body><main>
<header><h1>🏠 ${brand}</h1><form method="post" action="scan"><button>Scanner maintenant</button></form></header>
<nav class="tabs">${tabs}</nav>
${flash}<div class="status">${status}</div>
${content}
</main></body></html>""")

TRACK = {"fav": ("⭐", "Favori"), "contact": ("📞", "Contactée"), "visit": ("🏠", "Visite prévue"), "drop": ("❌", "Pas pour nous")}
SORTS = {"recent": "plus récentes", "prix": "prix", "trajet": "trajet", "surface": "surface"}
DPE_OK = set("ABCDEFG")


def ago(ts):
    if not ts:
        return "—"
    d = time.time() - ts
    if d < 90:
        return "à l'instant"
    if d < 3600:
        return f"il y a {int(d // 60)} min"
    if d < 86400:
        return f"il y a {int(d // 3600)} h"
    if d < 2 * 86400:
        return "hier"
    return datetime.fromtimestamp(ts).strftime("%d/%m")


def short_dest(name):
    m = re.search(r"\(([^)]+)\)", name or "")
    return m.group(1) if m else (name or "")


def dest_icon(name):
    return html.escape(_dest_icon(name))


def worst_minutes(v):
    vals = [r.get("counted") or r.get("minutes") for r in v.get("results", [])
            if not r.get("info_only") and r.get("minutes") is not None]
    return max(vals) if vals else None


def sort_items(items, sort):
    big = 10 ** 9
    if sort == "prix":
        return sorted(items, key=lambda v: v["listing"].get("rent") or big)
    if sort == "trajet":
        return sorted(items, key=lambda v: worst_minutes(v) or big)
    if sort == "surface":
        return sorted(items, key=lambda v: -(v["listing"].get("area") or 0))
    return sorted(items, key=lambda v: v.get("first_seen") or v.get("ts", 0), reverse=True)


def link(view="list", **q):
    q = {"v": view, **{k: val for k, val in q.items() if val not in (None, "")}}
    return "?" + urlencode(q)


def card(v, tag, back, esc=html.escape):
    l = v["listing"]
    lid = l["id"]
    img = "<div class='none'>🏠</div>" + (
        f"<img loading='lazy' referrerpolicy='no-referrer' src='{esc(l['image'])}' alt='' onerror='this.remove()'>"
        if l.get("image") and str(l["image"]).startswith("http") else "")
    first = v.get("first_seen") or v.get("ts", 0)
    new = "<span class='tag'>nouveau</span>" if time.time() - first < 86400 and v["status"] != "rejected" else ""
    src = esc((l.get("source") or l.get("alert_name") or "").split(" · ")[0])
    facts = " · ".join(x for x in (f"{l['area']:g} m²" if l.get("area") else "",
                                   f"{int(l['rooms'])} p." if l.get("rooms") else "") if x)
    badges = []
    if l.get("dpe") in DPE_OK:
        badges.append(f"<span class='b dpe {l['dpe']}' title='Classe énergie (DPE)'>{l['dpe']}</span>")
    if l.get("furnished") is False:
        badges.append("<span class='b nm'>📦 Non meublé</span>")
    elif l.get("furnished"):
        badges.append("<span class='b'>Meublé</span>")
    if l.get("floor") is not None:
        fl = "RDC" if l["floor"] == 0 else f"{l['floor']:g}e étage"
        if l.get("elevator") is True:
            fl += " · ascenseur"
        badges.append(f"<span class='b'>{fl}</span>")
    if l.get("approx"):
        badges.append(f"<span class='b warn' title='{esc(str(l['approx']))}'>⚠️ position estimée</span>")
    trips, infos = [], []
    for r in v.get("results", []):
        if r.get("minutes") is None:
            continue
        steps = esc("\n".join(x.strip().replace("*", "") for x in steps_of(r)))
        line = (f"<b>{esc(r['name'])}</b> — {r['minutes']} min{short_counted(r)} · {esc(r.get('summary') or '')}"
                f"<div class='it'>{steps}</div>")
        chip_cls = "info" if r.get("info_only") else ("" if r.get("ok") else "ko")
        trips.append(f"<span class='trip {chip_cls}' title='{esc(r['name'])} · {esc(r.get('summary') or '')}'>"
                     f"<span class='ic'>{dest_icon(r['name'])}</span>{r['minutes']}'</span>")
        if r.get("info_only"):
            infos.append(line)
        else:
            infos.insert(0, line)
    why = (f"<div class='why'>{esc(v.get('reason') or '')}</div>"
           if v["status"] in ("rejected", "duplicate", "pending") and v.get("reason") else "")
    anchor = "a" + hashlib.sha1(lid.encode()).hexdigest()[:10]
    acts = "".join(
        f"<form method='post' action='track'><input type='hidden' name='id' value='{esc(lid)}'>"
        f"<input type='hidden' name='tag' value='{k}'><input type='hidden' name='back' value='{esc(back)}#{anchor}'>"
        f"<button class='{'on' if tag == k else ''}' title='{lbl}'>{ico}</button></form>"
        for k, (ico, lbl) in TRACK.items())
    return (f"<article class='ad t-{tag or 'none'}' id='{anchor}'>"
            f"<a class='ph' href='{esc(l.get('link') or '#')}' target='_blank' rel='noopener'>{img}{new}<span class='src'>{src}</span></a>"
            f"<div class='body'><div class='top'><span class='price'>{int(l['rent']) if l.get('rent') else '?'} €</span>"
            f"<span class='muted'>{facts}</span></div>"
            f"<div class='where'>{esc(l.get('city') or '')} <span class='q'>{esc(l.get('postal_code') or '')}"
            f"{' · ' + esc(l['quartier']) if l.get('quartier') else ''}</span></div>"
            f"<div class='badges'>{''.join(badges)}</div>"
            f"<div class='trips'>{''.join(trips)}</div>{why}"
            + (f"<details><summary>Itinéraires</summary>{''.join(infos)}</details>" if infos else "")
            + f"<div class='foot'><span class='st muted'>{STATUS_LABEL.get(v['status'], v['status'])} · {ago(first)}</span>"
            f"<div class='acts'>{acts}</div></div></div></article>")


def render(app, flt=DEFAULT_FILTER, page=1, view="list", sort="recent"):
    esc = html.escape
    st = app.state
    listings = st.get("listings", {})
    track = {k: t for k, t in st.get("track", {}).items() if k in listings}
    tag_of = lambda v: (track.get(v["listing"]["id"]) or {}).get("tag")
    items = list(listings.values())
    ok_items = [v for v in items if FILTERS["ok"][1](v["status"])]
    new24 = sum(1 for v in ok_items if time.time() - (v.get("first_seen") or v.get("ts", 0)) < 86400)
    n_follow = sum(1 for t in track.values() if t["tag"] != "drop")
    tabs = "".join(
        f"<a class='{'on' if view == k else ''}' href='{link(k)}'>{lbl}</a>"
        for k, lbl in (("list", "Annonces"), ("track", f"⭐ Mon suivi ({n_follow})"), ("map", "🗺️ Carte"),
                       ("settings", "⚙️ Réglages")))
    last = app.last_scan
    try:
        last_ts = datetime.fromisoformat(last["at"]).timestamp() if last.get("at") else None
    except ValueError:
        last_ts = None
    quota = (st.get("idfm_quota") or {}).get("until", 0)
    status = [f"<span>Dernier scan <b>{ago(last_ts) if last_ts else 'en cours…'}</b></span>",
              f"<span class='ok'><b>{len(ok_items)}</b> annonces OK</span>",
              f"<span><b>{new24}</b> nouvelle{'s' if new24 > 1 else ''} en 24 h</span>"]
    if last.get("error"):
        status.append(f"<span class='ko'>⚠️ {esc(last['error'])[:160]}</span>")
    if quota > time.time():
        status.append(f"<span class='muted'>quota IDFM atteint, reprise {datetime.fromtimestamp(quota):%d/%m %H:%M}</span>")
    status_html = " <span class='sep'>|</span> ".join(status)
    flash, app.flash = app.flash, ""
    if app.auto_test_running:
        flash = ("⏳ Connexion automatique en cours : code demandé à Jinka, lecture de la boîte mail…"
                 "<script>setTimeout(()=>location.reload(),5000)</script>")
    head = ""
    if view == "settings":
        content = render_settings(app)
    elif view == "map":
        head = ('<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">'
                '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>')
        content = render_map(app, ok_items + [v for v in items if tag_of(v) in ("fav", "contact", "visit")
                                              and v not in ok_items], tag_of)
    elif view == "track":
        sections = []
        for k, (ico, lbl) in TRACK.items():
            sel = sort_items([v for v in items if tag_of(v) == k], "recent")
            if not sel:
                continue
            grid = "".join(card(v, k, link("track")) for v in sel)
            body = f"<div class='grid'>{grid}</div>"
            sections.append(f"<details><summary><h2 style='display:inline'>{ico} {lbl} ({len(sel)})</h2></summary>{body}</details>"
                            if k == "drop" else f"<h2>{ico} {lbl} ({len(sel)})</h2>{body}")
        content = "".join(sections) or (
            "<div class='empty' style='margin-top:14px'>Rien de suivi pour l'instant.<br>Sur une annonce, clique "
            "⭐ (favori), 📞 (contactée) ou 🏠 (visite prévue) pour la retrouver ici.</div>")
    else:
        counts = {k: sum(1 for v in items if FILTERS[k][1](v["status"]) and
                         (k not in ("ok", "wait") or tag_of(v) != "drop")) for k in FILTERS}
        sel = [v for v in items if FILTERS[flt][1](v["status"]) and (flt not in ("ok", "wait") or tag_of(v) != "drop")]
        sel = sort_items(sel, sort)
        pages = max(1, -(-len(sel) // PAGE_SIZE))
        page = min(max(1, page), pages)
        total = len(sel)
        sel = sel[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        back = link("list", f=flt, s=sort, p=page if page > 1 else None)
        pills = "".join(f"<a class='{'on' if flt == k else ''}' href='{link('list', f=k, s=sort)}'>"
                        f"{FILTERS[k][0]} ({counts[k]})</a>" for k in FILTERS)
        sorts = "".join(f"<a class='{'on' if sort == k else ''}' href='{link('list', f=flt, s=k)}'>{lbl}</a>"
                        for k, lbl in SORTS.items())
        nav = [f"<a href='{link('list', f=flt, s=sort, p=page - 1)}'>« précédentes</a>" if page > 1 else "",
               f"page {page} / {pages} ({total} annonces)",
               f"<a href='{link('list', f=flt, s=sort, p=page + 1)}'>suivantes »</a>" if page < pages else ""]
        pager = f"<div class='pager'>{' · '.join(x for x in nav if x)}</div>"
        grid = "".join(card(v, tag_of(v), back) for v in sel)
        dropped = sum(1 for v in items if tag_of(v) == "drop")
        content = (f"<div class='toolbar'><div class='pills'>{pills}</div><div class='sort muted'>Trier :{sorts}</div></div>"
                   + (f"<div class='muted' style='font-size:12px'>{dropped} annonce(s) écartée(s) par toi masquée(s) "
                      f"(voir <a href='{link('track')}'>Mon suivi</a>) · retirées 30 jours après détection, sauf suivies</div>"
                      if dropped else "<div class='muted' style='font-size:12px'>Les annonces sont retirées 30 jours "
                      "après leur détection, sauf celles que tu suis.</div>")
                   + pager + (f"<div class='grid'>{grid}</div>" if grid else "<div class='empty'>Rien pour l'instant</div>")
                   + pager)
    return PAGE.substitute(brand=esc(app.brand()) if hasattr(app, "brand") else "Jinka Transit", head=head, css=CSS,
                           tabs=tabs, flash=f"<div class='flash'>{flash}</div>" if flash else "",
                           status=status_html, content=content)


def render_map(app, items, tag_of):
    esc = html.escape
    pts = []
    for v in items:
        l = v["listing"]
        if l.get("lat") is None or l.get("lng") is None:
            continue
        w = worst_minutes(v)
        pts.append({"lat": l["lat"], "lon": l["lng"], "w": w, "t": tag_of(v) or "",
                    "h": (f"<b>{int(l['rent']) if l.get('rent') else '?'} € · {esc(str(l.get('area') or '?'))} m²</b><br>"
                          f"{esc(l.get('city') or '')}<br>"
                          + " · ".join(esc(f"{short_dest(r['name'])} {r['minutes']}'") for r in v.get("results", [])
                                       if not r.get("info_only") and r.get("minutes") is not None)
                          + f"<br><a href='{esc(l.get('link') or '#')}' target='_blank' rel='noopener'>voir l'annonce</a>"
                          + ("<br>⚠️ position estimée" if l.get("approx") else ""))})
    geo = app.state.get("geocode", {})
    dpts = []
    for d in app.opts.get("destinations", []):
        g = geo.get((d.get("address") or "").strip()) or {}
        if g.get("lat") is not None:
            dpts.append({"n": esc(d["name"]), "lat": g["lat"], "lon": g.get("lon")})
    if not pts:
        return "<div class='empty' style='margin-top:14px'>Aucune annonce localisée à afficher.</div>"
    return f"""<div class="card" style="padding:6px"><div id="map"></div></div>
<div class="muted" style="font-size:12px">Couleur = trajet le plus long vers tes adresses (compté avec les correspondances) :
<span style="color:#12805c">● ≤ 35 min</span> <span style="color:#d4a106">● 36–45 min</span> <span style="color:#b42318">● au-delà</span> ·
⭐ = suivie. Les positions estimées sont approximatives.</div>
<script>
const pts={safe_json(pts)}, dpts={safe_json(dpts)};
const map=L.map('map');
L.tileLayer('https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2'
 +'&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/png&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}',
 {{maxZoom:18,attribution:'© IGN Géoplateforme'}}).addTo(map);
const b=[];
pts.forEach(p=>{{b.push([p.lat,p.lon]);const c=p.w==null?'#667085':p.w<=35?'#12805c':p.w<=45?'#d4a106':'#b42318';
 L.circleMarker([p.lat,p.lon],{{radius:p.t?10:7,color:p.t?'#f5b546':'#fff',weight:p.t?3:1,fillColor:c,fillOpacity:.9}})
  .bindPopup(p.h).addTo(map)}});
dpts.forEach(d=>{{b.push([d.lat,d.lon]);L.marker([d.lat,d.lon]).bindTooltip(d.n,{{permanent:true}}).addTo(map)}});
map.fitBounds(b,{{padding:[20,20]}});
</script>"""


def render_settings(app):
    esc = html.escape
    st = app.state
    geocache = st.get("geocode", {})
    dests = "".join(
        f"<tr><td>{esc(d['name'])}{' <span class=muted>(info)</span>' if d.get('info_only') else ''}</td>"
        f"<td>{esc(d['address'])}</td>"
        f"<td>{esc((geocache.get(d['address'].strip()) or {}).get('label') or '— (au prochain scan)')}</td>"
        f"<td>{'—' if d.get('info_only') else str(d['max_minutes']) + ' min'}</td><td>{esc(d['arrival_time'])}</td></tr>"
        for d in app.opts["destinations"]) or "<tr><td colspan=5>Aucune adresse configurée</td></tr>"
    last = app.last_scan
    auth = st.get("jinka_auth") or {}
    if app.opts.get("jinka_token"):
        jinka_status = "<span class='ok'>jeton fourni dans la configuration</span>"
    elif auth.get("token"):
        when = datetime.fromtimestamp(auth.get("at", 0)).strftime("%d/%m/%Y %H:%M")
        jinka_status = f"<span class='ok'>connecté ({esc(auth.get('email', ''))}, le {when})</span>"
    else:
        jinka_status = "<span class='ko'>pas connecté</span> — reçois un code par email pour te connecter"
    code_form = ("" if not app.login else
                 f"<form method='post' action='jinka/verify' class='row'><input name='code' inputmode='numeric' "
                 f"pattern='[0-9]{{4}}' maxlength='4' placeholder='code à 4 chiffres reçu sur "
                 f"{esc(app.login_email)}' required autofocus><button>Valider</button></form>")
    auto_form = ("" if not (app.opts.get("jinka_email") and app.opts.get("mail_password")) else
                 "<form method='post' action='jinka/auto' class='row'><button class='ghost'>🔄 Tester la connexion "
                 f"automatique ({esc(app.opts['jinka_email'])})</button><span class='muted'>demande un code, le lit "
                 "dans la boîte mail et se connecte (≈ 30 s)</span></form>")
    o = app.opts
    crit = [f"loyer max {o['max_rent']} €" if o.get("max_rent") else "",
            f"surface min {o['min_area']} m²" if o.get("min_area") else "",
            "meublé uniquement" if o.get("furnished_only") else "meublé ou non",
            f"étage max {o['max_floor']} (au-delà : avec ascenseur)" if o.get("max_floor") else "",
            f"marche logement → station ≤ {o.get('max_walk_home_minutes', 5)} min",
            f"correspondances ≤ {o.get('max_transfers', 2)}, +{o.get('transfer_penalty_minutes', 3)} min chacune",
            "modes : " + ", ".join(o.get("allowed_modes") or [])]
    return f"""<div class="card"><b>Dernier scan</b> <span class="muted">{esc(last.get('at') or '—')}</span><br>
{esc(last.get('status') or '')}{f" <span class='ko'>{esc(last['error'])}</span>" if last.get('error') else ''}
<div class="row"><form method="post" action="notify/test"><button class="ghost">Envoyer une notification de test</button></form>
<form method="post" action="notify/sample"><button class="ghost">📧 Recevoir un exemple d'email d'annonce</button></form>
<a class="btn ghost" href="explore">🗺️ Zones compatibles (recherche élargie)</a></div></div>
<div class="card"><b>Critères</b><div class="muted">{esc(' · '.join(x for x in crit if x))}</div>
<div class="muted" style="font-size:12px;margin-top:4px">Modifiables dans Paramètres → Applications → Jinka Transit → Configuration.</div></div>
<div class="card"><b>Adresses</b><div class="wrap"><table><tr><th>Nom</th><th>Adresse saisie</th><th>Localisée à</th><th>Max</th><th>Arrivée</th></tr>{dests}</table></div></div>
<div class="card"><b>Connexion Jinka :</b> {jinka_status}
<form method="post" action="jinka/send" class="row"><input type="email" name="email" value="{esc(app.login_email or auth.get('email') or o.get('jinka_email') or '')}"
 placeholder="ton email Jinka" required><button>Recevoir un code</button></form>{code_form}{auto_form}</div>"""


def is_private(ip):
    """Adresse du réseau local (box, Wi-Fi), jamais une adresse Internet."""
    try:
        a = ipaddress.ip_address(ip)
        a = getattr(a, "ipv4_mapped", None) or a
        return a.is_private and not a.is_loopback
    except ValueError:
        return False


INGRESS_IP = "172.30.32.2"  # seul client autorisé dans HA : le proxy Ingress (déjà authentifié par HA)
ALLOW_ALL = os.environ.get("WEB_ALLOW_ALL") == "1"  # test local (docker-compose)


def start(app, port=8099):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _allowed(self):
            ip = self.client_address[0]
            if ALLOW_ALL or ip in (INGRESS_IP, "127.0.0.1", "::1"):
                return True
            # accès direct (http://homeassistant.local:8099) : réseau local uniquement, avec mot de passe
            pwd = (app.opts.get("local_password") or "").strip()
            if pwd and is_private(ip):
                auth = self.headers.get("Authorization", "")
                given = ""
                if auth.startswith("Basic "):
                    try:
                        given = base64.b64decode(auth[6:]).decode("utf-8", "replace").partition(":")[2]
                    except ValueError:
                        given = ""
                given = given.strip()
                if given and hmac.compare_digest(given.encode(), pwd.encode()):
                    return True
                if given:
                    log.warning("Accès direct refusé depuis %s : mot de passe incorrect.", ip)
                    time.sleep(1)  # ralentit les essais de mot de passe
                body = "Mot de passe requis.".encode()
                self.send_response(401)
                self.send_header("WWW-Authenticate", 'Basic realm="ALERTE IMMO", charset="UTF-8"')
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return False
            log.warning("Accès direct refusé depuis %s (%s).", ip,
                        "réseau non local" if pwd else "pas de mot de passe configuré")
            self._send(403, "Accès réservé à Home Assistant, ou au réseau local avec le mot de passe "
                            "(option « Mot de passe de la page en accès direct »).", "text/plain; charset=utf-8")
            return False

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._allowed():
                return
            path, _, query = self.path.partition("?")
            if path.endswith("/api/mail_samples"):
                return self._send(200, json.dumps(app.state.get("mail_samples", {}), ensure_ascii=False),
                                  "application/json")
            if path.endswith("/api/state"):
                return self._send(200, json.dumps({"last_scan": app.last_scan,
                                                  **{k: v for k, v in app.state.items() if k not in ("jinka_auth", "mail_samples")}},
                                                  ensure_ascii=False), "application/json")
            if path.rstrip("/").endswith("explore"):
                return self._send(200, render_explore(app))
            q = {k: v[0] for k, v in parse_qs(query).items()}
            flt = q["f"] if q.get("f") in FILTERS else DEFAULT_FILTER
            page = int(q["p"]) if q.get("p", "").isdigit() else 1
            view = q["v"] if q.get("v") in ("list", "track", "map", "settings") else "list"
            sort = q["s"] if q.get("s") in SORTS else "recent"
            self._send(200, render(app, flt, page, view, sort))

        def do_POST(self):
            if not self._allowed():
                return
            path = self.path.rstrip("/")
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
            if path.endswith("track"):
                app.set_track(form.get("id", ""), form.get("tag", ""))
                back = form.get("back", "")
                back = back if back.startswith("?") else "?"
                self.send_response(303)
                self.send_header("Location", "./" + back)
                self.end_headers()
                return
            if path.endswith("scan"):
                app.scan_now.set()
            elif path.endswith("jinka/send"):
                try:
                    app.jinka_send_code(form.get("email", ""))
                    app.flash = "📧 Code envoyé : regarde tes emails et saisis-le ci-dessous."
                except Exception as e:  # noqa: BLE001
                    app.flash = f"<span class='ko'>{html.escape(str(e))}</span>"
            elif path.endswith("notify/test"):
                try:
                    res = app.send_test()
                    app.flash = " · ".join(
                        f"<span class='{'ok' if ok else 'ko'}'>{'✅' if ok else '❌'} {html.escape(name)}</span>"
                        for name, ok in res) + " <span class='muted'>(détail des erreurs dans le Journal)</span>"
                except Exception as e:  # noqa: BLE001
                    app.flash = f"<span class='ko'>{html.escape(str(e))}</span>"
            elif path.endswith("notify/sample"):
                try:
                    to, ok = app.send_sample()
                    app.flash = (f"<span class='ok'>✅ Exemple d'annonce envoyé à {html.escape(str(to))}</span>" if ok
                                 else "<span class='ko'>❌ Échec de l'envoi (voir le Journal)</span>")
                except Exception as e:  # noqa: BLE001
                    app.flash = f"<span class='ko'>{html.escape(str(e))}</span>"
            elif path.endswith("jinka/auto"):
                try:
                    app.auto_login_now()
                except Exception as e:  # noqa: BLE001
                    app.flash = f"<span class='ko'>{html.escape(str(e))}</span>"
            elif path.endswith("jinka/verify"):
                try:
                    app.jinka_verify_code(form.get("code", ""))
                    app.flash = "<span class='ok'>✅ Connecté à Jinka. Premier scan lancé.</span>"
                except Exception as e:  # noqa: BLE001
                    app.flash = f"<span class='ko'>{html.escape(str(e))}</span>"
            elif path.endswith("explore/start"):
                try:
                    app.explore_start(max(3, min(40, int(form.get("radius") or 15))),
                                      max(10, min(180, int(form.get("max_minutes") or 45))))
                except Exception as e:  # noqa: BLE001
                    app.explore_progress = {"error": str(e)}
            redirect = ("../?v=settings" if ("/jinka/" in self.path or "/notify/" in self.path)
                        else "../explore" if "/explore/" in self.path else "./")
            # redirection relative : fonctionne derrière l'Ingress de Home Assistant
            self.send_response(303)
            self.send_header("Location", redirect)
            self.end_headers()

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


EXPLORE = Template("""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Zones compatibles</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{--bg:#fafafa;--fg:#1c1c1c;--muted:#666;--line:#e3e3e3;--card:#fff;--ok:#1a7f37;--ko:#b42318;--accent:#0b63ce}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--muted:#9a9a9a;--line:#2a2a2a;--card:#1a1a1a;--ok:#4ac26b;--ko:#f97066;--accent:#5aa2ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:6px;border-top:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);border-top:0}.ok{color:var(--ok)}.ko{color:var(--ko)}a{color:var(--accent)}
button{font:inherit;padding:7px 14px;border-radius:8px;border:1px solid var(--accent);background:var(--accent);color:#fff;cursor:pointer}
input{font:inherit;padding:6px 8px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--fg);width:70px}
#map{height:460px;border-radius:10px}.wrap{overflow-x:auto}textarea{width:100%;box-sizing:border-box;font:inherit;
background:var(--bg);color:var(--fg);border:1px solid var(--line);border-radius:8px;padding:8px}
</style></head><body><main>
<a href="./">← annonces</a>
<h1>Zones compatibles</h1>
<div class="muted">Communes d'où ${dests} sont joignables sans bus, depuis le centre de la commune
(marche jusqu'à la station comprise). À utiliser pour régler le secteur de tes alertes Jinka.</div>
<div class="card"><form method="post" action="explore/start">Temps max vers chaque adresse :
<input type="number" name="max_minutes" value="${max_minutes}" min="10" max="180"> min ·
rayon autour de tes adresses : <input type="number" name="radius" value="${radius}" min="3" max="40"> km
<button>${button}</button></form><div style="margin-top:8px">${status}</div></div>
${content}
</main></body></html>""")


def render_explore(app):
    esc = html.escape
    filt = [d for d in app.opts["destinations"] if not d["info_only"]]
    ex_max = (app.state.get("explore") or {}).get("max_minutes")
    dests = " et ".join(f"<b>{esc(d['name'])}</b> (≤ {ex_max or d['max_minutes']} min)" for d in filt) or "tes adresses"
    prog = app.explore_progress
    ex = app.state.get("explore")
    if prog.get("running"):
        status = (f"⏳ Calcul en cours : {prog.get('done', 0)} / {prog.get('total') or '…'} communes "
                  f"<script>setTimeout(()=>location.reload(),4000)</script>")
    elif prog.get("error"):
        status = f"<span class='ko'>{esc(prog['error'])}</span>"
    elif ex:
        n_ok = sum(r["ok"] for r in ex["results"])
        status = (f"Dernier calcul le {datetime.fromtimestamp(ex['at']).strftime('%d/%m %H:%M')} : "
                  f"<b class='ok'>{n_ok} communes compatibles</b> sur {len(ex['results'])} "
                  f"(seuil {ex.get('max_minutes') or 'des adresses'} min, rayon {ex['radius_km']} km, "
                  f"{ex.get('api_calls', 0)} appels IDFM, {ex.get('cache_hits', 0)} trajets déjà connus).")
    else:
        status = "Pas encore lancé (compter 2 à 5 minutes)."
    content = ""
    if ex and not prog.get("running"):
        ok_names = [r["nom"] + (f" ({r['cp']})" if r.get("cp") else "") for r in ex["results"] if r["ok"]]
        heads = "".join(f"<th>{esc(d['name'])}</th>" for d in ex["dests"])
        rows = []
        for r in ex["results"]:
            cells = "".join(
                f"<td class='{'ok' if t['ok'] else 'ko'}'>{t['minutes'] if t['minutes'] is not None else '—'} min{short_counted(t)}"
                f"<div class='muted'>{esc(t['summary'] or '')}</div></td>" for t in r["trips"])
            rows.append(f"<tr><td><b class='{'ok' if r['ok'] else 'ko'}'>{'✅' if r['ok'] else '❌'} "
                        f"{esc(r['nom'])}</b><div class='muted'>{esc(r.get('cp') or '')} · "
                        f"{r['distance_km']} km</div></td>{cells}</tr>")
        points = safe_json([{"n": html.escape(r["nom"]), "lat": r["lat"], "lon": r["lon"], "ok": r["ok"],
                              "t": html.escape(" · ").join(html.escape(f"{t['name']} {t['minutes'] if t['minutes'] is not None else '—'} min")
                                              for t in r["trips"])} for r in ex["results"]])
        dpoints = safe_json([{"n": html.escape(d["name"]), "lat": d["lat"], "lon": d["lon"]} for d in ex["dests"]])
        content = f"""<div class="card"><div id="map"></div></div>
<div class="card"><b>Communes compatibles</b> <span class="muted">(à recopier dans ton alerte Jinka)</span>
<textarea rows="4" readonly>{esc(', '.join(ok_names)) or 'aucune'}</textarea></div>
<div class="card wrap"><table><tr><th>Commune</th>{heads}</tr>{''.join(rows)}</table></div>
<script>
const pts={points}, dpts={dpoints};
const map=L.map('map');
L.tileLayer('https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2'
 +'&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/png&TILEMATRIX={{z}}&TILEROW={{y}}&TILECOL={{x}}',
 {{maxZoom:18,attribution:'© IGN Géoplateforme'}}).addTo(map);
const b=[];
pts.forEach(p=>{{b.push([p.lat,p.lon]);L.circleMarker([p.lat,p.lon],{{radius:p.ok?8:5,color:p.ok?'#1a7f37':'#b42318',
 fillOpacity:p.ok?.7:.35,weight:1}}).bindTooltip(p.n+' — '+p.t).addTo(map)}});
dpts.forEach(d=>L.marker([d.lat,d.lon]).bindTooltip(d.n,{{permanent:true}}).addTo(map));
map.fitBounds(b.length?b:dpts.map(d=>[d.lat,d.lon]));
</script>"""
    default_max = max([d["max_minutes"] for d in filt] or [45])
    return EXPLORE.substitute(dests=dests, radius=(ex or {}).get("radius_km", 15),
                              max_minutes=(ex or {}).get("max_minutes") or default_max,
                              button="Relancer" if ex else "Lancer la recherche", status=status, content=content)
