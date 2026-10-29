"""Règles d'import StatsBomb, sans réseau : testables avec un faux client.

- Les N derniers matchs JOUÉS (`match_status == "available"`) de l'équipe, à
  domicile ou à l'extérieur, du plus récent au plus ancien.
- 360 optionnel, MATCH PAR MATCH : demandé seulement si `match_status_360`
  vaut « available » ; sinon le match s'importe sans.
- Idempotent : `last_updated` inchangé → rien n'est retéléchargé ;
  `last_updated` changé → événements + compositions relus ; seul
  `last_updated_360` changé (ou 360 devenu disponible) → seul le 360 relu.
- Écriture match par match dans une transaction : une coupure ne laisse pas un
  match à moitié écrit.
- L'équipe ciblée est notée dans `AdversaireSuivi` (étape 3a) : c'est elle
  qu'affiche la page « Adversaire du week-end ».
"""
import http.client
from dataclasses import dataclass, field
from datetime import date, time

from django.db import transaction
from django.db.models import BooleanField, ExpressionWrapper, Q
from django.utils import timezone

from .clients.statsbomb import ErreurStatsBomb
from .models import AdversaireSuivi, Competition, DonneesMatch, Equipe, Match

DISPONIBLE = "available"
NB_DERNIERS = 5


class MatchInvalide(ValueError):
    """Match mal formé : signalé puis ignoré, l'import continue."""


# ── Lecture contrôlée d'un match de Matches v6 ───────────────────────────────
def _entier(valeur, nom, facultatif=False):
    if valeur is None and facultatif:
        return None
    if isinstance(valeur, bool) or not isinstance(valeur, int) or valeur < 0:
        raise MatchInvalide(f"{nom} invalide")
    return valeur


def _texte(valeur, longueur):
    return valeur.strip()[:longueur] if isinstance(valeur, str) else ""


def _equipe(brut, cote):
    bloc = brut.get(f"{cote}_team")
    if not isinstance(bloc, dict):
        raise MatchInvalide(f"{cote}_team absent")
    ident = _entier(bloc.get(f"{cote}_team_id"), f"{cote}_team_id")
    if ident <= 0:
        raise MatchInvalide(f"{cote}_team_id invalide")
    return {"id": ident, "nom": _texte(bloc.get(f"{cote}_team_name"), 120) or f"Équipe {ident}"}


def normaliser_match(brut):
    """Dict brut de Matches v6 → dict contrôlé ; MatchInvalide si mal formé."""
    if not isinstance(brut, dict):
        raise MatchInvalide("match non structuré")
    ident = _entier(brut.get("match_id"), "match_id")
    if ident <= 0:
        raise MatchInvalide("match_id invalide")
    texte_date = brut.get("match_date")
    if not isinstance(texte_date, str):
        raise MatchInvalide("match_date absente")
    try:
        jour = date.fromisoformat(texte_date[:10])
    except ValueError:
        raise MatchInvalide("match_date invalide") from None
    coup_envoi = None
    if isinstance(brut.get("kick_off"), str) and brut["kick_off"]:
        try:
            coup_envoi = time.fromisoformat(brut["kick_off"][:8])
        except ValueError:
            raise MatchInvalide("kick_off invalide") from None
    domicile, exterieur = _equipe(brut, "home"), _equipe(brut, "away")
    if domicile["id"] == exterieur["id"]:
        raise MatchInvalide("équipes identiques")
    return {
        "id": ident,
        "date": jour,
        "coup_envoi": coup_envoi,
        "domicile": domicile,
        "exterieur": exterieur,
        "score_domicile": _entier(brut.get("home_score"), "home_score", facultatif=True),
        "score_exterieur": _entier(brut.get("away_score"), "away_score", facultatif=True),
        "journee": _entier(brut.get("match_week"), "match_week", facultatif=True),
        "statut": _texte(brut.get("match_status"), 30),
        "statut_360": _texte(brut.get("match_status_360"), 30),
        "version": _texte(brut.get("last_updated"), 40),
        "version_360": _texte(brut.get("last_updated_360"), 40),
    }


def derniers_matchs(matchs_bruts, equipe_id, n=NB_DERNIERS):
    """(les n derniers matchs joués de l'équipe, du plus récent au plus
    ancien ; liste des erreurs « match <id> : <motif> » des matchs ignorés)."""
    retenus, erreurs = [], []
    for brut in matchs_bruts:
        try:
            m = normaliser_match(brut)
        except MatchInvalide as e:
            ident = brut.get("match_id") if isinstance(brut, dict) else None
            erreurs.append(f"match {ident if isinstance(ident, int) else '?'} : {e}")
            continue
        if m["statut"] != DISPONIBLE:
            continue
        if equipe_id not in (m["domicile"]["id"], m["exterieur"]["id"]):
            continue
        retenus.append(m)
    retenus.sort(key=lambda m: (m["date"], m["coup_envoi"] or time.min, m["id"]), reverse=True)
    return retenus[:max(0, n)], erreurs


def equipes_de(matchs_bruts):
    """{id: nom} des équipes présentes dans une liste de matchs (mal formés ignorés)."""
    equipes = {}
    for brut in matchs_bruts:
        try:
            m = normaliser_match(brut)
        except MatchInvalide:
            continue
        for cote in ("domicile", "exterieur"):
            equipes[m[cote]["id"]] = m[cote]["nom"]
    return equipes


# ── Décision : que faut-il (re)télécharger ? ─────────────────────────────────
def etat_en_base(sb_match_id):
    """{"version", "version_360", "a_360"} du brut déjà stocké, None sinon —
    sans charger les colonnes lourdes."""
    return (DonneesMatch.objects
            .filter(match__sb_match_id=sb_match_id)
            .annotate(a_360=ExpressionWrapper(Q(frames_360_z__isnull=False),
                                              output_field=BooleanField()))
            .values("version", "version_360", "a_360")
            .first())


def a_telecharger(etat, m, forcer=False, sans_360=False):
    """(relire événements + compositions ?, relire le 360 ?)."""
    principal = forcer or etat is None or etat["version"] != m["version"]
    if sans_360 or m["statut_360"] != DISPONIBLE:
        return principal, False
    relire_360 = (forcer or etat is None or not etat["a_360"]
                  or etat["version_360"] != m["version_360"])
    return principal, relire_360


# ── Écriture ─────────────────────────────────────────────────────────────────
TELECHARGE, A_JOUR = "telecharge", "a_jour"


@dataclass
class Bilan:
    retenus: int = 0
    telecharges: int = 0
    a_jour: int = 0
    avec_360: int = 0
    sans_360: int = 0
    erreurs: list = field(default_factory=list)
    suivi: bool = False  # adversaire enregistré pour la page « Adversaire du week-end »


def _liste(valeur, quoi, ident):
    if not isinstance(valeur, list):
        raise MatchInvalide(f"{quoi} inattendus pour le match {ident}")
    return valeur


def importer_match(client, competition, m, forcer=False, sans_360=False):
    """Importe un match normalisé. Rend TELECHARGE ou A_JOUR.

    Les appels réseau ont lieu AVANT la transaction (pas de transaction
    ouverte pendant un téléchargement) ; l'écriture se fait d'un bloc."""
    etat = etat_en_base(m["id"])
    principal, relire_360 = a_telecharger(etat, m, forcer, sans_360)
    evenements = compositions = frames = None
    if principal:
        evenements = _liste(client.evenements(m["id"]), "événements", m["id"])
        compositions = _liste(client.compositions(m["id"]), "compositions", m["id"])
    if relire_360:
        frames = _liste(client.frames_360(m["id"]), "images 360", m["id"])

    with transaction.atomic():
        equipes = {}
        for cote in ("domicile", "exterieur"):
            equipes[cote], _ = Equipe.objects.update_or_create(
                sb_team_id=m[cote]["id"], defaults={"nom": m[cote]["nom"]})
        match, _ = Match.objects.update_or_create(sb_match_id=m["id"], defaults={
            "competition": competition,
            "date": m["date"],
            "coup_envoi": m["coup_envoi"],
            "domicile": equipes["domicile"],
            "exterieur": equipes["exterieur"],
            "score_domicile": m["score_domicile"],
            "score_exterieur": m["score_exterieur"],
            "journee": m["journee"],
            "statut": m["statut"],
            "statut_360": m["statut_360"],
        })
        if not principal and not relire_360:
            return A_JOUR
        # Colonnes lourdes non chargées : save() n'écrit que celles qu'on pose.
        donnees = (DonneesMatch.objects
                   .defer("evenements_z", "compositions_z", "frames_360_z")
                   .filter(match=match).first())
        if donnees is None:
            donnees = DonneesMatch(match=match)
        if principal:
            donnees.evenements = evenements
            donnees.compositions = compositions
            donnees.version = m["version"]
        if relire_360:
            donnees.frames_360 = frames
            donnees.version_360 = m["version_360"]
        donnees.save()
    return TELECHARGE


def importer_adversaire(client, competition_id, season_id, equipe_id,
                        n=NB_DERNIERS, forcer=False, sans_360=False, journal=None):
    """Importe les n derniers matchs joués de l'équipe. Rend un Bilan.

    `journal(texte)` reçoit une ligne par match (date, ids, état), jamais un
    nom de joueur ni un identifiant de connexion. Lève ErreurStatsBomb si la
    compétition-saison n'est couverte par aucun compte."""
    journal = journal or (lambda texte: None)
    compte = client.compte_saison(competition_id, season_id)
    if compte is None:
        raise ErreurStatsBomb(
            f"compétition {competition_id} saison {season_id} absente du catalogue de vos comptes")
    infos = next((c for c in client.catalogue()
                  if (c.get("competition_id"), c.get("season_id")) == (competition_id, season_id)), {})
    competition, _ = Competition.objects.update_or_create(
        sb_competition_id=competition_id, sb_season_id=season_id, defaults={
            "nom": _texte(infos.get("competition_name"), 120) or f"Compétition {competition_id}",
            "saison": _texte(infos.get("season_name"), 40) or str(season_id),
            "compte": compte,
        })

    matchs_bruts = client.matchs(competition_id, season_id)
    retenus, erreurs = derniers_matchs(matchs_bruts, equipe_id, n)
    bilan = Bilan(retenus=len(retenus), erreurs=list(erreurs))
    for m in retenus:
        try:
            etat = importer_match(client, competition, m, forcer, sans_360)
        except (MatchInvalide, ErreurStatsBomb) as e:
            # Reprise au match suivant ; le match fautif reste dans son état précédent.
            bilan.erreurs.append(f"match {m['id']} : {e}")
            continue
        except http.client.HTTPException as e:
            # Réponse tronquée (IncompleteRead…) échappée au client : même
            # reprise. Seul le type est noté, le message pourrait porter le corps.
            bilan.erreurs.append(f"match {m['id']} : réponse interrompue ({type(e).__name__})")
            continue
        if etat == TELECHARGE:
            bilan.telecharges += 1
        else:
            bilan.a_jour += 1
        a_360 = DonneesMatch.objects.filter(match__sb_match_id=m["id"],
                                            frames_360_z__isnull=False).exists()
        if a_360:
            bilan.avec_360 += 1
        else:
            bilan.sans_360 += 1
        journal(f"{m['date']} match {m['id']} : "
                f"{'téléchargé' if etat == TELECHARGE else 'déjà à jour'}"
                f"{', avec 360' if a_360 else ', sans 360'}")
    bilan.suivi = suivre(competition, equipe_id, equipes_de(matchs_bruts).get(equipe_id))
    return bilan


def suivre(competition, equipe_id, nom):
    """Enregistre (ou date à nouveau) l'équipe CIBLÉE par l'import : c'est
    elle que montre la page « Adversaire du week-end ». Rien si l'équipe
    n'apparaît dans aucun match de la saison (identifiant erroné). Rend vrai
    si le suivi est posé."""
    if not nom:
        return False
    with transaction.atomic():
        equipe, _ = Equipe.objects.update_or_create(sb_team_id=equipe_id,
                                                    defaults={"nom": nom})
        AdversaireSuivi.objects.update_or_create(
            equipe=equipe, competition=competition,
            defaults={"importe_le": timezone.now()})
    return True
