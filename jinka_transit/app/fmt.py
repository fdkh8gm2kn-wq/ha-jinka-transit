"""Mise en forme des annonces pour les notifications et l'interface."""


def title_of(l):
    parts = []
    if l.get("rooms"):
        parts.append(f"{int(l['rooms'])}p")
    if l.get("area"):
        parts.append(f"{int(l['area'])} m²")
    if l.get("rent"):
        parts.append(f"{int(l['rent']):,} €".replace(",", " "))
    if l.get("dpe"):
        parts.append(f"DPE {l['dpe']}")
    if l.get("furnished") is False:
        parts.append("📦 NON MEUBLÉ")
    elif l.get("furnished"):
        parts.append("meublé")
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


def counted_txt(r):
    """« , compté 47 min avec les correspondances » quand une pénalité s'applique."""
    c = r.get("counted")
    return f", compté {c} min avec les correspondances" if c and c != r.get("minutes") else ""


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
                else f"📍 *{r['name']}* — {r['minutes']} min porte à porte{counted_txt(r)} (max {r.get('max', '?')})")
        lines.append(f"{head} · {r.get('walk_minutes', 0)} min à pied · {corr}")
        lines.extend(steps_of(r))
    if l.get("approx"):
        lines.append("")
        lines.append(f"⚠️ Pas d'adresse GPS : position estimée {l['approx']}")
    extra = building_info(l)
    if extra:
        lines.append(f"🏢 {extra}")
    lines.append("")
    lines.append(f"🔗 {l['link']}")
    if l.get("lat") is not None:
        lines.append(f"🗺️ https://maps.google.com/?q={l['lat']},{l['lng']}")
    if l.get("source"):
        lines.append(f"Source : {l['source']} · alerte « {l['alert_name']} »")
    return "\n".join(lines)


def short_of(v):
    """Version SMS : une ligne par adresse du filtre, puis le lien."""
    l = v["listing"]
    trips = " · ".join(f"{r['name'].split(' (')[0]} {r['minutes']}'" for r in v["results"]
                       if not r.get("info_only") and r.get("minutes") is not None)
    approx = "\n(position estimée)" if l.get("approx") else ""
    return f"{title_of(l)}\n{trips}{approx}\n{l['link']}"


def html_of(v):
    """Version email : photo, trajets détaillés et bouton vers l'annonce."""
    from html import escape as e
    l = v["listing"]
    blocks = []
    for r in v["results"]:
        if r.get("minutes") is None:
            blocks.append(f"<p style='color:#666'>ℹ️ <b>{e(r['name'])}</b> : {e(r.get('reason') or 'pas de trajet')}</p>")
            continue
        head = (f"ℹ️ <b>{e(r['name'])}</b> (pour info) — {r['minutes']} min" if r.get("info_only")
                else f"📍 <b>{e(r['name'])}</b> — <b>{r['minutes']} min</b> porte à porte{counted_txt(r)} (max {r.get('max', '?')})")
        steps = "".join(f"<li>{e(s.strip().replace('*', ''))}</li>" for s in steps_of(r))
        blocks.append(f"<p style='margin:14px 0 4px'>{head}</p><ul style='margin:0;padding-left:18px;color:#333'>{steps}</ul>")
    img = (f"<img src='{e(l['image'])}' alt='' style='width:100%;max-width:560px;border-radius:10px'>"
           if l.get("image") else "")
    maps = (f" · <a href='https://maps.google.com/?q={l['lat']},{l['lng']}'>voir sur la carte</a>"
            if l.get("lat") is not None else "")
    return f"""<div style="font-family:-apple-system,Segoe UI,Roboto,sans-serif;max-width:560px;color:#1c1c1c">
<h2 style="font-size:18px;margin:0 0 10px">{e(title_of(l))}</h2>{img}
{f"<p style='background:#fff4e5;padding:8px 10px;border-radius:8px'>⚠️ Pas d'adresse GPS : position estimée {e(l['approx'])}</p>" if l.get("approx") else ""}
{''.join(blocks)}
<p style="margin:18px 0"><a href="{e(l['link'])}" style="background:#0b63ce;color:#fff;padding:10px 16px;
border-radius:8px;text-decoration:none">Voir l'annonce</a>{maps}</p>
{f"<p>🏢 {e(building_info(l))}</p>" if building_info(l) else ""}
<p style="color:#888;font-size:12px">{e(l.get('source') or '')} · alerte « {e(l.get('alert_name') or '')} » · Jinka Transit</p></div>"""


def building_info(l):
    """Étage et sécurité de l'immeuble, quand l'annonce les donne (Bien'ici)."""
    parts = []
    if l.get("floor") is not None:
        parts.append("rez-de-chaussée" if l["floor"] == 0 else "1er étage" if l["floor"] == 1 else f"{l['floor']:g}e étage")
    if l.get("elevator") is True:
        parts.append("ascenseur")
    elif l.get("elevator") is False:
        parts.append("sans ascenseur")
    if l.get("safety"):
        parts.append(", ".join(l["safety"]))
    return " · ".join(parts)
