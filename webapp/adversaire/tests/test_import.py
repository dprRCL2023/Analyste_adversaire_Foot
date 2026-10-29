"""Étape 2 : règles d'import et commande, avec un faux client (aucun réseau)."""
import http.client
import io
from datetime import date
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from adversaire import importation
from adversaire.clients.statsbomb import ErreurStatsBomb
from adversaire.models import Competition, DonneesMatch, Equipe, Match, compresser, decompresser

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
        self.assertIn("Ignoré : match 801", erreurs)
        self.assertNotIn(inv.JOUEUR_INVENTE, sortie + erreurs)

    def test_arguments_obligatoires(self):
        with self.assertRaisesMessage(CommandError, "--competition et --saison"):
            self.lancer(FauxClient([]), "--equipe", "101")
        with self.assertRaisesMessage(CommandError, "Préciser"):
            self.lancer(FauxClient([]))

    def test_competition_non_couverte(self):
        with self.assertRaisesMessage(CommandError, "absente du catalogue"):
            self.lancer(FauxClient([], couvert=False), "--competition", str(inv.COMPETITION),
                        "--saison", str(inv.SAISON), "--equipe", str(inv.ADVERSAIRE))
