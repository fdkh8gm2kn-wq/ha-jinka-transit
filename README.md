# Jinka Transit — add-on Home Assistant

Surveille tes alertes **Jinka** (une alerte = un secteur) et t'envoie sur **WhatsApp** uniquement les annonces
depuis lesquelles **toutes** tes adresses (bureau, école, gare… jusqu'à 5) sont joignables en
**métro / RER / Transilien / tram — jamais en bus** — dans le temps max que tu fixes pour chacune.

Les trajets sont calculés par l'API officielle d'Île-de-France Mobilités (PRIM), pour un jour ouvré à l'heure
d'arrivée choisie, marche comprise (porte à porte).

## Installation en un clic

[![Ajouter le dépôt à Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffdkh8gm2kn-wq%2Fha-jinka-transit)

Clique, confirme dans Home Assistant, puis installe **Jinka Transit** dans la boutique d'add-ons.

## 1. Ce qu'il te faut (une fois)

| Quoi | Où |
|---|---|
| **Une alerte Jinka par secteur** | Sur jinka.fr : crée l'alerte (zone, budget, surface…). L'add-on lit ses annonces. |
| **Jeton Jinka** | Voir ci-dessous (ton compte Jinka se connecte sans doute via Google/Apple/code email). |
| **Clé API PRIM** (gratuite) | prim.iledefrance-mobilites.fr → créer un compte → *Mes jetons* → générer une clé. Abonne-toi à l'API « Calculateur Île-de-France Mobilités – Accès générique (v2) ». |
| **Clé CallMeBot** (WhatsApp, gratuite) | Suis callmebot.com/blog/free-api-whatsapp-messages : tu envoies un message d'activation au numéro indiqué, tu reçois une `apikey`. |

**Connexion Jinka 100 % automatique (recommandé)** : utilise pour Jinka une adresse email dédiée.
Dans la configuration, mets cette adresse dans `jinka_email` et le mot de passe de la boîte dans
`mail_password` (Gmail : mot de passe d'application). L'add-on demande un code à Jinka, le lit dans
la boîte (réception puis spam) et se reconnecte seul, y compris quand la session expire.

**Connexion Jinka par code email, à la main** : ouvre la page de l'add-on (« Appart métro/RER »),
tape ton email Jinka → « Recevoir un code » → saisis le code reçu → « Valider ». Rien d'autre à copier.
Si un jour la connexion expire, l'add-on te prévient : refais la même chose.

**Ou bien, récupérer le jeton Jinka à la main** : connecte-toi sur www.jinka.fr dans Chrome → `⌥⌘I` → onglet *Application* →
*Cookies* → `https://www.jinka.fr` → copie la valeur de `LA_API_TOKEN`. (Si un jour l'add-on t'écrit
« Jeton Jinka refusé ou expiré », refais cette manip.) Si ton compte a un vrai mot de passe, tu peux mettre
email + mot de passe à la place.

## 2. Tester en local avec Docker (sur le Mac)

```bash
cd ~/Documents/CLAUDE/appart
cp local/options.example.json local/options.json   # puis remplis-le
docker compose up --build -d
```

Interface : http://localhost:8099 (bouton « Scanner maintenant »). Logs : `docker compose logs -f`.
Astuce : pour un premier test sans recevoir 10 WhatsApp, laisse `whatsapp_phone` vide : les résultats
s'affichent seulement dans l'interface (puis supprime `local/state.json` avant de remettre le numéro).

## 3. Installer sur Home Assistant

1. Installe l'add-on **Samba share** (ou *Studio Code Server*) dans HA.
2. Copie le dossier `jinka_transit/` dans le partage **`addons`** → `\\homeassistant.local\addons\jinka_transit`.
3. *Paramètres → Modules complémentaires → Boutique* → menu ⋮ → **Rechercher des mises à jour**.
   L'add-on apparaît dans **Add-ons locaux** → *Installer* (le Supervisor construit l'image Docker lui-même).
4. Onglet **Configuration** : remplis jetons, adresses, durées → *Enregistrer* → *Démarrer*.
5. Active « Afficher dans la barre latérale » : l'interface « Appart métro/RER » apparaît dans HA.

Les données (annonces déjà vues) sont dans `/data` de l'add-on, incluses dans les sauvegardes HA.

## Zones compatibles (recherche élargie)

Lien « 🗺️ Zones compatibles » dans la page de l'add-on : calcule, sans Jinka, toutes les communes
(et arrondissements de Paris) d'où tes adresses de filtre sont joignables sans bus dans le temps voulu,
depuis le centre de chaque commune. Carte + liste à recopier dans le secteur de ton alerte Jinka.

## Réglages

- **destinations** : jusqu'à 5. Chaque ligne : `name`, `address` (adresse postale, « Gare de Lyon »,
  ou `48.85,2.30`), `max_minutes`, `arrival_time` (heure d'arrivée visée un jour ouvré).
  Une annonce n'est envoyée que si **toutes** les adresses respectent leur durée.
  Ajoute `info_only: true` à une adresse pour avoir seulement son trajet dans le message, sans filtrer
  (ex. Gare du Nord) : il n'est calculé que pour les annonces retenues.
- **max_rent** : loyer max en € (0 = pas de limite). Les annonces plus chères sont écartées sans calcul de trajet.
- **allowed_modes** : `metro`, `rer`, `transilien`, `tram`. Le bus est toujours exclu.
- **max_walk_minutes** : marche max entre le logement / l'adresse et la station.
- **jinka_alerts** : noms ou IDs d'alertes à surveiller (vide = toutes).
- **ha_notify_service** (optionnel) : ex. `notify.mobile_app_ton_iphone`, en plus de WhatsApp.

Si tu modifies une durée, une adresse ou les modes, les annonces refusées sont automatiquement réévaluées.

## Bon à savoir

- L'API Jinka utilisée est celle du site web (non officielle) : si Jinka la change, l'add-on peut casser.
- La position GPS fournie par Jinka est parfois approximative (centre du quartier) : vérifie avec le lien carte.
- Tests hors ligne : `python3 tests/test_offline.py`.
