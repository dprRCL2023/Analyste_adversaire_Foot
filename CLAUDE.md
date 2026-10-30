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
3. Fiche adversaire (StatsBomb), découpée (plan validé le 29/10/2026) :
   - **3a — fait** : rôles (`roles.py`, refus par défaut), `AdversaireSuivi`
     (équipe CIBLÉE par l'import), page « Adversaire du week-end » (`/`) avec
     les 5 derniers matchs importés, forme et bilan ;
   - **3b — fait** (plan détaillé validé le 29/10/2026) : table
     `IndicateursMatchEquipe` calculée À L'IMPORT (une ligne par match et par
     équipe), partagée avec l'Après-match ; colonne « xG pour – contre » sur `/` ;
   - 3c (plan détaillé validé le 30/10/2026, deux PR) :
     - **3c-1 — fait** : indicateurs v2 (+ ajouts du propriétaire), table
       `FicheAdversaire` calculée à l'import, page `/adversaire/<id du suivi>/`
       avec Résultats et forme, Système et XI probable, Joueurs clés ;
     - **3c-2 — fait** (plan validé le 30/10/2026) : affichage d'Attaque,
       Défense (avec Danger, Transitions, gardien), CPA et 360 (terrains SVG
       en grilles, barres), SANS modèle ni migration ; `VERSION_FICHE = 2`.
   - Ensuite : **Après-match** (rapport d'après-match, sur la table de 3b).
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
- **Rôles, REFUS PAR DÉFAUT** (`adversaire/roles.py`, étape 3a) : groupes
  `analyste` (importe, écrira les textes) et `staff` (consulte, exporte),
  créés par la migration 0002. Toute vue métier porte
  `@role_requis(peut_consulter)` (ou `peut_analyser`) : un compte connecté
  sans groupe reçoit 403 (« un scout ne voit rien »). Vérifié DANS la vue,
  jamais seulement en masquant un lien. Les comptes se rangent dans les
  groupes depuis l'admin (superutilisateur).
- **Tous les calculs à l'IMPORT, jamais pendant une requête web** : une vue
  lit des tables déjà calculées, n'appelle aucun fournisseur et ne
  décompresse jamais le brut.
- **Textes saisis par le staff (à venir)** : tables à part, rattachées au match
  en `on_delete=PROTECT` ; aucune commande d'import ou de recalcul n'y écrit,
  et elles restent exclues de toute synchronisation future.
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

## Page « Adversaire du week-end » (étape 3a)

- `AdversaireSuivi` (équipe, compétition, `importe_le`) : posé par
  `importer_adversaire` pour l'équipe CIBLÉE (jamais deviné : deux
  adversaires importés ont pu se rencontrer). Rien si l'équipe n'apparaît dans
  aucun match de la saison. ⚠️ Imports faits AVANT 3a : relancer la même
  commande (rien n'est retéléchargé, le suivi est créé).
- `/` : suivi le plus récemment importé par défaut, `?suivi=<id>` sinon
  (`int()`, invalide ou inconnu → 404) ; 5 derniers matchs de l'équipe DANS la
  compétition suivie, résultat vu par l'adversaire, forme et bilan.
- Requêtes : nombre fixe (pas de N+1, test), badge 360 par annotation
  `Exists(… frames_360_z IS NOT NULL)` : aucune colonne `*_z` lue (test sur
  le SQL capturé), pas de `nulls_last`.

## Indicateurs calculés à l'import (étape 3b)

- **`IndicateursMatchEquipe`** (migration 0003) : (match, équipe) unique, DEUX
  lignes par match (l'adversaire ET l'équipe rencontrée : « contre » se lit
  chez l'autre). Rattachée à `Match`, PAS à `DonneesMatch` (le mode « ligue »
  de l'Après-match gardera les indicateurs et jettera le brut). `valeurs` en
  JSON (la liste bouge encore : une colonne par indicateur = une migration à
  chaque ajout) ; un indicateur filtré ou trié en SQL sortira en vraie colonne.
- **`analyse/indicateurs.py`** : `calculer(evenements, compositions,
  frames_360, equipe_sb_id)`, Python PUR, déterministe, sans erreur sur un
  champ absent (None = « non disponible »). Blocs : `resultat` (buts dont csc,
  xG/npxG, tirs), `deroule` (xG cumulés minute par minute, buts, menace par
  tranche de 5 min en **OBV**, repli sur l'**xG** si le match n'a pas d'OBV —
  `menace_source`), `systeme` (formation, `Tactical Shift`, XI), `attaque`,
  `defense`, `cpa`, `gardien` (PSxG = `shot_execution_xg` si fourni), `joueurs`
  (minutes, OBV créée/concédée, xG), `trois_cent_soixante` (None sans 360 ;
  image sans acteur ou vide = « inexploitable », jamais une erreur). Période 5
  (tirs au but) exclue de tout. Définitions recopiées de Recrutement
  (`analyse_evenements.progressive`), jamais importées. Pas d'xT maison.
- **PPDA** : passes adverses dans LEURS 60 % (x ≤ 72 dans leur repère, hors
  phases arrêtées) ÷ nos tacles, duels gagnés, interceptions et fautes à
  x ≥ 48 dans le NÔTRE. ⚠️ Chaque équipe attaque vers x = 120 dans ses propres
  événements : ne jamais comparer des x de deux équipes sans retourner.
- **Périmé** = `version_calcul` ≠ `indicateurs.VERSION_CALCUL` OU
  `version_source` ≠ `importation.version_source(last_updated,
  last_updated_360, a_360)`. ⚠️ Changer une formule = augmenter
  `VERSION_CALCUL`, puis `recalculer_indicateurs`.
- **Quand** : à l'import, calcul AVANT la transaction (listes en mémoire,
  complétées en base par une seule ligne si seul le 360 ou seuls les
  événements sont relus), écriture DANS la transaction du brut. Match « déjà à
  jour » à lignes périmées → brut de CE match relu et recalculé. **Une erreur
  de calcul ne fait jamais perdre le brut** (brut stocké illisible compris : il
  ne bloque ni l'import ni le recalcul) : match écrit, erreur notée (type
  et `match_id` seulement), ligne absente ou périmée jusqu'au recalcul.
  `recalculer_indicateurs [--tous | --match M]` : sans réseau, identifiants
  d'abord puis un brut à la fois.
- **Page `/`** : xG lu par `xg_des_matchs` — une requête, deux clés extraites
  en SQL (`KeyTransform`), ni brut ni JSON complet chargés ; nombre de
  requêtes fixe (test). Le JSON n'est jamais injecté tel quel dans une page.

## Fiche adversaire (étape 3c-1)

- **Indicateurs v2** (`VERSION_CALCUL = 2`) : `resultat.par_motif` (buts, tirs,
  xG pour et contre par `play_pattern`, penalty et csc à part),
  `attaque.grille_tirs_xg`, `cpa.chutes_corners`, `systeme.xi[].position_id`,
  `danger` (xG et buts par tranche de 15 min — temps additionnel dans la
  dernière tranche de sa mi-temps, prolongations à part — et hauteur des
  actions défensives selon le score, mène / égalité / mené), `transitions`
  (tirs et xG dans les 15 s qui suivent une récupération, pour et contre ;
  grille des pertes suivies d'un tir adverse dans les 15 s ; fenêtre de temps
  seule, même période, en secondes entières). **Avec le 360 seulement**
  (`trois_cent_soixante`, None sans 360) : `relance` (passes en jeu courant
  depuis son tiers selon 0, 1, 2+ adversaires à moins de 5 m, réussite, vers
  l'avant), `gardien_sous_pression` (≥ 3 adversaires de champ dans son tiers ;
  longues = 32 m et plus), `ligne_defensive_par_score` (moyenne des 4 joueurs
  de champ les plus reculés, image ignorée s'il en voit moins de 4),
  `corners_pour` / `corners_contre` (attaquants et défenseurs de champ dans
  les 6 m et la surface), et par joueur `passes_5_elimines` (passe réussie,
  ≥ 5 adversaires de champ entre départ et arrivée) et
  `receptions_entre_lignes` (hors de son tiers, ≥ 3 adversaires devant et ≥ 2
  derrière). ⚠️ Le 360 ne voit que les joueurs à l'image : ces comptes sont
  des MINIMUMS. 5 m = 5 / 0,9144 yard (terrain StatsBomb en yards).
- **`FicheAdversaire`** (migration 0004, OneToOne avec le suivi) : synthèse
  des 5 derniers matchs par `analyse/fiche.py` (PUR, sans Django). Un match ne
  compte que si ses DEUX lignes sont à la version de calcul courante
  (« n matchs sur 5 », « n matchs sur 5 avec 360 »). **Sommes avant ratios**
  (PPDA, possession, xG par tir, réussites, hauteurs). XI probable :
  titularisations, puis titulaire au dernier match, puis minutes, puis id ;
  poste le plus fréquent (égalité → le plus récent) placé d'après
  `position_id` (`fiche.POSITIONS`). Formation la plus fréquente, égalité → la
  plus récente. Absent = titulaire ≥ 3 fois sur les 4 précédents et 0 minute
  au dernier (cause inconnue, libellé neutre). ⚠️ Si le match le plus récent
  n'est PAS calculé (`systeme.dernier_match_calcule` faux), ni absents ni
  départage « titulaire au dernier match » : la page affiche « Dernier match
  non calculé » (le 1er match calculé n'est pas le dernier joué). Joueurs clés : OBV / 90 (sur
  les seules minutes des matchs qui ont l'OBV), repli xG puis actions
  progressives sans OBV, « peu joué » < 90 min cumulées. Domicile / extérieur
  séparés pour le bloc Résultats. Grilles : niveaux 0 à 5 par RANG (quantiles)
  calculés à l'import (`fiche.niveaux`). Pas de moyenne de ligue en 3c.
- **Quand** : en fin d'`importer_adversaire` (le suivi importé) et dans
  `recalculer_indicateurs` (TOUS les suivis, après les matchs), depuis les
  lignes d'indicateurs, jamais le brut. Erreur de calcul → ancienne fiche
  gardée, type d'erreur seul dans la sortie.
- **Périmée** (`importation.fiche_perimee`) = `version_fiche` ≠
  `fiche.VERSION_FICHE` OU `matchs` ≠ les derniers matchs du suivi
  (`importation.matchs_du_suivi`, même requête que les pages) : bandeau sur la
  page, qui ne recalcule rien. ⚠️ Changer une règle de la fiche = augmenter
  `VERSION_FICHE`, puis `recalculer_indicateurs`.
- **Page** `/adversaire/<int:suivi_id>/` sous `@role_requis(peut_consulter)`
  (inconnu → 404) : 3 requêtes métier (suivi, matchs, fiche), ni `*_z` ni
  `IndicateursMatchEquipe` lus (test sur le SQL). Premiers NOMS de joueurs
  affichés (pros adverses, base locale) : échappement automatique, jamais
  `|safe` ni JSON injecté (test avec un nom hostile). Terrain SVG par
  attributs (`templatetags/terrain.py`, `{% localize off %}` : en fr, un
  nombre décimal s'écrirait avec une virgule) ; un gabarit partiel par bloc
  (`templates/adversaire/fiche/`), réutilisable par l'Après-match.
- **Base d'avant 3c-1** : `python manage.py migrate && python manage.py
  recalculer_indicateurs` (indicateurs v1 → v2 puis fiches), sans réseau.

## Fiche adversaire, blocs d'analyse (étape 3c-2)

- **`VERSION_FICHE = 2`**, trois règles calculées à l'IMPORT dans `fiche.py` :
  1. grilles « contre » (`attaque.grille_tirs_xg_contre`,
     `cpa.chutes_corners_contre`) lues chez l'équipe rencontrée puis
     **retournées** (`fiche.retourner` : rotation de 180°, indice i → 95 − i),
     pour être dessinées vers le but de l'adversaire ;
  2. chaque grille porte `min_non_vide` et `max` (légende « de … à … ») ;
  3. barres prêtes à dessiner (`fiche.largeurs` : entier de 0 à 100 rapporté
     au maximum du bloc) : `danger.barres_xg` (pour et contre sur la MÊME
     échelle) et `attaque.couloirs_barres`.
  Base d'avant 3c-2 : `python manage.py recalculer_indicateurs` (fiches
  seulement, ni réseau ni brut relu : `VERSION_CALCUL` reste à 2).
- **Blocs** (un gabarit partiel chacun, `templates/adversaire/fiche/`) :
  `_attaque.html`, `_defense.html` (ancres `#danger` et `#transitions`
  dedans), `_cpa.html`, `_360.html` (affiché seulement si au moins un match a
  du 360, « n matchs sur 5 avec 360 », comptes minimaux).
- **SVG** : balises `terrain_grille(grille, libellé, décimales)` et
  `barres(lignes, unité, décimales)` de `templatetags/terrain.py` (nombre
  FIXE de décimales : 0 pour les comptes, 1 pour les %, 2 pour les xG ;
  jamais `floatformat:"-2"`, qui écrivait « 0,10 ») — tri et CONTRÔLE seulement : niveaux
  entiers 0 à 5 et 96 cases exactement (sinon « Aucune donnée »), largeurs
  entières ramenées entre 0 et 100, classes de barre d'une liste fermée
  (`pour`, `contre`). Seuls ces entiers et classes vont dans un attribut ;
  les valeurs (texte des `<title>`, légendes) passent par `floatformat`,
  échappées. Couleurs `niv-0` à `niv-5` (vert pâle → rouge) dans `app.css`.
- ⚠️ `floatformat` localise TOUJOURS (virgule en fr), même sous
  `{% localize off %}` : ne jamais l'employer dans un attribut SVG.

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
