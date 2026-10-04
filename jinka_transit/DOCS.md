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
Dans la configuration, mets cette adresse dans « Email Jinka » et la **clé d'application Google**
(16 lettres, créée sur myaccount.google.com/apppasswords après activation de la validation en 2 étapes)
dans « Clé d'application Google ». L'add-on demande un code à Jinka, le lit dans
la boîte (réception puis spam) et se reconnecte seul, y compris quand la session expire.

**Connexion Jinka par code email, à la main** : ouvre la page de l'add-on (« Appart métro/RER »),
tape ton email Jinka → « Recevoir un code » → saisis le code reçu → « Valider ». Rien d'autre à copier.
Si un jour la connexion expire, l'add-on te prévient : refais la même chose.

**Ou bien, récupérer le jeton Jinka à la main** : connecte-toi sur www.jinka.fr dans Chrome → `⌥⌘I` → onglet *Application* →
*Cookies* → `https://www.jinka.fr` → copie la valeur de `LA_API_TOKEN`. (Si un jour l'add-on t'écrit
« Jeton Jinka refusé ou expiré », refais cette manip.) Si ton compte a un vrai mot de passe, tu peux mettre
email + mot de passe à la place.

Les données (annonces déjà vues) sont dans `/data` de l'add-on, incluses dans les sauvegardes HA.

## Zones compatibles (recherche élargie)

Lien « 🗺️ Zones compatibles » dans la page de l'add-on : calcule, sans Jinka, toutes les communes
(et arrondissements de Paris) d'où tes adresses de filtre sont joignables sans bus dans le temps voulu,
depuis le centre de chaque commune. Carte + liste à recopier dans le secteur de ton alerte Jinka.

## Notifications

- **Email** (`email_to`) : annonce complète (photo, itinéraires détaillés, bouton vers l'annonce), envoyée
  depuis la boîte Gmail dédiée avec la même clé d'application Google.
- **SMS Free Mobile** (`free_sms_user` + `free_sms_key`) : version courte (prix, ville, temps, lien), gratuite,
  vers le numéro de la ligne Free qui a activé l'option « Notifications par SMS ».
- WhatsApp (CallMeBot) reste possible. Tous les canaux configurés sont utilisés.

## Bien'ici

En plus de Jinka, l'add-on interroge Bien'ici (API JSON publique du site) avec les mêmes critères
(meublé, loyer max, surface min) sur les 112 communes des alertes Jinka. Un logement déjà vu sur
l'autre source (même code postal, loyer à 15 € près, surface à 1,5 m² près) est marqué « doublon » et
n'est pas renvoyé. Les messages Bien'ici indiquent l'étage et digicode / gardien / interphone.

## Annonces sans GPS

Si Jinka ne donne pas de coordonnées, l'add-on lit la fiche publique de l'annonce : coordonnées, puis
« stations proches » indiquées par Jinka, puis station citée dans le texte (« gare de La Garenne-Colombes »,
« métro Nation »…). Le trajet est alors calculé depuis cette station et le message indique
« ⚠️ position estimée ». Sans rien de tout ça, l'annonce est écartée.

## Réglages

- **destinations** : jusqu'à 5. Chaque ligne : `name`, `address` (adresse postale, « Gare de Lyon »,
  ou `48.85,2.30`), `max_minutes`, `arrival_time` (heure d'arrivée visée un jour ouvré).
  Une annonce n'est envoyée que si **toutes** les adresses respectent leur durée.
  Ajoute `info_only: true` à une adresse pour avoir seulement son trajet dans le message, sans filtrer
  (ex. Gare du Nord) : il n'est calculé que pour les annonces retenues.
- **max_rent** : loyer max en € (0 = pas de limite). Les annonces plus chères sont écartées sans calcul de trajet.
- **max_walk_home_minutes** : marche max du logement à la 1re station (5 min par défaut).
- **allowed_modes** : `metro`, `rer`, `transilien`, `tram`. Le bus est toujours exclu.
- **max_walk_minutes** : marche max entre le logement / l'adresse et la station.
- **jinka_alerts** : noms ou IDs d'alertes à surveiller (vide = toutes).
- **ha_notify_service** (optionnel) : ex. `notify.mobile_app_ton_iphone`, en plus de WhatsApp.

Si tu modifies une durée, une adresse ou les modes, les annonces refusées sont automatiquement réévaluées.

## Bon à savoir

- L'API Jinka utilisée est celle du site web (non officielle) : si Jinka la change, l'add-on peut casser.
- La position GPS fournie par Jinka est parfois approximative (centre du quartier) : vérifie avec le lien carte.
- Tests hors ligne : `python3 tests/test_offline.py`.
