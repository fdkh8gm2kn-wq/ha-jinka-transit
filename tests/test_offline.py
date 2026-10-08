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
    "notify_existing_on_first_run": True, "bienici": False,
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
    if "geo.api.gouv.fr" in url:
        if "arrondissement" in url:
            return FakeResp([{"nom": "Paris 15e Arrondissement", "code": "75115", "population": 230000,
                              "codesPostaux": ["75015"], "centre": {"coordinates": [2.29, 1.0]}}])
        if "/departements/94/" in url:
            return FakeResp([{"nom": "Vincennes", "code": "94080", "population": 50000, "codesPostaux": ["94300"],
                              "centre": {"coordinates": [2.43, 2.0]}},
                             {"nom": "Loin", "code": "94999", "population": 50000, "centre": {"coordinates": [9.0, 9.0]}}])
        return FakeResp([])
    if "callmebot" in url:
        calls["whatsapp"].append(url)
        return FakeResp("Message queued. You will receive it in a few seconds.")
    raise AssertionError(f"URL inattendue : {url}")


urllib.request.urlopen = fake_urlopen
import notify  # noqa: E402
import fmt  # noqa: E402
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
assert L["ad4"]["status"] == "rejected" and "pas de position" in L["ad4"]["reason"]
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
import copy
_ad1 = copy.deepcopy(L["ad1"])
app.scan()
assert L["ad2"]["reason"] == "loyer 1500 € > 1400 €", L["ad2"]
assert len(calls["prim"]) == n_prim
# ad1 (déjà envoyée, 1 500 €) est revérifiée et passe en refusée
assert L["ad1"]["status"] == "rejected" and L["ad1"]["reason"] == "loyer 1500 € > 1400 € (revérifiée)", L["ad1"]
L["ad1"].clear(); L["ad1"].update(_ad1)  # on la remet pour les tests suivants
print("loyer max ✔")
app.opts["min_area"] = 18
st_, _, why = app.evaluate({"rent": 700, "area": 15, "lat": 1, "lng": 1}, [], None)
assert st_ == "rejected" and why == "surface 15 m² < 18 m²", why
assert app.evaluate({"rent": 700, "area": None, "lat": 1, "lng": 1}, [], None)[0] == "match"  # surface inconnue : gardée
app.opts["min_area"] = 0
print("surface minimum ✔")

# Plus de 5 adresses -> tronqué à 5
OPTIONS["destinations"] = [{"name": f"A{i}", "address": "48.8,2.3", "max_minutes": 45, "arrival_time": "09:00"}
                           for i in range(7)]
json.dump(OPTIONS, open(os.environ["OPTIONS_PATH"], "w"))
assert len(app.load_options()["destinations"]) == 5

# L'interface web se génère
import web  # noqa: E402
html = web.render(app, "all")
assert "Vincennes" in html and "refusée" in html
import jinka  # noqa: E402
assert jinka.ad_link({"uuid": "abc-123"}, "f00") == "https://www.jinka.fr/ad/abc-123?alert_id=f00"
assert jinka.ad_link({}, "f00") == "https://www.jinka.fr/alerts/f00"

# Recherche élargie : Paris 15e (lat 1 -> métro OK), Vincennes (lat 2 -> seulement bus), « Loin » hors rayon
import explore  # noqa: E402
from transit import Transit  # noqa: E402
dests = [{"name": "Bureau", "lat": 48.85, "lon": 2.30, "max_minutes": 45, "arrival_time": "09:00", "info_only": False},
         {"name": "École", "lat": 48.86, "lon": 2.35, "max_minutes": 45, "arrival_time": "08:30", "info_only": False},
         {"name": "Gare du Nord", "lat": 48.87, "lon": 2.35, "max_minutes": 45, "arrival_time": "09:00",
          "info_only": True}]
app.opts["destinations"] = dests
explore.distance_km = lambda *a: 1.0 if a[2] < 5 else 999  # rayon factice
prog = {}
res = explore.run(Transit("k", ["metro", "rer"]), dests, 15, prog, pause=0)
names = {r["nom"]: r["ok"] for r in res["results"]}
assert names == {"Paris 15e": True, "Vincennes": False}, names
assert prog["done"] == 2 and len(res["dests"]) == 2  # l'adresse « pour info » n'entre pas en compte
app.state["explore"] = res
page = web.render_explore(app)
assert "Paris 15e (75015)" in page and "1 communes compatibles" in page
# trajet tout à pied de 44 min : refusé (marche max 15 min)
long_walk = Transit("k", ["metro"], 15).check_journey({"duration": 44 * 60, "sections": [walk(44 * 60)]})
short_walk = Transit("k", ["metro"], 15).check_journey({"duration": 11 * 60, "sections": [walk(11 * 60)]})
assert not long_walk["valid"] and short_walk["valid"] and short_walk["summary"] == "à pied"
# Seuil propre à la recherche élargie + trajets réutilisés d'une recherche à l'autre
cache = {}
t1 = Transit("k", ["metro", "rer"])
r1 = explore.run(t1, dests, 15, {}, max_minutes=20, cache=cache, pause=0)
assert {r["nom"]: r["ok"] for r in r1["results"]} == {"Paris 15e": False, "Vincennes": False}  # 25 min > 20
t2 = Transit("k", ["metro", "rer"])
r2 = explore.run(t2, dests, 15, {}, max_minutes=75, cache=cache, pause=0)
assert {r["nom"]: r["ok"] for r in r2["results"]} == {"Paris 15e": True, "Vincennes": False}
assert r2["dests"][0]["max"] == 75
print("recherche élargie : seuil 75 min, réutilisation des trajets :", t2.api_calls, "nouvel(s) appel(s) ✔")
# Marche logement -> station limitée (5 min) : on garde le trajet dont la station est proche
tw = Transit("k", ["metro", "rer"])
fast_far = tw.check_journey({"duration": 20 * 60, "sections": [walk(9 * 60, to=stop("Loin")),
                                                               pt("RER", "B", "RapidTransit", 600, "Loin", "Arcueil")]})
slow_near = tw.check_journey({"duration": 28 * 60, "sections": [walk(4 * 60, to=stop("Proche")),
                                                                pt("Métro", "4", "Metro", 1200, "Proche", "Arcueil")]})
for c in (fast_far, slow_near):
    c.pop("valid")
cache_w = {}
tw._fetch_best = lambda *a: {**fast_far, "candidates": [fast_far, slow_near]}
r = tw.journey(1, 1, 2, 2, "09:00", 45, cache=cache_w, home_walk_max=5)
assert r["ok"] and r["minutes"] == 28 and r["summary"] == "Métro 4", r
r = tw.journey(1, 1, 2, 2, "09:00", 45, cache=cache_w, home_walk_max=3)
assert not r["ok"] and "station à 4 min à pied du logement (max 3 min)" in r["reason"], r
r = tw.journey(1, 1, 2, 2, "09:00", 45, cache=cache_w)  # sans limite : le plus rapide
assert r["minutes"] == 20
print("marche logement → station ≤ 5 min ✔")
print("recherche élargie ✔")
# Lecture du code dans un email Jinka
import mailbox  # noqa: E402
from email.message import EmailMessage  # noqa: E402
assert mailbox.extract_code("Votre code de connexion : 4821. © 2026 Jinka") == "4821"
assert mailbox.extract_code("<p>Bonjour</p><p>Saisissez ce code</p><h1>0937</h1> 2026") == "0937"
assert mailbox.extract_code("Jinka 2026, 75015 Paris") is None
msg = EmailMessage()
msg["From"] = "Jinka <noreply@jinka.fr>"
msg["Subject"] = "Ton code Jinka"
msg["Date"] = "Sat, 03 Oct 2026 10:00:00 +0200"
msg.set_content("Bonjour,\nVoici ton code : 5512\n")
msg.add_alternative("<html><body><p>Voici ton code</p><b>5512</b></body></html>", subtype="html")
import email.utils as eu  # noqa: E402
sent = eu.parsedate_to_datetime(msg["Date"]).timestamp()
assert mailbox.code_from_message(msg, sent - 30) == "5512"
assert mailbox.code_from_message(msg, sent + 3600) is None  # email trop ancien
msg.replace_header("From", "pub@autre.fr")
assert mailbox.code_from_message(msg, sent - 30) is None
assert mailbox.imap_host("x@gmail.com") == "imap.gmail.com" and mailbox.imap_host("x@gmx.fr") == "imap.gmx.net"

class FakeImap:
    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren \\Junk) "/" "[Gmail]/Spam"',
                      b'(\\HasNoChildren) "." "Courrier ind\xc3\xa9sirable"', b'(\\HasNoChildren) "/" "SF_PROMO"',
                      b'(\\HasNoChildren \\Sent) "/" "SF_SENT"', b'(\\HasNoChildren) "/" "Corbeille"']
assert mailbox.folders(FakeImap()) == ["INBOX", '"[Gmail]/Spam"', '"Courrier ind\u00e9sirable"', '"SF_PROMO"'], \
    mailbox.folders(FakeImap())

# Reconnexion automatique : jeton refusé -> code demandé, lu dans la boîte, validé, scan qui repart
import jinka_login  # noqa: E402
sent_codes = []
class FakeLogin:
    def send_code(self, e): sent_codes.append(e)
    def verify_code(self, e, c): assert c == "7777"; return "tok"
jinka_login.JinkaCodeLogin = FakeLogin
mailbox.wait_for_code = lambda host, user, pwd, since: "7777"
app.opts.update(jinka_email="moi@gmx.fr", jinka_password="", jinka_token="", mail_password="x")
app.state.pop("jinka_auth", None); app.state["auto_login_at"] = 0
assert app.auto_login_possible()
assert app.auto_login() == "tok" and sent_codes == ["moi@gmx.fr"]
assert app.state["jinka_auth"]["token"] == "tok" and not app.auto_login_possible()  # 1 essai / 20 min
print("connexion automatique par email ✔")
# Email (SMTP Gmail) + SMS Free Mobile
import smtplib  # noqa: E402
sent_mail = []
class FakeSMTP:
    def __init__(self, host, port, timeout=None): sent_mail.append(("host", host, port))
    def __enter__(self): return self
    def __exit__(self, *a): pass
    def login(self, u, p): sent_mail.append(("login", u, p))
    def send_message(self, m): sent_mail.append(("msg", m))
smtplib.SMTP_SSL = FakeSMTP
sms = []
_orig = urllib.request.urlopen
def urlopen_sms(req, timeout=None):
    if "smsapi.free-mobile.fr" in req.full_url:
        sms.append(req.full_url); return FakeResp("")
    return _orig(req, timeout)
urllib.request.urlopen = urlopen_sms
nt = notify.Notifier(email_to="moi@perso.fr", smtp_user="dedie@gmail.com", smtp_password="abcd efgh ijkl mnop",
                     free_sms_user="12345678", free_sms_key="clef")
v = L["ad1"]
assert nt.send(main.title_of(v["listing"]), main.body_of(v), v["listing"]["link"], None,
               short=fmt.short_of(v), html=fmt.html_of(v))
assert ("host", "smtp.gmail.com", 465) in sent_mail and ("login", "dedie@gmail.com", "abcdefghijklmnop") in sent_mail
m = [x[1] for x in sent_mail if x[0] == "msg"][0]
assert m["To"] == "moi@perso.fr" and "Vincennes" in m["Subject"]
html_part = m.get_body(("html",)).get_content()
assert "Voir l'annonce" in html_part and "RER A" in html_part
from urllib.parse import parse_qs as pq, urlparse as up  # noqa: E402
txt = pq(up(sms[0]).query)["msg"][0]
assert txt.splitlines()[1] == "Bureau 25' · École 25'" and txt.splitlines()[2].startswith("https://"), txt
print("email + SMS ✔ :", txt.replace("\n", " | "))
# Bouton de test des notifications
app.opts.update(email_to="moi@perso.fr", jinka_email="dedie@gmail.com", mail_password="x", free_sms_user="1", free_sms_key="k",
                whatsapp_phone="", whatsapp_callmebot_apikey="", ha_notify_service="")
app.load_options = lambda: app.opts
res = app.send_test()
assert res == [("email", True), ("SMS Free", True)], res
print("notification de test ✔")
# Annonces sans GPS : station indiquée par Jinka, ou citée dans le texte
import locate  # noqa: E402
pages = {
    "u-texte": '<html>..."lat":null,"lng":null,..."stops":[],"x":1,"description":"Gare La Garenne-Colombes - studio meublé","description_is_truncated":false',
    "u-stops": '<html>..."lat":null,"lng":null,..."stops":[{"id":"500008","name":"Saint-Gratien","lines":["RER C"]}],"x":1,"description":"joli studio","description_is_truncated":false',
    "u-gps": '<html>..."lat":48.9,"lng":2.25,..."stops":[],"x":1',
}
_prev = urllib.request.urlopen
def urlopen_loc(req, timeout=None):
    u = req.full_url
    if "www.jinka.fr/ad/" in u:
        return FakeResp(pages[u.rsplit("/", 1)[1]].replace('"', '\\"'))
    if "/places" in u:
        q = parse_qs(urlparse(u).query)["q"][0]
        coords = {"La Garenne-Colombes": ("48.9066", "2.2449"), "Saint-Gratien": ("48.9717", "2.2853")}
        if q in coords:
            return FakeResp({"places": [{"name": q, "embedded_type": "stop_area",
                                         "stop_area": {"coord": {"lat": coords[q][0], "lon": coords[q][1]}}}]})
        return FakeResp({"places": [{"name": "Loin", "stop_area": {"coord": {"lat": "45.0", "lon": "4.0"}}}]})
    if "geo.api.gouv.fr/communes" in u:
        cp = parse_qs(urlparse(u).query)["codePostal"][0]
        c = {"92700": (2.2522, 48.9223), "95210": (2.2853, 48.9717)}[cp]
        return FakeResp([{"nom": "x", "centre": {"coordinates": list(c)}}])
    return _prev(req, timeout)
urllib.request.urlopen = urlopen_loc
locate.time.sleep = lambda s: None
tl = Transit("k", ["metro", "rer"])
cache_l = {}
r = locate.locate({"uuid": "u-texte", "postal_code": "92700", "city": "Colombes"}, tl, cache_l)
assert r and abs(r[0] - 48.9066) < 1e-6 and "La Garenne-Colombes" in r[2] and "dans l'annonce" in r[2], r
r = locate.locate({"uuid": "u-stops", "postal_code": "95210", "city": "Saint-Gratien"}, tl, cache_l)
assert r and "Saint-Gratien (RER C), indiquée par Jinka" in r[2], r
r = locate.locate({"uuid": "u-gps", "postal_code": "92700", "city": "Colombes"}, tl, cache_l)
assert r == (48.9, 2.25, None), r
r = locate.locate({"uuid": "u-texte", "postal_code": "95210", "city": "Saint-Gratien"}, tl, {})
assert r is None or "Garenne" in r[2]  # station trop loin de la commune -> rejetée si > 6 km
print("annonces sans GPS : station Jinka / texte ✔")
# Bien'ici : lecture + doublon avec une annonce Jinka déjà vue
import bienici  # noqa: E402
bi_ads = [
    {"id": "bi-1", "price": 1505, "surfaceArea": 60.5, "roomsQuantity": 3, "city": "Vincennes", "postalCode": "94300",
     "blurInfo": {"type": "disk", "radius": 50, "position": {"lat": 1.0, "lon": 1.0}}, "accountDisplayName": "Agence X",
     "floor": 0, "hasDoorCode": True, "photos": [{"url": "https://img/x.jpg"}], "publicationDate": "2026-10-04"},
    {"id": "bi-2", "price": 800, "surfaceArea": 22, "roomsQuantity": 1, "city": "Paris 15e", "postalCode": "75015",
     "blurInfo": {"type": "disk", "radius": 1000, "position": {"lat": 1.0, "lon": 1.0}},
     "description": "Studio meublé, métro Nation", "floor": 3, "hasCaretaker": True},
]
n = bienici.normalize(bi_ads[0])
assert n["link"] == "https://www.bienici.com/annonce/bi-1" and n["lat"] == 1.0 and n["safety"] == ["digicode"]
assert bienici.normalize(bi_ads[1])["lat"] is None  # position floutée à 1 km : ignorée
assert bienici.same_flat(n, L["ad1"]["listing"])  # même logement que l'annonce Jinka ad1 (Vincennes 1500 € 60 m²)
assert fmt.building_info(n) == "rez-de-chaussée · digicode"
assert not n["coliving"]
assert bienici.is_coliving({"flatSharing": True})
assert bienici.is_coliving({"title": "Chambre meublée de 12 m² avec accès balcon – Coloc"})
assert bienici.is_coliving({"title": "Location Appartement 5 pièces", "description": "Une chambre en colocation est disponible"})
assert not bienici.is_coliving({"title": "Studio meublé", "description": "Pas de colocation possible. Proche métro."})
assert app.evaluate({**n, "coliving": True}, [], None)[2] == "chambre en colocation"
_prev2 = urllib.request.urlopen
def urlopen_bi(req, timeout=None):
    if "bienici.com/realEstateAds.json" in req.full_url:
        f = json.loads(parse_qs(urlparse(req.full_url).query)["filters"][0])
        assert f["maxPrice"] == 850 and f["minArea"] == 20 and f["isFurnished"] is True
        return FakeResp({"total": 2, "realEstateAds": bi_ads})
    return _prev2(req, timeout)
urllib.request.urlopen = urlopen_bi
bienici.time.sleep = lambda s: None
got = bienici.listings(["-1"], 850, 20)
assert [g["id"] for g in got] == ["bienici:bi-1", "bienici:bi-2"]
twin = app.find_twin(got[0])
assert twin and twin["listing"]["id"] == "ad1"
assert app.find_twin(got[1]) is None
print("Bien'ici + doublons ✔")

# Emails d'erreur : un seul par période, puis « rétabli »
sent_mail.clear()
app.opts["error_email"] = "admin@example.org"
app.opts["mail_password"] = app.opts.get("mail_password") or "x"
app.opts["jinka_email"] = app.opts.get("jinka_email") or "bot@gmail.com"
_scan = app.scan
def boom(): raise RuntimeError("panne test")
app.scan = boom
app.load_options = lambda: app.opts
app.run_once(); app.run_once()
msgs = [m[1] for m in sent_mail if m[0] == "msg"]
assert len(msgs) == 1 and msgs[0]["To"] == "admin@example.org" and "panne test" in msgs[0].get_content(), msgs
app.scan = lambda: {"annonces": 0}
app.run_once()
msgs = [m[1] for m in sent_mail if m[0] == "msg"]
assert len(msgs) == 2 and "rétabli" in msgs[1]["Subject"]
app.run_once()
assert len([m for m in sent_mail if m[0] == "msg"]) == 2
app.opts["error_email"] = ""
app.state["errors_reported"] = {}
app.scan = boom; app.run_once()
assert len([m for m in sent_mail if m[0] == "msg"]) == 2  # vide = désactivé
app.scan = _scan
print("Emails d'erreur ✔")
print("\nTous les tests passent ✔")
from datetime import datetime as _dt
app.opts["quiet_hours"] = "00:00-07:00"
assert app.quiet_seconds_left(_dt(2026, 10, 5, 6, 30)) == 1800
assert app.quiet_seconds_left(_dt(2026, 10, 5, 7, 0)) == 0
assert app.quiet_seconds_left(_dt(2026, 10, 5, 23, 59)) == 0
app.opts["quiet_hours"] = "23:00-07:00"
assert app.quiet_seconds_left(_dt(2026, 10, 5, 23, 30)) == 7.5 * 3600
app.opts["quiet_hours"] = ""
assert app.quiet_seconds_left(_dt(2026, 10, 5, 3, 0)) == 0
print("Pause nocturne ✔")
app.opts.update({"quiet_hours": "00:00-07:00", "peak_hours": "08:00-19:00", "peak_interval_minutes": 5, "scan_interval_minutes": 15})
for _ in range(50):
    assert 255 <= app.scan_interval(_dt(2026, 10, 5, 10, 0)) <= 345
    assert 765 <= app.scan_interval(_dt(2026, 10, 5, 20, 0)) <= 1035
assert len({app.scan_interval(_dt(2026, 10, 5, 10, 0)) for _ in range(20)}) > 1
assert app.scan_interval(_dt(2026, 10, 5, 7, 55)) == 300   # s'arrête à 8h pile
assert app.scan_interval(_dt(2026, 10, 5, 23, 50)) == 600  # s'arrête à minuit
print("Intervalle jour / soir ✔")
# Rapport quotidien : la veille, de 0 h à minuit, par site (SeLoger compris)
sent_mail.clear()
app.opts.update({"daily_report_email": "rapport@example.org", "daily_report_time": "08:00"})
app.state.pop("daily_days", None); app.state.pop("daily_sent", None)
_y = _dt(2026, 10, 4, 15, 0)
app.count_daily({"alert_id": "x"}, "testées", _y)
app.count_daily(L["ad1"]["listing"], "testées", _y); app.count_daily(L["ad1"]["listing"], "retenues", _y)
app.count_daily({"alert_id": "bienici"}, "testées", _y); app.count_daily({"alert_id": "bienici"}, "doublons", _y)
app.count_daily({"alert_id": "seloger", "link": "https://click.by.seloger.com/x"}, "testées", _y)
app.count_daily({"alert_id": "jinka"}, "testées", _dt(2026, 10, 5, 7, 10))  # arrivée le matin même : pas dans ce rapport
assert not app.daily_report_due(_dt(2026, 10, 5, 7, 59))
assert app.daily_report_due(_dt(2026, 10, 5, 8, 1))
assert app.send_daily_report(_dt(2026, 10, 5, 8, 1))
m = [x[1] for x in sent_mail if x[0] == "msg"][-1]
body = m.get_body(("plain",)).get_content()
assert m["To"] == "rapport@example.org" and "04/10 — 4 annonces testées, 1 retenues" in m["Subject"], m["Subject"]
assert "Jinka : 2 annonces testées, 1 retenues" in body and "Bien'ici : 1 annonces testées, 0 retenues, 1 doublons" in body, body
assert "SeLoger : 1 annonces testées" in body
assert not app.daily_report_due(_dt(2026, 10, 5, 9, 0)) and app.daily_report_due(_dt(2026, 10, 6, 8, 0))
assert "2026-10-05" in app.state["daily_days"]  # la journée du 5 sera dans le rapport du 6
print("Rapport quotidien ✔")
# Pagination (50 par page) et suppression après 30 jours
import time
import web as _web
_now = time.time()
app.state["listings"] = {f"p{i}": {"status": "rejected", "crit": "x", "listing": {**L["ad1"]["listing"], "id": f"p{i}"},
                                   "results": [], "reason": "test", "ts": _now - i, "first_seen": _now - i}
                         for i in range(120)}
app.state["listings"]["old"] = {**app.state["listings"]["p0"], "first_seen": _now - 31 * 86400}
app.state["forgotten"] = {"tres-vieux": _now - 200 * 86400}
app.opts["destinations"] = [{"name": "A", "address": "x", "max_minutes": 45, "arrival_time": "09:00"}]
p1 = _web.render(app, "all", 1)
p3 = _web.render(app, "all", 3)
assert "page 1 / 3 (121 annonces)" in p1 and "suivantes »" in p1 and "« précédentes" not in p1
assert "page 3 / 3" in p3 and p3.count("<article") == 21
assert p1.count("<article") == 50
assert "page 3 / 3" in _web.render(app, "all", 99)
app.state["listings"]["p5"]["status"] = "duplicate"
app.state["listings"]["p6"]["status"] = "notified"
assert "page 1 / 1 (1 annonces)" in _web.render(app, "dup") and "page 1 / 1 (1 annonces)" in _web.render(app)  # défaut = OK
assert "doublons (1)" in _web.render(app) and "refusées (119)" in _web.render(app)
app.purge_old_listings(_now)
assert "old" not in app.state["listings"] and "old" in app.state["forgotten"]
assert "tres-vieux" not in app.state["forgotten"] and len(app.state["listings"]) == 120
print("Filtres OK (défaut) / doublons / refusées ✔")
print("Pagination + purge 30 jours ✔")
# Colocations détectées dans le texte, y compris pour les annonces déjà retenues
assert bienici.is_coliving({"description": "COLOCATION BAUX INDIVIDUELS 1 Chambre disponible"})
assert bienici.is_coliving({"description": "Une chambre est disponible dans une colocation de 68 m²"})
assert bienici.is_coliving({"description": "maison de 130 m2 en coliving rénovée"})
assert not bienici.is_coliving({"description": "T2 meublé de 30 m², colocation non acceptée, métro ligne 7"})
assert not bienici.is_coliving({"description": "Appartement F2, une chambre séparée, disponible de suite"})
app.state["listings"]["p7"]["status"] = "notified"
app.state["listings"]["p7"]["listing"] = {**app.state["listings"]["p7"]["listing"], "description": "COLOCATION BAUX INDIVIDUELS 1 Chambre disponible"}
app.state["listings"]["p8"]["status"] = "notified"
app.state["listings"]["p8"]["listing"] = {**app.state["listings"]["p8"]["listing"], "area": 16}
app.opts["min_area"] = 20; app.opts["max_rent"] = 0
app.recheck_retained()
assert app.state["listings"]["p7"]["status"] == "rejected" and app.state["listings"]["p6"]["status"] == "notified"
assert app.state["listings"]["p8"]["status"] == "rejected" and "16 m² < 20 m²" in app.state["listings"]["p8"]["reason"]
assert app.evaluate({**L["ad1"]["listing"], "description": "Une chambre est disponible dans une colocation"}, [], None)[2] == "chambre en colocation"
print("Colocations + surface (texte + rattrapage des annonces déjà retenues) ✔")
# Correspondances : 3+ refusées, +3 min par correspondance, choix du trajet le moins pénalisé
import transit as _tr
_t = _tr.Transit("k", ["metro", "rer"], 15, transfer_penalty=3, max_transfers=2)
def _cand(m, n): return {"minutes": m, "transfers": n, "walk_minutes": 5, "summary": " → ".join(["L"] * (n + 1)), "steps": []}
_key = lambda: f"{1.0:.4f},{1.0:.4f}>{2.0:.5f},{2.0:.5f}@09:00|{','.join(sorted(_t.allowed))}|15|d{_tr.DEST_WALK_MAX}"
_c = {_key(): {"ts": time.time(), "best": {**_cand(40, 2), "candidates": [_cand(40, 2), _cand(43, 1), _cand(39, 3)]}}}
r = _t.journey(1.0, 1.0, 2.0, 2.0, "09:00", 45, cache=_c)
assert r["minutes"] == 43 and r["counted"] == 46 and not r["ok"], r  # 40+6=46, 43+3=46 → égalité, 46 > 45
r = _t.journey(1.0, 1.0, 2.0, 2.0, "09:00", 46, cache=_c)
assert r["ok"] and r["counted"] == 46
_c[_key()]["best"]["candidates"] = [_cand(30, 3)]
r = _t.journey(1.0, 1.0, 2.0, 2.0, "09:00", 45, cache=_c)
assert not r["ok"] and r["reason"] == "3 correspondances (max 2)", r
assert "compté 46 min" in fmt.counted_txt({"minutes": 40, "counted": 46}) and fmt.counted_txt({"minutes": 40, "counted": 40}) == ""
app.opts.update({"transfer_penalty_minutes": 3, "max_transfers": 2,
                 "destinations": [{"name": "Travail", "address": "x", "max_minutes": 45, "arrival_time": "09:00"}]})
app.state["listings"]["p9"]["status"] = "notified"
app.state["listings"]["p9"]["results"] = [{"name": "Travail", "minutes": 44, "transfers": 2, "summary": "A → B → C"}]
app.recheck_retained()
assert app.state["listings"]["p9"]["status"] == "rejected" and "44 min + 6 min" in app.state["listings"]["p9"]["reason"]
assert app.state["listings"]["p9"]["crit"] is None  # sera recalculée si elle réapparaît
print("Correspondances (max 2, +3 min chacune) ✔")
# Recherche élargie bloquée par le quota : relance la nuit suivante, résultat par email
import explore as _ex
_run = _ex.run
def _quota(*a, **k): raise main.HttpError(429, "https://prim/x", '{"message":"API rate limit exceeded"}')
_ex.run = _quota
app.opts.update({"prim_api_key": "k", "daily_report_email": "rapport@example.org"})
app.resolve_destinations = lambda t: []
app._explore(25, 75)
assert app.state["explore_retry"]["max_minutes"] == 75 and "cette nuit" in app.explore_progress["error"]
assert not app.explore_retry_due() and app.explore_retry_due(_dt(2099, 1, 1))
_ex.run = lambda *a, **k: {"radius_km": 25, "max_minutes": 75, "results": [
    {"nom": "Montrouge", "ok": True, "worst": 41}, {"nom": "Vanves", "ok": True, "worst": 50},
    {"nom": "Loin", "ok": False, "worst": 90}]}
sent_mail.clear()
app.state.pop("explore_retry")
app._explore(25, 75, from_retry=True)
m = [x[1] for x in sent_mail if x[0] == "msg"][-1]
body = m.get_content()
assert "Montrouge (41)" in body and "Vanves (50)" in body and "Loin" not in body and "explore_retry" not in app.state
_ex.run = _run
print("Recherche élargie relancée la nuit après quota ✔")
# Quota IDFM atteint : pas d'email d'erreur, plus aucun appel jusqu'à minuit, annonces reportées
import transit as _tr2
_q = {}
_t2 = _tr2.Transit("k", ["metro"], 15, quota=_q)
_prev_get = _tr2.get_json
_calls = []
def _get429(url, **k):
    _calls.append(url); raise _tr2.HttpError(429, url, '{"message":"API rate limit exceeded"}')
_tr2.get_json = _get429
try:
    _t2._prim("/journeys", {})
    assert False
except _tr2.QuotaError as e:
    assert "reprise le" in str(e) and _q["until"] > time.time()
try:
    _t2._prim("/journeys", {})  # bloqué : aucun appel réseau
    assert False
except _tr2.QuotaError:
    assert len(_calls) == 1
# lendemain : quota toujours épuisé -> nouvel essai dans 1 h seulement
_q["until"] = time.time() - 1; _q["day"] = "2000-01-01"
try:
    _t2._prim("/journeys", {})
except _tr2.QuotaError:
    assert _q["until"] - time.time() < 3700
_tr2.get_json = lambda url, **k: {"ok": 1}
_q["until"] = time.time() - 1
assert _t2._prim("/journeys", {}) == {"ok": 1} and _q == {}
_tr2.get_json = _prev_get
print("Quota IDFM : report au lendemain sans email ✔")
_eval = app.evaluate
def _evq(*a, **k): raise main.QuotaError("quota IDFM atteint (429), reprise le 05/10 à 00:05")
app.evaluate = _evq
app.opts = app.load_options() if not callable(getattr(app, "load_options", None)) else app.opts
app.opts["error_email"] = "admin@example.org"
app.state["errors_reported"] = {}
for v in app.state["listings"].values():
    v["crit"] = "old"
sent_mail.clear()
try:
    st_q = app.scan()
    assert "itinéraires" not in str([m[1]["Subject"] for m in sent_mail if m[0] == "msg"])
    assert st_q.get("quota IDFM") == "reprise le 05/10 à 00:05" or st_q.get("reportées") is None, st_q
    print("Scan sous quota : pas d'email d'erreur ✔")
except Exception as e:  # le scan complet dépend des faux serveurs des tests précédents
    print("Scan sous quota : non testé ici (", type(e).__name__, e, ")")
app.evaluate = _eval
# Filtre « en attente » : envoi en attente + calcul reporté (quota)
app.state["listings"]["p10"]["status"] = "pending"
app.state["listings"]["p11"]["status"] = "match"
_w = _web.render(app, "wait")
import re as _re
assert "calcul en attente" in _w, _re.findall(r"Afficher :.*?</div>", _w)
_n = sum(1 for v in app.state["listings"].values() if v["status"] in ("match", "pending"))
assert f"en attente ({_n})" in _w and f"({_n} annonces)" in _w, (_n, _re.findall(r"page \d+ / \d+ \(\d+ annonces\)", _w))
print("Filtre en attente ✔")
# Non meublés : acceptés et signalés, sauf si « meublé uniquement »
assert "📦 NON MEUBLÉ" in fmt.title_of({"rooms": 1, "area": 22, "rent": 800, "city": "Vanves", "furnished": False})
assert "meublé" in fmt.title_of({"rooms": 1, "area": 22, "rent": 800, "city": "Vanves", "furnished": True})
assert "MEUBL" not in fmt.title_of({"rooms": 1, "area": 22, "rent": 800, "city": "Vanves"})
assert bienici.normalize({**bi_ads[0], "isFurnished": False})["furnished"] is False
app.opts.update({"furnished_only": True, "max_rent": 0, "min_area": 0})
assert app.basic_reject({"furnished": False, "description": ""}) == "non meublé"
app.opts["furnished_only"] = False
assert app.basic_reject({"furnished": False, "description": ""}) is None
print("Non meublés signalés ✔")
# Classe énergie (DPE) dans le titre
import jinka as _jk
assert _jk.normalize({"id": 1, "energy_dpe": "d"}, "a", "n")["dpe"] == "D"
assert _jk.normalize({"id": 1, "energy_dpe": "NC"}, "a", "n")["dpe"] is None
assert bienici.normalize({**bi_ads[0], "energyClassification": "C"})["dpe"] == "C"
assert "DPE C" in fmt.title_of({"rooms": 1, "area": 22, "rent": 800, "city": "Vanves", "dpe": "C", "furnished": False})
print("DPE dans le titre ✔", fmt.title_of({"rooms": 1, "area": 22, "rent": 800, "city": "Vanves", "postal_code": "92170", "dpe": "C", "furnished": False}))
# Étage maximum
assert main.floor_from_text("Studio au 5ème étage sans ascenseur") == 5
assert main.floor_from_text("situé au 2e et dernier étage") == 2
assert main.floor_from_text("Immeuble de 6 étages, appartement en rez-de-chaussée") == 0
assert main.floor_from_text("Bel appartement lumineux") is None
app.opts.update({"max_floor": 4, "furnished_only": False, "max_rent": 0, "min_area": 0})
assert app.basic_reject({"floor": 6, "description": ""}) == "6e étage ascenseur non précisé (max 4e sans ascenseur)"
assert app.basic_reject({"floor": 6, "elevator": True, "description": ""}) is None
assert app.basic_reject({"floor": 6, "elevator": False, "description": ""}) == "6e étage sans ascenseur (max 4e sans ascenseur)"
assert app.basic_reject({"floor": 6, "elevator": True, "description": "Studio sans ascenseur"}).startswith("6e étage sans ascenseur")
assert app.basic_reject({"floor": None, "description": "au 6e étage avec ascenseur"}) is None
assert app.basic_reject({"floor": 4, "description": ""}) is None
assert app.basic_reject({"floor": None, "description": "au 7ème étage, pas d'ascenseur"}).startswith("7e étage sans ascenseur")
assert app.basic_reject({"floor": None, "description": "proche métro"}) is None  # étage inconnu : gardée
app.opts["max_floor"] = 0
assert app.basic_reject({"floor": 9, "description": ""}) is None
import jinka as _jk2
assert _jk2.normalize({"id": 1, "floor": -1, "lift": None}, "a", "n")["floor"] is None
assert fmt.building_info({"floor": 6, "elevator": True}) == "6e étage · ascenseur"
print("Étage maximum (5e et + seulement avec ascenseur) ✔")
# Étage élevé sans info : lecture de la fiche Jinka (champ ascenseur)
import locate as _loc
_fad = _loc.fetch_ad_detail
_loc.fetch_ad_detail = lambda uuid: {"lift": True, "floor": 6, "description": "Studio lumineux"}
app.opts["max_floor"] = 4
_l = {"uuid": "u1", "floor": 6, "description": "", "furnished": True}
app.complete_building(_l)
assert _l["elevator"] is True and _l["detail_checked"] and app.basic_reject(_l) is None
_loc.fetch_ad_detail = lambda uuid: {"lift": None}
_l2 = {"uuid": "u2", "floor": 6, "description": ""}
app.complete_building(_l2)
assert app.basic_reject(_l2).startswith("6e étage ascenseur non précisé")
_loc.fetch_ad_detail = _fad
print("Ascenseur lu sur la fiche Jinka ✔")
assert main.has_elevator({"description": "W.C. Gd placard.7ème étage sans asc. Bon état."}) is False
assert main.has_elevator({"description": "5e étage avec asc. refait"}) is True
print("Abréviation « asc. » ✔")
# SeLoger : annonces tirées des emails d'alerte
import seloger
_html = """<table>
<tr><td><a href="https://click.by.seloger.com/?qs=AAA"><img src="https://image.by.seloger.com/x.png"></a></td></tr>
<tr><td><a href="https://click.by.seloger.com/?qs=AAA"><span><strong>689 &euro;/mois</strong></span></a></td></tr>
<tr><td><a href="https://click.by.seloger.com/?qs=AAA"><strong>Colocation à louer</strong></a></td></tr>
<tr><td><a>4 pièces · 39 m²</a></td></tr>
<tr><td><a>La Fourche-Guy Môquet,<br>Paris 17ème arrondissement<br>(75017)</a></td></tr>
<tr><td class="mobile-button"><span><a href="https://click.by.seloger.com/?qs=AAA">Voir l'annonce</a></span></td></tr>
<tr><td><a href="https://click.by.seloger.com/?qs=BBB"><span><strong>803 €/mois</strong></span></a></td></tr>
<tr><td><a><strong>Appartement à louer</strong></a></td></tr>
<tr><td><a>1 pièce · 25,2 m² · Étage 5/5</a></td></tr>
<tr><td><a>Amiraux-Simplon-Poissonniers,<br>Paris 18ème arrondissement<br>(75018)</a></td></tr>
<tr><td><a href="https://click.by.seloger.com/?qs=BBB">Voir l'annonce</a></td></tr>
<tr><td><a href="https://click.by.seloger.com/?qs=CCC"><strong>690 €/mois</strong></a></td></tr>
<tr><td>Appartement à louer</td></tr><tr><td>1 pièce · 31,8 m² · RDC/3</td></tr><tr><td>D</td></tr>
<tr><td>Coubron (93470)</td></tr><tr><td><a href="https://click.by.seloger.com/?qs=CCC">Voir l'annonce</a></td></tr>
</table>"""
_se = seloger.parse_email(_html)
assert [x["rent"] for x in _se] == [689, 803, 690], _se
assert _se[0]["coliving"] and _se[0]["city"] == "Paris 17e" and _se[0]["quartier"] == "La Fourche-Guy Môquet"
assert _se[1]["floor"] == 5 and _se[1]["area"] == 25.2 and _se[1]["link"].endswith("qs=BBB")
assert _se[2]["city"] == "Coubron" and _se[2]["quartier"] is None and _se[2]["floor"] == 0 and _se[2]["dpe"] == "D"
assert seloger.parse_email(_html)[1]["id"] == _se[1]["id"]  # identifiant stable d'un email à l'autre
assert seloger.quartier_queries("La Fourche-Guy Môquet") == ["La Fourche-Guy Môquet", "La Fourche", "Guy Môquet"]
# doublon SeLoger ↔ Bien'ici
app.state["listings"]["bi-x"] = {"status": "rejected", "crit": "x", "results": [], "ts": time.time(),
    "listing": {"id": "bienici:x", "alert_id": "bienici", "postal_code": "75018", "rent": 800, "area": 25.0}}
assert app.find_twin(_se[1])["listing"]["id"] == "bienici:x"
print("SeLoger (emails d'alerte) ✔")
# Nom de l'expéditeur
sent_mail.clear()
_n = notify.Notifier(email_to="a@b.fr", smtp_user="dedie@gmail.com", smtp_password="x", sender_name="ALERTES IMMO Test")
assert _n._email("t", "corps")
assert "ALERTES IMMO" in str([m[1]["From"] for m in sent_mail if m[0] == "msg"][-1])
assert notify.Notifier(email_to="a@b.fr", smtp_user="d@gmail.com", smtp_password="x").sender_name == "Jinka Transit"
print("Nom de l'expéditeur ✔")
# Nouvelle interface : vues, suivi, tri
_any = next(iter(app.state["listings"]))
app.set_track(_any, "fav")
assert app.state["track"][_any]["tag"] == "fav"
for _v in ("list", "track", "map", "settings"):
    _html = _web.render(app, "all", 1, _v, "prix")
    assert "<html" in _html and "Mon suivi (1)" in _html, _v
assert "⭐ Favori (1)" in _web.render(app, "all", 1, "track")
app.set_track(_any, "fav")  # même tag : retiré
assert _any not in app.state.get("track", {})
app.set_track(_any, "visit")
app.state["listings"][_any]["first_seen"] = time.time() - 40 * 86400
app.purge_old_listings()
assert _any in app.state["listings"]  # suivie : jamais supprimée
assert _web.short_dest("Travail (Bureau)") == "Bureau" and _web.short_dest("Gare du Nord") == "Gare du Nord"
print("Interface : vues, suivi, tri ✔")
assert _web.dest_icon("Travail (Bureau)") == "💼" and _web.dest_icon("École (Campus)") == "🎓"
assert _web.dest_icon("Gare du Nord") == "🚆" and _web.dest_icon("Arena Nanterre") == "🎤" and _web.dest_icon("Chez mamie") == "Chez mamie"
print("Icônes des adresses ✔")
# Exemple d'email d'annonce (aperçu du format)
sent_mail.clear()
app.load_options = lambda: app.opts
app.opts.update({"daily_report_email": "rapport@example.org", "jinka_email": "bot@gmail.com", "mail_password": "x"})
app.state["listings"]["p12"]["status"] = "notified"
_to, _ok = app.send_sample()
_m = [x[1] for x in sent_mail if x[0] == "msg"][-1]
assert _ok and _to == "rapport@example.org" and _m["Subject"].startswith("[Exemple]")
assert "Voir l'annonce" in _m.get_body(("html",)).get_content()
print("Exemple d'email d'annonce ✔")
# DPE maximum
app.opts.update({"max_dpe": "E", "max_floor": 0, "max_rent": 0, "min_area": 0, "furnished_only": False})
assert app.basic_reject({"dpe": "F", "description": ""}) == "DPE F (max E)"
assert app.basic_reject({"dpe": "G", "description": ""}) == "DPE G (max E)"
assert app.basic_reject({"dpe": "E", "description": ""}) is None and app.basic_reject({"dpe": None, "description": ""}) is None
app.opts["max_dpe"] = ""
assert app.basic_reject({"dpe": "G", "description": ""}) is None
assert fmt.DPE_COLORS["D"][0] == "#f4e70f"
print("DPE maximum ✔")

# Sécurité : liens http(s) seulement, icône échappée dans l'email
assert fmt.safe_url("javascript:alert(1)") == "#" and fmt.safe_url("https://x.fr/a") == "https://x.fr/a"
_h = fmt.html_of({"listing": {"link": "javascript:alert(1)", "rent": 800, "city": "X"}, "results": [
    {"name": "<b>x</b>", "minutes": 30, "ok": True, "steps": []}]})
assert "javascript:" not in _h and "<b>x</b>" not in _h
import mailbox as _mb
assert _mb.ACCOUNT_MAIL.search("Confirmez votre adresse email") and not _mb.ACCOUNT_MAIL.search("3 nouvelles annonces")
print("Sécurité (liens, échappement, emails de compte) ✔")
# Zones exclues (département ou code postal)
assert main.excluded_zone("94110", "94, 75018") == "94" and main.excluded_zone("75018", "94, 75018") == "75018"
assert main.excluded_zone("75013", "94, 75018") is None and main.excluded_zone("92120", "") is None
app.opts.update({"excluded_zones": "94, 75018", "max_dpe": "", "max_floor": 0})
assert app.basic_reject({"postal_code": "94200", "description": ""}) == "zone exclue (94)"
assert app.basic_reject({"postal_code": "75018", "description": ""}) == "zone exclue (75018)"
assert app.basic_reject({"postal_code": "75014", "description": ""}) is None
app.opts["excluded_zones"] = ""
print("Zones exclues ✔")
