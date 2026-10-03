"""Mini interface web (onglet dans Home Assistant via Ingress, ou http://localhost:8099 en local)."""

import html
import json
import threading
from string import Template
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
</style></head><body><main>
<h1>Jinka Transit</h1>
<div class="muted">Annonces Jinka filtrées par trajet en transports lourds (sans bus)</div>
<div class="card"><b>Dernier scan :</b> ${last_at} — ${last_status}${last_error}
<form method="post" action="scan" style="display:inline;margin-left:12px"><button>Scanner maintenant</button></form></div>
<div class="card"><b>Adresses</b><table><tr><th>Nom</th><th>Adresse saisie</th><th>Localisée à</th><th>Max</th><th>Arrivée</th></tr>${dests}</table>
<div class="muted">Loyer max : ${rent} · Modes autorisés : ${modes} · marche max vers une station : ${walk} min</div></div>
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
            f"<span class='{'ok' if r['ok'] else 'ko'}'>{esc(r['name'])} : "
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
    return PAGE.substitute(
        last_at=esc(last["at"] or "—"), last_status=esc(last["status"]),
        last_error=f" <span class='ko'>{esc(last['error'])}</span>" if last.get("error") else "",
        dests=dests, modes=esc(", ".join(app.opts.get("allowed_modes") or [])),
        walk=app.opts.get("max_walk_minutes", 15), filters=filters,
        rent=f"{app.opts['max_rent']} €" if app.opts.get("max_rent") else "aucun",
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
            flt = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("f", "all")
            self._send(200, render(app, flt))

        def do_POST(self):
            if self.path.rstrip("/").endswith("scan"):
                app.scan_now.set()
            # redirection relative : fonctionne derrière l'Ingress de Home Assistant
            self.send_response(303)
            self.send_header("Location", "./")
            self.end_headers()

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
