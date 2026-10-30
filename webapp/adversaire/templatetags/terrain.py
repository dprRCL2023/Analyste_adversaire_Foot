"""Terrains et barres SVG de la fiche adversaire (dont l'écart d'xG, 3c-3).

CSP stricte : le SVG est dessiné par ATTRIBUTS (x, y, class…), sans `style=`
ni script ; les couleurs sont dans `app.css`. Les coordonnées, niveaux et
largeurs viennent de la fiche calculée à l'import : les balises ne font que
TRIER et CONTRÔLER (entiers bornés, classes d'une liste fermée), jamais
calculer une donnée. Une valeur inattendue n'arrive jamais dans un attribut.
"""
import math

from django import template

from ..analyse.indicateurs import COLONNES, RANGEES

register = template.Library()

TAILLE_CASE = 10  # repère StatsBomb 120 × 80, grille de 10 × 10
NIVEAU_MAX = 5
CLASSES_BARRE = ("pour", "contre")
DECIMALES = (0, 1, 2)  # 0 : comptes ; 1 : pourcentages ; 2 : xG
# Écart d'xG : repère 100 × 60, une colonne de 20 par match, ligne zéro à y = 30.
NB_COLONNES_ECART = 5
LARGEUR_COLONNE = 20
LARGEUR_BARRE = 12
LIGNE_ZERO = 30
DEMI_HAUTEUR = 28


def _entier(valeur):
    return isinstance(valeur, int) and not isinstance(valeur, bool)


def _nombre(valeur):
    """Nombre fini (pour le texte d'une case ou d'une barre), sinon None."""
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    if isinstance(valeur, float) and not math.isfinite(valeur):
        return None
    return valeur


def _decimales(valeur):
    """Nombre FIXE de décimales du texte affiché ; hors liste → 2."""
    return valeur if _entier(valeur) and valeur in DECIMALES else 2


@register.inclusion_tag("adversaire/fiche/_terrain_xi.html")
def terrain_xi(joueurs):
    """XI probable sur un terrain ; les joueurs sans poste connu sont listés à part."""
    joueurs = [j for j in (joueurs if isinstance(joueurs, list) else []) if isinstance(j, dict)]
    places = [j for j in joueurs if _entier(j.get("x")) and _entier(j.get("y"))]
    return {"sur_terrain": places, "sans_poste": [j for j in joueurs if j not in places]}


@register.inclusion_tag("adversaire/fiche/_terrain_grille.html")
def terrain_grille(grille, libelle, decimales=2):
    """Grille 12 × 8 (`{"valeurs", "niveaux", "min_non_vide", "max"}` de la
    fiche) en 96 cases `niv-0` à `niv-5`. Grille mal formée (taille, niveau
    hors de 0 à 5 ou non entier) ou sans aucune case non vide : « Aucune donnée ».
    `decimales` (fixe) : 2 pour une grille d'xG, 0 pour une grille de comptes."""
    grille = grille if isinstance(grille, dict) else {}
    niveaux, valeurs = grille.get("niveaux"), grille.get("valeurs")
    taille = COLONNES * RANGEES
    if (not isinstance(niveaux, list) or len(niveaux) != taille
            or not all(_entier(n) and 0 <= n <= NIVEAU_MAX for n in niveaux)
            or not any(niveaux)):
        return {"vide": True, "libelle": libelle}
    valeurs = valeurs if isinstance(valeurs, list) and len(valeurs) == taille else [None] * taille
    cases = [{"x": (i % COLONNES) * TAILLE_CASE, "y": (i // COLONNES) * TAILLE_CASE,
              "niveau": n, "valeur": _nombre(valeurs[i])}
             for i, n in enumerate(niveaux)]
    return {"vide": False, "libelle": libelle, "cases": cases, "decimales": _decimales(decimales),
            "min_non_vide": _nombre(grille.get("min_non_vide")), "max": _nombre(grille.get("max"))}


@register.inclusion_tag("adversaire/fiche/_barres.html")
def barres(lignes, unite="", decimales=2):
    """Barres horizontales : `[{"libelle", "series": [{"classe", "valeur",
    "largeur"}]}]` de la fiche. Classe hors de la liste fermée : série
    ignorée ; largeur non entière → 0, ramenée entre 0 et 100.
    `decimales` (fixe) : 1 pour des pourcentages, 2 pour des xG."""
    propres = []
    for ligne in (lignes if isinstance(lignes, list) else []):
        if not isinstance(ligne, dict):
            continue
        series = []
        for serie in (ligne.get("series") if isinstance(ligne.get("series"), list) else []):
            if not isinstance(serie, dict) or serie.get("classe") not in CLASSES_BARRE:
                continue
            largeur = serie.get("largeur")
            largeur = min(100, max(0, largeur)) if _entier(largeur) else 0
            series.append({"classe": serie["classe"], "largeur": largeur,
                           "valeur": _nombre(serie.get("valeur"))})
        if series:
            libelle = ligne.get("libelle")
            propres.append({"libelle": libelle if isinstance(libelle, str) else "", "series": series})
    return {"lignes": propres, "unite": unite if isinstance(unite, str) else "",
            "decimales": _decimales(decimales)}


@register.inclusion_tag("adversaire/fiche/_ecarts_xg.html")
def ecarts_xg(colonnes):
    """Barres verticales de l'écart d'xG, de part et d'autre de zéro :
    `[{"date", "rencontre", "calcule", "valeur", "hauteur", "classe"}]` de la
    fiche, du plus ancien au plus récent ; 5 colonnes au plus. Hauteur non
    entière → 0, ramenée entre 0 et 100 ; classe hors liste fermée → aucune
    barre. Les coordonnées sont des entiers tirés de l'indice et de la hauteur."""
    colonnes = [c for c in (colonnes if isinstance(colonnes, list) else []) if isinstance(c, dict)]
    propres = []
    for i, colonne in enumerate(colonnes[:NB_COLONNES_ECART]):
        classe = colonne.get("classe") if colonne.get("classe") in CLASSES_BARRE else None
        hauteur = colonne.get("hauteur")
        hauteur = min(100, max(0, hauteur)) if _entier(hauteur) and classe else 0
        barre = hauteur * DEMI_HAUTEUR // 100
        date, rencontre = colonne.get("date"), colonne.get("rencontre")
        propres.append({
            "x": i * LARGEUR_COLONNE + (LARGEUR_COLONNE - LARGEUR_BARRE) // 2,
            "milieu": i * LARGEUR_COLONNE + LARGEUR_COLONNE // 2,
            "y": LIGNE_ZERO - barre if classe == "pour" else LIGNE_ZERO,
            "hauteur": barre,
            "classe": classe if barre else None,
            "calcule": colonne.get("calcule") is True,
            "valeur": _nombre(colonne.get("valeur")),
            "date": date if isinstance(date, str) else "",
            "rencontre": rencontre if isinstance(rencontre, str) else "",
        })
    return {"colonnes": propres, "largeur_barre": LARGEUR_BARRE, "ligne_zero": LIGNE_ZERO,
            "largeur": NB_COLONNES_ECART * LARGEUR_COLONNE, "hauteur": 2 * LIGNE_ZERO}
