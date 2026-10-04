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
app.scan()
assert L["ad2"]["reason"] == "loyer 1500 € > 1400 €", L["ad2"]
assert len(calls["prim"]) == n_prim
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
print("\nTous les tests passent ✔")
