"""Vues de l'appli Analyste. Connexion exigée par LoginRequiredMiddleware ;
rôle exigé par `roles.role_requis` (refus par défaut).

⚠️ Aucune vue n'appelle un fournisseur ni ne calcule sur le brut : tout se
calcule à l'import (règle du pôle). Les colonnes `*_z` ne sont jamais lues ici.
"""
from django.db.models import Exists, OuterRef, Q
from django.http import Http404
from django.shortcuts import render

from .importation import NB_DERNIERS
from .models import AdversaireSuivi, DonneesMatch, Match
from .roles import peut_consulter, role_requis

POINTS = {"V": 3, "N": 1, "D": 0}


def _suivi_choisi(request, suivis):
    """Suivi demandé par `?suivi=<id>` (404 si invalide ou inconnu), sinon le
    plus récemment importé, sinon None."""
    if "suivi" not in request.GET:
        return suivis[0] if suivis else None
    try:
        ident = int(request.GET["suivi"])
    except ValueError:
        raise Http404 from None
    for suivi in suivis:
        if suivi.id == ident:
            return suivi
    raise Http404


def derniers_matchs(suivi):
    """Les NB_DERNIERS matchs importés de l'adversaire suivi, du plus récent au
    plus ancien, avec `a_360` (annotation : le brut n'est pas chargé)."""
    equipe = suivi.equipe_id
    avec_360 = DonneesMatch.objects.filter(match=OuterRef("pk"), frames_360_z__isnull=False)
    return list(Match.objects
                .filter(Q(domicile_id=equipe) | Q(exterieur_id=equipe),
                        competition_id=suivi.competition_id)
                .select_related("competition", "domicile", "exterieur")
                .annotate(a_360=Exists(avec_360))
                .order_by("-date", "-coup_envoi", "-sb_match_id")[:NB_DERNIERS])


def ligne_match(match, equipe_id):
    """Un match vu par l'adversaire : lieu, équipe rencontrée, buts, V/N/D."""
    a_domicile = match.domicile_id == equipe_id
    pour, contre = ((match.score_domicile, match.score_exterieur) if a_domicile
                    else (match.score_exterieur, match.score_domicile))
    resultat = None
    if pour is not None and contre is not None:
        resultat = "V" if pour > contre else "D" if pour < contre else "N"
    return {
        "match": match,
        "a_domicile": a_domicile,
        "rencontre": match.exterieur if a_domicile else match.domicile,
        "pour": pour,
        "contre": contre,
        "resultat": resultat,
    }


def bilan(lignes):
    """Forme (du plus récent au plus ancien), points et buts des matchs au score connu."""
    jouees = [ligne for ligne in lignes if ligne["resultat"]]
    return {
        "forme": [ligne["resultat"] for ligne in jouees],
        "points": sum(POINTS[ligne["resultat"]] for ligne in jouees),
        "pour": sum(ligne["pour"] for ligne in jouees),
        "contre": sum(ligne["contre"] for ligne in jouees),
        "matchs": len(jouees),
    }


@role_requis(peut_consulter)
def accueil(request):
    """« Adversaire du week-end » : les derniers matchs importés de l'adversaire choisi."""
    suivis = list(AdversaireSuivi.objects.select_related("equipe", "competition"))
    suivi = _suivi_choisi(request, suivis)
    lignes = []
    if suivi is not None:
        lignes = [ligne_match(m, suivi.equipe_id) for m in derniers_matchs(suivi)]
    return render(request, "adversaire/accueil.html", {
        "suivis": suivis,
        "suivi": suivi,
        "lignes": lignes,
        "bilan": bilan(lignes),
        "nb_derniers": NB_DERNIERS,
    })
