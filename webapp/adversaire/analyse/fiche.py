"""Synthèse des derniers matchs de l'adversaire (étape 3c-1), calcul PUR.

`construire(elements)` reçoit, du plus récent au plus ancien, au plus 5
éléments `{"match": pk, "date": "AAAA-MM-JJ", "a_domicile": bool,
"rencontre": nom de l'équipe rencontrée, "score_pour": int ou None,
"score_contre": int ou None (score officiel de `Match`, vu par l'adversaire),
"pour": valeurs de l'adversaire, "contre": valeurs de l'équipe rencontrée}`
(`IndicateursMatchEquipe.valeurs`, None si absentes ou périmées) et rend un
dict JSON prêt à afficher. Ni Django, ni réseau : appelé à l'IMPORT et par
`recalculer_indicateurs`, jamais pendant une requête web.

Règles :
- un match ne compte que si ses DEUX lignes sont là (« calculée sur n matchs
  sur 5 ») ;
- SOMMES AVANT RATIOS : PPDA, xG par tir, possession, réussites, hauteurs…
  se calculent sur les totaux, jamais en moyennant des ratios ;
- déterministe, aucune erreur sur une clé absente (None = « non disponible ») ;
- `VERSION_FICHE` augmente à chaque changement de règle : la page signale
  alors une fiche périmée (`manage.py recalculer_indicateurs`).
"""
import math
from bisect import bisect_right
from collections import Counter

from .indicateurs import COLONNES, ETATS_SCORE, RANGEES, TRANCHES_15

# 2 (étape 3c-2) : grilles « contre » retournées dans le repère de l'adversaire,
# bornes de légende des grilles, largeurs des barres (tranches de 15 min, couloirs).
# 3 (étape 3c-3) : tendances match par match (`tendances`), écart d'xG en barres.
VERSION_FICHE = 3

SEUIL_PEU_JOUE = 90  # minutes cumulées sur les matchs de la fiche
NB_XI = 11
NB_CLES = 5
NB_TIREURS = 5
NB_NIVEAUX = 5
TAILLE_GRILLE = COLONNES * RANGEES
TITULARISATIONS_ABSENT = 3  # titulaire au moins 3 fois sur les 4 matchs précédents

LIBELLES_MOTIFS = [
    ("jeu_courant", "Jeu courant"),
    ("contre_attaque", "Contre-attaque"),
    ("corner", "Corner"),
    ("coup_franc", "Coup franc"),
    ("penalty", "Penalty"),
    ("touche", "Touche"),
    ("six_metres", "Six mètres"),
    ("gardien", "Relance du gardien"),
    ("engagement", "Engagement"),
    ("csc", "Contre son camp"),
    ("autre", "Autre"),
]
CHAMPS_MOTIF = ("tirs_pour", "buts_pour", "xg_pour", "tirs_contre", "buts_contre", "xg_contre")
LIBELLES_ETATS = {"mene": "Mène", "egalite": "Égalité", "est_mene": "Mené"}
LIBELLES_COULOIRS = {"gauche": "Gauche", "axe": "Axe", "droite": "Droite"}
LIBELLES_PRESSION = [
    ("0", "Aucun adversaire à moins de 5 m"),
    ("1", "1 adversaire à moins de 5 m"),
    ("2+", "2 adversaires ou plus à moins de 5 m"),
]
LIBELLES_GARDIEN = [
    ("sous_pression", "3 adversaires ou plus dans son tiers"),
    ("hors_pression", "Moins de 3 adversaires dans son tiers"),
]

# Place de chaque poste StatsBomb (`position_id`) sur un terrain 120 × 80,
# l'équipe attaquant vers la droite (gauche du joueur = haut, y petit).
POSITIONS = {
    1: (6, 40),     # Goalkeeper
    2: (26, 70),    # Right Back
    3: (20, 54),    # Right Center Back
    4: (18, 40),    # Center Back
    5: (20, 26),    # Left Center Back
    6: (26, 10),    # Left Back
    7: (42, 72),    # Right Wing Back
    8: (42, 8),     # Left Wing Back
    9: (38, 52),    # Right Defensive Midfield
    10: (36, 40),   # Center Defensive Midfield
    11: (38, 28),   # Left Defensive Midfield
    12: (60, 70),   # Right Midfield
    13: (54, 54),   # Right Center Midfield
    14: (54, 40),   # Center Midfield
    15: (54, 26),   # Left Center Midfield
    16: (60, 10),   # Left Midfield
    17: (84, 70),   # Right Wing
    18: (72, 56),   # Right Attacking Midfield
    19: (72, 40),   # Center Attacking Midfield
    20: (72, 24),   # Left Attacking Midfield
    21: (84, 10),   # Left Wing
    22: (96, 50),   # Right Center Forward
    23: (98, 40),   # Striker
    24: (96, 30),   # Left Center Forward
    25: (88, 40),   # Secondary Striker
}
DECALAGES = (0, 8, -8, 16, -16, 24, -24)  # deux joueurs au même poste


# ── Lecture prudente ─────────────────────────────────────────────────────────
def _d(valeur):
    return valeur if isinstance(valeur, dict) else {}


def _liste(valeur):
    return valeur if isinstance(valeur, list) else []


def _num(valeur):
    """Nombre fini (entier gardé entier), ou None."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    if isinstance(valeur, float) and not math.isfinite(valeur):
        return None
    return valeur


def _ident(valeur):
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) and valeur > 0 else None


def _texte(valeur):
    return valeur if isinstance(valeur, str) else ""


def _r(valeur, chiffres=3):
    return None if valeur is None else round(valeur, chiffres)


def _ratio(numerateur, denominateur, chiffres=3):
    return _r(numerateur / denominateur, chiffres) if denominateur else None


def _pct(numerateur, denominateur):
    return _r(100 * numerateur / denominateur, 1) if denominateur else None


def _par_match(total, nombre):
    return _r(total / nombre, 2) if nombre else None


def _val(valeurs, *chemin):
    for cle in chemin:
        valeurs = _d(valeurs).get(cle)
    return valeurs


def _somme(elements, cote, *chemin):
    """Somme des nombres trouvés au `chemin` des valeurs `cote` ; absents ignorés."""
    return sum(v for v in (_num(_val(e[cote], *chemin)) for e in elements) if v is not None)


# ── Grilles 12 × 8 ───────────────────────────────────────────────────────────
def niveaux(grille):
    """Niveau de chaque case : 0 si vide, sinon 1 à 5 selon son RANG parmi les
    cases non vides (quantiles ; ex aequo au rang le plus haut)."""
    non_vides = sorted(v for v in grille if v > 0)
    resultat = []
    for v in grille:
        if v <= 0:
            resultat.append(0)
        elif len(non_vides) == 1:
            resultat.append(NB_NIVEAUX)
        else:
            rang = bisect_right(non_vides, v) - 1
            resultat.append(1 + (NB_NIVEAUX - 1) * rang // (len(non_vides) - 1))
    return resultat


def retourner(grille):
    """Rotation de 180° d'une grille 12 × 8 : la case (rangée r, colonne c)
    devient (7 − r, 11 − c), soit l'indice i → 95 − i. Une grille lue chez
    l'équipe rencontrée (qui attaque vers x = 120 dans SES événements) passe
    ainsi dans le repère de l'adversaire."""
    return list(reversed(grille))


def _grille(elements, cote, *chemin, retourne=False):
    """{"valeurs", "niveaux", "min_non_vide", "max"} : grilles additionnées
    case par case ; `retourne` pour une grille lue chez l'équipe rencontrée.
    Les bornes servent à la légende (« de … à … ») : le gabarit ne calcule rien."""
    total = [0] * TAILLE_GRILLE
    for e in elements:
        grille = _liste(_val(e[cote], *chemin))
        if len(grille) != TAILLE_GRILLE:
            continue
        for i, v in enumerate(grille):
            v = _num(v)
            if v is not None:
                total[i] += v
    total = [round(v, 3) if isinstance(v, float) else v for v in total]
    if retourne:
        total = retourner(total)
    non_vides = [v for v in total if v > 0]
    return {"valeurs": total, "niveaux": niveaux(total),
            "min_non_vide": min(non_vides) if non_vides else None,
            "max": max(non_vides) if non_vides else None}


def largeurs(valeurs):
    """Largeur entière de 0 à 100 de chaque barre, rapportée au maximum de la
    série ; 0 si la valeur est absente ou si le maximum est nul."""
    nombres = [_num(v) for v in valeurs]
    maximum = max((v for v in nombres if v is not None), default=0)
    if maximum <= 0:
        return [0] * len(nombres)
    return [max(0, round(100 * v / maximum)) if v is not None else 0 for v in nombres]


def hauteurs_ecart(valeurs):
    """Barre de chaque écart, de part et d'autre de zéro : `{"hauteur", "classe"}`,
    hauteur entière de 0 à 100 rapportée au plus grand écart en valeur absolue,
    classe `pour` (écart > 0) ou `contre` (écart < 0). Écart absent ou nul :
    hauteur 0, classe None."""
    nombres = [_num(v) for v in valeurs]
    maximum = max((abs(v) for v in nombres if v is not None), default=0)
    barres = []
    for v in nombres:
        if v is None or v == 0 or maximum <= 0:
            barres.append({"hauteur": 0, "classe": None})
        else:
            barres.append({"hauteur": min(100, round(100 * abs(v) / maximum)),
                           "classe": "pour" if v > 0 else "contre"})
    return barres


# ── Résultats et forme ───────────────────────────────────────────────────────
def _bilan(elements):
    n = len(elements)

    def s(*chemin):
        return _somme(elements, "pour", *chemin)

    xg_pour, xg_contre = s("resultat", "xg_pour"), s("resultat", "xg_contre")
    tirs_pour, tirs_contre = s("resultat", "tirs_pour"), s("resultat", "tirs_contre")
    passes = s("attaque", "passes")
    passes_adverses = _somme(elements, "contre", "attaque", "passes")
    return {
        "matchs": n,
        "buts_pour": s("resultat", "buts_pour"),
        "buts_contre": s("resultat", "buts_contre"),
        "xg_pour": _r(xg_pour),
        "xg_contre": _r(xg_contre),
        "xg_pour_par_match": _par_match(xg_pour, n),
        "xg_contre_par_match": _par_match(xg_contre, n),
        "tirs_pour": tirs_pour,
        "tirs_contre": tirs_contre,
        "xg_par_tir_pour": _ratio(xg_pour, tirs_pour),
        "xg_par_tir_contre": _ratio(xg_contre, tirs_contre),
        # PPDA sur plusieurs matchs = Σ passes adverses ÷ Σ actions défensives.
        "ppda": _ratio(s("defense", "ppda_passes_adverses"), s("defense", "ppda_actions"), 2),
        "possession_pct": _pct(passes, passes + passes_adverses),
    }


def _par_motif(elements):
    totaux = {}
    for e in elements:
        for motif, bloc in _d(_val(e["pour"], "resultat", "par_motif")).items():
            cible = totaux.setdefault(motif, dict.fromkeys(CHAMPS_MOTIF, 0))
            for champ in CHAMPS_MOTIF:
                v = _num(_d(bloc).get(champ))
                if v is not None:
                    cible[champ] += v
    ordre = [cle for cle, _ in LIBELLES_MOTIFS]
    libelles = dict(LIBELLES_MOTIFS)
    lignes = []
    for motif in sorted(totaux, key=lambda m: (ordre.index(m) if m in ordre else len(ordre), m)):
        ligne = {"motif": motif, "libelle": libelles.get(motif, motif)}
        ligne.update(totaux[motif])
        ligne["xg_pour"], ligne["xg_contre"] = _r(ligne["xg_pour"]), _r(ligne["xg_contre"])
        lignes.append(ligne)
    return lignes


def _resultats(elements):
    return {
        "total": _bilan(elements),
        "domicile": _bilan([e for e in elements if e.get("a_domicile") is True]),
        "exterieur": _bilan([e for e in elements if e.get("a_domicile") is not True]),
        "par_motif": _par_motif(elements),
    }


# ── Tendances match par match (étape 3c-3) ───────────────────────────────────
def _score(valeur):
    return valeur if isinstance(valeur, int) and not isinstance(valeur, bool) and valeur >= 0 else None


def _issue(pour, contre):
    """V / N / D d'après le score officiel, None s'il est inconnu (même règle
    que `views.ligne_match`, recopiée ici en calcul pur)."""
    if pour is None or contre is None:
        return None
    return "V" if pour > contre else "D" if pour < contre else "N"


def _date_affichee(date):
    """« AAAA-MM-JJ » → « JJ/MM/AAAA » ; tout autre texte est gardé tel quel."""
    date = _texte(date)
    morceaux = date.split("-")
    if len(morceaux) == 3 and all(m.isdigit() for m in morceaux):
        return "/".join(reversed(morceaux))
    return date


def _tendances(elements):
    """Une ligne par match (TOUS les éléments, calculés ou non), du plus récent
    au plus ancien ; barres de l'écart d'xG du plus ancien au plus récent.
    Un match non calculé garde date, lieu, équipe rencontrée et score."""
    matchs = []
    for e in elements:
        score_pour, score_contre = _score(e.get("score_pour")), _score(e.get("score_contre"))
        ligne = {
            "match": _ident(e.get("match")),
            "date": _date_affichee(e.get("date")),
            "a_domicile": e.get("a_domicile") is True,
            "rencontre": _texte(e.get("rencontre")),
            "score_pour": score_pour,
            "score_contre": score_contre,
            "resultat": _issue(score_pour, score_contre),
            "calcule": isinstance(e.get("pour"), dict) and isinstance(e.get("contre"), dict),
            "xg_pour": None, "xg_contre": None, "ecart_xg": None,
            "tirs_pour": None, "tirs_contre": None,
            "possession_pct": None, "ppda": None, "formation": None,
        }
        if ligne["calcule"]:
            resultat = _d(_val(e["pour"], "resultat"))
            xg_pour, xg_contre = _r(_num(resultat.get("xg_pour"))), _r(_num(resultat.get("xg_contre")))
            # Mêmes formules et mêmes arrondis que le bloc Résultats, sur ce seul match.
            bilan = _bilan([e])
            formation = _val(e["pour"], "systeme", "formation_depart")
            ligne.update({
                "xg_pour": xg_pour,
                "xg_contre": xg_contre,
                "ecart_xg": _r(xg_pour - xg_contre) if xg_pour is not None and xg_contre is not None else None,
                "tirs_pour": _num(resultat.get("tirs_pour")),
                "tirs_contre": _num(resultat.get("tirs_contre")),
                "possession_pct": bilan["possession_pct"],
                "ppda": bilan["ppda"],
                "formation": _libelle_formation(formation) if isinstance(formation, str) else None,
            })
        matchs.append(ligne)
    chronologie = list(reversed(matchs))
    barres = hauteurs_ecart([m["ecart_xg"] for m in chronologie])
    return {
        "matchs": matchs,
        "nb_calcules": sum(1 for m in matchs if m["calcule"]),
        "ecarts_xg": [{"date": m["date"], "rencontre": m["rencontre"], "calcule": m["calcule"],
                       "valeur": m["ecart_xg"], "hauteur": b["hauteur"], "classe": b["classe"]}
                      for m, b in zip(chronologie, barres)],
    }


# ── Système, XI probable, minutes, absents ───────────────────────────────────
def _libelle_formation(formation):
    return "-".join(formation) if formation else None


def _fiche_joueur(joueurs, ident, source):
    joueur = joueurs.setdefault(ident, {
        "id": ident, "nom": "", "numero": None, "titularisations": 0,
        "titulaire_dernier": False, "minutes": 0, "matchs": 0, "postes": []})
    # Éléments du plus récent au plus ancien : le nom le plus récent l'emporte.
    if not joueur["nom"]:
        joueur["nom"] = _texte(source.get("nom"))
    if joueur["numero"] is None:
        joueur["numero"] = _ident(source.get("numero"))
    return joueur


def _poste_habituel(postes):
    """(position_id, poste) le plus fréquent ; à égalité, le plus récent."""
    if not postes:
        return None, None
    comptes = Counter(postes)
    return max(comptes, key=lambda p: (comptes[p], -postes.index(p)))


def _placer(xi):
    """Coordonnées (x, y) entières du terrain SVG, d'après `position_id` ;
    None si le poste est inconnu. Deux joueurs au même poste sont décalés."""
    occupees = set()
    for joueur in xi:
        position = POSITIONS.get(joueur["position_id"])
        if position is None:
            joueur["x"] = joueur["y"] = None
            continue
        x, y = position
        for decalage in DECALAGES:
            if 4 <= y + decalage <= 76 and (x, y + decalage) not in occupees:
                y += decalage
                break
        occupees.add((x, y))
        joueur["x"], joueur["y"] = x, y


def _systeme(elements, dernier_calcule=True):
    """`elements` : matchs CALCULÉS seulement. `dernier_calcule` : le match le
    plus récent JOUÉ en fait partie (donc `elements[0]`). Sinon, `elements[0]`
    n'est pas le dernier match : ni absents, ni départage « titulaire au
    dernier match »."""
    formations, premiere = Counter(), {}
    changements, joueurs = [], {}
    for i, e in enumerate(elements):
        systeme = _d(_val(e["pour"], "systeme"))
        formation = systeme.get("formation_depart")
        if isinstance(formation, str) and formation:
            formations[formation] += 1
            premiere.setdefault(formation, i)
        for changement in _liste(systeme.get("changements")):
            changement = _d(changement)
            if isinstance(changement.get("formation"), str) and changement["formation"]:
                changements.append({"date": e.get("date"), "minute": _num(changement.get("minute")),
                                    "formation": _libelle_formation(changement["formation"])})
        for titulaire in _liste(systeme.get("xi")):
            titulaire = _d(titulaire)
            ident = _ident(titulaire.get("id"))
            if ident is None:
                continue
            joueur = _fiche_joueur(joueurs, ident, titulaire)
            joueur["titularisations"] += 1
            joueur["titulaire_dernier"] = joueur["titulaire_dernier"] or (dernier_calcule and i == 0)
            joueur["postes"].append((_ident(titulaire.get("position_id")),
                                     titulaire.get("poste") if isinstance(titulaire.get("poste"), str) else None))
        for ligne in _liste(_val(e["pour"], "joueurs", "liste")):
            ligne = _d(ligne)
            ident = _ident(ligne.get("id"))
            if ident is None:
                continue
            minutes = _num(ligne.get("minutes")) or 0
            joueur = _fiche_joueur(joueurs, ident, ligne)
            joueur["minutes"] += minutes
            if minutes > 0:
                joueur["matchs"] += 1

    ordre = sorted(formations, key=lambda f: (-formations[f], premiere[f]))
    titulaires = sorted((j for j in joueurs.values() if j["titularisations"]),
                        key=lambda j: (-j["titularisations"], not j["titulaire_dernier"],
                                       -j["minutes"], j["id"]))[:NB_XI]
    xi = []
    for joueur in titulaires:
        position_id, poste = _poste_habituel(joueur["postes"])
        xi.append({"id": joueur["id"], "nom": joueur["nom"], "numero": joueur["numero"],
                   "poste": poste, "position_id": position_id,
                   "titularisations": joueur["titularisations"], "minutes": joueur["minutes"]})
    _placer(xi)

    absents = []
    if elements and dernier_calcule:
        a_joue = {_ident(_d(ligne).get("id")) for ligne in _liste(_val(elements[0]["pour"], "joueurs", "liste"))
                  if (_num(_d(ligne).get("minutes")) or 0) > 0}
        comptes = Counter(_ident(_d(t).get("id")) for e in elements[1:]
                          for t in _liste(_val(e["pour"], "systeme", "xi")))
        comptes.pop(None, None)
        for ident, nombre in comptes.items():
            if nombre >= TITULARISATIONS_ABSENT and ident not in a_joue:
                absents.append({"id": ident, "nom": joueurs[ident]["nom"],
                                "numero": joueurs[ident]["numero"],
                                "titularisations_precedentes": nombre})
        absents.sort(key=lambda a: (-a["titularisations_precedentes"], a["nom"], a["id"]))

    return {
        "formation_probable": _libelle_formation(ordre[0]) if ordre else None,
        "formations": [{"formation": _libelle_formation(f), "matchs": formations[f]} for f in ordre],
        "changements": changements,
        "xi_probable": xi,
        "minutes": [{"id": j["id"], "nom": j["nom"], "numero": j["numero"], "minutes": j["minutes"],
                     "matchs": j["matchs"], "titularisations": j["titularisations"]}
                    for j in sorted((j for j in joueurs.values() if j["minutes"] > 0),
                                    key=lambda j: (-j["minutes"], j["nom"], j["id"]))],
        "absents": absents,
        "dernier_match_calcule": bool(elements) and dernier_calcule,
        "matchs_precedents": max(0, len(elements) - 1) if dernier_calcule else 0,
    }


# ── Joueurs clés ─────────────────────────────────────────────────────────────
def _joueurs(elements):
    obv_disponible = any(_val(e["pour"], "joueurs", "obv_disponible") is True for e in elements)
    stats = {}
    for e in elements:
        avec_obv = _val(e["pour"], "joueurs", "obv_disponible") is True
        for ligne in _liste(_val(e["pour"], "joueurs", "liste")):
            ligne = _d(ligne)
            ident = _ident(ligne.get("id"))
            if ident is None:
                continue
            s = stats.setdefault(ident, {
                "id": ident, "nom": "", "minutes": 0, "minutes_obv": 0, "obv_cree": 0.0,
                "obv_concede": 0.0, "xg": 0.0, "actions_progressives": 0,
                "passes_5_elimines": None, "receptions_entre_lignes": None})
            if not s["nom"]:
                s["nom"] = _texte(ligne.get("nom"))
            minutes = _num(ligne.get("minutes")) or 0
            s["minutes"] += minutes
            if avec_obv:
                # OBV par 90 : seulement sur les minutes des matchs qui ont l'OBV.
                s["minutes_obv"] += minutes
                s["obv_cree"] += _num(ligne.get("obv_cree")) or 0
                s["obv_concede"] += _num(ligne.get("obv_concede")) or 0
            s["xg"] += _num(ligne.get("xg")) or 0
            s["actions_progressives"] += _num(ligne.get("actions_progressives")) or 0
            for cle in ("passes_5_elimines", "receptions_entre_lignes"):
                v = _num(ligne.get(cle))
                if v is not None:
                    s[cle] = (s[cle] or 0) + v

    lignes = []
    for s in stats.values():
        if s["minutes"] <= 0:
            continue
        avec_obv = s["minutes_obv"] > 0
        lignes.append({
            "id": s["id"],
            "nom": s["nom"],
            "minutes": s["minutes"],
            "peu_joue": s["minutes"] < SEUIL_PEU_JOUE,
            "obv_cree": _r(s["obv_cree"]) if avec_obv else None,
            "obv_concede": _r(s["obv_concede"]) if avec_obv else None,
            "obv_cree_90": _r(s["obv_cree"] * 90 / s["minutes_obv"]) if avec_obv else None,
            "obv_concede_90": _r(s["obv_concede"] * 90 / s["minutes_obv"]) if avec_obv else None,
            "xg": _r(s["xg"]),
            "xg_90": _r(s["xg"] * 90 / s["minutes"]),
            "actions_progressives": s["actions_progressives"],
            "passes_5_elimines": s["passes_5_elimines"],
            "receptions_entre_lignes": s["receptions_entre_lignes"],
        })
    if obv_disponible:
        lignes.sort(key=lambda j: (j["obv_cree_90"] is None, -(j["obv_cree_90"] or 0),
                                   -j["minutes"], j["nom"], j["id"]))
    else:  # repli signalé à l'écran : xG puis actions progressives
        lignes.sort(key=lambda j: (-j["xg"], -j["actions_progressives"], -j["minutes"],
                                   j["nom"], j["id"]))

    def meilleurs(champ):
        retenus = [j for j in lignes if j[champ]]
        return sorted(retenus, key=lambda j: (-j[champ], -j["minutes"], j["nom"], j["id"]))[:NB_CLES]

    return {
        "obv_disponible": obv_disponible,
        "seuil_peu_joue": SEUIL_PEU_JOUE,
        "cles": lignes[:NB_CLES],
        # 360 : None partout sans 360, donc listes vides.
        "eliminateurs": meilleurs("passes_5_elimines"),
        "entre_les_lignes": meilleurs("receptions_entre_lignes"),
    }


# ── Attaque, défense, danger, transitions, CPA (affichés en 3c-2) ────────────
def _attaque(elements):
    n = len(elements)

    def s(*chemin):
        return _somme(elements, "pour", "attaque", *chemin)

    passes = s("passes")
    passes_adverses = _somme(elements, "contre", "attaque", "passes")
    couloirs = {c: s("couloirs", c) for c in ("gauche", "axe", "droite")}
    total_couloirs = sum(couloirs.values())
    couloirs_pct = {c: _pct(v, total_couloirs) for c, v in couloirs.items()}
    largeurs_couloirs = largeurs(list(couloirs_pct.values()))
    return {
        "possession_pct": _pct(passes, passes + passes_adverses),
        "passes_par_match": _par_match(passes, n),
        "reussite_passes_pct": _pct(s("passes_reussies"), passes),
        "passes_progressives_par_match": _par_match(s("passes_progressives"), n),
        "conduites_progressives_par_match": _par_match(s("conduites_progressives"), n),
        "entrees_dernier_tiers_par_match": _par_match(s("entrees_dernier_tiers"), n),
        "entrees_surface_par_match": _par_match(s("entrees_surface"), n),
        "centres_par_match": _par_match(s("centres"), n),
        "couloirs_pct": couloirs_pct,
        # Barres prêtes à dessiner : largeur rapportée au couloir le plus utilisé.
        "couloirs_barres": [
            {"libelle": LIBELLES_COULOIRS[c], "series": [
                {"classe": "pour", "valeur": couloirs_pct[c], "largeur": largeur}]}
            for c, largeur in zip(couloirs_pct, largeurs_couloirs)],
        "grille_progression": _grille(elements, "pour", "attaque", "grille_progression"),
        "grille_tirs_xg_pour": _grille(elements, "pour", "attaque", "grille_tirs_xg"),
        # Tirs concédés : lus chez l'équipe rencontrée, retournés vers le but de l'adversaire.
        "grille_tirs_xg_contre": _grille(elements, "contre", "attaque", "grille_tirs_xg", retourne=True),
    }


def _defense(elements):
    n = len(elements)

    def s(*chemin):
        return _somme(elements, "pour", *chemin)

    actions = sum(s("danger", "hauteur_par_score", etat, "actions") for etat in ETATS_SCORE)
    hauteurs = sum(s("danger", "hauteur_par_score", etat, "somme_hauteur") for etat in ETATS_SCORE)
    avec_psxg = [e for e in elements if _num(_val(e["pour"], "gardien", "psxg")) is not None]
    psxg = _somme(avec_psxg, "pour", "gardien", "psxg")
    buts_psxg = _somme(avec_psxg, "pour", "gardien", "buts_encaisses_sur_tir")
    cadres, arrets = s("gardien", "tirs_cadres_subis"), s("gardien", "arrets")
    return {
        "ppda": _ratio(s("defense", "ppda_passes_adverses"), s("defense", "ppda_actions"), 2),
        "pressions_par_match": _par_match(s("defense", "pressions"), n),
        "contre_pressing_par_match": _par_match(s("defense", "contre_pressing"), n),
        # Σ des x de ses actions défensives ÷ leur nombre, sur tous les matchs.
        "hauteur_actions_defensives": _ratio(hauteurs, actions, 1),
        "recuperations_par_match": _par_match(s("defense", "recuperations"), n),
        "recuperations_hautes_par_match": _par_match(s("defense", "recuperations_hautes"), n),
        "pertes_par_match": _par_match(s("defense", "pertes"), n),
        "grille_recuperations": _grille(elements, "pour", "defense", "grille_recuperations"),
        "grille_pertes": _grille(elements, "pour", "defense", "grille_pertes"),
        "xg_par_tir_contre": _ratio(s("resultat", "xg_contre"), s("resultat", "tirs_contre")),
        "gardien": {
            "tirs_cadres_subis": cadres,
            "arrets": arrets,
            "arrets_pct": _pct(arrets, cadres),
            # PSxG : seulement sur les matchs où StatsBomb le fournit.
            "psxg": _r(psxg) if avec_psxg else None,
            "psxg_moins_buts": _r(psxg - buts_psxg) if avec_psxg else None,
            "matchs_psxg": len(avec_psxg),
        },
    }


def _a_l_indice(valeur, i):
    valeur = _liste(valeur)
    return (_num(valeur[i]) or 0) if i < len(valeur) else 0


def _danger(elements):
    tranches = []
    for i, libelle in enumerate(TRANCHES_15):
        ligne = {"tranche": libelle}
        for cle in ("xg_pour", "xg_contre", "buts_pour", "buts_contre"):
            total = sum(_a_l_indice(_val(e["pour"], "danger", cle), i) for e in elements)
            ligne[cle] = _r(total) if cle.startswith("xg") else total
        tranches.append(ligne)
    # Barres doubles (pour, contre) sur une même échelle : le maximum du bloc.
    echelle = largeurs([t["xg_pour"] for t in tranches] + [t["xg_contre"] for t in tranches])
    n = len(tranches)
    barres = [{"libelle": t["tranche"], "series": [
        {"classe": "pour", "valeur": t["xg_pour"], "largeur": echelle[i]},
        {"classe": "contre", "valeur": t["xg_contre"], "largeur": echelle[n + i]}]}
        for i, t in enumerate(tranches)]
    par_score = []
    for etat in ETATS_SCORE:
        actions = _somme(elements, "pour", "danger", "hauteur_par_score", etat, "actions")
        somme = _somme(elements, "pour", "danger", "hauteur_par_score", etat, "somme_hauteur")
        par_score.append({"etat": etat, "libelle": LIBELLES_ETATS[etat], "actions": actions,
                          "hauteur": _ratio(somme, actions, 1)})
    return {"tranches": tranches, "barres_xg": barres, "hauteur_par_score": par_score}


def _transitions(elements):
    n = len(elements)

    def s(*chemin):
        return _somme(elements, "pour", "transitions", *chemin)

    def cote(nom):
        tirs, xg = s(nom, "tirs"), s(nom, "xg")
        return {"recuperations": s(nom, "recuperations"), "tirs": tirs, "xg": _r(xg),
                "buts": s(nom, "buts"), "tirs_par_match": _par_match(tirs, n),
                "xg_par_match": _par_match(xg, n)}

    return {
        "fenetre_secondes": 15,
        "pour": cote("pour"),
        "contre": cote("contre"),
        "pertes_vers_tir": s("pertes_vers_tir"),
        "grille_pertes_vers_tir": _grille(elements, "pour", "transitions", "grille_pertes_vers_tir"),
    }


def _cpa(elements):
    n = len(elements)

    def s(*chemin):
        return _somme(elements, "pour", "cpa", *chemin)

    issues = {}
    for cote in ("pour", "contre"):
        issues[cote] = {sorte: {"tirs": s(cote, sorte, "tirs"), "xg": _r(s(cote, sorte, "xg")),
                                "buts": s(cote, sorte, "buts")}
                        for sorte in ("corner", "coup_franc")}
    tireurs = {}
    for e in elements:
        for tireur in _liste(_val(e["pour"], "cpa", "tireurs")):
            tireur = _d(tireur)
            ident = _ident(tireur.get("id"))
            if ident is None:
                continue
            cible = tireurs.setdefault(ident, {"id": ident, "nom": _texte(tireur.get("nom")),
                                               "corners": 0, "coups_francs": 0})
            cible["corners"] += _num(tireur.get("corners")) or 0
            cible["coups_francs"] += _num(tireur.get("coups_francs")) or 0
    corners = s("corners")
    return {
        "corners": corners,
        "corners_par_match": _par_match(corners, n),
        "coups_francs_par_match": _par_match(s("coups_francs"), n),
        "corners_concedes_par_match": _par_match(_somme(elements, "contre", "cpa", "corners"), n),
        "pour": issues["pour"],
        "contre": issues["contre"],
        "tireurs": sorted(tireurs.values(),
                          key=lambda t: (-(t["corners"] + t["coups_francs"]), t["nom"], t["id"]))[:NB_TIREURS],
        "chutes_corners": _grille(elements, "pour", "cpa", "chutes_corners"),
        # Corners adverses : lus chez l'équipe rencontrée, retournés (repère de l'adversaire).
        "chutes_corners_contre": _grille(elements, "contre", "cpa", "chutes_corners", retourne=True),
    }


# ── 360 (seulement sur les matchs qui l'ont) ─────────────────────────────────
def _trois_cent_soixante(elements):
    avec = [e for e in elements if isinstance(_val(e["pour"], "trois_cent_soixante"), dict)]
    if not avec:
        return None
    n = len(avec)

    def s(*chemin):
        return _somme(avec, "pour", "trois_cent_soixante", *chemin)

    relance = s("relance", "passes")
    paliers = []
    for palier, libelle in LIBELLES_PRESSION:
        passes = s("relance", "par_pression", palier, "passes")
        paliers.append({
            "pression": palier, "libelle": libelle, "passes": passes,
            "part_pct": _pct(passes, relance),
            "reussite_pct": _pct(s("relance", "par_pression", palier, "reussies"), passes),
            "vers_l_avant_pct": _pct(s("relance", "par_pression", palier, "vers_l_avant"), passes),
        })
    gardien = []
    for situation, libelle in LIBELLES_GARDIEN:
        passes = s("gardien_sous_pression", situation, "passes")
        longues = s("gardien_sous_pression", situation, "longues")
        gardien.append({
            "situation": situation, "libelle": libelle, "passes": passes, "longues": longues,
            "part_longues_pct": _pct(longues, passes),
            "reussite_longues_pct": _pct(s("gardien_sous_pression", situation, "longues_reussies"), longues),
        })
    ligne = []
    for etat in ETATS_SCORE:
        images = s("ligne_defensive_par_score", etat, "images")
        ligne.append({"etat": etat, "libelle": LIBELLES_ETATS[etat], "images": images,
                      "hauteur": _ratio(s("ligne_defensive_par_score", etat, "somme_hauteur"), images, 1)})

    def corners(cote):
        nombre = s(f"corners_{cote}", "corners")
        bloc = {"corners": nombre}
        for cle in ("attaquants_6m", "defenseurs_6m", "attaquants_surface", "defenseurs_surface"):
            bloc[cle] = _ratio(s(f"corners_{cote}", cle), nombre, 1)  # moyenne par corner
        return bloc

    return {
        "matchs_avec_360": n,
        "passes_cassant_une_ligne_par_match": _par_match(s("passes_cassant_une_ligne"), n),
        "receptions_dans_l_espace_pct": _pct(s("receptions_dans_l_espace"), s("receptions_avec_image")),
        "relance": {"passes": relance, "sans_adversaire_pct": _pct(paliers[0]["passes"], relance),
                    "paliers": paliers},
        "gardien": gardien,
        "ligne_defensive_par_score": ligne,
        "corners_pour": corners("pour"),
        "corners_contre": corners("contre"),
        "passes_5_elimines_par_match": _par_match(s("passes_5_elimines"), n),
        "receptions_entre_lignes_par_match": _par_match(s("receptions_entre_lignes"), n),
    }


# ── Point d'entrée ───────────────────────────────────────────────────────────
def construire(elements):
    """Fiche de l'adversaire (dict JSON) à partir de ses derniers matchs, du
    plus récent au plus ancien. Un match sans ses deux lignes est écarté."""
    elements = [e for e in _liste(elements) if isinstance(e, dict)]
    calcules = [e for e in elements if isinstance(e.get("pour"), dict) and isinstance(e.get("contre"), dict)]
    # Le plus récent JOUÉ est-il calculé ? Sinon calcules[0] n'est pas le dernier match.
    dernier_calcule = bool(calcules) and calcules[0] is elements[0]
    trois_cent_soixante = _trois_cent_soixante(calcules)
    return {
        "version_fiche": VERSION_FICHE,
        "nb_matchs": len(elements),
        "nb_matchs_calcules": len(calcules),
        "matchs_avec_360": trois_cent_soixante["matchs_avec_360"] if trois_cent_soixante else 0,
        "resultats": _resultats(calcules),
        # Seul bloc qui reçoit aussi les matchs non calculés (« non calculé »).
        "tendances": _tendances(elements),
        "systeme": _systeme(calcules, dernier_calcule),
        "joueurs": _joueurs(calcules),
        "attaque": _attaque(calcules),
        "defense": _defense(calcules),
        "danger": _danger(calcules),
        "transitions": _transitions(calcules),
        "cpa": _cpa(calcules),
        "trois_cent_soixante": trois_cent_soixante,
    }
