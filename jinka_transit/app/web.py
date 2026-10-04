"""Mini interface web (onglet dans Home Assistant via Ingress, ou http://localhost:8099 en local)."""

import html
import json
import threading
from string import Template
from urllib.parse import parse_qs
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fmt import steps_of, title_of

STATUS_LABEL = {"notified": "✅ envoyée", "match": "⏳ OK, en attente", "silent": "✅ OK (1er scan, non envoyée)",
                "rejected": "❌ refusée"}

PAGE = Template("""<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jinka Transit</title>
<style>
:root{--bg:#fafafa;--fg:#1c1c1c;--muted:#666;--line:#e3e3e3;--card:#fff;--ok:#1a7f37;--ko:#b42318;--accent:#0b63ce}
@media (prefers-color-scheme:dark){:root{--bg:#111;--fg:#eee;--muted:#9a9a9a;--line:#2a2a2a;--card:#1a1a1a;--ok:#4ac26b;--ko:#f97066;--accent:#5aa2ff}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.45 system-ui,-apple-system,sans-serif}
main{max-width:1100px;margin:0 auto;padding:16px}
h1{font-size:20px;margin:0 0 4px}.muted{color:var(--muted)}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:12px 0}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:7px 6px;border-top:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);border-top:0}.ok{color:var(--ok)}.ko{color:var(--ko)}
a{color:var(--accent)}button{font:inherit;padding:7px 14px;border-radius:8px;border:1px solid var(--accent);
background:var(--accent);color:#fff;cursor:pointer}.wrap{overflow-x:auto}
.filters a{margin-right:10px}
.row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.row input{font:inherit;padding:7px 10px;border-radius:8px;
border:1px solid var(--line);background:var(--bg);color:var(--fg);min-width:0;flex:1 1 200px}
.flash{padding:10px 14px;border-radius:10px;margin:12px 0;border:1px solid var(--line);background:var(--card)}
</style></head><body><main>
<h1>Jinka Transit</h1>
<div class="muted">Annonces Jinka filtrées par trajet en transports lourds (sans bus) ·
<a href="explore">🗺️ Zones compatibles (recherche élargie)</a></div>
${flash}<div class="card"><b>Connexion Jinka :</b> ${jinka_status}
<form method="post" action="jinka/send" class="row"><input type="email" name="email" value="${jinka_email}"
 placeholder="ton email Jinka" required><button>Recevoir un code</button></form>${code_form}${auto_form}</div>
<div class="card"><b>Dernier scan :</b> ${last_at} — ${last_status}${last_error}
<form method="post" action="scan" style="display:inline;margin-left:12px"><button>Scanner maintenant</button></form>
<form method="post" action="notify/test" style="display:inline;margin-left:8px"><button>Envoyer une notification de test</button></form></div>
<div class="card"><b>Adresses</b><table><tr><th>Nom</th><th>Adresse saisie</th><th>Localisée à</th><th>Max</th><th>Arrivée</th></tr>${dests}</table>
<div class="muted">Loyer max : ${rent} · surface min : ${area} · Modes autorisés : ${modes} · marche max logement → station : ${home_walk} min · marche max côté destination : ${walk} min</div></div>
<div class="card"><div class="filters">Afficher : ${filters}</div><div class="wrap"><table>
<tr><th>Annonce</th><th>Statut</th><th>Trajets</th><th>Vue le</th></tr>${rows}</table></div></div>
</main></body></html>""")


def render(app, flt):
    esc = html.escape
    st = app.state
    geocache = st.get("geocode", {})
    dests = "".join(
        f"<tr><td>{esc(d['name'])}</td><td>{esc(d['address'])}</td>"
        f"<td>{esc((geocache.get(d['address'].strip()) or {}).get('label') or '— (au prochain scan)')}</td>"
        f"<td>{d['max_minutes']} min</td><td>{esc(d['arrival_time'])}</td></tr>"
        for d in app.opts["destinations"]) or "<tr><td colspan=5>Aucune adresse configurée</td></tr>"

    items = sorted(st.get("listings", {}).values(), key=lambda v: v.get("ts", 0), reverse=True)
    if flt == "ok":
        items = [v for v in items if v["status"] != "rejected"]
    elif flt == "ko":
        items = [v for v in items if v["status"] == "rejected"]
    rows = []
    for v in items[:300]:
        l = v["listing"]
        trips = "<br>".join(
            f"<span class='{'muted' if r.get('info_only') else 'ok' if r['ok'] else 'ko'}'>"
            f"{esc(r['name'])}{' (info)' if r.get('info_only') else ''} : "
            f"{r['minutes'] if r.get('minutes') is not None else '—'} min</span> "
            f"<span class='muted'>{esc(r.get('summary') or r.get('reason') or '')}</span>"
            + (f"<details><summary class='muted'>itinéraire</summary><div class='muted' style='white-space:pre-line'>"
               f"{esc(chr(10).join(x.strip().replace('*', '') for x in steps_of(r)))}</div></details>"
               if r.get("steps") else "")
            for r in v.get("results", [])) or f"<span class='ko'>{esc(v.get('reason', ''))}</span>"
        cls = "ko" if v["status"] == "rejected" else "ok"
        rows.append(
            f"<tr><td><a href='{esc(l['link'])}' target='_blank' rel='noopener'>{esc(title_of(l))}</a>"
            f"<div class='muted'>{esc(l.get('source') or '')} · {esc(l.get('alert_name') or '')}</div></td>"
            f"<td class='{cls}'>{STATUS_LABEL.get(v['status'], v['status'])}</td><td>{trips}</td>"
            f"<td class='muted'>{datetime.fromtimestamp(v.get('ts', 0)).strftime('%d/%m %H:%M')}</td></tr>")
    filters = " ".join(
        f"<a href='?f={k}'{' style=font-weight:700' if flt == k else ''}>{label}</a>"
        for k, label in (("all", "toutes"), ("ok", "OK"), ("ko", "refusées")))
    last = app.last_scan
    auth = app.state.get("jinka_auth") or {}
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
    flash, app.flash = app.flash, ""
    if app.auto_test_running:
        flash = ("⏳ Connexion automatique en cours : code demandé à Jinka, lecture de la boîte mail…"
                 "<script>setTimeout(()=>location.reload(),5000)</script>")
    return PAGE.substitute(
        flash=f"<div class='flash'>{flash}</div>" if flash else "",
        jinka_status=jinka_status, code_form=code_form,
        auto_form=("" if not (app.opts.get("jinka_email") and app.opts.get("mail_password")) else
                   "<form method='post' action='jinka/auto' class='row'><button>🔄 Tester la connexion automatique"
                   f" ({esc(app.opts['jinka_email'])})</button><span class='muted'>demande un code, le lit dans "
                   "la boîte mail et se connecte (≈ 30 s)</span></form>"),
        jinka_email=esc(app.login_email or auth.get("email") or app.opts.get("jinka_email") or ""),
        last_at=esc(last["at"] or "—"), last_status=esc(last["status"]),
        last_error=f" <span class='ko'>{esc(last['error'])}</span>" if last.get("error") else "",
        dests=dests, modes=esc(", ".join(app.opts.get("allowed_modes") or [])),
        walk=app.opts.get("max_walk_minutes", 15), filters=filters,
        home_walk=app.opts.get("max_walk_home_minutes", 5),
        rent=f"{app.opts['max_rent']} €" if app.opts.get("max_rent") else "aucun",
        area=f"{app.opts['min_area']} m²" if app.opts.get("min_area") else "aucune",
        rows="".join(rows) or "<tr><td colspan=4 class='muted'>Rien pour l'instant</td></tr>")


def start(app, port=8099):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path.endswith("/api/state"):
                return self._send(200, json.dumps({"last_scan": app.last_scan, **app.state},
                                                  ensure_ascii=False), "application/json")
            if path.rstrip("/").endswith("explore"):
                return self._send(200, render_explore(app))
            flt = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("f", "all")
            self._send(200, render(app, flt))

        def do_POST(self):
            path = self.path.rstrip("/")
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
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
            redirect = "../" if ("/jinka/" in self.path or "/notify/" in self.path) else "../explore" if "/explore/" in self.path else "./"
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
                f"<td class='{'ok' if t['ok'] else 'ko'}'>{t['minutes'] if t['minutes'] is not None else '—'} min"
                f"<div class='muted'>{esc(t['summary'] or '')}</div></td>" for t in r["trips"])
            rows.append(f"<tr><td><b class='{'ok' if r['ok'] else 'ko'}'>{'✅' if r['ok'] else '❌'} "
                        f"{esc(r['nom'])}</b><div class='muted'>{esc(r.get('cp') or '')} · "
                        f"{r['distance_km']} km</div></td>{cells}</tr>")
        points = json.dumps([{"n": r["nom"], "lat": r["lat"], "lon": r["lon"], "ok": r["ok"],
                              "t": " · ".join(f"{t['name']} {t['minutes'] if t['minutes'] is not None else '—'} min"
                                              for t in r["trips"])} for r in ex["results"]])
        dpoints = json.dumps([{"n": d["name"], "lat": d["lat"], "lon": d["lon"]} for d in ex["dests"]])
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
