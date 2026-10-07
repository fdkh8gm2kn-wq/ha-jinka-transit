"""Mise en forme des annonces pour les notifications et l'interface."""

import re


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


DEST_ICONS = ((r"travail|bureau|boulot|job|work", "💼"), (r"[ée]cole|fac\b|facult|univ|campus|lyc[ée]e|school", "🎓"),
              (r"gare|train", "🚆"), (r"arena|concert|salle|z[ée]nith|olympia|stade", "🎤"))


def safe_url(u):
    """Lien utilisable dans une page ou un email : http(s) uniquement (jamais « javascript: »)."""
    u = str(u or "").strip()
    return u if re.match(r"(?i)^https?://", u) else "#"


def dest_icon(name, short=True):
    """Icône d'une adresse (💼 travail, 🎓 école, 🚆 gare, 🎤 salle de concert), sinon son nom court."""
    import re
    for pat, ico in DEST_ICONS:
        if re.search(pat, name or "", re.I):
            return ico
    m = re.search(r"\(([^)]+)\)", name or "")
    return m.group(1) if (m and short) else (name or "")


# Couleurs officielles de l'étiquette énergie (DPE 2021)
DPE_COLORS = {"A": ("#009c6d", "#fff"), "B": ("#52b153", "#fff"), "C": ("#a5cc74", "#111"), "D": ("#f4e70f", "#111"),
              "E": ("#f0b40f", "#111"), "F": ("#eb8235", "#fff"), "G": ("#d7221f", "#fff")}


def html_of(v):
    """Version email, façon alerte de portail immobilier : photo, prix, badges, trajets en icônes, bouton."""
    from html import escape as e
    l = v["listing"]
    brand = e(v.get("brand") or "Jinka Transit")
    link = e(safe_url(l.get("link")))
    font = "font-family:-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
    badge = ("display:inline-block;padding:3px 8px;border-radius:6px;font-size:12px;font-weight:600;"
             "margin:0 4px 4px 0;background:#f2f4f7;color:#344054")
    photo = (f"<a href='{link}'><img src='{e(safe_url(l['image']))}' alt='' width='600' "
             f"style='display:block;width:100%;max-width:600px;height:auto;border:0'></a>"
             if l.get("image") and str(l["image"]).startswith("http") else
             f"<a href='{link}' style='display:block;text-decoration:none;background:#eef2ff;text-align:center;"
             f"font-size:56px;line-height:180px'>🏠</a>")
    facts = " · ".join(x for x in (
        f"{int(l['rooms'])} pièce{'s' if l['rooms'] > 1 else ''}" if l.get("rooms") else "",
        f"{l['area']:g} m²" if l.get("area") else "", building_info(l)) if x)
    badges = []
    if l.get("dpe") in DPE_COLORS:
        bg, fg = DPE_COLORS[l["dpe"]]
        badges.append(f"<span title='Classe énergie (DPE)' style='{badge};background:{bg};color:{fg};"
                      f"font-weight:800;border-radius:4px 10px 10px 4px;padding:3px 11px 3px 8px'>{l['dpe']}</span>")
    if l.get("furnished") is False:
        badges.append(f"<span style='{badge};background:#eef2ff;color:#3a5bdc'>📦 Non meublé</span>")
    elif l.get("furnished"):
        badges.append(f"<span style='{badge}'>Meublé</span>")
    if l.get("source"):
        badges.append(f"<span style='{badge}'>{e(str(l['source']).split(' · ')[0])}</span>")
    chips, details = [], []
    for r in v.get("results", []):
        if r.get("minutes") is None:
            continue
        info = r.get("info_only")
        bg, fg = ("#f2f4f7", "#667085") if info else (("#e7f6ef", "#12805c") if r.get("ok") else ("#fdecea", "#b42318"))
        chips.append(f"<td style='padding:0 6px 6px 0'><span style='display:inline-block;padding:6px 10px;border-radius:10px;"
                     f"background:{bg};color:{fg};font-size:16px;font-weight:700;white-space:nowrap'>"
                     f"<span style='font-size:20px'>{e(dest_icon(r['name']))}</span> {r['minutes']}'</span></td>")
        steps = "<br>".join(e(x.strip().replace("*", "")) for x in steps_of(r))
        details.append(f"<p style='margin:12px 0 2px;font-size:14px'><b>{e(dest_icon(r['name']))} {e(r['name'])}</b> — "
                       f"{r['minutes']} min porte à porte{counted_txt(r) if not info else ''}"
                       f"{'' if info else ' (max ' + str(r.get('max', '?')) + ')'}</p>"
                       f"<p style='margin:0;font-size:13px;color:#475467;line-height:1.5'>{steps}</p>")
    approx = (f"<p style='margin:10px 0 0;padding:8px 10px;border-radius:8px;background:#fef4e2;color:#a15c07;font-size:13px'>"
              f"⚠️ Position estimée : {e(str(l['approx']))}</p>" if l.get("approx") else "")
    maps = (f"<a href='https://maps.google.com/?q={l['lat']},{l['lng']}' style='color:#3a5bdc;font-size:13px'>"
            f"Voir sur la carte</a>" if l.get("lat") is not None and not l.get("approx") else "")
    where = e(l.get("city") or "") + (f" <span style='color:#667085;font-weight:400'>{e(l.get('postal_code') or '')}"
                                     f"{' · ' + e(l['quartier']) if l.get('quartier') else ''}</span>")
    rent = f"{int(l['rent']):,}".replace(",", " ") if l.get("rent") else "?"
    return f"""<div style="background:#f6f7f9;padding:16px 0;{font}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;margin:0 auto">
<tr><td style="padding:0 12px 10px;font-size:13px;color:#667085">🏠 <b style="color:#16181d">{brand}</b> · nouvelle annonce</td></tr>
<tr><td style="background:#fff;border:1px solid #e4e7ec;border-radius:14px;overflow:hidden">{photo}
<div style="padding:16px 18px 18px;color:#16181d">
<div style="font-size:28px;font-weight:800;line-height:1.1">{rent} €<span style="font-size:14px;font-weight:500;color:#667085"> /mois CC</span></div>
<div style="font-size:15px;color:#344054;margin-top:4px">{e(facts)}</div>
<div style="font-size:16px;font-weight:700;margin:8px 0">{where}</div>
<div>{''.join(badges)}</div>{approx}
<table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:12px"><tr>{''.join(chips)}</tr></table>
<a href="{link}" style="display:block;margin-top:14px;background:#3a5bdc;color:#fff;text-align:center;padding:13px;
border-radius:10px;font-size:16px;font-weight:700;text-decoration:none">Voir l'annonce</a>
<div style="text-align:center;margin-top:8px">{maps}</div>
</div></td></tr>
<tr><td style="padding:14px 6px 0"><div style="font-size:15px;font-weight:700;color:#16181d">Itinéraires</div>{''.join(details)}</td></tr>
<tr><td style="padding:16px 6px 0;font-size:12px;color:#98a2b3">{e(l.get('source') or '')} · alerte « {e(l.get('alert_name') or '')} » · {brand}</td></tr>
</table></div>"""


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
