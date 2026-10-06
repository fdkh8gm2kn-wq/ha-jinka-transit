"""Envoi des notifications : email (SMTP), SMS Free Mobile, WhatsApp (CallMeBot), service notify de HA."""

import logging
import os
import smtplib
import time
from email.message import EmailMessage
from email.utils import formataddr

from http_util import HttpError, request

log = logging.getLogger("notify")


SMTP_HOSTS = {"gmail.com": "smtp.gmail.com", "googlemail.com": "smtp.gmail.com", "gmx.fr": "mail.gmx.net",
              "gmx.com": "mail.gmx.net", "laposte.net": "smtp.laposte.net", "orange.fr": "smtp.orange.fr",
              "free.fr": "smtp.free.fr", "icloud.com": "smtp.mail.me.com", "yahoo.fr": "smtp.mail.yahoo.com"}


class Notifier:
    def __init__(self, whatsapp_phone="", callmebot_apikey="", ha_service="", email_to="", smtp_user="",
                 smtp_password="", smtp_server="", free_sms_user="", free_sms_key="", sender_name=""):
        self.phone = (whatsapp_phone or "").replace(" ", "")
        self.apikey = (callmebot_apikey or "").strip()
        self.ha_service = (ha_service or "").strip()
        self.email_to = [a.strip() for a in (email_to or "").replace(";", ",").split(",") if a.strip()]
        self.smtp_user = (smtp_user or "").strip()
        self.smtp_password = (smtp_password or "").strip()
        domain = self.smtp_user.rsplit("@", 1)[-1].lower()
        self.smtp_server = (smtp_server or "").strip() or SMTP_HOSTS.get(domain, f"smtp.{domain}")
        if "gmail" in self.smtp_server:
            self.smtp_password = self.smtp_password.replace(" ", "")
        self.free_user = (free_sms_user or "").strip()
        self.free_key = (free_sms_key or "").strip()
        self.sender_name = (sender_name or "").strip() or "Jinka Transit"
        self._last_whatsapp = 0.0

    @property
    def configured(self):
        return bool((self.phone and self.apikey) or self.ha_service or self.email_ok or self.sms_ok)

    @property
    def email_ok(self):
        return bool(self.email_to and self.smtp_user and self.smtp_password)

    @property
    def sms_ok(self):
        return bool(self.free_user and self.free_key)

    def send(self, title, message, url=None, image=None, short=None, html=None):
        """Envoie sur tous les canaux configurés. Renvoie True si au moins un a réussi.
        `short` : version courte (SMS) ; `html` : version riche (email)."""
        ok = False
        if self.email_ok:
            ok |= self._email(title, message, html)
        if self.sms_ok:
            ok |= self._free_sms(short or f"{title}\n{url or ''}")
        if self.phone and self.apikey:
            ok |= self._whatsapp(f"*{title}*\n{message}")
        if self.ha_service:
            ok |= self._home_assistant(title, message, url, image)
        if not self.configured:
            log.warning("Aucun canal de notification configuré : %s", title)
        return ok

    def _email(self, subject, text, html=None):
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = formataddr((self.sender_name, self.smtp_user))
        msg["To"] = ", ".join(self.email_to)
        msg.set_content(text.replace("*", ""))
        if html:
            msg.add_alternative(html, subtype="html")
        try:
            with smtplib.SMTP_SSL(self.smtp_server, 465, timeout=30) as s:
                s.login(self.smtp_user, self.smtp_password)
                s.send_message(msg)
            return True
        except (smtplib.SMTPException, OSError) as e:
            log.error("Échec email via %s : %s", self.smtp_server, e)
            return False

    def _free_sms(self, text):
        """API SMS gratuite de Free Mobile (vers son propre numéro)."""
        try:
            status, _ = request("https://smsapi.free-mobile.fr/sendmsg",
                                params={"user": self.free_user, "pass": self.free_key, "msg": text[:999]},
                                timeout=30)
            return status == 200
        except (HttpError, OSError) as e:
            log.error("Échec SMS Free Mobile : %s", e)
            return False

    def _whatsapp(self, text):
        # CallMeBot demande de ne pas enchaîner les messages trop vite
        wait = 4 - (time.time() - self._last_whatsapp)
        if wait > 0:
            time.sleep(wait)
        try:
            _, body = request("https://api.callmebot.com/whatsapp.php",
                              params={"phone": self.phone, "text": text, "apikey": self.apikey},
                              timeout=30)
            self._last_whatsapp = time.time()
            if "error" in body.lower() and "queued" not in body.lower():
                log.error("CallMeBot a refusé le message : %s", body[:200])
                return False
            return True
        except (HttpError, OSError) as e:
            log.error("Échec WhatsApp : %s", e)
            return False

    def _home_assistant(self, title, message, url, image):
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            log.error("SUPERVISOR_TOKEN absent : le service HA ne fonctionne que dans l'add-on.")
            return False
        domain, _, service = self.ha_service.partition(".")
        if not service:
            domain, service = "notify", domain
        payload = {"title": title, "message": message}
        data = {}
        if url:
            data.update({"url": url, "clickAction": url})
        if image:
            data["image"] = image
        if data:
            payload["data"] = data
        try:
            request(f"http://supervisor/core/api/services/{domain}/{service}",
                    json_body=payload, headers={"Authorization": f"Bearer {token}"},
                    method="POST", timeout=20)
            return True
        except HttpError as e:
            if data and e.status == 400:  # service notify qui n'accepte pas "data"
                payload.pop("data")
                try:
                    request(f"http://supervisor/core/api/services/{domain}/{service}",
                            json_body=payload, headers={"Authorization": f"Bearer {token}"},
                            method="POST", timeout=20)
                    return True
                except (HttpError, OSError) as e2:
                    e = e2
            log.error("Échec notification HA (%s) : %s", self.ha_service, e)
            return False
        except OSError as e:
            log.error("Échec notification HA (%s) : %s", self.ha_service, e)
            return False
