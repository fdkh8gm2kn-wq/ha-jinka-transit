"""Connexion à Jinka par code reçu par email, comme sur www.jinka.fr/sign/in/email.

1. `send_code(email)` : Jinka envoie un code à 4 chiffres par email (action serveur Next.js).
2. `verify_code(email, code)` : on valide le code (NextAuth, fournisseur "email-code-signin")
   et on récupère le jeton d'API utilisé ensuite pour lire les alertes.
"""

import http.cookiejar
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

from http_util import UA
from jinka import JinkaAuthError

WEB = "https://www.jinka.fr"

log = logging.getLogger("jinka")


class JinkaCodeLogin:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def _open(self, url, data=None, headers=None, method=None):
        hdrs = {"User-Agent": UA, "Accept-Language": "fr-FR,fr;q=0.9"}
        hdrs.update(headers or {})
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with self.opener.open(req, timeout=30) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except OSError as e:
            raise JinkaAuthError(f"Jinka injoignable : {e}") from None

    def _action_id(self):
        """L'identifiant de l'action « submit_email » change à chaque mise en ligne du site :
        on le relit dans le JavaScript de la page de connexion."""
        _, html = self._open(f"{WEB}/sign/in/email")
        for chunk in dict.fromkeys(re.findall(r'/_next/static/chunks/[^"\\]+\.js', html)):
            _, js = self._open(WEB + chunk)
            m = re.search(r'createServerReference\)\("([0-9a-f]{40,})"[^)]*?"submit_email"', js)
            if m:
                return m.group(1)
        raise JinkaAuthError("Formulaire de connexion Jinka introuvable (le site a peut-être changé).")

    def send_code(self, email):
        email = email.strip().lower()
        status, body = self._open(
            f"{WEB}/sign/in/email", data=json.dumps([email]).encode(), method="POST",
            headers={"Next-Action": self._action_id(), "Accept": "text/x-component",
                     "Content-Type": "text/plain;charset=UTF-8", "Origin": WEB,
                     "Referer": f"{WEB}/sign/in/email"})
        result = None
        for line in body.splitlines():
            try:
                obj = json.loads(line.partition(":")[2])
            except ValueError:
                continue
            if isinstance(obj, dict) and "success" in obj:
                result = obj
        if result is None:
            raise JinkaAuthError(f"Réponse inattendue de Jinka à l'envoi du code (HTTP {status}).")
        if not result["success"]:
            raise JinkaAuthError(f"Jinka refuse l'envoi du code : {result.get('message') or 'erreur'}")
        log.info("Code de connexion Jinka envoyé par email.")

    def verify_code(self, email, code):
        _, body = self._open(f"{WEB}/api/auth/csrf")
        try:
            csrf = json.loads(body)["csrfToken"]
        except (ValueError, KeyError):
            raise JinkaAuthError("Impossible d'initialiser la connexion Jinka.") from None
        form = urllib.parse.urlencode({
            "email": email.strip().lower(), "code": code.strip(), "csrfToken": csrf,
            "callbackUrl": f"{WEB}/alerts", "json": "true", "redirect": "false",
        }).encode()
        status, body = self._open(
            f"{WEB}/api/auth/callback/email-code-signin", data=form, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Origin": WEB,
                     "X-Auth-Return-Redirect": "1"})
        if status >= 400 or "error=" in body:
            raise JinkaAuthError("Code refusé par Jinka (faux ou expiré) : redemande un code.")

        token = self._cookie("LA_API_TOKEN")
        if not token:
            _, session = self._open(f"{WEB}/api/auth/session")
            try:
                token = find_token(json.loads(session))
            except ValueError:
                token = None
        if not token:
            names = sorted(c.name for c in self.jar)
            raise JinkaAuthError(f"Connexion acceptée mais jeton introuvable (cookies : {', '.join(names)}).")
        log.info("Connexion Jinka réussie.")
        return token

    def _cookie(self, name):
        for c in self.jar:
            if c.name == name and c.value:
                return urllib.parse.unquote(c.value)
        return None


def find_token(obj, key=""):
    """Cherche un jeton d'accès dans la session NextAuth (clé contenant « token »)."""
    if isinstance(obj, dict):
        for preferred in ("access_token", "accessToken", "api_token", "apiToken", "token"):
            v = obj.get(preferred)
            if isinstance(v, str) and len(v) > 20:
                return v
        for k, v in obj.items():
            found = find_token(v, k)
            if found:
                return found
    elif isinstance(obj, str) and "token" in key.lower() and len(obj) > 20:
        return obj
    return None
