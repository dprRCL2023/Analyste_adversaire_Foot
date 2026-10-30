# Analyste — l'adversaire du week-end

Appli interne de l'USL Dunkerque pour préparer l'adversaire du week-end à
partir des données StatsBomb et Second Spectrum. Connexion obligatoire.

État : **étape 3b** : squelette (connexion, sécurité, Docker), import
StatsBomb des derniers matchs d'un adversaire (ligne de commande), rôles,
page « Adversaire du week-end » (5 derniers matchs importés, forme, bilan,
xG pour et contre) et indicateurs de chaque match calculés à l'import.
Le détail et la suite sont dans `CLAUDE.md`.

## Comptes et rôles

Tout compte doit être dans un groupe, sinon il reçoit « Accès refusé » :

- `analyste` : importe et, plus tard, écrit les textes ;
- `staff` : consulte et exporte.

Créer un superutilisateur (`python manage.py createsuperuser`), puis, dans
l'admin (`/admin/`), créer les comptes et les ranger dans `analyste` ou `staff`.

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

L'équipe importée apparaît ensuite sur la page « Adversaire du week-end »
(http://127.0.0.1:8010/). Un adversaire importé avant cette page : relancer
la même commande (rien n'est retéléchargé).

## Indicateurs des matchs

L'import calcule, pour chaque match et chacune des deux équipes, les
indicateurs (xG, déroulé, système, attaque, PPDA, coups de pied arrêtés,
gardien, joueurs, 360 s'il est là). Après une évolution des formules, ou
pour rattraper une erreur de calcul signalée par l'import :

```bash
python manage.py recalculer_indicateurs            # lignes périmées ou manquantes
python manage.py recalculer_indicateurs --tous     # tout
python manage.py recalculer_indicateurs --match M  # un match (id StatsBomb)
```

Sans réseau : la commande relit le brut déjà stocké en base.

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
