"""Indicateurs d'UN match vus par UNE équipe (étape 3b), calcul PUR.

`calculer(evenements, compositions, frames_360, equipe_sb_id) -> dict`
lit les listes brutes de StatsBomb (Events v8, Lineups v4, 360 Frames v3) et
rend un dict JSON (nombres, listes, chaînes). Ni Django, ni réseau : appelé à
l'IMPORT (et par `recalculer_indicateurs`), jamais pendant une requête web.

Règles :
- déterministe, et AUCUNE erreur sur un champ absent ou mal formé : la valeur
  vaut alors None (« non disponible » à l'écran) ou l'événement est ignoré ;
- repère StatsBomb : terrain 120 × 80, chaque équipe attaque vers x = 120 dans
  SES événements. Les passes adverses comparées à nos actions défensives (PPDA)
  sont donc lues dans le repère adverse, nos actions dans le nôtre ;
- la séance de tirs au but (période 5) est exclue de tout ;
- `VERSION_CALCUL` augmente à chaque changement de formule : les lignes
  calculées avant deviennent périmées (`manage.py recalculer_indicateurs`).

Définitions recopiées de `recrutement/webapp/players/analyse_evenements.py`
(action progressive, surface, dernier tiers), jamais importées.

Version 2 (étape 3c-1) : buts et xG par type d'action (`resultat.par_motif`),
xG par zone de tir, chutes des corners, `position_id` du XI, « quand il est
dangereux » (xG par tranche de 15 min, hauteur des actions défensives selon
le score), transitions (15 s après une récupération, pertes suivies d'un tir
adverse) et, SEULEMENT avec le 360 : pression sur la relance, gardien sous
pression, ligne défensive selon le score, présence dans la surface sur
corner, passes qui éliminent 5 adversaires et réceptions entre les lignes.
⚠️ Le 360 ne montre que les joueurs visibles à l'image : les comptes
d'adversaires sont des MINIMUMS (un joueur hors champ n'est pas compté).
"""
import math
from collections import defaultdict
from itertools import accumulate

VERSION_CALCUL = 2

LARGEUR = 80.0
BUT = (120.0, 40.0)
SURFACE = (102.0, 18.0, 62.0)  # x minimal, y minimal, y maximal
DERNIER_TIERS = 80.0
# PPDA : passes adverses dans LEURS 60 % de terrain (x ≤ 72 dans leur repère),
# donc nos actions défensives à x ≥ 48 dans le nôtre.
ZONE_PPDA = 72.0
RECUPERATION_HAUTE = 80.0
COLONNES, RANGEES = 12, 8  # grille de 10 × 10 m
TRANCHE = 5  # minutes, pour la menace
MINUTE_MAX = 130
PERIODE_TIRS_AU_BUT = 5

# Issues de tir en minuscules : comparées sans tenir compte de la casse
# (StatsBomb écrit « Saved to Post », t minuscule).
CADRES = {"goal", "saved", "saved to post"}
ARRETS = {"saved", "saved to post"}
PHASES_ARRETEES = {"Corner", "Free Kick", "Throw-in", "Goal Kick", "Kick Off"}
DUEL_GAGNE = {"Won", "Success", "Success In Play", "Success Out"}
EXPULSION = {"Red Card", "Second Yellow"}
ACTIONS_DEFENSIVES = {"Pressure", "Duel", "Interception", "Foul Committed",
                      "Ball Recovery", "Block", "Clearance"}
PERTES = {"Dispossessed", "Miscontrol"}

# ── Version 2 ────────────────────────────────────────────────────────────────
YARD = 0.9144  # le terrain StatsBomb est en yards
RAYON_PRESSION = 5 / YARD  # « adversaire à moins de 5 m »
LONG_BALLON = 32 / YARD  # passe longue : 32 m et plus (≈ 35 yards)
FENETRE_TRANSITION = 15  # secondes
TIERS = 40.0  # propre tiers : x < 40 dans le repère de l'équipe
SIX_METRES = (114.0, 30.0, 50.0)  # x minimal, y minimal, y maximal
ELIMINES_MIN = 5  # passe qui laisse au moins 5 adversaires derrière le ballon
ADVERSAIRES_DANS_LE_TIERS = 3  # gardien « sous pression »
TRANCHES_15 = ["0-15", "15-30", "30-45+", "45-60", "60-75", "75-90+", "prol."]
MOTIFS = {
    "Regular Play": "jeu_courant",
    "From Counter": "contre_attaque",
    "From Corner": "corner",
    "From Free Kick": "coup_franc",
    "From Throw In": "touche",
    "From Goal Kick": "six_metres",
    "From Keeper": "gardien",
    "From Kick Off": "engagement",
}
ETATS_SCORE = ("mene", "egalite", "est_mene")
PRESSIONS = ("0", "1", "2+")
PASSE_RATEE = {"Incomplete", "Out"}
# Réception « entre les lignes » : hors de son tiers, avec au moins 3
# adversaires de champ visibles devant (côté but) et 2 déjà dépassés.
ENTRE_LIGNES_DEVANT, ENTRE_LIGNES_DERRIERE = 3, 2


# ── Lecture prudente ─────────────────────────────────────────────────────────
def _d(valeur):
    return valeur if isinstance(valeur, dict) else {}


def _liste(valeur):
    return valeur if isinstance(valeur, list) else []


def _nom(valeur):
    return _d(valeur).get("name")


def _num(valeur):
    """Nombre fini, ou None (booléens, textes, NaN refusés)."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    valeur = float(valeur)
    return valeur if math.isfinite(valeur) else None


def _ident(valeur):
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) and valeur > 0 else None


def _loc(valeur):
    if not isinstance(valeur, (list, tuple)) or len(valeur) < 2:
        return None
    x, y = _num(valeur[0]), _num(valeur[1])
    return None if x is None or y is None else (x, y)


def _r(valeur, chiffres=3):
    return None if valeur is None else round(valeur, chiffres)


def _type(e):
    return _nom(e.get("type"))


def _equipe(e):
    return _ident(_d(e.get("team")).get("id"))


def _joueur(e):
    joueur = _d(e.get("player"))
    return _ident(joueur.get("id")), joueur.get("name") if isinstance(joueur.get("name"), str) else ""


def _minute(e):
    return max(0, int(_num(e.get("minute")) or 0))


def _temps(e):
    return max(0.0, (_num(e.get("minute")) or 0.0) + (_num(e.get("second")) or 0.0) / 60)


def _periode(e):
    periode = e.get("period")
    return periode if isinstance(periode, int) and not isinstance(periode, bool) else 0


def _cle(e):
    """(période, secondes) : ordre chronologique, en secondes ENTIÈRES quand
    StatsBomb les donne (comparaison exacte de la fenêtre de 15 s)."""
    return _periode(e), max(0.0, (_num(e.get("minute")) or 0.0) * 60 + (_num(e.get("second")) or 0.0))


# ── Géométrie ────────────────────────────────────────────────────────────────
def _dist_but(x, y):
    return math.hypot(BUT[0] - x, BUT[1] - y)


def dans_surface(x, y):
    return x >= SURFACE[0] and SURFACE[1] <= y <= SURFACE[2]


def progressive(depart, arrivee):
    """Avance le ballon d'au moins 25 % de la distance au but, ou entre dans la
    surface — hors de son propre tiers défensif d'arrivée."""
    (x, y), (fx, fy) = depart, arrivee
    if fx < 40:
        return False
    if dans_surface(fx, fy) and not dans_surface(x, y):
        return True
    return _dist_but(fx, fy) <= 0.75 * _dist_but(x, y)


def _case(loc):
    colonne = min(COLONNES - 1, max(0, int(loc[0] // 10)))
    rangee = min(RANGEES - 1, max(0, int(loc[1] // 10)))
    return rangee * COLONNES + colonne


def _grille():
    return [0] * (COLONNES * RANGEES)


def _couloir(y):
    if y < LARGEUR / 3:
        return "gauche"
    return "droite" if y > 2 * LARGEUR / 3 else "axe"


# ── Tirs ─────────────────────────────────────────────────────────────────────
def _tir(e):
    return _d(e.get("shot"))


def _xg(e):
    return _num(_tir(e).get("statsbomb_xg")) or 0.0


def _issue(e):
    return _nom(_tir(e).get("outcome"))


def _issue_minuscule(e):
    issue = _issue(e)
    return issue.lower() if isinstance(issue, str) else None


def _penalty(e):
    return _nom(_tir(e).get("type")) == "Penalty"


def _bilan_tirs(tirs):
    xg = sum(_xg(e) for e in tirs)
    return {
        "tirs": len(tirs),
        "tirs_cadres": sum(1 for e in tirs if _issue_minuscule(e) in CADRES),
        "buts": sum(1 for e in tirs if _issue(e) == "Goal"),
        "xg": _r(xg),
        "npxg": _r(sum(_xg(e) for e in tirs if not _penalty(e))),
        "xg_par_tir": _r(xg / len(tirs)) if tirs else None,
    }


# ── Progression (passes réussies hors phase arrêtée, conduites) ─────────────
def _avancee(e):
    """(départ, arrivée) d'une passe réussie hors phase arrêtée ou d'une
    conduite, None sinon (ou si une coordonnée manque)."""
    t = _type(e)
    if t == "Pass":
        passe = _d(e.get("pass"))
        if passe.get("outcome") is not None or _nom(passe.get("type")) in PHASES_ARRETEES:
            return None
        arrivee = _loc(passe.get("end_location"))
    elif t == "Carry":
        arrivee = _loc(_d(e.get("carry")).get("end_location"))
    else:
        return None
    depart = _loc(e.get("location"))
    if depart is None or arrivee is None:
        return None
    return depart, arrivee


def _est_progressive(e):
    trajet = _avancee(e)
    if trajet is None:
        return False
    depart, arrivee = trajet
    if _type(e) == "Carry" and math.hypot(arrivee[0] - depart[0], arrivee[1] - depart[1]) < 5:
        return False
    return progressive(depart, arrivee)


# ── Blocs ────────────────────────────────────────────────────────────────────
def _resultat(nous, eux):
    tirs_pour = [e for e in nous if _type(e) == "Shot"]
    tirs_contre = [e for e in eux if _type(e) == "Shot"]
    pour, contre = _bilan_tirs(tirs_pour), _bilan_tirs(tirs_contre)
    # « Own Goal For » est rangé chez l'équipe qui en profite.
    csc_pour = sum(1 for e in nous if _type(e) == "Own Goal For")
    csc_contre = sum(1 for e in eux if _type(e) == "Own Goal For")
    return {
        "buts_pour": pour["buts"] + csc_pour,
        "buts_contre": contre["buts"] + csc_contre,
        "csc_pour": csc_pour,
        "csc_contre": csc_contre,
        "xg_pour": pour["xg"],
        "xg_contre": contre["xg"],
        "npxg_pour": pour["npxg"],
        "npxg_contre": contre["npxg"],
        "tirs_pour": pour["tirs"],
        "tirs_contre": contre["tirs"],
        "tirs_cadres_pour": pour["tirs_cadres"],
        "tirs_cadres_contre": contre["tirs_cadres"],
        "xg_par_tir_pour": pour["xg_par_tir"],
        "xg_par_tir_contre": contre["xg_par_tir"],
        "par_motif": _par_motif(nous, eux),
    }


def _motif(e):
    """Type d'action d'un tir : penalty, sinon `play_pattern` StatsBomb."""
    if _penalty(e):
        return "penalty"
    return MOTIFS.get(_nom(e.get("play_pattern")), "autre")


def _par_motif(nous, eux):
    """{motif: tirs, buts et xG pour et contre} ; « csc » pour les buts contre son camp."""
    motifs = {}

    def ligne(motif):
        return motifs.setdefault(motif, {"tirs_pour": 0, "buts_pour": 0, "xg_pour": 0.0,
                                         "tirs_contre": 0, "buts_contre": 0, "xg_contre": 0.0})

    for cote, evts in (("pour", nous), ("contre", eux)):
        for e in evts:
            nature = _type(e)
            if nature == "Shot":
                bloc = ligne(_motif(e))
                bloc[f"tirs_{cote}"] += 1
                bloc[f"xg_{cote}"] += _xg(e)
                if _issue(e) == "Goal":
                    bloc[f"buts_{cote}"] += 1
            elif nature == "Own Goal For":
                ligne("csc")[f"buts_{cote}"] += 1
    for bloc in motifs.values():
        bloc["xg_pour"], bloc["xg_contre"] = _r(bloc["xg_pour"]), _r(bloc["xg_contre"])
    return motifs


def _cumul(tirs, nb_minutes):
    increments = [0.0] * nb_minutes
    for e in tirs:
        increments[min(_minute(e), nb_minutes - 1)] += _xg(e)
    return [round(v, 3) for v in accumulate(increments)]


def _deroule(nous, eux, fin, avec_obv):
    nb_minutes = min(MINUTE_MAX, int(fin)) + 1
    nb_tranches = (nb_minutes - 1) // TRANCHE + 1
    tirs_pour = [e for e in nous if _type(e) == "Shot"]
    tirs_contre = [e for e in eux if _type(e) == "Shot"]

    buts = [{"minute": _minute(e), "pour": pour}
            for pour, evts in ((True, nous), (False, eux)) for e in evts
            if (_type(e) == "Shot" and _issue(e) == "Goal") or _type(e) == "Own Goal For"]
    buts.sort(key=lambda b: (b["minute"], not b["pour"]))

    def menace(evts):
        tranches = [0.0] * nb_tranches
        for e in evts:
            if avec_obv:
                valeur = _num(e.get("obv_for_net"))
            else:
                valeur = _xg(e) if _type(e) == "Shot" else None
            if valeur is not None:
                tranches[min(_minute(e) // TRANCHE, nb_tranches - 1)] += valeur
        return [round(v, 3) for v in tranches]

    return {
        "xg_cumule_pour": _cumul(tirs_pour, nb_minutes),
        "xg_cumule_contre": _cumul(tirs_contre, nb_minutes),
        "buts": buts,
        "tranche_minutes": TRANCHE,
        # OBV si StatsBomb le fournit pour ce match, sinon repli sur l'xG.
        "menace_source": "obv" if avec_obv else "xg",
        "menace_pour": menace(nous),
        "menace_contre": menace(eux),
    }


def _formation(valeur):
    if isinstance(valeur, int) and not isinstance(valeur, bool) and valeur > 0:
        return str(valeur)
    if isinstance(valeur, str) and valeur.isdigit():
        return valeur
    return None


def _noms(nous, compositions, equipe):
    """{id: {"nom", "numero"}} : compositions d'abord, événements en repli."""
    noms = {}
    for bloc in compositions if isinstance(compositions, list) else []:
        bloc = _d(bloc)
        if _ident(bloc.get("team_id")) != equipe:
            continue
        for entree in bloc.get("lineup") if isinstance(bloc.get("lineup"), list) else []:
            entree = _d(entree)
            ident = _ident(entree.get("player_id"))
            if ident is not None:
                noms[ident] = {"nom": entree.get("player_name") if isinstance(entree.get("player_name"), str) else "",
                               "numero": _ident(entree.get("jersey_number"))}
    for e in nous:
        ident, nom = _joueur(e)
        if ident is not None and not noms.get(ident, {}).get("nom"):
            noms.setdefault(ident, {"nom": "", "numero": None})["nom"] = nom
        remplacant = _d(_d(e.get("substitution")).get("replacement"))
        ident = _ident(remplacant.get("id"))
        if ident is not None and not noms.get(ident, {}).get("nom"):
            nom = remplacant.get("name") if isinstance(remplacant.get("name"), str) else ""
            noms.setdefault(ident, {"nom": "", "numero": None})["nom"] = nom
    return noms


def _titulaires(nous, compositions, equipe):
    """[(id, nom, poste, numéro, position_id)] du XI de départ : « Starting XI »
    des événements, sinon les compositions (`start_reason`). `position_id` est
    l'identifiant StatsBomb du poste (1 = gardien … 25), None s'il manque."""
    for e in nous:
        if _type(e) != "Starting XI":
            continue
        tactique = _d(e.get("tactics"))
        lineup = tactique.get("lineup") if isinstance(tactique.get("lineup"), list) else []
        xi = []
        for entree in lineup:
            entree = _d(entree)
            ident, nom = _joueur(entree)
            if ident is not None:
                poste = _d(entree.get("position"))
                xi.append((ident, nom, _nom(poste), _ident(entree.get("jersey_number")),
                           _ident(poste.get("id"))))
        return _formation(tactique.get("formation")), xi
    xi = []
    for bloc in compositions if isinstance(compositions, list) else []:
        bloc = _d(bloc)
        if _ident(bloc.get("team_id")) != equipe:
            continue
        for entree in bloc.get("lineup") if isinstance(bloc.get("lineup"), list) else []:
            entree = _d(entree)
            postes = [p for p in _liste(entree.get("positions")) if isinstance(p, dict)]
            depart = next((p for p in postes if p.get("start_reason") == "Starting XI"), None)
            ident = _ident(entree.get("player_id"))
            if depart is not None and ident is not None:
                nom = entree.get("player_name") if isinstance(entree.get("player_name"), str) else ""
                xi.append((ident, nom, depart.get("position") if isinstance(depart.get("position"), str) else None,
                           _ident(entree.get("jersey_number")), _ident(depart.get("position_id"))))
    return None, xi


def _minutes(nous, titulaires, fin):
    """{id: minutes jouées} : titulaires dès 0, remplacements, expulsions,
    sorties et retours (« Player Off » / « Player On »), prolongations comprises."""
    entre_a = {ident: 0.0 for ident in titulaires}
    total = defaultdict(float)

    def sortir(ident, t):
        if ident in entre_a:
            total[ident] += max(0.0, t - entre_a.pop(ident))

    def entrer(ident, t):
        if ident is not None and ident not in entre_a:
            entre_a[ident] = t
            total.setdefault(ident, 0.0)

    for e in nous:
        t, (ident, _) = _temps(e), _joueur(e)
        nature = _type(e)
        if nature == "Substitution":
            sortir(ident, t)
            entrer(_ident(_d(_d(e.get("substitution")).get("replacement")).get("id")), t)
        elif nature == "Player Off":
            sortir(ident, t)
        elif nature == "Player On":
            entrer(ident, t)
        elif nature in ("Bad Behaviour", "Foul Committed"):
            bloc = _d(e.get("bad_behaviour" if nature == "Bad Behaviour" else "foul_committed"))
            if _nom(bloc.get("card")) in EXPULSION:
                sortir(ident, t)
    for ident in list(entre_a):
        sortir(ident, fin)
    return {ident: int(round(v)) for ident, v in total.items()}


def _systeme(nous, minutes, formation, xi):
    return {
        "formation_depart": formation,
        "changements": [{"minute": _minute(e), "formation": _formation(_d(e.get("tactics")).get("formation"))}
                        for e in nous if _type(e) == "Tactical Shift"],
        "xi": [{"id": ident, "nom": nom, "poste": poste, "numero": numero, "position_id": position_id}
               for ident, nom, poste, numero, position_id in xi],
        "minutes_totales": max(minutes.values(), default=0),
    }


def _attaque(nous, eux):
    passes = reussies = centres = progressives_passes = progressives_conduites = 0
    dernier_tiers = surface = 0
    grille = _grille()
    couloirs = {"gauche": 0, "axe": 0, "droite": 0}
    for e in nous:
        nature = _type(e)
        if nature == "Pass":
            passe = _d(e.get("pass"))
            passes += 1
            if passe.get("outcome") is None:
                reussies += 1
            if passe.get("cross") is True and _nom(passe.get("type")) not in PHASES_ARRETEES:
                centres += 1
        trajet = _avancee(e)
        if trajet is None:
            continue
        depart, arrivee = trajet
        if arrivee[0] >= DERNIER_TIERS > depart[0]:
            dernier_tiers += 1
        if dans_surface(*arrivee) and not dans_surface(*depart):
            surface += 1
        if _est_progressive(e):
            if nature == "Pass":
                progressives_passes += 1
            else:
                progressives_conduites += 1
            grille[_case(arrivee)] += 1
            couloirs[_couloir(arrivee[1])] += 1
    passes_adverses = sum(1 for e in eux if _type(e) == "Pass")
    grille_tirs = [0.0] * (COLONNES * RANGEES)
    for e in nous:
        loc = _loc(e.get("location"))
        if _type(e) == "Shot" and loc is not None:
            grille_tirs[_case(loc)] += _xg(e)
    return {
        # xG des tirs par zone de départ (« contre » : dans la ligne de l'autre équipe).
        "grille_tirs_xg": [round(v, 3) for v in grille_tirs],
        # Part des passes du match : approximation classique de la possession.
        "possession": _r(passes / (passes + passes_adverses)) if passes + passes_adverses else None,
        "passes": passes,
        "passes_reussies": reussies,
        "passes_progressives": progressives_passes,
        "conduites_progressives": progressives_conduites,
        "entrees_dernier_tiers": dernier_tiers,
        "entrees_surface": surface,
        "centres": centres,
        "grille_progression": grille,  # arrivées des actions progressives
        "couloirs": couloirs,
    }


def _action_ppda(e):
    nature = _type(e)
    if nature == "Duel":
        duel = _d(e.get("duel"))
        return _nom(duel.get("type")) == "Tackle" or _nom(duel.get("outcome")) in DUEL_GAGNE
    return nature in ("Interception", "Foul Committed")


def _defense(nous, eux):
    passes_adverses = 0
    for e in eux:
        loc = _loc(e.get("location"))
        if (_type(e) == "Pass" and loc is not None and loc[0] <= ZONE_PPDA
                and _nom(_d(e.get("pass")).get("type")) not in PHASES_ARRETEES):
            passes_adverses += 1
    actions = 0
    hauteurs = []
    recuperations = recuperations_hautes = 0
    grille_recuperations, grille_pertes = _grille(), _grille()
    for e in nous:
        nature, loc = _type(e), _loc(e.get("location"))
        if loc is None:
            continue
        if _action_ppda(e) and loc[0] >= 120 - ZONE_PPDA:
            actions += 1
        if nature in ACTIONS_DEFENSIVES:
            hauteurs.append(loc[0])
        if nature == "Ball Recovery" and _d(e.get("ball_recovery")).get("recovery_failure") is not True:
            recuperations += 1
            grille_recuperations[_case(loc)] += 1
            if loc[0] >= RECUPERATION_HAUTE:
                recuperations_hautes += 1
        if nature in PERTES or (nature == "Dribble"
                                and _nom(_d(e.get("dribble")).get("outcome")) == "Incomplete"):
            grille_pertes[_case(loc)] += 1
    return {
        "ppda": _r(passes_adverses / actions, 2) if actions else None,
        "ppda_passes_adverses": passes_adverses,
        "ppda_actions": actions,
        "pressions": sum(1 for e in nous if _type(e) == "Pressure"),
        "contre_pressing": sum(1 for e in nous if e.get("counterpress") is True),
        "hauteur_actions_defensives": _r(sum(hauteurs) / len(hauteurs), 1) if hauteurs else None,
        "recuperations": recuperations,
        "recuperations_hautes": recuperations_hautes,
        "pertes": sum(grille_pertes),
        "grille_recuperations": grille_recuperations,
        "grille_pertes": grille_pertes,
    }


def _issues_cpa(tirs):
    blocs = {}
    for cle, motif in (("corner", "From Corner"), ("coup_franc", "From Free Kick")):
        choisis = [e for e in tirs if _nom(e.get("play_pattern")) == motif]
        blocs[cle] = {"tirs": len(choisis), "xg": _r(sum(_xg(e) for e in choisis)),
                      "buts": sum(1 for e in choisis if _issue(e) == "Goal")}
    return blocs


def _cpa(nous, eux):
    corners = coups_francs = 0
    tireurs = {}
    chutes = _grille()  # arrivée des passes de corner
    for e in nous:
        nature = _type(e)
        if nature == "Pass":
            sorte = _nom(_d(e.get("pass")).get("type"))
            arrivee = _loc(_d(e.get("pass")).get("end_location"))
            if sorte == "Corner" and arrivee is not None:
                chutes[_case(arrivee)] += 1
        elif nature == "Shot":
            sorte = _nom(_tir(e).get("type"))
        else:
            continue
        cle = {"Corner": "corners", "Free Kick": "coups_francs"}.get(sorte)
        if cle is None or (nature == "Shot" and cle == "corners"):
            continue
        if cle == "corners":
            corners += 1
        else:
            coups_francs += 1
        ident, nom = _joueur(e)
        if ident is not None:
            fiche = tireurs.setdefault(ident, {"id": ident, "nom": nom, "corners": 0, "coups_francs": 0})
            fiche[cle] += 1
    return {
        "corners": corners,
        "coups_francs": coups_francs,
        "pour": _issues_cpa([e for e in nous if _type(e) == "Shot"]),
        "contre": _issues_cpa([e for e in eux if _type(e) == "Shot"]),
        "tireurs": sorted(tireurs.values(),
                          key=lambda t: (-(t["corners"] + t["coups_francs"]), t["nom"], t["id"])),
        "chutes_corners": chutes,
    }


def _gardien(eux):
    cadres = [e for e in eux if _type(e) == "Shot" and _issue_minuscule(e) in CADRES]
    psxg = [v for v in (_num(_tir(e).get("shot_execution_xg")) for e in cadres) if v is not None]
    buts = sum(1 for e in cadres if _issue(e) == "Goal")
    total = sum(psxg) if psxg else None
    return {
        "tirs_cadres_subis": len(cadres),
        "arrets": sum(1 for e in cadres if _issue_minuscule(e) in ARRETS),
        "buts_encaisses_sur_tir": buts,
        # PSxG (xG après tir) : seulement si StatsBomb le fournit.
        "psxg": _r(total),
        "psxg_moins_buts": _r(total - buts) if total is not None else None,
    }


def _joueurs(nous, minutes, noms, avec_obv, par_joueur_360=None):
    """`par_joueur_360` : {id: compteurs 360}, None sans 360 (compteurs à None)."""
    stats = {}

    def fiche(ident):
        return stats.setdefault(ident, {"obv_cree": 0.0, "obv_concede": 0.0, "xg": 0.0,
                                        "actions_progressives": 0})

    for e in nous:
        ident, _ = _joueur(e)
        if ident is None:
            continue
        f = fiche(ident)
        f["obv_cree"] += _num(e.get("obv_for_net")) or 0.0
        f["obv_concede"] += _num(e.get("obv_against_net")) or 0.0
        if _type(e) == "Shot":
            f["xg"] += _xg(e)
        if _est_progressive(e):
            f["actions_progressives"] += 1
    liste = []
    for ident in set(minutes) | set(stats):
        f = fiche(ident)
        liste.append({
            "id": ident,
            "nom": noms.get(ident, {}).get("nom") or "",
            "numero": noms.get(ident, {}).get("numero"),
            "minutes": minutes.get(ident, 0),
            "obv_cree": _r(f["obv_cree"]) if avec_obv else None,
            "obv_concede": _r(f["obv_concede"]) if avec_obv else None,
            "xg": _r(f["xg"]),
            "actions_progressives": f["actions_progressives"],
            "passes_5_elimines": (None if par_joueur_360 is None
                                  else par_joueur_360.get(ident, {}).get("passes_5_elimines", 0)),
            "receptions_entre_lignes": (None if par_joueur_360 is None
                                        else par_joueur_360.get(ident, {}).get("receptions_entre_lignes", 0)),
        })
    liste.sort(key=lambda j: (-j["minutes"], j["nom"], j["id"]))
    return {"obv_disponible": avec_obv, "liste": liste}


def _defenseurs_cote_but(image):
    """Adversaires (gardien exclu) plus près du but que l'acteur, d'après
    `freeze_frame` ; None sans acteur ou sans joueur visible."""
    joueurs = [j for j in _liste(image.get("freeze_frame")) if isinstance(j, dict)]
    acteur = next((j for j in joueurs if j.get("actor") is True and _loc(j.get("location"))), None)
    if acteur is None:
        return None
    x_acteur = _loc(acteur["location"])[0]
    return float(sum(1 for j in joueurs
                     if j.get("teammate") is False and j.get("keeper") is not True
                     and _loc(j.get("location")) and _loc(j.get("location"))[0] > x_acteur))


def _trois_cent_soixante(frames, nous):
    """Bloc 360, ou None sans 360. Une image sans zone visible, sans acteur ou
    vide est comptée « inexploitable », jamais une erreur."""
    if not isinstance(frames, list):
        return None
    par_id = {e.get("id"): e for e in nous if isinstance(e.get("id"), str)}
    images = inexploitables = casse_ligne = receptions = dans_l_espace = 0
    cote_but = []
    for image in frames:
        if not isinstance(image, dict):
            inexploitables += 1
            continue
        uuid = image.get("event_uuid")
        e = par_id.get(uuid) if isinstance(uuid, str) else None
        if e is None:  # action de l'autre équipe, événement inconnu ou identifiant illisible
            continue
        images += 1
        if _type(e) == "Pass" and image.get("line_breaking_pass") is True:
            casse_ligne += 1
        if _type(e) == "Ball Receipt*":
            receptions += 1
            if image.get("ball_receipt_in_space") is True:
                dans_l_espace += 1
        nombre = _num(image.get("num_defenders_on_goal_side_of_actor"))
        if nombre is None:
            nombre = _defenseurs_cote_but(image)
        if nombre is None:
            inexploitables += 1
        else:
            cote_but.append(nombre)
    return {
        "images": images,
        "images_inexploitables": inexploitables,
        "passes_cassant_une_ligne": casse_ligne,
        "receptions_avec_image": receptions,
        "receptions_dans_l_espace": dans_l_espace,
        "defenseurs_cote_but_moyen": _r(sum(cote_but) / len(cote_but), 2) if cote_but else None,
    }


# ── Version 2 : quand il est dangereux ───────────────────────────────────────
def _est_but(e):
    return (_type(e) == "Shot" and _issue(e) == "Goal") or _type(e) == "Own Goal For"


def _etat_score(cle, buts_pour, buts_contre):
    """« mene », « egalite » ou « est_mene » juste AVANT l'instant `cle`."""
    pour = sum(1 for b in buts_pour if b < cle)
    contre = sum(1 for b in buts_contre if b < cle)
    if pour > contre:
        return "mene"
    return "est_mene" if pour < contre else "egalite"


def _tranche_15(e):
    """Indice dans TRANCHES_15. Le temps additionnel reste dans la dernière
    tranche de sa mi-temps ; prolongations regroupées ; None sinon."""
    periode, minute = _periode(e), _minute(e)
    if periode == 1:
        return min(minute, 44) // 15
    if periode == 2:
        return 3 + min(max(minute - 45, 0), 44) // 15
    if periode in (3, 4):
        return 6
    return None


def _moyennes_par_etat(etats, nombre):
    """Ajoute « hauteur » (moyenne) à chaque état ; la somme reste pour les cumuls."""
    for bloc in etats.values():
        bloc["hauteur"] = _r(bloc["somme_hauteur"] / bloc[nombre], 1) if bloc[nombre] else None
        bloc["somme_hauteur"] = _r(bloc["somme_hauteur"])
    return etats


def _danger(nous, eux, buts_pour, buts_contre):
    nb = len(TRANCHES_15)
    xg = {"pour": [0.0] * nb, "contre": [0.0] * nb}
    buts = {"pour": [0] * nb, "contre": [0] * nb}
    for cote, evts in (("pour", nous), ("contre", eux)):
        for e in evts:
            tranche = _tranche_15(e)
            if tranche is None:
                continue
            if _type(e) == "Shot":
                xg[cote][tranche] += _xg(e)
            if _est_but(e):
                buts[cote][tranche] += 1
    hauteurs = {etat: {"actions": 0, "somme_hauteur": 0.0} for etat in ETATS_SCORE}
    for e in nous:
        loc = _loc(e.get("location"))
        if _type(e) in ACTIONS_DEFENSIVES and loc is not None:
            bloc = hauteurs[_etat_score(_cle(e), buts_pour, buts_contre)]
            bloc["actions"] += 1
            bloc["somme_hauteur"] += loc[0]
    return {
        "tranches": list(TRANCHES_15),
        "xg_pour": [round(v, 3) for v in xg["pour"]],
        "xg_contre": [round(v, 3) for v in xg["contre"]],
        "buts_pour": buts["pour"],
        "buts_contre": buts["contre"],
        # Hauteur moyenne (x, son repère) de ses actions défensives selon le score.
        "hauteur_par_score": _moyennes_par_etat(hauteurs, "actions"),
    }


# ── Version 2 : transitions ──────────────────────────────────────────────────
def _recuperation(e):
    nature = _type(e)
    if nature == "Ball Recovery":
        return _d(e.get("ball_recovery")).get("recovery_failure") is not True
    if nature == "Interception":
        return _nom(_d(e.get("interception")).get("outcome")) in DUEL_GAGNE
    if nature == "Duel":
        duel = _d(e.get("duel"))
        return _nom(duel.get("type")) == "Tackle" and _nom(duel.get("outcome")) in DUEL_GAGNE
    return False


def _perte(e):
    """Perte de balle en jeu : dépossédé, contrôle manqué, dribble raté, passe
    ratée hors phase arrêtée."""
    nature = _type(e)
    if nature in PERTES:
        return True
    if nature == "Dribble":
        return _nom(_d(e.get("dribble")).get("outcome")) == "Incomplete"
    if nature == "Pass":
        passe = _d(e.get("pass"))
        return (_nom(passe.get("outcome")) in PASSE_RATEE
                and _nom(passe.get("type")) not in PHASES_ARRETEES)
    return False


def _suit(cle, debuts):
    """Vrai si `cle` tombe dans les 15 s qui suivent l'un des `debuts` (même période)."""
    periode, secondes = cle
    return any(p == periode and 0 <= secondes - s <= FENETRE_TRANSITION for p, s in debuts)


def _apres_recuperation(evts):
    debuts = [_cle(e) for e in evts if _recuperation(e)]
    tirs = [e for e in evts if _type(e) == "Shot" and _suit(_cle(e), debuts)]
    return {"recuperations": len(debuts), "tirs": len(tirs),
            "xg": _r(sum(_xg(e) for e in tirs)),
            "buts": sum(1 for e in tirs if _issue(e) == "Goal")}


def _transitions(nous, eux):
    """Tirs dans les 15 s qui suivent une récupération (« pour » : les siennes ;
    « contre » : celles de l'adversaire), et zones de ses pertes suivies d'un
    tir adverse dans les 15 s. Fenêtre de temps seule, dans la même période :
    chaque tir n'est compté qu'une fois."""
    tirs_adverses = [_cle(e) for e in eux if _type(e) == "Shot"]
    grille = _grille()
    for e in nous:
        loc = _loc(e.get("location"))
        if loc is None or not _perte(e):
            continue
        periode, secondes = _cle(e)
        if any(p == periode and 0 <= s - secondes <= FENETRE_TRANSITION for p, s in tirs_adverses):
            grille[_case(loc)] += 1
    return {
        "fenetre_secondes": FENETRE_TRANSITION,
        "pour": _apres_recuperation(nous),
        "contre": _apres_recuperation(eux),
        "pertes_vers_tir": sum(grille),
        "grille_pertes_vers_tir": grille,
    }


# ── Version 2 : 360 (seulement s'il est là) ─────────────────────────────────
def _images_par_evenement(frames):
    """{event_uuid: image} ; ce qui n'est pas une image à identifiant texte est ignoré."""
    images = {}
    for image in frames:
        if isinstance(image, dict) and isinstance(image.get("event_uuid"), str):
            images[image["event_uuid"]] = image
    return images


def _image(images, e):
    ident = e.get("id")
    return images.get(ident) if isinstance(ident, str) else None


def _visibles(image):
    """[(position, partenaire, gardien, acteur)] des joueurs visibles lisibles.
    Repère de l'ACTEUR : son équipe attaque vers x = 120."""
    joueurs = []
    for j in _liste(image.get("freeze_frame")) if isinstance(image, dict) else []:
        if not isinstance(j, dict) or not isinstance(j.get("teammate"), bool):
            continue
        loc = _loc(j.get("location"))
        if loc is not None:
            joueurs.append((loc, j["teammate"], j.get("keeper") is True, j.get("actor") is True))
    return joueurs


def _adversaires(joueurs, avec_gardien=False):
    return [loc for loc, partenaire, gardien, _ in joueurs
            if not partenaire and (avec_gardien or not gardien)]


def _partenaires(joueurs):
    """Partenaires de l'acteur, sans l'acteur ni le gardien."""
    return [loc for loc, partenaire, gardien, acteur in joueurs
            if partenaire and not gardien and not acteur]


def dans_six_metres(x, y):
    return x >= SIX_METRES[0] and SIX_METRES[1] <= y <= SIX_METRES[2]


def _longueur(e):
    passe = _d(e.get("pass"))
    longueur = _num(passe.get("length"))
    if longueur is not None:
        return longueur
    depart, arrivee = _loc(e.get("location")), _loc(passe.get("end_location"))
    if depart is None or arrivee is None:
        return None
    return math.hypot(arrivee[0] - depart[0], arrivee[1] - depart[1])


def _relance(nous, images):
    """Ses passes en jeu courant parties de son tiers (x < 40), rangées selon
    le nombre d'adversaires visibles à moins de 5 m du passeur (0, 1, 2+)."""
    paliers = {p: {"passes": 0, "reussies": 0, "vers_l_avant": 0} for p in PRESSIONS}
    for e in nous:
        if _type(e) != "Pass":
            continue
        passe = _d(e.get("pass"))
        depart = _loc(e.get("location"))
        if depart is None or depart[0] >= TIERS or _nom(passe.get("type")) in PHASES_ARRETEES:
            continue
        joueurs = _visibles(_image(images, e))
        if not joueurs:
            continue
        proches = sum(1 for loc in _adversaires(joueurs, avec_gardien=True)
                      if math.hypot(loc[0] - depart[0], loc[1] - depart[1]) < RAYON_PRESSION)
        bloc = paliers[PRESSIONS[min(proches, 2)]]
        bloc["passes"] += 1
        if passe.get("outcome") is None:
            bloc["reussies"] += 1
        arrivee = _loc(passe.get("end_location"))
        if arrivee is not None and arrivee[0] > depart[0]:
            bloc["vers_l_avant"] += 1
    return {"passes": sum(b["passes"] for b in paliers.values()), "par_pression": paliers}


def _gardien_sous_pression(nous, images):
    """Passes de son gardien (poste « Goalkeeper » de l'événement), selon
    qu'au moins 3 adversaires de champ visibles sont dans son tiers ;
    longues = 32 m et plus."""
    blocs = {cle: {"passes": 0, "longues": 0, "longues_reussies": 0}
             for cle in ("sous_pression", "hors_pression")}
    for e in nous:
        if _type(e) != "Pass" or _nom(e.get("position")) != "Goalkeeper":
            continue
        joueurs = _visibles(_image(images, e))
        if not joueurs:
            continue
        dans_le_tiers = sum(1 for loc in _adversaires(joueurs) if loc[0] < TIERS)
        bloc = blocs["sous_pression" if dans_le_tiers >= ADVERSAIRES_DANS_LE_TIERS else "hors_pression"]
        bloc["passes"] += 1
        longueur = _longueur(e)
        if longueur is not None and longueur >= LONG_BALLON:
            bloc["longues"] += 1
            if _d(e.get("pass")).get("outcome") is None:
                bloc["longues_reussies"] += 1
    return blocs


def _ligne_defensive(eux, images, buts_pour, buts_contre):
    """Hauteur de SA ligne défensive quand l'adversaire a le ballon : moyenne
    des x (son repère) de ses 4 joueurs de champ visibles les plus reculés.
    Image ignorée s'il en voit moins de 4."""
    etats = {etat: {"images": 0, "somme_hauteur": 0.0} for etat in ETATS_SCORE}
    for e in eux:
        joueurs = _visibles(_image(images, e))
        # Action adverse : ses joueurs sont « teammate: false », dans le repère adverse.
        siens = sorted(120.0 - loc[0] for loc in _adversaires(joueurs))
        if len(siens) < 4:
            continue
        bloc = etats[_etat_score(_cle(e), buts_pour, buts_contre)]
        bloc["images"] += 1
        bloc["somme_hauteur"] += sum(siens[:4]) / 4
    return _moyennes_par_etat(etats, "images")


def _corners_360(evts, images):
    """Corners tirés par `evts` : partenaires (attaquants) et adversaires
    (défenseurs) de champ visibles dans les 6 m et dans la surface au moment
    du corner. Sommes : la moyenne se fait sur le nombre de corners."""
    bloc = {"corners": 0, "attaquants_6m": 0, "defenseurs_6m": 0,
            "attaquants_surface": 0, "defenseurs_surface": 0}
    for e in evts:
        if _type(e) != "Pass" or _nom(_d(e.get("pass")).get("type")) != "Corner":
            continue
        joueurs = _visibles(_image(images, e))
        if not joueurs:
            continue
        bloc["corners"] += 1
        for role, positions in (("attaquants", _partenaires(joueurs)),
                                ("defenseurs", _adversaires(joueurs))):
            bloc[f"{role}_6m"] += sum(1 for x, y in positions if dans_six_metres(x, y))
            bloc[f"{role}_surface"] += sum(1 for x, y in positions if dans_surface(x, y))
    return bloc


def _joueurs_360(nous, images):
    """{id: passes réussies qui laissent au moins 5 adversaires de champ
    visibles derrière le ballon (entre le départ et l'arrivée), réceptions
    réussies entre les lignes}."""
    stats = {}
    for e in nous:
        ident, _ = _joueur(e)
        depart = _loc(e.get("location"))
        joueurs = _visibles(_image(images, e))
        if ident is None or depart is None or not joueurs:
            continue
        adversaires = _adversaires(joueurs)
        nature = _type(e)
        if nature == "Pass":
            passe = _d(e.get("pass"))
            arrivee = _loc(passe.get("end_location"))
            if passe.get("outcome") is not None or arrivee is None:
                continue
            elimines = sum(1 for loc in adversaires if depart[0] < loc[0] < arrivee[0])
            if elimines >= ELIMINES_MIN:
                stats.setdefault(ident, {"passes_5_elimines": 0, "receptions_entre_lignes": 0})
                stats[ident]["passes_5_elimines"] += 1
        elif nature == "Ball Receipt*" and _d(e.get("ball_receipt")).get("outcome") is None:
            devant = sum(1 for loc in adversaires if loc[0] > depart[0])
            derriere = sum(1 for loc in adversaires if loc[0] < depart[0])
            if (depart[0] >= TIERS and devant >= ENTRE_LIGNES_DEVANT
                    and derriere >= ENTRE_LIGNES_DERRIERE):
                stats.setdefault(ident, {"passes_5_elimines": 0, "receptions_entre_lignes": 0})
                stats[ident]["receptions_entre_lignes"] += 1
    return stats


def _complements_360(nous, eux, images, par_joueur, buts_pour, buts_contre):
    return {
        "relance": _relance(nous, images),
        "gardien_sous_pression": _gardien_sous_pression(nous, images),
        "ligne_defensive_par_score": _ligne_defensive(eux, images, buts_pour, buts_contre),
        "corners_pour": _corners_360(nous, images),
        "corners_contre": _corners_360(eux, images),
        "passes_5_elimines": sum(s["passes_5_elimines"] for s in par_joueur.values()),
        "receptions_entre_lignes": sum(s["receptions_entre_lignes"] for s in par_joueur.values()),
    }


# ── Point d'entrée ───────────────────────────────────────────────────────────
def calculer(evenements, compositions, frames_360, equipe_sb_id):
    """Indicateurs du match vus par `equipe_sb_id` (dict JSON). `frames_360`
    vaut None quand le 360 n'est pas disponible : bloc « trois_cent_soixante »
    à None, compteurs 360 des joueurs à None."""
    evts = [e for e in (evenements if isinstance(evenements, list) else [])
            if isinstance(e, dict) and e.get("period") != PERIODE_TIRS_AU_BUT]
    nous = [e for e in evts if _equipe(e) == equipe_sb_id]
    eux = [e for e in evts if _equipe(e) is not None and _equipe(e) != equipe_sb_id]
    fin = max((_temps(e) for e in evts), default=0.0)
    avec_obv = any(_num(e.get("obv_for_net")) is not None for e in evts)
    buts_pour = [_cle(e) for e in nous if _est_but(e)]
    buts_contre = [_cle(e) for e in eux if _est_but(e)]
    images = _images_par_evenement(frames_360) if isinstance(frames_360, list) else None
    par_joueur_360 = _joueurs_360(nous, images) if images is not None else None

    formation, xi = _titulaires(nous, compositions, equipe_sb_id)
    minutes = _minutes(nous, [ident for ident, *_ in xi], fin)
    noms = _noms(nous, compositions, equipe_sb_id)
    trois_cent_soixante = _trois_cent_soixante(frames_360, nous)
    if trois_cent_soixante is not None:
        trois_cent_soixante.update(_complements_360(nous, eux, images, par_joueur_360,
                                                    buts_pour, buts_contre))
    return {
        "version_calcul": VERSION_CALCUL,
        "resultat": _resultat(nous, eux),
        "deroule": _deroule(nous, eux, fin, avec_obv),
        "danger": _danger(nous, eux, buts_pour, buts_contre),
        "systeme": _systeme(nous, minutes, formation, xi),
        "attaque": _attaque(nous, eux),
        "defense": _defense(nous, eux),
        "transitions": _transitions(nous, eux),
        "cpa": _cpa(nous, eux),
        "gardien": _gardien(eux),
        "joueurs": _joueurs(nous, minutes, noms, avec_obv, par_joueur_360),
        "trois_cent_soixante": trois_cent_soixante,
    }
