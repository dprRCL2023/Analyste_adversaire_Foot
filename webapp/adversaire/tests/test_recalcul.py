"""Étape 3b : `recalculer_indicateurs`, depuis le brut stocké, sans réseau.

Le garde-fou réseau de `tests/__init__.py` reste actif : tout appel réel ferait
échouer le test."""
import io
from datetime import date
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase

from adversaire import importation
from adversaire.analyse import fiche, indicateurs
from adversaire.models import (AdversaireSuivi, Competition, DonneesMatch, Equipe,
                               FicheAdversaire, IndicateursMatchEquipe, Match)

from . import donnees_inventees as inv
from .test_import import FauxClient, importer


class RecalculTest(TestCase):
    def setUp(self):
        importer(FauxClient([
            inv.match(601, "2026-10-01", statut_360="available", version_360="v360-a"),
            inv.match(602, "2026-10-08", domicile=103, exterieur=inv.ADVERSAIRE),
        ]))

    def lancer(self, *args):
        sortie, erreurs = io.StringIO(), io.StringIO()
        call_command("recalculer_indicateurs", *args, stdout=sortie, stderr=erreurs)
        return sortie.getvalue(), erreurs.getvalue()

    def versions(self):
        return dict(IndicateursMatchEquipe.objects
                    .values_list("match__sb_match_id", "version_calcul").distinct())

    def test_rien_de_perime(self):
        self.assertEqual(importation.matchs_a_recalculer(), [])
        sortie, _ = self.lancer()
        self.assertIn("0 match(s) recalculé(s), 0 en échec, 0 sans brut.", sortie)

    def test_version_de_calcul_augmentee_seuls_les_perimes(self):
        nouvelle = indicateurs.VERSION_CALCUL + 1
        with mock.patch.object(indicateurs, "VERSION_CALCUL", nouvelle):
            self.assertEqual(len(importation.matchs_a_recalculer()), 2)
            self.lancer("--match", "601")
            self.assertEqual(importation.matchs_a_recalculer(),
                             [Match.objects.get(sb_match_id=602).pk])
            sortie, _ = self.lancer()
            self.assertEqual(importation.matchs_a_recalculer(), [])
        self.assertIn("1 match(s) recalculé(s)", sortie)
        self.assertEqual(self.versions(), {601: nouvelle, 602: nouvelle})
        self.assertEqual(IndicateursMatchEquipe.objects.count(), 4)

    def test_ligne_manquante_recalculee(self):
        IndicateursMatchEquipe.objects.filter(match__sb_match_id=602,
                                              equipe__sb_team_id=103).delete()
        self.assertEqual(importation.matchs_a_recalculer(),
                         [Match.objects.get(sb_match_id=602).pk])
        self.lancer()
        self.assertEqual(IndicateursMatchEquipe.objects.count(), 4)

    def test_tous(self):
        sortie, _ = self.lancer("--tous")
        self.assertIn("2 match(s) recalculé(s), 0 en échec, 0 sans brut.", sortie)

    def test_match_sans_brut_signale(self):
        Match.objects.create(sb_match_id=700, competition=Competition.objects.get(), date=date(2026, 9, 1),
                             domicile=Equipe.objects.get(sb_team_id=inv.ADVERSAIRE),
                             exterieur=Equipe.objects.get(sb_team_id=102))
        sortie, erreurs = self.lancer("--tous")
        self.assertIn("2 match(s) recalculé(s), 0 en échec, 1 sans brut.", sortie)
        self.assertIn("Sans brut : match 700", erreurs)
        sortie, erreurs = self.lancer("--match", "700")
        self.assertIn("1 sans brut", sortie)

    def test_match_inconnu(self):
        with self.assertRaisesMessage(CommandError, "absent de la base"):
            self.lancer("--match", "999999")

    def test_echec_de_calcul_signale_sans_contenu(self):
        with mock.patch("adversaire.analyse.indicateurs.calculer",
                        side_effect=KeyError("contenu à ne pas afficher")):
            sortie, erreurs = self.lancer("--tous")
        self.assertIn("0 match(s) recalculé(s), 2 en échec", sortie)
        self.assertIn("Échec du calcul : match 601 (KeyError)", erreurs)
        self.assertNotIn("contenu à ne pas afficher", erreurs)
        self.assertEqual(IndicateursMatchEquipe.objects.count(), 4)  # anciennes lignes gardées

    def test_brut_illisible_signale_et_les_autres_recalcules(self):
        DonneesMatch.objects.filter(match__sb_match_id=601).update(evenements_z=b"pas du zlib")
        sortie, erreurs = self.lancer("--tous")
        self.assertIn("1 match(s) recalculé(s), 1 en échec, 0 sans brut.", sortie)
        self.assertIn("Échec du calcul : match 601", erreurs)

    def test_aucune_sortie_ne_porte_un_nom_de_joueur(self):
        sortie, erreurs = self.lancer("--tous")
        self.assertNotIn(inv.JOUEUR_INVENTE, sortie + erreurs)

    # ── Fiches (étape 3c-1) ──────────────────────────────────────────────────
    def test_indicateurs_v1_vers_v2_puis_fiches_reconstruites(self):
        """Base d'avant 3c-1 : lignes en version 1, fiche absente."""
        IndicateursMatchEquipe.objects.update(version_calcul=1)
        FicheAdversaire.objects.all().delete()
        self.assertEqual(len(importation.matchs_a_recalculer()), 2)
        sortie, _ = self.lancer()
        self.assertIn("2 match(s) recalculé(s)", sortie)
        self.assertIn("1 fiche(s) reconstruite(s), 0 en échec.", sortie)
        self.assertEqual(set(IndicateursMatchEquipe.objects.values_list("version_calcul", flat=True)),
                         {indicateurs.VERSION_CALCUL})
        f = FicheAdversaire.objects.get()
        self.assertEqual((f.version_fiche, f.nb_matchs_calcules), (fiche.VERSION_FICHE, 2))

    def test_fiche_d_une_autre_version_reconstruite(self):
        FicheAdversaire.objects.update(version_fiche=0, valeurs={})
        sortie, _ = self.lancer()
        self.assertIn("0 match(s) recalculé(s)", sortie)
        self.assertIn("1 fiche(s) reconstruite(s)", sortie)
        f = FicheAdversaire.objects.get()
        self.assertEqual(f.version_fiche, fiche.VERSION_FICHE)
        self.assertEqual(f.valeurs["nb_matchs_calcules"], 2)

    def test_fiche_v1_reconstruite_en_v2_sans_relire_le_brut(self):
        """Étape 3c-2 : seules les règles de la fiche changent ; les indicateurs
        v2 sont à jour, aucun calcul sur le brut ne doit être relancé."""
        FicheAdversaire.objects.update(version_fiche=1)
        with mock.patch("adversaire.analyse.indicateurs.calculer",
                        side_effect=AssertionError("brut relu")):
            sortie, erreurs = self.lancer()
        self.assertIn("0 match(s) recalculé(s), 0 en échec, 0 sans brut.", sortie)
        self.assertIn("1 fiche(s) reconstruite(s), 0 en échec.", sortie)
        f = FicheAdversaire.objects.get()
        self.assertEqual((f.version_fiche, f.valeurs["version_fiche"]), (2, 2))
        self.assertIn("barres_xg", f.valeurs["danger"])
        self.assertIn("min_non_vide", f.valeurs["cpa"]["chutes_corners_contre"])

    def test_echec_de_fiche_signale_sans_contenu_ancienne_gardee(self):
        avant = FicheAdversaire.objects.get().valeurs
        with mock.patch("adversaire.analyse.fiche.construire",
                        side_effect=ValueError("contenu à ne pas afficher")):
            sortie, erreurs = self.lancer()
        suivi = AdversaireSuivi.objects.get()
        self.assertIn("0 fiche(s) reconstruite(s), 1 en échec.", sortie)
        self.assertIn(f"Échec de la fiche : suivi {suivi.pk} (ValueError)", erreurs)
        self.assertNotIn("contenu à ne pas afficher", erreurs)
        self.assertEqual(FicheAdversaire.objects.get().valeurs, avant)
