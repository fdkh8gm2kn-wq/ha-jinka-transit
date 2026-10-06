"""Lecture des annonces de tes alertes Jinka (API web non officielle de jinka.fr)."""

import logging

from http_util import HttpError, get_json

API = "https://api.jinka.fr/apiv2"
HEADERS = {"Origin": "https://www.jinka.fr", "Accept-Language": "fr-FR,fr;q=0.9"}

log = logging.getLogger("jinka")


class JinkaAuthError(Exception):
    pass


class Jinka:
    def __init__(self, email="", password="", token=""):
        self.email = (email or "").strip()
        self.password = password or ""
        self.static_token = (token or "").strip().removeprefix("Bearer ").strip()
        self.token = self.static_token or None

    def _auth(self):
        if self.static_token:
            self.token = self.static_token
            return
        if not (self.email and self.password):
            raise JinkaAuthError("Pas connecté à Jinka : ouvre la page de l'add-on et connecte-toi avec le code reçu par email.")
        try:
            data = get_json(
                f"{API}/user/auth",
                data={"email": self.email, "password": self.password},
                headers=HEADERS,
            )
        except HttpError as e:
            raise JinkaAuthError(f"Connexion Jinka refusée ({e.status}). Vérifie email/mot de passe "
                                 "ou utilise jinka_token.") from None
        self.token = (data or {}).get("access_token")
        if not self.token:
            raise JinkaAuthError("Jinka n'a pas renvoyé de jeton d'accès.")

    def _get(self, path, params=None, retry=True):
        if not self.token:
            self._auth()
        try:
            return get_json(f"{API}{path}", params=params,
                            headers={**HEADERS, "Authorization": f"Bearer {self.token}"})
        except HttpError as e:
            if e.status in (401, 403):
                if retry and not self.static_token:
                    self.token = None
                    return self._get(path, params, retry=False)
                raise JinkaAuthError("Jeton Jinka refusé ou expiré : récupère-en un nouveau "
                                     "(voir la doc de l'add-on).") from None
            raise

    def alerts(self):
        data = self._get("/alert")
        return data if isinstance(data, list) else []

    def listings(self, alert_filter=None, max_pages=3, known=()):
        """Renvoie les annonces (normalisées) des alertes choisies, les plus récentes d'abord.

        Jinka trie du plus récent au plus ancien : dès qu'une page ne contient que des annonces
        déjà traitées (`known`), on arrête de paginer cette alerte."""
        wanted = {str(a).strip().lower() for a in (alert_filter or []) if str(a).strip()}
        seen = set()
        out = []
        alerts = self.alerts()
        if not alerts:
            log.warning("Aucune alerte trouvée sur ton compte Jinka : crée une alerte sur le secteur voulu.")
        for alert in alerts:
            alert_id = str(alert.get("id"))
            name = str(alert.get("name") or alert_id)
            if wanted and alert_id.lower() not in wanted and name.lower() not in wanted:
                continue
            page = 1
            while page <= max_pages:
                data = self._get(f"/alert/{alert_id}/dashboard", {"filter": "all", "page": page}) or {}
                ads = data.get("ads") or []
                fresh = 0
                for ad in ads:
                    item = normalize(ad, alert_id, name)
                    if item["id"] and item["id"] not in seen:
                        seen.add(item["id"])
                        out.append(item)
                        fresh += item["id"] not in known
                if ads and not fresh:
                    log.info("Alerte « %s » : rien de nouveau après la page %d.", name, page)
                    break
                pagination = data.get("pagination") or {}
                nb_pages = pagination.get("nbPages") or pagination.get("nb_pages") or 1
                if page >= int(nb_pages):
                    break
                page += 1
        return out


def _num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def normalize(ad, alert_id, alert_name):
    ad_id = str(ad.get("id") or "")
    images = ad.get("images") or []
    if isinstance(images, str):
        images = [images]
    return {
        "id": ad_id,
        "alert_id": alert_id,
        "alert_name": alert_name,
        "rent": _num(ad.get("rent")),
        "area": _num(ad.get("area")),
        "rooms": _num(ad.get("room")),
        "bedrooms": _num(ad.get("bedroom")),
        "type": ad.get("type"),
        "city": ad.get("city"),
        "postal_code": ad.get("postal_code"),
        "lat": _num(ad.get("lat")),
        "lng": _num(ad.get("lng")),
        "source": ad.get("source_label") or ad.get("source"),
        "created_at": ad.get("created_at"),
        "expired": bool(ad.get("expired_at")),
        "deleted": bool(ad.get("deleted_at") or ad.get("deleted")),
        "image": images[0] if images and isinstance(images[0], str) else None,
        "link": ad_link(ad, alert_id),
        "uuid": ad.get("uuid"),
        "stops": ad.get("stops") or [],
        "description": (ad.get("description") or "")[:2000],
        "quartier": ad.get("quartier_name"),
        "furnished": ad.get("furnished"),
        "dpe": dpe_letter(ad.get("energy_dpe") or ad.get("dpe")),
        "floor": _num(ad.get("floor")),
    }


def dpe_letter(v):
    """Classe énergie (A à G) ou None."""
    v = (v or "").strip().upper() if isinstance(v, str) else ""
    return v if len(v) == 1 and v in "ABCDEFG" else None


def ad_link(ad, alert_id):
    """Lien vers la fiche de l'annonce sur jinka.fr (même format que le site : /ad/<uuid>)."""
    uuid = ad.get("uuid")
    if uuid:
        return f"https://www.jinka.fr/ad/{uuid}?alert_id={alert_id}"
    if ad.get("webview_link"):
        return ad["webview_link"]
    return f"https://www.jinka.fr/alerts/{alert_id}"
