"""Lecture du code de connexion Jinka dans une boîte mail dédiée (IMAP), pour une reconnexion
100 % automatique."""

import email
import imaplib
import logging
import re
import time
from datetime import datetime, timedelta
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from html import unescape

from jinka import JinkaAuthError

# Serveur IMAP déduit du domaine de l'adresse (si mail_imap_server est vide)
IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com", "googlemail.com": "imap.gmail.com",
    "gmx.fr": "imap.gmx.net", "gmx.com": "imap.gmx.net", "gmx.net": "imap.gmx.net",
    "laposte.net": "imap.laposte.net", "orange.fr": "imap.orange.fr", "wanadoo.fr": "imap.orange.fr",
    "free.fr": "imap.free.fr", "sfr.fr": "imap.sfr.fr", "icloud.com": "imap.mail.me.com",
    "me.com": "imap.mail.me.com", "yahoo.fr": "imap.mail.yahoo.com", "yahoo.com": "imap.mail.yahoo.com",
    "outlook.fr": "outlook.office365.com", "outlook.com": "outlook.office365.com",
    "hotmail.fr": "outlook.office365.com", "hotmail.com": "outlook.office365.com",
}

log = logging.getLogger("mailbox")


def imap_host(address, configured=""):
    if configured:
        return configured.strip()
    domain = address.rsplit("@", 1)[-1].lower()
    return IMAP_HOSTS.get(domain, f"imap.{domain}")


def wait_for_code(host, user, password, since_ts, timeout=240, poll=10):
    """Attend l'email de Jinka arrivé après `since_ts` et renvoie le code à 4 chiffres."""
    deadline = time.time() + timeout
    while True:
        code = find_code(host, user, password, since_ts)
        if code:
            return code
        if time.time() > deadline:
            raise JinkaAuthError(f"Aucun email de code Jinka reçu dans {user} en {timeout // 60} min.")
        time.sleep(poll)


def clean_password(host, password):
    """Les clés d'application Google s'affichent en 4 groupes séparés par des espaces."""
    password = (password or "").strip()
    return password.replace(" ", "") if "gmail" in host or "google" in host else password


def find_code(host, user, password, since_ts):
    password = clean_password(host, password)
    try:
        imap = imaplib.IMAP4_SSL(host, 993, timeout=30)
    except OSError as e:
        raise JinkaAuthError(f"Serveur mail {host} injoignable : {e}") from None
    try:
        try:
            imap.login(user, password)
        except imaplib.IMAP4.error as e:
            detail = e.args[0].decode(errors="replace") if e.args and isinstance(e.args[0], bytes) else str(e)
            raise JinkaAuthError(f"Connexion à la boîte mail {user} refusée par {host} ({detail}). Vérifie la "
                                 "clé d'application Google (16 lettres).") from None
        for folder in folders(imap):
            if imap.select(folder, readonly=True)[0] != "OK":
                continue
            day = (datetime.fromtimestamp(since_ts) - timedelta(days=1)).strftime("%d-%b-%Y")
            typ, data = imap.search(None, "SINCE", day)
            if typ != "OK":
                continue
            for num in reversed(data[0].split()[-30:]):
                typ, msg_data = imap.fetch(num, "(RFC822)")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                code = code_from_message(email.message_from_bytes(msg_data[0][1]), since_ts)
                if code:
                    log.info("Code Jinka trouvé dans %s.", folder)
                    return code
        return None
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass


SKIP_FLAGS = ("\\Sent", "\\Drafts", "\\Trash", "\\Noselect", "\\All", "\\Archive")
SKIP_NAMES = re.compile(r"sent|envoy|draft|brouillon|trash|corbeille|deleted|supprim", re.I)


def folders(imap):
    """Réception d'abord, puis tous les autres dossiers (spam, « Infos et promos »…) sauf
    envoyés, brouillons et corbeille."""
    out = ["INBOX"]
    typ, data = imap.list()
    if typ == "OK":
        for line in data or []:
            line = line.decode(errors="ignore")
            m = re.match(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
            if not m:
                continue
            name = m.group("name").strip().strip('"')
            if name.upper() == "INBOX" or any(f in m.group("flags") for f in SKIP_FLAGS) or SKIP_NAMES.search(name):
                continue
            if f'"{name}"' not in out:
                out.append(f'"{name}"')
    return out


def code_from_message(msg, since_ts):
    sender = str(make_header(decode_header(msg.get("From", ""))))
    if "jinka" not in sender.lower():
        return None
    try:
        sent = parsedate_to_datetime(msg.get("Date")).timestamp()
    except (TypeError, ValueError):
        sent = 0
    if sent and sent < since_ts - 120:  # ancien code : on ignore
        return None
    subject = str(make_header(decode_header(msg.get("Subject", ""))))
    return extract_code(subject + "\n" + body_text(msg))


def body_text(msg):
    parts = []
    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = part.get_content_type()
        if ctype in ("text/plain", "text/html"):
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
            if ctype == "text/html":
                text = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(style|script).*?</\1>", " ", text)))
            parts.append(text)
    return "\n".join(parts)


def extract_code(text):
    """Le code à 4 chiffres : de préférence juste après le mot « code », en évitant les années."""
    years = {str(y) for y in range(datetime.now().year - 2, datetime.now().year + 3)}
    m = re.search(r"code\D{0,80}?\b(\d{4})\b", text, re.I)
    if m and m.group(1) not in years:
        return m.group(1)
    candidates = [c for c in re.findall(r"(?<![\d.,/:])\b(\d{4})\b(?![\d.,/:])", text) if c not in years]
    return candidates[0] if len(set(candidates)) == 1 else None


ALERT_SENDERS = ("seloger", "leboncoin")
ACCOUNT_MAIL = re.compile(r"(?i)confirm|mot de passe|password|v[ée]rifi|connexion|identifiant|code de|s[ée]curit")


def alert_emails(host, user, password, since_days=3, max_per_folder=60):
    """Emails d'alerte SeLoger / Leboncoin reçus dans la boîte dédiée (les plus récents d'abord)."""
    password = clean_password(host, password)
    imap = imaplib.IMAP4_SSL(host, 993, timeout=30)
    out = []
    try:
        imap.login(user, password)
        day = (datetime.now() - timedelta(days=since_days)).strftime("%d-%b-%Y")
        for folder in folders(imap):
            if imap.select(folder, readonly=True)[0] != "OK":
                continue
            typ, data = imap.search(None, "SINCE", day)
            if typ != "OK":
                continue
            for num in reversed(data[0].split()[-max_per_folder:]):
                typ, head = imap.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])")
                if typ != "OK" or not head or not isinstance(head[0], tuple):
                    continue
                h = email.message_from_bytes(head[0][1])
                sender = str(make_header(decode_header(h.get("From", ""))))
                site = next((s for s in ALERT_SENDERS if s in sender.lower()), None)
                subject = str(make_header(decode_header(h.get("Subject", ""))))
                if not site or ACCOUNT_MAIL.search(subject):
                    continue  # pas les emails de compte (confirmation, mot de passe…)
                typ, msg_data = imap.fetch(num, "(BODY.PEEK[])")
                if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                out.append({"site": site, "id": h.get("Message-ID") or f"{folder}:{num.decode()}",
                            "from": sender, "subject": str(make_header(decode_header(h.get("Subject", "")))),
                            "date": h.get("Date"), "html": html_part(msg)})
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return out


def html_part(msg):
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_type() == "text/html":
            payload = part.get_payload(decode=True) or b""
            return payload.decode(part.get_content_charset() or "utf-8", "replace")
    return body_text(msg)


def special_folders(imap):
    """Dossiers spéciaux de la boîte (RFC 6154) : {"\\All": "[Gmail]/Tous les messages", "\\Trash": …, "\\Junk": …}."""
    out = {}
    typ, data = imap.list()
    if typ != "OK":
        return out
    for line in data or []:
        line = line.decode(errors="ignore") if isinstance(line, bytes) else str(line)
        m = re.match(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$', line)
        if not m:
            continue
        name = m.group("name").strip().strip('"')
        for flag in ("\\All", "\\Trash", "\\Junk"):
            if flag in m.group("flags"):
                out[flag] = name
    return out


def cleanup(host, user, password, older_than_days):
    """Supprime définitivement les messages reçus il y a plus de N jours dans la boîte dédiée :
    tous les messages (et le spam) vont à la corbeille, puis ces messages sont effacés de la corbeille.
    Renvoie (nombre mis à la corbeille, nombre effacés définitivement)."""
    password = clean_password(host, password)
    imap = imaplib.IMAP4_SSL(host, 993, timeout=60)
    moved = purged = 0
    q = lambda name: '"' + name.replace('"', '\\"') + '"'
    try:
        imap.login(user, password)
        folders_ = special_folders(imap)
        trash = folders_.get("\\Trash")
        if not trash:
            raise RuntimeError("Corbeille introuvable dans la boîte mail.")
        day = (datetime.now() - timedelta(days=older_than_days)).strftime("%d-%b-%Y")
        can_move = b"MOVE" in b" ".join(c if isinstance(c, bytes) else str(c).encode()
                                         for c in (getattr(imap, "capabilities", ()) or ()))
        for folder in (folders_.get("\\All") or "INBOX", folders_.get("\\Junk")):
            if not folder or imap.select(q(folder))[0] != "OK":
                continue
            typ, data = imap.uid("SEARCH", None, "BEFORE", day)
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            for i in range(0, len(uids), 200):
                seq = b",".join(uids[i:i + 200]).decode()
                if can_move:
                    imap.uid("MOVE", seq, q(trash))
                else:
                    imap.uid("COPY", seq, q(trash))
                    imap.uid("STORE", seq, "+FLAGS", "(\\Deleted)")
            if uids and not can_move:
                imap.expunge()
            moved += len(uids)
        if imap.select(q(trash))[0] == "OK":
            typ, data = imap.uid("SEARCH", None, "BEFORE", day)
            uids = data[0].split() if typ == "OK" and data and data[0] else []
            for i in range(0, len(uids), 200):
                imap.uid("STORE", b",".join(uids[i:i + 200]).decode(), "+FLAGS", "(\\Deleted)")
            if uids:
                imap.expunge()
            purged = len(uids)
    finally:
        try:
            imap.logout()
        except Exception:  # noqa: BLE001
            pass
    return moved, purged
