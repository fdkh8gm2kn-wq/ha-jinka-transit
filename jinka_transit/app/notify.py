"""Envoi des notifications : WhatsApp (CallMeBot) et/ou un service notify de Home Assistant."""

import logging
import os
import time

from http_util import HttpError, request

log = logging.getLogger("notify")


class Notifier:
    def __init__(self, whatsapp_phone="", callmebot_apikey="", ha_service=""):
        self.phone = (whatsapp_phone or "").replace(" ", "")
        self.apikey = (callmebot_apikey or "").strip()
        self.ha_service = (ha_service or "").strip()
        self._last_whatsapp = 0.0

    @property
    def configured(self):
        return bool((self.phone and self.apikey) or self.ha_service)

    def send(self, title, message, url=None, image=None):
        """Envoie sur tous les canaux configurés. Renvoie True si au moins un a réussi."""
        ok = False
        if self.phone and self.apikey:
            ok |= self._whatsapp(f"*{title}*\n{message}")
        if self.ha_service:
            ok |= self._home_assistant(title, message, url, image)
        if not self.configured:
            log.warning("Aucun canal de notification configuré : %s", title)
        return ok

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
