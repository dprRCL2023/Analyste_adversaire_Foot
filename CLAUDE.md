# ANALYSTE — analyse de l'adversaire du week-end (USL Dunkerque)

Règles communes (circuit de développement, agent GitHub, garde-fous, fichiers
sensibles) : `../CLAUDE.md`. Ce fichier ne décrit que l'appli Analyste.
Plan validé par Julien sur l'issue n°32 (28/10/2026).

## But

Une page par adversaire (« Match J+n contre X »), préparée par l'analyste en
début de semaine, consultée par le staff : forme et résultats, système et XI
probable, comment il attaque / défend, coups de pied arrêtés, profil physique,
séquences horodatées (playlists + notes), synthèse et export PDF.
Périmètre de la 1re version : les **5 derniers matchs** de l'adversaire.

## Décisions de Julien (issue n°32)

- **Pas de vidéo** : séquences = liste filtrable d'horodatages (période +
  minute + seconde), playlists et notes. Ni lecteur, ni `VIDEOS_DIR`, ni route
  vidéo. La CSP n'ouvre donc aucune origine tierce.
- **Second Spectrum** : accès du RC Lens.
- **Serveur** : en LOCAL pour le moment (`docker compose`, 127.0.0.1:8010).
- **StatsBomb 360** : optionnel, MATCH PAR MATCH — appelé seulement si
  `match_status_360` vaut « available », et tout calcul 360 tolère joueurs,
  zones et acteur manquants.
- Techno (Q1), rôles (Q6), CI (Q8) : recommandations du plan, par défaut.

## Stack

- Django 5.2 + MySQL 8 (comme Recrutement), gabarits rendus côté serveur,
  gunicorn sous Docker. Appli AUTONOME : aucun import depuis `recrutement/` ni
  `performance/` (code utile RECOPIÉ, jamais partagé), base et comptes à elle.
- `webapp/analyste_site/` : réglages, URLs, `securite.py` (CSP avec nonce).
- `webapp/adversaire/` : l'appli (modèles, clients, calculs à venir).

## Étapes (une PR par étape, toutes HARD)

1. **Squelette** — fait : connexion obligatoire, CSP, docker-compose, `.env.example`.
2. **Modèles + client StatsBomb + import** — fait (plan de l'étape 2 validé le
   29/10/2026) : `Competition`, `Equipe`, `Match`, `DonneesMatch` (brut
   compressé zlib EN BASE, pas de cache disque), `clients/statsbomb.py`,
   `importation.py` (règles, sans réseau), commande `importer_adversaire`,
   admin en lecture seule. Aucune vue.
3. Fiche adversaire (StatsBomb).
4. Second Spectrum (physique, rapprochement SS ↔ SB par date + équipes).
5. Séquences horodatées, playlists, notes (sans vidéo).
6. Synthèse + export PDF (ReportLab).
7. Déploiement (plus tard, serveur à choisir).

## Règles du pôle

- **Connexion obligatoire partout** (`LoginRequiredMiddleware`, sans
  interrupteur) : données sous licence. Un test de vue se connecte lui-même
  (`force_login`).
- **CSP stricte** : `script-src 'self' 'nonce-…'`, `style-src 'self'` SANS
  `'unsafe-inline'`, aucune origine tierce. Dans les gabarits : ni attribut
  `style=` ni `on…=` (un test le vérifie) — terrains en SVG par attributs.
- **Aucun secret ni donnée réelle dans le dépôt** : identifiants StatsBomb /
  Second Spectrum lus depuis l'environnement, utilisés côté serveur seulement,
  jamais envoyés au navigateur ni journalisés. `.env.example` sans valeurs (un
  test le vérifie). Tests : faux JSON inventés, aucun appel réseau réel.
- **Aucune route `/media/`.**
- **django-axes : REMOTE_ADDR seulement** (`securite.ip_client`). X-Forwarded-For
  n'est lu que si `AXES_PROXY_COUNT` ≥ 1 est posé EXPRÈS derrière un vrai proxy
  (un test vérifie qu'un en-tête inventé ne lève pas le verrouillage).

## Import StatsBomb (étape 2)

- **En ligne de commande SEULEMENT** : aucune vue web n'appelle un fournisseur.
- **Deux comptes**, choisis d'après le catalogue `/v4/competitions` de chacun
  (un compte hors licence reçoit `[]` SANS erreur : jamais d'essai-erreur).
  Les appels par match partent avec le compte de leur compétition.
  `Competition.compte` garde l'INDICE du compte, jamais l'adresse.
- Adresses : `/v4/competitions`, `/v6/competitions/{c}/seasons/{s}/matches`,
  `/v8/events/{m}`, `/v4/lineups/{m}`, **`/v3/360-frames/{m}`** (Recrutement
  est encore en v2). Chemins construits avec des entiers seulement.
- Reprise sur 429 / 5xx / coupure (3 essais, 5 s puis 10 s), aucune sur
  401 / 403. Réponse et décompression gzip bornées à 200 Mo. SSL TOUJOURS
  vérifié (pas d'interrupteur `STATSBOMB_VERIFY_SSL`). **Aucune redirection
  suivie** : le client passe par `statsbomb.OUVREUR`, dont le gestionnaire
  refuse tout 30x (sinon `Authorization` partirait vers l'hôte désigné) —
  `ErreurStatsBomb` sans nouvel essai (test). Les erreurs ne portent
  que chemin + code : jamais l'en-tête `Authorization` (test).
- **5 derniers matchs JOUÉS** (`match_status == "available"`), domicile ou
  extérieur, triés par (date, coup d'envoi, `match_id`) décroissants.
- **Idempotent** : `last_updated` inchangé → rien ; changé → événements +
  compositions ; seul `last_updated_360` changé, ou 360 devenu disponible →
  360 seul ; `--forcer` → tout. Les métadonnées du match (score…) sont
  toujours remises à jour. Réseau AVANT la transaction, écriture d'un bloc
  par match ; un match mal formé ou en panne est signalé et sauté.
- ⚠️ `DonneesMatch` (~1 Mo par match) : ne JAMAIS charger les colonnes `*_z`
  dans une liste (`defer`/`only`, ou une annotation `isnull` pour « a du 360 »).
- Tests : `urllib.request.urlopen` ET `OpenerDirector.open` (donc
  `OUVREUR`) sont remplacés dans TOUTE la suite par une fonction qui fait
  échouer le test (`tests/__init__.py`) ; un test simule `OUVREUR.open` ; données inventées
  dans `tests/donnees_inventees.py`. Un test vérifie que les migrations
  suivent les modèles (`makemigrations --check`).

```bash
python manage.py importer_adversaire --catalogue
python manage.py importer_adversaire --competition C --saison S --equipes
python manage.py importer_adversaire --competition C --saison S --equipe E \
    [--derniers 5] [--sans-360] [--forcer]
```

## Commandes

```bash
# Tests (depuis analyste/webapp). MySQL comme en prod :
pip install -r requirements-test.txt
DEBUG=1 DB_ENGINE=mysql DB_USER=… DB_PASSWORD=… python manage.py test adversaire
# Dépannage rapide sur SQLite :
DEBUG=1 DB_ENGINE=sqlite python manage.py test adversaire

# Lancer en local sous Docker (depuis analyste/)
cp .env.example .env   # puis compléter À LA MAIN
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
# → http://127.0.0.1:8010
```

⚠️ Le CI (`.github/workflows/tests.yml`) ne couvre PAS encore `analyste/` :
job à ajouter à la main par Julien (`modif-ci`). En attendant, chaque PR donne
la commande ci-dessus.
