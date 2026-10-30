"""Terrains SVG de la fiche adversaire.

CSP stricte : le SVG est dessiné par ATTRIBUTS (x, y, class…), sans `style=`
ni script ; les couleurs sont dans `app.css`. Les coordonnées viennent de la
fiche calculée à l'import (entiers du repère StatsBomb 120 × 80) : la balise
ne fait que trier ce qui se place et ce qui ne se place pas.
"""
from django import template

register = template.Library()


def _entier(valeur):
    return isinstance(valeur, int) and not isinstance(valeur, bool)


@register.inclusion_tag("adversaire/fiche/_terrain_xi.html")
def terrain_xi(joueurs):
    """XI probable sur un terrain ; les joueurs sans poste connu sont listés à part."""
    joueurs = [j for j in (joueurs if isinstance(joueurs, list) else []) if isinstance(j, dict)]
    places = [j for j in joueurs if _entier(j.get("x")) and _entier(j.get("y"))]
    return {"sur_terrain": places, "sans_poste": [j for j in joueurs if j not in places]}
