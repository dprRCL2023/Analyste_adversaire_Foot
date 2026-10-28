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
2. Modèles + client StatsBomb (urllib + gzip, deux comptes) + `importer_adversaire`
   (cache brut hors dépôt, idempotent sur `last_updated` / `last_updated_360`).
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
