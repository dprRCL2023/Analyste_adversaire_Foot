"""Étape 3a : page « Adversaire du week-end » (matchs importés, sans calcul ni réseau).

Données INVENTÉES (cf. `donnees_inventees.py`), écrites directement en base."""
import re
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from adversaire.models import (AdversaireSuivi, Competition, DonneesMatch, Equipe,
                               IndicateursMatchEquipe, Match)

from . import donnees_inventees as inv

URL = reverse("accueil")


class PageAdversaireTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.competition = Competition.objects.create(
            sb_competition_id=inv.COMPETITION, sb_season_id=inv.SAISON,
            nom="Ligue Inventée", saison="2026/2027")
        cls.equipes = {i: Equipe.objects.create(sb_team_id=i, nom=nom)
                       for i, nom in inv.EQUIPES.items()}
        compte = get_user_model().objects.create_user("staff-test", password="x")
        compte.groups.add(Group.objects.get(name="staff"))
        cls.compte = compte

    def setUp(self):
        self.client.force_login(self.compte)
        self.ident = 1000

    # ── Aides ────────────────────────────────────────────────────────────────
    def match(self, jour, domicile, exterieur, scores=(1, 0), avec_360=False,
              competition=None, coup_envoi=time(20, 0)):
        self.ident += 1
        m = Match.objects.create(
            sb_match_id=self.ident, competition=competition or self.competition,
            date=jour, coup_envoi=coup_envoi,
            domicile=self.equipes[domicile], exterieur=self.equipes[exterieur],
            score_domicile=scores[0], score_exterieur=scores[1], journee=3,
            statut="available", statut_360="available" if avec_360 else "unscheduled")
        donnees = DonneesMatch(match=m, version="v1")
        donnees.evenements = inv.evenements(m.sb_match_id)
        donnees.compositions = inv.compositions(m.sb_match_id)
        donnees.frames_360 = inv.frames_360(m.sb_match_id) if avec_360 else None
        donnees.save()
        return m

    def suivre(self, equipe, il_y_a=0):
        return AdversaireSuivi.objects.create(
            equipe=self.equipes[equipe], competition=self.competition,
            importe_le=timezone.now() - timedelta(hours=il_y_a))

    def lignes(self, reponse):
        return reponse.context["lignes"]

    # ── Cas ──────────────────────────────────────────────────────────────────
    def test_sans_suivi_message_et_commande(self):
        reponse = self.client.get(URL)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Aucun adversaire importé")
        self.assertContains(reponse, "importer_adversaire --competition C --saison S --equipe E")

    def test_suivi_le_plus_recent_par_defaut_et_choix(self):
        ancien = self.suivre(103, il_y_a=48)
        recent = self.suivre(inv.ADVERSAIRE, il_y_a=1)
        reponse = self.client.get(URL)
        self.assertEqual(reponse.context["suivi"], recent)
        self.assertContains(reponse, f'<option value="{ancien.id}"')
        reponse = self.client.get(URL, {"suivi": str(ancien.id)})
        self.assertEqual(reponse.context["suivi"], ancien)

    def test_parametre_suivi_invalide_ou_inconnu_404(self):
        suivi = self.suivre(inv.ADVERSAIRE)
        for valeur in ("abc", "", "1.5", str(suivi.id + 999), "-1"):
            with self.subTest(valeur=valeur):
                self.assertEqual(self.client.get(URL, {"suivi": valeur}).status_code, 404)

    def test_cinq_plus_recents_dans_l_ordre(self):
        self.suivre(inv.ADVERSAIRE)
        debut = date(2026, 9, 1)
        matchs = [self.match(debut + timedelta(days=7 * k), inv.ADVERSAIRE, 102)
                  for k in range(6)]
        reponse = self.client.get(URL)
        vus = [ligne["match"].id for ligne in self.lignes(reponse)]
        self.assertEqual(vus, [m.id for m in reversed(matchs[1:])])  # le 6e, plus ancien, absent

    def test_match_entre_deux_autres_equipes_absent(self):
        self.suivre(inv.ADVERSAIRE)
        garde = self.match(date(2026, 10, 1), inv.ADVERSAIRE, 102)
        self.match(date(2026, 10, 2), 103, 104)
        self.assertEqual([ligne["match"] for ligne in self.lignes(self.client.get(URL))], [garde])

    def test_autre_competition_absente(self):
        self.suivre(inv.ADVERSAIRE)
        coupe = Competition.objects.create(sb_competition_id=1, sb_season_id=2,
                                           nom="Coupe Inventée", saison="2026/2027")
        garde = self.match(date(2026, 10, 1), inv.ADVERSAIRE, 102)
        self.match(date(2026, 10, 5), inv.ADVERSAIRE, 103, competition=coupe)
        self.assertEqual([ligne["match"] for ligne in self.lignes(self.client.get(URL))], [garde])

    def test_resultat_vu_par_l_adversaire(self):
        self.suivre(inv.ADVERSAIRE)
        self.match(date(2026, 10, 1), inv.ADVERSAIRE, 102, scores=(2, 1))  # V à domicile
        self.match(date(2026, 10, 2), 103, inv.ADVERSAIRE, scores=(3, 0))  # D à l'extérieur
        self.match(date(2026, 10, 3), 104, inv.ADVERSAIRE, scores=(0, 2))  # V à l'extérieur
        self.match(date(2026, 10, 4), inv.ADVERSAIRE, 105, scores=(1, 1))  # N à domicile
        reponse = self.client.get(URL)
        lignes = self.lignes(reponse)
        self.assertEqual([ligne["resultat"] for ligne in lignes], ["N", "V", "D", "V"])
        self.assertEqual([(ligne["pour"], ligne["contre"]) for ligne in lignes],
                         [(1, 1), (2, 0), (0, 3), (2, 1)])
        self.assertEqual([ligne["a_domicile"] for ligne in lignes], [True, False, False, True])
        self.assertEqual(lignes[1]["rencontre"], self.equipes[104])
        bilan = reponse.context["bilan"]
        self.assertEqual(bilan["forme"], ["N", "V", "D", "V"])
        self.assertEqual((bilan["points"], bilan["pour"], bilan["contre"]), (7, 5, 5))
        self.assertContains(reponse, '<span class="res res-D">D</span>', html=True)

    def test_score_absent(self):
        self.suivre(inv.ADVERSAIRE)
        self.match(date(2026, 10, 1), inv.ADVERSAIRE, 102, scores=(None, None))
        reponse = self.client.get(URL)
        self.assertIsNone(self.lignes(reponse)[0]["resultat"])
        self.assertEqual(reponse.context["bilan"]["matchs"], 0)
        self.assertContains(reponse, "<td>—</td>", html=True)

    def test_badge_360(self):
        self.suivre(inv.ADVERSAIRE)
        self.match(date(2026, 10, 1), inv.ADVERSAIRE, 102, avec_360=True)
        self.match(date(2026, 10, 2), inv.ADVERSAIRE, 103, avec_360=False)
        reponse = self.client.get(URL)
        self.assertEqual([ligne["match"].a_360 for ligne in self.lignes(reponse)], [False, True])
        self.assertContains(reponse, 'class="badge-360"', count=1)

    def test_nombre_de_requetes_fixe_et_brut_jamais_lu(self):
        """Autant de requêtes pour 1 match que pour 5 (pas de N+1), et aucune
        colonne `*_z` lue : seul « frames_360_z IS NOT NULL » (annotation) est permis."""
        self.suivre(inv.ADVERSAIRE)
        self.match(date(2026, 9, 1), inv.ADVERSAIRE, 102, avec_360=True)
        with CaptureQueriesContext(connection) as un_match:
            self.client.get(URL)
        self.suivre(103, il_y_a=5)
        for k in range(5):
            self.match(date(2026, 9, 10 + k), 104, inv.ADVERSAIRE, avec_360=bool(k % 2))
        with self.assertNumQueries(len(un_match)):
            reponse = self.client.get(URL)
        self.assertEqual(len(self.lignes(reponse)), 5)
        with CaptureQueriesContext(connection) as requetes:
            self.client.get(URL)
        sql = " ".join(q["sql"] for q in requetes)
        self.assertNotIn("evenements_z", sql)
        self.assertNotIn("compositions_z", sql)
        sans_annotation = re.sub(r"frames_360_z[`\"]?\s+IS\s+NOT\s+NULL", "", sql,
                                 flags=re.IGNORECASE)
        self.assertNotIn("frames_360_z", sans_annotation)
        self.assertNotIn("NULLS LAST", sql.upper())

    def indicateurs(self, match, equipe, xg_pour, xg_contre):
        IndicateursMatchEquipe.objects.create(
            match=match, equipe=self.equipes[equipe], version_calcul=1, version_source="v1|-",
            valeurs={"resultat": {"xg_pour": xg_pour, "xg_contre": xg_contre},
                     "joueurs": {"liste": [{"nom": inv.JOUEUR_INVENTE}]}})

    def test_colonne_xg_ou_tiret(self):
        """Étape 3b : xG lu dans la table calculée à l'import, vu par l'adversaire."""
        self.suivre(inv.ADVERSAIRE)
        calcule = self.match(date(2026, 10, 2), 103, inv.ADVERSAIRE)
        self.match(date(2026, 10, 1), inv.ADVERSAIRE, 102)  # rien de calculé
        self.indicateurs(calcule, inv.ADVERSAIRE, 1.25, 0.5)
        self.indicateurs(calcule, 103, 0.5, 1.25)  # la ligne de l'autre équipe n'est pas lue
        reponse = self.client.get(URL)
        lignes = self.lignes(reponse)
        self.assertEqual((lignes[0]["xg_pour"], lignes[0]["xg_contre"]), (1.25, 0.5))
        self.assertEqual((lignes[1]["xg_pour"], lignes[1]["xg_contre"]), (None, None))
        self.assertContains(reponse, "xG pour – contre")
        self.assertNotContains(reponse, inv.JOUEUR_INVENTE)

    def test_xg_requetes_fixes_et_brut_jamais_lu(self):
        self.suivre(inv.ADVERSAIRE)
        m = self.match(date(2026, 9, 1), inv.ADVERSAIRE, 102)
        self.indicateurs(m, inv.ADVERSAIRE, 0.8, 0.3)
        with CaptureQueriesContext(connection) as un_match:
            self.client.get(URL)
        for k in range(4):
            m = self.match(date(2026, 9, 10 + k), 104, inv.ADVERSAIRE)
            self.indicateurs(m, inv.ADVERSAIRE, 0.1 * k, 0.2)
        with self.assertNumQueries(len(un_match)):
            self.client.get(URL)
        sql = " ".join(q["sql"] for q in un_match)
        self.assertNotIn("evenements_z", sql)
        self.assertNotIn("compositions_z", sql)

    def test_xg_refuse_sans_role(self):
        compte = get_user_model().objects.create_user("sans-role-3b", password="x")
        self.client.force_login(compte)
        self.assertEqual(self.client.get(URL).status_code, 403)

    def test_aucun_nom_de_joueur_sur_la_page(self):
        self.suivre(inv.ADVERSAIRE)
        self.match(date(2026, 10, 1), inv.ADVERSAIRE, 102, avec_360=True)
        self.assertNotContains(self.client.get(URL), inv.JOUEUR_INVENTE)
