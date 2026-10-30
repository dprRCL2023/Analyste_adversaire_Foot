"""Étape 2 : règles d'import et commande, avec un faux client (aucun réseau)."""
import http.client
import io
from datetime import date
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from adversaire import importation
from adversaire.analyse import fiche, indicateurs
from adversaire.clients.statsbomb import ErreurStatsBomb
from adversaire.models import (AdversaireSuivi, Competition, DonneesMatch, Equipe,
                               FicheAdversaire, IndicateursMatchEquipe, Match, compresser,
                               decompresser)

from . import donnees_inventees as inv

COMMANDE = "adversaire.management.commands.importer_adversaire.StatsBomb"


class FauxClient:
    """Même interface que StatsBomb, nourri de JSON inventés ; note ses appels."""

    def __init__(self, matchs, couvert=True, en_panne=()):
        self._matchs = matchs
        self.par_saison = {(inv.COMPETITION, inv.SAISON): 1} if couvert else {}
        self.en_panne = set(en_panne)
        self.appels = []

    def catalogue(self):
        return inv.catalogue() if self.par_saison else []

    def compte_saison(self, competition_id, season_id):
        return self.par_saison.get((competition_id, season_id))

    def matchs(self, competition_id, season_id):
        return self._matchs

    def _appel(self, quoi, match_id, donnees):
        self.appels.append((quoi, match_id))
        if match_id in self.en_panne:
            raise ErreurStatsBomb(f"StatsBomb a répondu 503 sur /v8/events/{match_id}")
        return donnees

    def evenements(self, match_id):
        return self._appel("evenements", match_id, inv.evenements(match_id))

    def compositions(self, match_id):
        return self._appel("compositions", match_id, inv.compositions(match_id))

    def frames_360(self, match_id):
        return self._appel("360", match_id, inv.frames_360(match_id))

    def appels_de(self, quoi):
        return sorted(m for q, m in self.appels if q == quoi)


def importer(client, **options):
    return importation.importer_adversaire(client, inv.COMPETITION, inv.SAISON,
                                           inv.ADVERSAIRE, **options)


class CompressionTest(SimpleTestCase):
    def test_aller_retour(self):
        objet = {"liste": [1, 2.5, None, "é"], "ok": True}
        self.assertEqual(decompresser(compresser(objet)), objet)
        self.assertIsNone(decompresser(None))
        self.assertIsNone(decompresser(b""))

    def test_proprietes(self):
        donnees = DonneesMatch()
        donnees.evenements = inv.evenements(1)
        donnees.frames_360 = None
        self.assertEqual(donnees.evenements, inv.evenements(1))
        self.assertIsNone(donnees.frames_360_z)
        self.assertIsNone(donnees.frames_360)


class MigrationTest(TestCase):
    def test_migrations_a_jour_avec_les_modeles(self):
        """`makemigrations --check` sort en erreur si un modèle diverge des migrations."""
        call_command("makemigrations", "adversaire", "--check", "--dry-run",
                     stdout=io.StringIO(), stderr=io.StringIO())


class DerniersMatchsTest(SimpleTestCase):
    def test_cinq_derniers_joues_de_l_equipe(self):
        matchs = [inv.match(100 + j, f"2026-09-{j:02d}") for j in range(1, 8)]
        matchs += [
            inv.match(200, "2026-10-20", statut="scheduled"),          # pas encore joué
            inv.match(201, "2026-10-21", domicile=103, exterieur=104),  # autre équipe
            inv.match(202, "2026-10-01", domicile=105, exterieur=inv.ADVERSAIRE),  # à l'extérieur
        ]
        retenus, erreurs = importation.derniers_matchs(matchs, inv.ADVERSAIRE)
        self.assertEqual([m["id"] for m in retenus], [202, 107, 106, 105, 104])
        self.assertEqual(erreurs, [])

    def test_egalite_de_date(self):
        matchs = [inv.match(300, "2026-10-05", kick_off="15:00:00.000"),
                  inv.match(301, "2026-10-05", kick_off="20:00:00.000"),
                  inv.match(302, "2026-10-05", kick_off="20:00:00.000"),
                  inv.match(303, "2026-10-05", kick_off=None)]
        retenus, _ = importation.derniers_matchs(matchs, inv.ADVERSAIRE)
        self.assertEqual([m["id"] for m in retenus], [302, 301, 300, 303])

    def test_match_mal_forme_signale_et_ignore(self):
        mauvais_id = inv.match(400, "2026-10-01")
        mauvais_id["match_id"] = "400"
        mauvaise_date = inv.match(401, "2026-10-01")
        mauvaise_date["match_date"] = "pas-une-date"
        sans_equipe = inv.match(402, "2026-10-01")
        del sans_equipe["home_team"]
        mauvais_score = inv.match(403, "2026-10-01", scores=("deux", 0))
        retenus, erreurs = importation.derniers_matchs(
            [mauvais_id, mauvaise_date, sans_equipe, mauvais_score, "n'importe quoi",
             inv.match(404, "2026-10-02")], inv.ADVERSAIRE)
        self.assertEqual([m["id"] for m in retenus], [404])
        self.assertEqual(len(erreurs), 5)
        self.assertTrue(any(e.startswith("match 401") for e in erreurs))

    def test_normalisation(self):
        m = importation.normaliser_match(inv.match(500, "2026-10-03T00:00:00",
                                                   version_360="2026-10-04T08:00:00"))
        self.assertEqual(m["date"], date(2026, 10, 3))
        self.assertEqual(m["coup_envoi"].hour, 20)
        self.assertEqual(m["domicile"], {"id": inv.ADVERSAIRE, "nom": inv.EQUIPES[inv.ADVERSAIRE]})
        self.assertEqual(m["version_360"], "2026-10-04T08:00:00")


class ImportTest(TestCase):
    def setUp(self):
        self.matchs = [
            inv.match(601, "2026-10-01", statut_360="available", version_360="v360-a"),
            inv.match(602, "2026-10-08", domicile=103, exterieur=inv.ADVERSAIRE),
        ]

    def test_premier_import(self):
        client = FauxClient(self.matchs)
        bilan = importer(client)
        self.assertEqual((bilan.retenus, bilan.telecharges, bilan.a_jour), (2, 2, 0))
        self.assertEqual((bilan.avec_360, bilan.sans_360), (1, 1))
        competition = Competition.objects.get()
        self.assertEqual((competition.nom, competition.compte), ("Ligue Inventée", 1))
        self.assertEqual(Equipe.objects.get(sb_team_id=103).nom, inv.EQUIPES[103])
        match = Match.objects.get(sb_match_id=602)
        self.assertEqual((match.domicile.sb_team_id, match.exterieur.sb_team_id),
                         (103, inv.ADVERSAIRE))
        self.assertEqual(match.donnees.evenements, inv.evenements(602))
        self.assertEqual(match.donnees.compositions, inv.compositions(602))

    def test_360_seulement_si_disponible(self):
        client = FauxClient(self.matchs)
        importer(client)
        self.assertEqual(client.appels_de("360"), [601])
        self.assertIsNone(DonneesMatch.objects.get(match__sb_match_id=602).frames_360)
        frames = DonneesMatch.objects.get(match__sb_match_id=601).frames_360
        # Images sans zone visible, sans acteur ou sans joueur : gardées telles quelles.
        self.assertEqual(frames, inv.frames_360(601))
        self.assertEqual(frames[2]["freeze_frame"], [])
        self.assertNotIn("visible_area", frames[2])

    def test_sans_360_force(self):
        client = FauxClient(self.matchs)
        bilan = importer(client, sans_360=True)
        self.assertEqual(client.appels_de("360"), [])
        self.assertEqual(bilan.sans_360, 2)

    def test_relance_sans_changement_ne_telecharge_rien(self):
        importer(FauxClient(self.matchs))
        client = FauxClient(self.matchs)
        bilan = importer(client)
        self.assertEqual(client.appels, [])
        self.assertEqual((bilan.telecharges, bilan.a_jour, bilan.avec_360), (0, 2, 1))

    def test_last_updated_change_relit_evenements_et_compositions(self):
        importer(FauxClient(self.matchs))
        self.matchs[0]["last_updated"] = "2026-10-15T09:00:00.000"
        client = FauxClient(self.matchs)
        bilan = importer(client)
        self.assertEqual(client.appels_de("evenements"), [601])
        self.assertEqual(client.appels_de("compositions"), [601])
        self.assertEqual(client.appels_de("360"), [])
        self.assertEqual(bilan.telecharges, 1)
        donnees = DonneesMatch.objects.get(match__sb_match_id=601)
        self.assertEqual(donnees.version, "2026-10-15T09:00:00.000")
        self.assertIsNotNone(donnees.frames_360)  # 360 conservé

    def test_seul_last_updated_360_change(self):
        importer(FauxClient(self.matchs))
        self.matchs[0]["last_updated_360"] = "v360-b"
        client = FauxClient(self.matchs)
        importer(client)
        self.assertEqual(client.appels, [("360", 601)])
        self.assertEqual(DonneesMatch.objects.get(match__sb_match_id=601).version_360, "v360-b")

    def test_360_devenu_disponible(self):
        importer(FauxClient(self.matchs))
        self.matchs[1]["match_status_360"] = "available"  # sans last_updated_360
        client = FauxClient(self.matchs)
        importer(client)
        self.assertEqual(client.appels, [("360", 602)])
        self.assertIsNotNone(DonneesMatch.objects.get(match__sb_match_id=602).frames_360)

    def test_forcer_relit_tout(self):
        importer(FauxClient(self.matchs))
        client = FauxClient(self.matchs)
        importer(client, forcer=True)
        self.assertEqual(client.appels_de("evenements"), [601, 602])
        self.assertEqual(client.appels_de("360"), [601])

    def test_score_mis_a_jour_meme_sans_retelechargement(self):
        importer(FauxClient(self.matchs))
        self.matchs[0]["home_score"] = 4
        importer(FauxClient(self.matchs))
        self.assertEqual(Match.objects.get(sb_match_id=601).score_domicile, 4)

    def test_panne_sur_un_match_reprise_au_suivant(self):
        client = FauxClient(self.matchs, en_panne={602})
        bilan = importer(client)
        self.assertEqual(bilan.telecharges, 1)
        self.assertEqual(len(bilan.erreurs), 1)
        self.assertTrue(bilan.erreurs[0].startswith("match 602"))
        self.assertFalse(Match.objects.filter(sb_match_id=602).exists())
        self.assertTrue(DonneesMatch.objects.filter(match__sb_match_id=601).exists())

    def test_reponse_tronquee_reprise_au_suivant(self):
        client = FauxClient(self.matchs)
        evenements = client.evenements

        def tronque(match_id):
            if match_id == 602:
                raise http.client.IncompleteRead(b"[{")
            return evenements(match_id)

        client.evenements = tronque
        bilan = importer(client)
        self.assertEqual(bilan.telecharges, 1)
        self.assertEqual(bilan.erreurs, ["match 602 : réponse interrompue (IncompleteRead)"])
        self.assertFalse(Match.objects.filter(sb_match_id=602).exists())
        self.assertTrue(DonneesMatch.objects.filter(match__sb_match_id=601).exists())

    def test_reponse_inattendue_ignoree(self):
        client = FauxClient(self.matchs)
        client.evenements = lambda match_id: {"pas": "une liste"}
        bilan = importer(client)
        self.assertEqual(bilan.telecharges, 0)
        self.assertEqual(len(bilan.erreurs), 2)
        self.assertFalse(Match.objects.exists())

    def test_competition_non_couverte(self):
        with self.assertRaisesMessage(ErreurStatsBomb, "absente du catalogue"):
            importer(FauxClient(self.matchs, couvert=False))
        self.assertFalse(AdversaireSuivi.objects.exists())

    def test_suivi_cree_puis_redate(self):
        """Étape 3a : l'équipe CIBLÉE est notée, pas ses adversaires."""
        bilan = importer(FauxClient(self.matchs))
        self.assertTrue(bilan.suivi)
        suivi = AdversaireSuivi.objects.select_related("equipe", "competition").get()
        self.assertEqual(suivi.equipe.sb_team_id, inv.ADVERSAIRE)
        self.assertEqual(suivi.competition.sb_competition_id, inv.COMPETITION)
        premier = suivi.importe_le
        bilan = importer(FauxClient(self.matchs))  # rien à retélécharger
        self.assertEqual(bilan.telecharges, 0)
        suivi.refresh_from_db()
        self.assertEqual(AdversaireSuivi.objects.count(), 1)
        self.assertGreaterEqual(suivi.importe_le, premier)

    def test_suivi_de_chaque_adversaire_importe(self):
        importer(FauxClient(self.matchs))
        importation.importer_adversaire(FauxClient(self.matchs), inv.COMPETITION,
                                        inv.SAISON, 103)
        self.assertEqual(sorted(AdversaireSuivi.objects.values_list("equipe__sb_team_id",
                                                                    flat=True)),
                         [inv.ADVERSAIRE, 103])

    def test_aucun_suivi_si_equipe_absente_de_la_saison(self):
        bilan = importation.importer_adversaire(FauxClient(self.matchs), inv.COMPETITION,
                                                inv.SAISON, 999)
        self.assertFalse(bilan.suivi)
        self.assertEqual(bilan.retenus, 0)
        self.assertFalse(AdversaireSuivi.objects.exists())
        self.assertFalse(Equipe.objects.filter(sb_team_id=999).exists())


class IndicateursImportTest(TestCase):
    """Étape 3b : indicateurs calculés à l'import, dans la transaction du brut."""

    def setUp(self):
        self.matchs = [
            inv.match(601, "2026-10-01", statut_360="available", version_360="v360-a"),
            inv.match(602, "2026-10-08", domicile=103, exterieur=inv.ADVERSAIRE),
        ]

    def lignes(self, sb_match_id):
        return {ligne.equipe.sb_team_id: ligne for ligne in
                IndicateursMatchEquipe.objects.filter(match__sb_match_id=sb_match_id)
                .select_related("equipe")}

    def test_deux_lignes_par_match_telecharge(self):
        bilan = importer(FauxClient(self.matchs))
        self.assertEqual(bilan.indicateurs, 4)
        self.assertEqual(bilan.erreurs_calcul, [])
        lignes = self.lignes(601)
        self.assertEqual(sorted(lignes), [inv.ADVERSAIRE, 102])
        ligne = lignes[inv.ADVERSAIRE]
        self.assertEqual(ligne.version_calcul, indicateurs.VERSION_CALCUL)
        self.assertEqual(ligne.version_source, "2026-10-01T10:00:00.000|v360-a")
        self.assertTrue(ligne.avec_360)
        self.assertEqual(ligne.valeurs["systeme"]["formation_depart"], "442")
        self.assertIsNotNone(ligne.valeurs["trois_cent_soixante"])
        sans_360 = self.lignes(602)[inv.ADVERSAIRE]
        self.assertFalse(sans_360.avec_360)
        self.assertTrue(sans_360.version_source.endswith("|-"))
        self.assertIsNone(sans_360.valeurs["trois_cent_soixante"])

    def test_relance_a_jour_ne_recalcule_ni_ne_decompresse(self):
        importer(FauxClient(self.matchs))
        with mock.patch("adversaire.analyse.indicateurs.calculer") as calcul, \
                mock.patch("adversaire.models.decompresser") as decompression:
            bilan = importer(FauxClient(self.matchs))
        calcul.assert_not_called()
        decompression.assert_not_called()
        self.assertEqual((bilan.a_jour, bilan.indicateurs), (2, 0))

    def test_last_updated_modifie_recalcule(self):
        importer(FauxClient(self.matchs))
        self.matchs[0]["last_updated"] = "2026-10-15T09:00:00.000"
        bilan = importer(FauxClient(self.matchs))
        self.assertEqual(bilan.indicateurs, 2)
        ligne = self.lignes(601)[inv.ADVERSAIRE]
        # 360 conservé en base et toujours utilisé par le calcul.
        self.assertEqual(ligne.version_source, "2026-10-15T09:00:00.000|v360-a")
        self.assertTrue(ligne.avec_360)

    def test_seul_le_360_relu_recalcule_avec_les_evenements_stockes(self):
        importer(FauxClient(self.matchs))
        self.matchs[1]["match_status_360"] = "available"
        self.matchs[1]["last_updated_360"] = "v360-z"
        client = FauxClient(self.matchs)
        importer(client)
        self.assertEqual(client.appels, [("360", 602)])
        ligne = self.lignes(602)[inv.ADVERSAIRE]
        self.assertTrue(ligne.avec_360)
        self.assertTrue(ligne.version_source.endswith("|v360-z"))
        # Événements non retéléchargés : relus en base pour le calcul.
        self.assertEqual(ligne.valeurs["systeme"]["formation_depart"], "442")

    def test_lignes_perimees_recalculees_sans_reseau(self):
        importer(FauxClient(self.matchs))
        client = FauxClient(self.matchs)
        with mock.patch.object(indicateurs, "VERSION_CALCUL", indicateurs.VERSION_CALCUL + 1):
            bilan = importer(client)
        self.assertEqual(client.appels, [])
        self.assertEqual((bilan.a_jour, bilan.indicateurs), (2, 4))
        self.assertEqual(set(IndicateursMatchEquipe.objects.values_list("version_calcul", flat=True)),
                         {indicateurs.VERSION_CALCUL + 1})

    def test_erreur_de_calcul_brut_garde(self):
        with mock.patch("adversaire.analyse.indicateurs.calculer",
                        side_effect=ZeroDivisionError("contenu à ne pas afficher")):
            bilan = importer(FauxClient(self.matchs))
        self.assertEqual(bilan.telecharges, 2)
        self.assertEqual(bilan.indicateurs, 0)
        self.assertEqual(len(bilan.erreurs_calcul), 2)
        self.assertIn("match 601", " ".join(bilan.erreurs_calcul))
        self.assertIn("ZeroDivisionError", bilan.erreurs_calcul[0])
        self.assertNotIn("contenu à ne pas afficher", " ".join(bilan.erreurs_calcul))
        self.assertEqual(DonneesMatch.objects.count(), 2)
        self.assertFalse(IndicateursMatchEquipe.objects.exists())
        # Au passage suivant, le match « déjà à jour » est rattrapé.
        bilan = importer(FauxClient(self.matchs))
        self.assertEqual((bilan.a_jour, bilan.indicateurs), (2, 4))

    def test_brut_stocke_illisible_le_360_est_ecrit_quand_meme(self):
        importer(FauxClient(self.matchs))
        DonneesMatch.objects.filter(match__sb_match_id=602).update(evenements_z=b"pas du zlib")
        self.matchs[1]["match_status_360"] = "available"
        self.matchs[1]["last_updated_360"] = "v360-z"
        bilan = importer(FauxClient(self.matchs))
        self.assertEqual(bilan.telecharges, 1)
        self.assertEqual(len(bilan.erreurs_calcul), 1)
        self.assertIn("match 602", bilan.erreurs_calcul[0])
        donnees = DonneesMatch.objects.get(match__sb_match_id=602)
        self.assertEqual(donnees.version_360, "v360-z")
        self.assertIsNotNone(donnees.frames_360_z)

    def test_brut_illisible_au_rattrapage_n_arrete_pas_l_import(self):
        importer(FauxClient(self.matchs))
        DonneesMatch.objects.filter(match__sb_match_id=601).update(evenements_z=b"pas du zlib")
        with mock.patch.object(indicateurs, "VERSION_CALCUL", indicateurs.VERSION_CALCUL + 1):
            bilan = importer(FauxClient(self.matchs))
        self.assertEqual((bilan.a_jour, bilan.indicateurs), (2, 2))
        self.assertEqual(len(bilan.erreurs_calcul), 1)
        self.assertIn("match 601", bilan.erreurs_calcul[0])


class FicheImportTest(TestCase):
    """Étape 3c-1 : fiche de l'adversaire reconstruite en fin d'import."""

    def setUp(self):
        self.matchs = [
            inv.match(601, "2026-10-01", statut_360="available", version_360="v360-a"),
            inv.match(602, "2026-10-08", domicile=103, exterieur=inv.ADVERSAIRE),
        ]

    def test_fiche_creee_en_fin_d_import(self):
        bilan = importer(FauxClient(self.matchs))
        self.assertEqual((bilan.fiche, bilan.erreur_fiche), (2, ""))
        f = FicheAdversaire.objects.select_related("suivi__equipe").get()
        self.assertEqual(f.suivi.equipe.sb_team_id, inv.ADVERSAIRE)
        self.assertEqual(f.version_fiche, fiche.VERSION_FICHE)
        # Du plus récent au plus ancien, comme la page.
        self.assertEqual(f.matchs, [Match.objects.get(sb_match_id=602).pk,
                                    Match.objects.get(sb_match_id=601).pk])
        self.assertEqual((f.nb_matchs_calcules, f.valeurs["matchs_avec_360"]), (2, 1))
        self.assertFalse(importation.fiche_perimee(f, f.matchs))

    def test_fiche_mise_a_jour_a_l_import_suivant(self):
        importer(FauxClient(self.matchs))
        FicheAdversaire.objects.update(valeurs={}, nb_matchs_calcules=0)
        bilan = importer(FauxClient(self.matchs))  # rien à retélécharger
        self.assertEqual(bilan.telecharges, 0)
        f = FicheAdversaire.objects.get()
        self.assertEqual(f.nb_matchs_calcules, 2)
        self.assertEqual(f.valeurs["nb_matchs_calcules"], 2)
        self.assertEqual(FicheAdversaire.objects.count(), 1)

    def test_bilan_domicile_exterieur_et_possession_dans_la_fiche(self):
        """601 : l'adversaire reçoit 102 ; 602 : il va chez 103. « Contre » se lit
        sur la ligne de l'équipe rencontrée de CHAQUE match."""
        importer(FauxClient(self.matchs))

        def modifier(sb_match_id, sb_team_id, buts=None, passes=None):
            ligne = IndicateursMatchEquipe.objects.get(match__sb_match_id=sb_match_id,
                                                       equipe__sb_team_id=sb_team_id)
            if buts is not None:
                ligne.valeurs["resultat"]["buts_pour"] = buts
            if passes is not None:
                ligne.valeurs["attaque"]["passes"] = passes
            ligne.save()

        modifier(601, inv.ADVERSAIRE, buts=3, passes=1)
        modifier(601, 102, passes=0)
        modifier(602, inv.ADVERSAIRE, buts=1, passes=1)
        modifier(602, 103, passes=3)
        importation.construire_fiche(AdversaireSuivi.objects.get())
        r = FicheAdversaire.objects.get().valeurs["resultats"]
        self.assertEqual((r["domicile"]["matchs"], r["domicile"]["buts_pour"]), (1, 3))
        self.assertEqual((r["exterieur"]["matchs"], r["exterieur"]["buts_pour"]), (1, 1))
        self.assertEqual(r["total"]["buts_pour"], 4)
        # Sommes avant ratio : 2 passes sur 2 + 3 = 40 %, pas la moyenne de 100 % et 25 %.
        self.assertEqual(r["domicile"]["possession_pct"], 100.0)
        self.assertEqual(r["exterieur"]["possession_pct"], 25.0)
        self.assertEqual(r["total"]["possession_pct"], 40.0)

    def test_lignes_d_une_ancienne_version_ignorees(self):
        importer(FauxClient(self.matchs))
        IndicateursMatchEquipe.objects.update(version_calcul=1)
        suivi = AdversaireSuivi.objects.get()
        self.assertEqual(importation.construire_fiche(suivi), 0)

    def test_erreur_de_fiche_ancienne_fiche_gardee(self):
        importer(FauxClient(self.matchs))
        avant = FicheAdversaire.objects.get().valeurs
        with mock.patch("adversaire.analyse.fiche.construire",
                        side_effect=KeyError("contenu à ne pas afficher")):
            bilan = importer(FauxClient(self.matchs))
        self.assertEqual(bilan.erreur_fiche, "KeyError")
        self.assertEqual(FicheAdversaire.objects.get().valeurs, avant)

    def test_pas_de_fiche_sans_suivi(self):
        importation.importer_adversaire(FauxClient(self.matchs), inv.COMPETITION, inv.SAISON, 999)
        self.assertFalse(FicheAdversaire.objects.exists())


class CommandeTest(TestCase):
    def lancer(self, client, *args):
        sortie, erreurs = io.StringIO(), io.StringIO()
        with mock.patch(COMMANDE, return_value=client):
            call_command("importer_adversaire", *args, stdout=sortie, stderr=erreurs)
        return sortie.getvalue(), erreurs.getvalue()

    def test_catalogue(self):
        sortie, _ = self.lancer(FauxClient([]), "--catalogue")
        self.assertIn(f"--competition {inv.COMPETITION} --saison {inv.SAISON}", sortie)
        self.assertIn("Ligue Inventée 2026/2027", sortie)
        self.assertIn("(compte 2)", sortie)

    def test_equipes(self):
        matchs = [inv.match(701, "2026-10-01"), inv.match(702, "2026-10-02", 104, 105)]
        sortie, _ = self.lancer(FauxClient(matchs), "--competition", str(inv.COMPETITION),
                                "--saison", str(inv.SAISON), "--equipes")
        for ident in (inv.ADVERSAIRE, 102, 104, 105):
            self.assertIn(f"--equipe {ident}  {inv.EQUIPES[ident]}", sortie)

    def test_import_et_bilan_sans_nom_de_joueur(self):
        matchs = [inv.match(801, "2026-10-01"), inv.match(802, "2026-10-02")]
        matchs[0]["match_date"] = "illisible"
        sortie, erreurs = self.lancer(
            FauxClient(matchs), "--competition", str(inv.COMPETITION),
            "--saison", str(inv.SAISON), "--equipe", str(inv.ADVERSAIRE))
        self.assertIn("1 match(s) retenu(s) : 1 téléchargé(s)", sortie)
        self.assertIn("1 ignoré(s)", sortie)
        self.assertIn("2 ligne(s) d'indicateurs calculée(s), 0 erreur(s) de calcul", sortie)
        self.assertIn("Fiche adversaire calculée sur 1 match(s).", sortie)
        self.assertIn("Ignoré : match 801", erreurs)
        self.assertNotIn(inv.JOUEUR_INVENTE, sortie + erreurs)

    def test_echec_de_la_fiche_signale_sans_contenu(self):
        with mock.patch("adversaire.analyse.fiche.construire",
                        side_effect=KeyError("contenu à ne pas afficher")):
            _, erreurs = self.lancer(
                FauxClient([inv.match(803, "2026-10-01")]), "--competition", str(inv.COMPETITION),
                "--saison", str(inv.SAISON), "--equipe", str(inv.ADVERSAIRE))
        self.assertIn("Fiche adversaire : calcul en échec (KeyError)", erreurs)
        self.assertNotIn("contenu à ne pas afficher", erreurs)

    def test_equipe_absente_signalee(self):
        sortie, erreurs = self.lancer(
            FauxClient([inv.match(901, "2026-10-01")]), "--competition", str(inv.COMPETITION),
            "--saison", str(inv.SAISON), "--equipe", "999")
        self.assertIn("0 match(s) retenu(s)", sortie)
        self.assertIn("n'apparaîtra pas", erreurs)

    def test_arguments_obligatoires(self):
        with self.assertRaisesMessage(CommandError, "--competition et --saison"):
            self.lancer(FauxClient([]), "--equipe", "101")
        with self.assertRaisesMessage(CommandError, "Préciser"):
            self.lancer(FauxClient([]))

    def test_competition_non_couverte(self):
        with self.assertRaisesMessage(CommandError, "absente du catalogue"):
            self.lancer(FauxClient([], couvert=False), "--competition", str(inv.COMPETITION),
                        "--saison", str(inv.SAISON), "--equipe", str(inv.ADVERSAIRE))
