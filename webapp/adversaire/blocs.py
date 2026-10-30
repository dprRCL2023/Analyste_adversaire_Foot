"""Blocs de la fiche adversaire : liste ordonnée, une ligne par bloc.

Retirer une ligne de `BLOCS_FICHE` retire le bloc ET son lien du sommaire,
sans autre modification. Aucun calcul ici : la vue lit `FicheAdversaire.valeurs`.
"""
from typing import NamedTuple, Optional


class Bloc(NamedTuple):
    cle: str
    titre: str  # texte du lien dans le sommaire
    ancre: str  # id HTML du bloc (sans « # »)
    gabarit: str
    cle_valeurs: Optional[str] = None  # clé de `valeurs` qui doit être renseignée pour afficher


BLOCS_FICHE = [
    Bloc("resultats", "Résultats et forme", "resultats", "adversaire/fiche/_resultats.html"),
    Bloc("systeme", "Système et XI probable", "systeme", "adversaire/fiche/_systeme.html"),
    Bloc("joueurs", "Joueurs clés", "joueurs", "adversaire/fiche/_joueurs.html"),
    Bloc("attaque", "Attaque", "attaque", "adversaire/fiche/_attaque.html"),
    Bloc("defense", "Défense", "defense", "adversaire/fiche/_defense.html"),
    Bloc("cpa", "Coups de pied arrêtés", "cpa", "adversaire/fiche/_cpa.html"),
    Bloc("trois_cent_soixante", "360", "trois-cent-soixante", "adversaire/fiche/_360.html",
         cle_valeurs="trois_cent_soixante"),
    Bloc("glossaire", "Comment lire la fiche", "glossaire", "adversaire/fiche/_glossaire.html"),
]


def blocs_affiches(valeurs):
    """Blocs de `BLOCS_FICHE` à afficher pour ces `valeurs`, dans l'ordre : ceux
    qui exigent une clé ne sont gardés que si elle est renseignée."""
    valeurs = valeurs or {}
    return [bloc for bloc in BLOCS_FICHE
            if bloc.cle_valeurs is None or valeurs.get(bloc.cle_valeurs)]
