"""Vues de l'appli Analyste. Connexion exigée par LoginRequiredMiddleware ;
rôle exigé par `roles.role_requis` (refus par défaut).

⚠️ Aucune vue n'appelle un fournisseur ni ne calcule sur le brut : tout se
calcule à l'import (règle du pôle). Les colonnes `*_z` ne sont jamais lues ici.
"""
from django.db.models import Exists, OuterRef
from django.db.models.fields.json import KeyTransform
from django.http import Http404
from django.shortcuts import get_object_or_404, render

from .importation import NB_DERNIERS, fiche_perimee, matchs_du_suivi
from .models import AdversaireSuivi, DonneesMatch, FicheAdversaire, IndicateursMatchEquipe
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
    avec_360 = DonneesMatch.objects.filter(match=OuterRef("pk"), frames_360_z__isnull=False)
    return list(matchs_du_suivi(suivi)
                .select_related("competition", "domicile", "exterieur")
                .annotate(a_360=Exists(avec_360))[:NB_DERNIERS])


def _nombre(valeur):
    if isinstance(valeur, bool) or not isinstance(valeur, (int, float)):
        return None
    return float(valeur)


def xg_des_matchs(matchs, equipe_id):
    """{pk du match: (xG pour, xG contre)} de l'équipe, lus dans
    `IndicateursMatchEquipe` (déjà calculés à l'import). Une seule requête, et
    seules les deux clés utiles sont extraites en SQL : ni le brut ni le JSON
    complet ne sont chargés."""
    if not matchs:
        return {}
    resultat = KeyTransform("resultat", "valeurs")
    lignes = (IndicateursMatchEquipe.objects
              .filter(match_id__in=[m.pk for m in matchs], equipe_id=equipe_id)
              .values("match_id", xg_pour=KeyTransform("xg_pour", resultat),
                      xg_contre=KeyTransform("xg_contre", resultat)))
    return {ligne["match_id"]: (_nombre(ligne["xg_pour"]), _nombre(ligne["xg_contre"]))
            for ligne in lignes}


def ligne_match(match, equipe_id, xg=None):
    """Un match vu par l'adversaire : lieu, équipe rencontrée, buts, V/N/D, xG."""
    a_domicile = match.domicile_id == equipe_id
    pour, contre = ((match.score_domicile, match.score_exterieur) if a_domicile
                    else (match.score_exterieur, match.score_domicile))
    resultat = None
    if pour is not None and contre is not None:
        resultat = "V" if pour > contre else "D" if pour < contre else "N"
    xg_pour, xg_contre = xg or (None, None)
    return {
        "xg_pour": xg_pour,
        "xg_contre": xg_contre,
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
        matchs = derniers_matchs(suivi)
        xg = xg_des_matchs(matchs, suivi.equipe_id)
        lignes = [ligne_match(m, suivi.equipe_id, xg.get(m.pk)) for m in matchs]
    return render(request, "adversaire/accueil.html", {
        "suivis": suivis,
        "suivi": suivi,
        "lignes": lignes,
        "bilan": bilan(lignes),
        "nb_derniers": NB_DERNIERS,
    })


@role_requis(peut_consulter)
def fiche(request, suivi_id):
    """Fiche de l'adversaire : LIT la synthèse calculée à l'import
    (`FicheAdversaire`), ne calcule rien. Trois requêtes métier : suivi,
    matchs, fiche ; ni brut ni indicateurs par match chargés."""
    suivi = get_object_or_404(AdversaireSuivi.objects.select_related("equipe", "competition"),
                              pk=suivi_id)
    matchs = derniers_matchs(suivi)
    lignes = [ligne_match(m, suivi.equipe_id) for m in matchs]
    fiche_stockee = FicheAdversaire.objects.filter(suivi=suivi).first()
    return render(request, "adversaire/fiche.html", {
        "suivi": suivi,
        "lignes": lignes,
        "bilan": bilan(lignes),
        "fiche": fiche_stockee,
        "valeurs": fiche_stockee.valeurs if fiche_stockee else None,
        "perimee": fiche_stockee is not None and fiche_perimee(fiche_stockee, [m.pk for m in matchs]),
        "nb_derniers": NB_DERNIERS,
    })
