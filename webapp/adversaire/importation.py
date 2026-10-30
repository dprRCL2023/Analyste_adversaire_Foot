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
- Indicateurs (étape 3b) : deux lignes `IndicateursMatchEquipe` par match,
  calculées AVANT la transaction (listes déjà en mémoire) et écrites DANS la
  même transaction que le brut. Match « déjà à jour » mais lignes absentes ou
  périmées : le brut de CE match est relu et recalculé. Une erreur de calcul
  ne fait jamais perdre le brut (le match est écrit, l'erreur notée).
- Fiche (étape 3c-1) : en fin d'import, la synthèse des derniers matchs du
  suivi (`FicheAdversaire`) est recalculée depuis les lignes d'indicateurs,
  sans brut. Une erreur de calcul garde l'ancienne fiche.
"""
import http.client
import zlib
from dataclasses import dataclass, field
from datetime import date, time

from django.db import transaction
from django.db.models import BooleanField, ExpressionWrapper, Q
from django.utils import timezone

from .analyse import fiche, indicateurs
from .clients.statsbomb import ErreurStatsBomb
from .models import (AdversaireSuivi, Competition, DonneesMatch, Equipe, FicheAdversaire,
                     IndicateursMatchEquipe, Match)

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


# ── Indicateurs (étape 3b) ───────────────────────────────────────────────────
class ErreurCalcul(Exception):
    """Le calcul des indicateurs a échoué. Le message ne porte que le TYPE de
    l'erreur (jamais de contenu du brut) ; le brut reste écrit."""


class BrutAbsent(Exception):
    """Aucun brut stocké pour ce match : rien à recalculer."""


def version_source(version, version_360, a_360):
    """Version du brut qui a servi au calcul (`last_updated` | `last_updated_360`,
    « - » sans 360) : une révision StatsBomb, ou un 360 arrivé, la change."""
    return f"{version or ''}|{(version_360 or '') if a_360 else '-'}"


def calculer_valeurs(domicile_sb, exterieur_sb, evenements, compositions, frames):
    """{id SB de l'équipe: valeurs} pour les deux équipes du match ; lève
    ErreurCalcul si le module de calcul échoue."""
    try:
        return {ident: indicateurs.calculer(evenements, compositions, frames, ident)
                for ident in (domicile_sb, exterieur_sb)}
    except Exception as e:  # une formule fautive ne doit jamais faire perdre le brut
        raise ErreurCalcul(type(e).__name__) from None


def ecrire_indicateurs(match, equipes, valeurs, source, avec_360):
    """Écrit (ou remplace) les lignes du match. À appeler dans une transaction."""
    for equipe in equipes:
        IndicateursMatchEquipe.objects.update_or_create(match=match, equipe=equipe, defaults={
            "version_calcul": indicateurs.VERSION_CALCUL,
            "version_source": source,
            "avec_360": avec_360,
            "valeurs": valeurs[equipe.sb_team_id],
        })


def lignes_a_jour(match_pk, equipe_ids, source):
    """Vrai si chaque équipe a sa ligne, à la version de calcul et de source attendues."""
    a_jour = IndicateursMatchEquipe.objects.filter(
        match_id=match_pk, equipe_id__in=equipe_ids,
        version_calcul=indicateurs.VERSION_CALCUL, version_source=source).count()
    return a_jour == len(set(equipe_ids))


def recalculer_match(match):
    """Relit le brut d'UN match (une seule ligne `DonneesMatch`) et réécrit ses
    deux lignes, sans réseau. `match` doit porter `domicile` et `exterieur`.
    Lève BrutAbsent ou ErreurCalcul ; rend le nombre de lignes écrites."""
    donnees = DonneesMatch.objects.filter(match_id=match.pk).first()
    if donnees is None:
        raise BrutAbsent(f"match {match.sb_match_id} sans brut")
    try:
        frames = donnees.frames_360
        evenements, compositions = donnees.evenements, donnees.compositions
    except (zlib.error, ValueError) as e:  # brut illisible (zlib, UTF-8, JSON) : noté, jamais bloquant
        raise ErreurCalcul(type(e).__name__) from None
    valeurs = calculer_valeurs(match.domicile.sb_team_id, match.exterieur.sb_team_id,
                               evenements, compositions, frames)
    with transaction.atomic():
        ecrire_indicateurs(match, [match.domicile, match.exterieur], valeurs,
                           version_source(donnees.version, donnees.version_360, frames is not None),
                           frames is not None)
    return 2


def matchs_a_recalculer():
    """pk des matchs dont le brut existe mais dont une ligne manque ou est
    périmée — sans charger aucune colonne `*_z`."""
    attendues = {}
    for mid, eid, calcul, source in IndicateursMatchEquipe.objects.values_list(
            "match_id", "equipe_id", "version_calcul", "version_source"):
        attendues[(mid, eid)] = (calcul, source)
    etats = (DonneesMatch.objects
             .annotate(a_360=ExpressionWrapper(Q(frames_360_z__isnull=False),
                                               output_field=BooleanField()))
             .values_list("match_id", "version", "version_360", "a_360",
                          "match__domicile_id", "match__exterieur_id"))
    perimes = []
    for mid, version, version_360, a_360, domicile, exterieur in etats:
        attendu = (indicateurs.VERSION_CALCUL, version_source(version, version_360, a_360))
        if attendues.get((mid, domicile)) != attendu or attendues.get((mid, exterieur)) != attendu:
            perimes.append(mid)
    return sorted(perimes)


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
    indicateurs: int = 0  # lignes d'indicateurs calculées pendant ce passage
    erreurs_calcul: list = field(default_factory=list)
    fiche: int = -1  # matchs calculés dans la fiche reconstruite ; -1 si aucune
    erreur_fiche: str = ""  # type de l'erreur de calcul de la fiche, sinon vide


@dataclass
class ResultatMatch:
    etat: str  # TELECHARGE ou A_JOUR
    lignes: int = 0  # lignes d'indicateurs écrites
    erreur_calcul: str = ""  # type de l'erreur de calcul, sinon vide


def _liste(valeur, quoi, ident):
    if not isinstance(valeur, list):
        raise MatchInvalide(f"{quoi} inattendus pour le match {ident}")
    return valeur


def _completer_brut(etat, m, evenements, compositions, frames, principal, relire_360):
    """Complète, depuis la base, ce qui n'a pas été retéléchargé (une seule
    ligne, seulement les colonnes utiles) pour calculer sur le brut entier."""
    colonnes = []
    if not principal:
        colonnes += ["evenements_z", "compositions_z"]
    if not relire_360 and etat is not None and etat["a_360"]:
        colonnes.append("frames_360_z")
    if colonnes:
        stocke = DonneesMatch.objects.only(*colonnes).get(match__sb_match_id=m["id"])
        if not principal:
            evenements, compositions = stocke.evenements, stocke.compositions
        if "frames_360_z" in colonnes:
            frames = stocke.frames_360
    return evenements, compositions, frames


def _rattraper(match, equipes, etat):
    """Match « déjà à jour » : recalcule ses lignes si elles manquent ou sont périmées."""
    source = version_source(etat["version"], etat["version_360"], etat["a_360"])
    if lignes_a_jour(match.pk, [e.pk for e in equipes], source):
        return ResultatMatch(A_JOUR)
    try:
        return ResultatMatch(A_JOUR, recalculer_match(match))
    except (ErreurCalcul, BrutAbsent) as e:
        return ResultatMatch(A_JOUR, 0, str(e))


def importer_match(client, competition, m, forcer=False, sans_360=False):
    """Importe un match normalisé. Rend un ResultatMatch (TELECHARGE ou A_JOUR).

    Les appels réseau ont lieu AVANT la transaction (pas de transaction
    ouverte pendant un téléchargement) ; l'écriture se fait d'un bloc, brut
    et indicateurs ensemble."""
    etat = etat_en_base(m["id"])
    principal, relire_360 = a_telecharger(etat, m, forcer, sans_360)
    evenements = compositions = frames = None
    if principal:
        evenements = _liste(client.evenements(m["id"]), "événements", m["id"])
        compositions = _liste(client.compositions(m["id"]), "compositions", m["id"])
    if relire_360:
        frames = _liste(client.frames_360(m["id"]), "images 360", m["id"])

    valeurs, erreur, source, avec_360 = None, "", "", False
    if principal or relire_360:
        try:
            tout = _completer_brut(etat, m, evenements, compositions, frames, principal, relire_360)
        except (zlib.error, ValueError) as e:  # brut stocké illisible : le brut téléchargé est écrit quand même
            erreur = type(e).__name__
        else:
            avec_360 = tout[2] is not None
            source = version_source(
                m["version"] if principal else etat["version"],
                m["version_360"] if relire_360 else (etat or {}).get("version_360", ""),
                avec_360)
            try:
                valeurs = calculer_valeurs(m["domicile"]["id"], m["exterieur"]["id"], *tout)
            except ErreurCalcul as e:
                erreur = str(e)

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
        if principal or relire_360:
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
            if valeurs is not None:
                ecrire_indicateurs(match, [equipes["domicile"], equipes["exterieur"]],
                                   valeurs, source, avec_360)
    if not principal and not relire_360:
        return _rattraper(match, [equipes["domicile"], equipes["exterieur"]], etat)
    return ResultatMatch(TELECHARGE, 2 if valeurs is not None else 0, erreur)


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
            resultat = importer_match(client, competition, m, forcer, sans_360)
        except (MatchInvalide, ErreurStatsBomb) as e:
            # Reprise au match suivant ; le match fautif reste dans son état précédent.
            bilan.erreurs.append(f"match {m['id']} : {e}")
            continue
        except http.client.HTTPException as e:
            # Réponse tronquée (IncompleteRead…) échappée au client : même
            # reprise. Seul le type est noté, le message pourrait porter le corps.
            bilan.erreurs.append(f"match {m['id']} : réponse interrompue ({type(e).__name__})")
            continue
        etat = resultat.etat
        bilan.indicateurs += resultat.lignes
        if resultat.erreur_calcul:
            bilan.erreurs_calcul.append(
                f"match {m['id']} : calcul des indicateurs en échec ({resultat.erreur_calcul})")
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
                f"{', avec 360' if a_360 else ', sans 360'}"
                f"{', indicateurs calculés' if resultat.lignes else ''}")
    suivi = suivre(competition, equipe_id, equipes_de(matchs_bruts).get(equipe_id))
    bilan.suivi = suivi is not None
    if suivi is not None:
        try:
            bilan.fiche = construire_fiche(suivi)
        except ErreurCalcul as e:
            bilan.erreur_fiche = str(e)
    return bilan


def suivre(competition, equipe_id, nom):
    """Enregistre (ou date à nouveau) l'équipe CIBLÉE par l'import : c'est
    elle que montre la page « Adversaire du week-end ». Rien si l'équipe
    n'apparaît dans aucun match de la saison (identifiant erroné). Rend le
    suivi, ou None."""
    if not nom:
        return None
    with transaction.atomic():
        equipe, _ = Equipe.objects.update_or_create(sb_team_id=equipe_id,
                                                    defaults={"nom": nom})
        suivi, _ = AdversaireSuivi.objects.update_or_create(
            equipe=equipe, competition=competition,
            defaults={"importe_le": timezone.now()})
    return suivi


# ── Fiche de l'adversaire (étape 3c-1) ───────────────────────────────────────
def matchs_du_suivi(suivi):
    """Matchs de l'adversaire suivi DANS sa compétition, du plus récent au
    plus ancien (requête non tranchée). Même ordre pour la fiche et pour les
    pages : c'est ce qui permet de repérer une fiche périmée."""
    equipe = suivi.equipe_id
    return (Match.objects
            .filter(Q(domicile_id=equipe) | Q(exterieur_id=equipe),
                    competition_id=suivi.competition_id)
            .order_by("-date", "-coup_envoi", "-sb_match_id"))


def elements_de_fiche(suivi):
    """(pk des NB_DERNIERS matchs, éléments pour `fiche.construire`). Lit les
    lignes d'indicateurs À JOUR de la version de calcul, jamais le brut."""
    # Une seule requête (jointure) : noms des équipes et score officiel, aucune colonne `*_z`.
    matchs = list(matchs_du_suivi(suivi)
                  .select_related("domicile", "exterieur")
                  .only("date", "domicile", "exterieur", "score_domicile", "score_exterieur",
                        "domicile__nom", "exterieur__nom")[:NB_DERNIERS])
    lignes = {(ligne["match_id"], ligne["equipe_id"]): ligne["valeurs"]
              for ligne in IndicateursMatchEquipe.objects
              .filter(match_id__in=[m.pk for m in matchs],
                      version_calcul=indicateurs.VERSION_CALCUL)
              .values("match_id", "equipe_id", "valeurs")}
    elements = []
    for m in matchs:
        a_domicile = m.domicile_id == suivi.equipe_id
        autre = m.exterieur_id if a_domicile else m.domicile_id
        pour, contre = ((m.score_domicile, m.score_exterieur) if a_domicile
                        else (m.score_exterieur, m.score_domicile))
        elements.append({"match": m.pk, "date": m.date.isoformat(), "a_domicile": a_domicile,
                         "rencontre": (m.exterieur if a_domicile else m.domicile).nom,
                         "score_pour": pour, "score_contre": contre,
                         "pour": lignes.get((m.pk, suivi.equipe_id)),
                         "contre": lignes.get((m.pk, autre))})
    return [m.pk for m in matchs], elements


def construire_fiche(suivi):
    """(Re)calcule la fiche du suivi. Rend le nombre de matchs calculés ;
    lève ErreurCalcul (type seulement) en gardant l'ancienne fiche."""
    pks, elements = elements_de_fiche(suivi)
    try:
        valeurs = fiche.construire(elements)
    except Exception as e:  # une règle fautive ne doit jamais effacer l'ancienne fiche
        raise ErreurCalcul(type(e).__name__) from None
    FicheAdversaire.objects.update_or_create(suivi=suivi, defaults={
        "version_fiche": fiche.VERSION_FICHE,
        "matchs": pks,
        "nb_matchs_calcules": valeurs["nb_matchs_calcules"],
        "valeurs": valeurs,
    })
    return valeurs["nb_matchs_calcules"]


def fiche_perimee(fiche_stockee, pks_actuels):
    """Vrai si la fiche date d'une autre règle ou d'autres matchs."""
    return (fiche_stockee.version_fiche != fiche.VERSION_FICHE
            or list(fiche_stockee.matchs or []) != list(pks_actuels))
