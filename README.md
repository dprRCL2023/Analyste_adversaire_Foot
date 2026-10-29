# Analyste — l'adversaire du week-end

Appli interne de l'USL Dunkerque pour préparer l'adversaire du week-end à
partir des données StatsBomb et Second Spectrum. Connexion obligatoire.

État : **étape 2** : squelette (connexion, sécurité, Docker), plus l'import
StatsBomb des derniers matchs d'un adversaire (ligne de commande, consultation
dans l'admin en lecture seule). Le détail et la suite sont dans `CLAUDE.md`.

## Importer un adversaire (StatsBomb)

Identifiants StatsBomb dans `analyste/.env` (cf. `.env.example`), puis :

```bash
# Sous Docker : docker compose exec web python manage.py …
python manage.py importer_adversaire --catalogue                      # compétitions couvertes
python manage.py importer_adversaire --competition C --saison S --equipes   # équipes (ids)
python manage.py importer_adversaire --competition C --saison S --equipe E  # 5 derniers matchs
```

Options : `--derniers N`, `--sans-360`, `--forcer` (tout relire). Une relance
ne retélécharge que ce que StatsBomb a révisé.

## Démarrer en local

```bash
cd analyste
cp .env.example .env          # compléter les valeurs à la main (jamais commité)
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Puis ouvrir http://127.0.0.1:8010.

## Tests

```bash
cd analyste/webapp
pip install -r requirements-test.txt
DEBUG=1 DB_ENGINE=mysql DB_USER=<compte> DB_PASSWORD=<mot de passe> python manage.py test adversaire
```
