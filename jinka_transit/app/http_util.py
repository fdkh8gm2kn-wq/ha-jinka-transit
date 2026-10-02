"""Petit client HTTP basé sur la stdlib (aucune dépendance à installer)."""

import json
import urllib.error
import urllib.parse
import urllib.request

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)


class HttpError(Exception):
    def __init__(self, status, url, body):
        super().__init__(f"HTTP {status} sur {url.split('?')[0]} : {body[:300]}")
        self.status = status
        self.body = body


def request(url, params=None, headers=None, data=None, json_body=None, method=None, timeout=30):
    """Fait une requête et renvoie (status, texte). Lève HttpError si status >= 400."""
    if params:
        query = urllib.parse.urlencode(params, doseq=True)
        url = f"{url}{'&' if '?' in url else '?'}{query}"
    hdrs = {"User-Agent": UA, "Accept": "application/json"}
    hdrs.update(headers or {})
    body = None
    if json_body is not None:
        body = json.dumps(json_body).encode()
        hdrs["Content-Type"] = "application/json"
    elif data is not None:
        body = urllib.parse.urlencode(data).encode()
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise HttpError(e.code, url, e.read().decode("utf-8", "replace")) from None


def get_json(url, **kwargs):
    _, text = request(url, **kwargs)
    return json.loads(text) if text else None
