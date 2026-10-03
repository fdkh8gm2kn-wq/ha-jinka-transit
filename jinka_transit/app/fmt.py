"""Mise en forme des annonces pour les notifications et l'interface."""


def title_of(l):
    parts = []
    if l.get("rooms"):
        parts.append(f"{int(l['rooms'])}p")
    if l.get("area"):
        parts.append(f"{int(l['area'])} m²")
    if l.get("rent"):
        parts.append(f"{int(l['rent']):,} €".replace(",", " "))
    where = " ".join(x for x in (l.get("city"), f"({l['postal_code']})" if l.get("postal_code") else "") if x)
    return f"🏠 {' · '.join(parts) or 'Annonce'} — {where}".strip(" —")


MODE_ICON = {"Metro": "🚇", "Funicular": "🚠", "RapidTransit": "🚆", "LocalTrain": "🚆", "Train": "🚆",
             "Tramway": "🚊"}


def meters(m):
    return f"{m / 1000:.1f} km".replace(".", ",") if m and m >= 1000 else f"{m or 0} m"


def steps_of(r):
    """Itinéraire détaillé vers une destination, une ligne par étape."""
    steps = r.get("steps")
    if not steps:
        return [f"   {r.get('summary', '')}"] if r.get("summary") else []
    out = []
    for i, st in enumerate(steps):
        nxt = steps[i + 1] if i + 1 < len(steps) else None
        if st["kind"] == "walk":
            if st.get("role") == "start" and nxt and nxt["kind"] == "ride":
                out.append(f"   🚶 {st['minutes']} min ({meters(st['meters'])}) jusqu'à *{nxt['from']}*")
            elif st.get("role") == "end":
                prev = steps[i - 1] if i else {}
                out.append(f"   🚶 {st['minutes']} min ({meters(st['meters'])}) de *{prev.get('to') or 'la station'}* "
                           f"jusqu'à {r['name'] or 'destination'}")
            elif len(steps) == 1:
                out.append(f"   🚶 {st['minutes']} min ({meters(st['meters'])}) à pied, directement")
            else:
                out.append(f"   🚶 {st['minutes']} min ({meters(st['meters'])}) à pied")
        elif st["kind"] == "ride":
            stops = f", {st['stops']} arrêt{'s' if st['stops'] > 1 else ''}" if st.get("stops") else ""
            direction = f" (dir. {st['direction']})" if st.get("direction") else ""
            out.append(f"   {MODE_ICON.get(st['mode'], '🚉')} *{st['line']}*{direction} : "
                       f"{st['from']} → {st['to']} · {st['minutes']} min{stops}")
        elif st["kind"] == "transfer":
            extra = f", {st['meters']} m" if st.get("meters") else ""
            wait = f" + {st['wait']} min d'attente" if st.get("wait") else ""
            out.append(f"   🔁 Correspondance à {st.get('at') or 'la station'} : "
                       f"{st['minutes']} min à pied{extra}{wait}")
    return out


def body_of(v):
    l = v["listing"]
    lines = []
    for r in v["results"]:
        lines.append("")
        if r.get("minutes") is None:
            lines.append(f"ℹ️ *{r['name']}* (pour info) — {r.get('reason') or 'pas de trajet'}")
            continue
        transfers = r.get("transfers", 0)
        corr = "direct" if not transfers else f"{transfers} correspondance{'s' if transfers > 1 else ''}"
        head = (f"ℹ️ *{r['name']}* (pour info) — {r['minutes']} min porte à porte" if r.get("info_only")
                else f"📍 *{r['name']}* — {r['minutes']} min porte à porte (max {r.get('max', '?')})")
        lines.append(f"{head} · {r.get('walk_minutes', 0)} min à pied · {corr}")
        lines.extend(steps_of(r))
    lines.append("")
    lines.append(f"🔗 {l['link']}")
    if l.get("lat") is not None:
        lines.append(f"🗺️ https://maps.google.com/?q={l['lat']},{l['lng']}")
    if l.get("source"):
        lines.append(f"Source : {l['source']} · alerte « {l['alert_name']} »")
    return "\n".join(lines)
