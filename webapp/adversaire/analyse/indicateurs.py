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
"""
import math
from collections import defaultdict
from itertools import accumulate

VERSION_CALCUL = 1

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
    }


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
    """[(id, nom, poste, numéro)] du XI de départ : « Starting XI » des
    événements, sinon les compositions (`start_reason`)."""
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
                xi.append((ident, nom, _nom(entree.get("position")), _ident(entree.get("jersey_number"))))
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
                           _ident(entree.get("jersey_number"))))
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
        "xi": [{"id": ident, "nom": nom, "poste": poste, "numero": numero}
               for ident, nom, poste, numero in xi],
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
    return {
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
    for e in nous:
        nature = _type(e)
        if nature == "Pass":
            sorte = _nom(_d(e.get("pass")).get("type"))
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


def _joueurs(nous, minutes, noms, avec_obv):
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
        e = par_id.get(image.get("event_uuid"))
        if e is None:  # action de l'autre équipe, ou événement inconnu
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


# ── Point d'entrée ───────────────────────────────────────────────────────────
def calculer(evenements, compositions, frames_360, equipe_sb_id):
    """Indicateurs du match vus par `equipe_sb_id` (dict JSON). `frames_360`
    vaut None quand le 360 n'est pas disponible : bloc « trois_cent_soixante » à None."""
    evts = [e for e in (evenements if isinstance(evenements, list) else [])
            if isinstance(e, dict) and e.get("period") != PERIODE_TIRS_AU_BUT]
    nous = [e for e in evts if _equipe(e) == equipe_sb_id]
    eux = [e for e in evts if _equipe(e) is not None and _equipe(e) != equipe_sb_id]
    fin = max((_temps(e) for e in evts), default=0.0)
    avec_obv = any(_num(e.get("obv_for_net")) is not None for e in evts)

    formation, xi = _titulaires(nous, compositions, equipe_sb_id)
    minutes = _minutes(nous, [ident for ident, *_ in xi], fin)
    noms = _noms(nous, compositions, equipe_sb_id)
    return {
        "version_calcul": VERSION_CALCUL,
        "resultat": _resultat(nous, eux),
        "deroule": _deroule(nous, eux, fin, avec_obv),
        "systeme": _systeme(nous, minutes, formation, xi),
        "attaque": _attaque(nous, eux),
        "defense": _defense(nous, eux),
        "cpa": _cpa(nous, eux),
        "gardien": _gardien(eux),
        "joueurs": _joueurs(nous, minutes, noms, avec_obv),
        "trois_cent_soixante": _trois_cent_soixante(frames_360, nous),
    }
