"""Test de bout en bout sans réseau : faux Jinka, faux IDFM, faux WhatsApp.

    python3 tests/test_offline.py
"""

import json
import os
import sys
import tempfile
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "jinka_transit", "app"))

tmp = tempfile.mkdtemp()
os.environ["OPTIONS_PATH"] = os.path.join(tmp, "options.json")
os.environ["STATE_PATH"] = os.path.join(tmp, "state.json")

OPTIONS = {
    "jinka_email": "a@b.c", "jinka_password": "x", "jinka_token": "", "jinka_alerts": [],
    "prim_api_key": "k",
    "destinations": [
        {"name": "Bureau", "address": "48.85,2.30", "max_minutes": 45, "arrival_time": "09:00"},
        {"name": "École", "address": "48.86,2.35", "max_minutes": 30, "arrival_time": "08:30"},
        {"name": "Gare du Nord", "address": "48.87,2.35", "max_minutes": 45, "arrival_time": "09:00",
         "info_only": True},
    ],
    "allowed_modes": ["metro", "rer", "tram"], "max_walk_minutes": 15,
    "whatsapp_phone": "+33600000000", "whatsapp_callmebot_apikey": "123", "ha_notify_service": "",
    "scan_interval_minutes": 15, "max_pages_per_alert": 3, "max_notifications_per_run": 10,
    "notify_existing_on_first_run": True,
}
json.dump(OPTIONS, open(os.environ["OPTIONS_PATH"], "w"))


def ad(i, lat, lng):
    return {"id": i, "rent": 1500, "area": 60, "room": 3, "city": "Vincennes", "postal_code": "94300",
            "lat": lat, "lng": lng, "source_label": "SeLoger", "created_at": f"2026-10-0{i[-1]}"}


ADS = [ad("ad1", 1.0, 1.0),   # métro rapide partout -> OK
       ad("ad2", 2.0, 2.0),   # seul trajet = bus -> refusé
       ad("ad3", 3.0, 3.0),   # RER mais 40 min vers l'école (> 30) -> refusé
       {**ad("ad4", 4.0, 4.0), "lat": None}]  # pas de coordonnées


def stop(name):
    return {"embedded_type": "stop_point", "name": f"{name} (Paris)",
            "stop_point": {"name": name, "stop_area": {"name": name}}}


def pt(mode, code, phys, dur, frm="A", to="B", n=3, direction=None):
    return {"type": "public_transport", "duration": dur, "from": stop(frm), "to": stop(to),
            "stop_date_times": [{}] * (n + 1),
            "display_informations": {"commercial_mode": mode, "code": code, "direction": direction},
            "links": [{"type": "physical_mode", "id": f"physical_mode:{phys}"}]}


def walk(d, frm=None, to=None, length=None):
    s = {"type": "street_network", "mode": "walking", "duration": d,
         "from": frm or {"name": "logement"}, "to": to or {"name": "dest"}}
    if length:
        s["path"] = [{"length": length // 2}, {"length": length - length // 2}]
    return s


def journeys_for(lat, to_lat):
    if abs(to_lat - 48.87) < 1e-6:  # adresse pour info : 90 min, ne doit pas filtrer
        return [{"duration": 90 * 60, "nb_transfers": 0,
                 "sections": [walk(60), pt("RER", "B", "RapidTransit", 5000, "Gare A", "Gare du Nord"), walk(60)]}]
    if lat == 1.0:
        return [{"duration": 25 * 60, "nb_transfers": 1,
                 "sections": [walk(300, to=stop("Vincennes"), length=420),
                              pt("RER", "A", "RapidTransit", 600, "Vincennes", "Nation", 2, "Saint-Germain-en-Laye"),
                              {"type": "transfer", "duration": 180, "from": stop("Nation"), "to": stop("Nation"),
                               "path": [{"length": 150}]},
                              {"type": "waiting", "duration": 120},
                              pt("Métro", "1", "Metro", 400, "Nation", "Concorde", 9, "La Défense"),
                              walk(80, frm=stop("Concorde"))]}]
    if lat == 2.0:
        return [{"duration": 20 * 60, "sections": [walk(100), pt("Bus", "86", "Bus", 900), walk(200)]}]
    if lat == 3.0:
        mins = 40 if abs(to_lat - 48.86) < 1e-6 else 30
        return [{"duration": mins * 60, "sections": [walk(200), pt("RER", "E", "RapidTransit", 1500), walk(100)]}]
    return []


calls = {"prim": [], "whatsapp": []}


class FakeResp:
    def __init__(self, body, status=200):
        self.body, self.status = json.dumps(body).encode() if not isinstance(body, str) else body.encode(), status

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def fake_urlopen(req, timeout=None):
    url = req.full_url
    if "user/auth" in url:
        return FakeResp({"access_token": "tok"})
    if url.endswith("/apiv2/alert"):
        assert req.headers["Authorization"] == "Bearer tok"
        return FakeResp([{"id": 42, "name": "Est parisien"}])
    if "/dashboard" in url:
        return FakeResp({"ads": ADS, "pagination": {"nbPages": 1}})
    if "/journeys" in url:
        from urllib.parse import parse_qs, urlparse
        q = parse_qs(urlparse(url).query)
        calls["prim"].append(q)
        lat = float(q["from"][0].split(";")[1])
        to_lat = float(q["to"][0].split(";")[1])
        return FakeResp({"journeys": journeys_for(lat, to_lat)})
    if "callmebot" in url:
        calls["whatsapp"].append(url)
        return FakeResp("Message queued. You will receive it in a few seconds.")
    raise AssertionError(f"URL inattendue : {url}")


urllib.request.urlopen = fake_urlopen
import notify  # noqa: E402
notify.time.sleep = lambda s: None

import main  # noqa: E402

app = main.App()
stats = app.scan()
print("stats :", stats)
L = app.state["listings"]
for k in ("ad1", "ad2", "ad3", "ad4"):
    print(k, L[k]["status"], "|", L[k]["reason"])

assert L["ad1"]["status"] == "notified"
assert L["ad2"]["status"] == "rejected" and "aucun trajet" in L["ad2"]["reason"]
assert L["ad3"]["status"] == "rejected" and "40 min > 30 min" in L["ad3"]["reason"]
assert L["ad4"]["status"] == "rejected" and "coordonnées" in L["ad4"]["reason"]
assert len(calls["whatsapp"]) == 1
# le bus est toujours interdit côté requête, et le Transilien aussi ici (non coché)
forb = calls["prim"][0]["forbidden_uris[]"]
assert "physical_mode:Bus" in forb and "physical_mode:LocalTrain" in forb and "physical_mode:Metro" not in forb
assert calls["prim"][0]["datetime_represents"] == ["arrival"]

msg = calls["whatsapp"][0]
from urllib.parse import parse_qs, urlparse  # noqa: E402
text = parse_qs(urlparse(msg).query)["text"][0]
print("\n--- message WhatsApp ---\n" + text + "\n------------------------")
for frag in ("jusqu'à *Vincennes*", "420 m", "*RER A* (dir. Saint-Germain-en-Laye) : Vincennes → Nation · 10 min, 2 arrêts",
             "Correspondance à Nation : 3 min à pied, 150 m + 2 min d'attente",
             "*Métro 1* (dir. La Défense) : Nation → Concorde", "de *Concorde* jusqu'à Bureau", "1 correspondance"):
    assert frag in text, frag
assert "ℹ️ *Gare du Nord* (pour info) — 90 min" in text
# l'adresse pour info n'est jamais calculée pour les annonces refusées
assert not any(abs(float(q["to"][0].split(";")[1]) - 48.87) < 1e-6 and float(q["from"][0].split(";")[1]) == 2.0
               for q in calls["prim"])

# 2e scan : rien de nouveau, aucun appel IDFM ni message
n_prim = len(calls["prim"])
app.scan()
assert len(calls["prim"]) == n_prim and len(calls["whatsapp"]) == 1

# On passe l'école à 45 min : ad3 est réévaluée et envoyée
OPTIONS["destinations"][1]["max_minutes"] = 45
json.dump(OPTIONS, open(os.environ["OPTIONS_PATH"], "w"))
app.opts = app.load_options()
n_prim = len(calls["prim"])
st = app.scan()
assert L["ad3"]["status"] == "notified", L["ad3"]
# filtre entièrement en cache ; seul le trajet « pour info » (jamais calculé avant) est demandé
assert len(calls["prim"]) == n_prim + 1 and st["trajets en cache"] >= 2, st
print("changement de durée sans aucun appel IDFM ✔")
assert L["ad1"]["status"] == "notified" and len(calls["whatsapp"]) == 2
print("ad3 envoyée après passage de l'école à 45 min ✔")

# Loyer max : à 1 400 €, les annonces à 1 500 € sont écartées sans appel IDFM
OPTIONS["max_rent"] = 1400
json.dump(OPTIONS, open(os.environ["OPTIONS_PATH"], "w"))
app.opts = app.load_options()
n_prim = len(calls["prim"])
L["ad2"]["crit"] = "old"  # force une réévaluation de ad2 (refusée)
app.scan()
assert L["ad2"]["reason"] == "loyer 1500 € > 1400 €", L["ad2"]
assert len(calls["prim"]) == n_prim
print("loyer max ✔")

# Plus de 5 adresses -> tronqué à 5
OPTIONS["destinations"] = [{"name": f"A{i}", "address": "48.8,2.3", "max_minutes": 45, "arrival_time": "09:00"}
                           for i in range(7)]
json.dump(OPTIONS, open(os.environ["OPTIONS_PATH"], "w"))
assert len(app.load_options()["destinations"]) == 5

# L'interface web se génère
import web  # noqa: E402
html = web.render(app, "all")
assert "Vincennes" in html and "refusée" in html
print("\nTous les tests passent ✔")
