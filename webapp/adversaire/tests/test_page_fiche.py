"""Étape 3c-1 : page fiche `/adversaire/<id du suivi>/`, qui LIT `FicheAdversaire`.

Données INVENTÉES écrites directement en base ; aucun calcul ni réseau
pendant la requête."""
import re
from datetime import date, time, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from adversaire.analyse import fiche, indicateurs
from adversaire.models import (AdversaireSuivi, Competition, DonneesMatch, Equipe,
                               FicheAdversaire, Match)

from . import donnees_inventees as inv

NOM_HOSTILE = '<script>alert("x")</script>'


def valeurs_inventees():
    """Une fiche calculée comme à l'import, sur le match « v2 » inventé."""
    return fiche.construire([{
        "match": 1, "date": "2026-10-01", "a_domicile": True,
        "pour": indicateurs.calculer(inv.evenements_v2(), inv.compositions_riches(),
                                     inv.frames_v2(), inv.ADVERSAIRE),
        "contre": indicateurs.calculer(inv.evenements_v2(), inv.compositions_riches(),
                                       inv.frames_v2(), 102),
    }])


class PageFicheTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.competition = Competition.objects.create(
            sb_competition_id=inv.COMPETITION, sb_season_id=inv.SAISON,
            nom="Ligue Inventée", saison="2026/2027")
        cls.equipes = {i: Equipe.objects.create(sb_team_id=i, nom=nom)
                       for i, nom in inv.EQUIPES.items()}
        cls.compte = get_user_model().objects.create_user("staff-fiche", password="x")
        cls.compte.groups.add(Group.objects.get(name="staff"))

    def setUp(self):
        self.client.force_login(self.compte)
        self.ident = 5000

    # ── Aides ────────────────────────────────────────────────────────────────
    def match(self, jour, domicile=inv.ADVERSAIRE, exterieur=102, avec_360=False):
        self.ident += 1
        m = Match.objects.create(
            sb_match_id=self.ident, competition=self.competition, date=jour,
            coup_envoi=time(20, 0), domicile=self.equipes[domicile],
            exterieur=self.equipes[exterieur], score_domicile=2, score_exterieur=1,
            journee=4, statut="available")
        donnees = DonneesMatch(match=m, version="v1")
        donnees.evenements = inv.evenements(m.sb_match_id)
        donnees.compositions = inv.compositions(m.sb_match_id)
        donnees.frames_360 = inv.frames_360(m.sb_match_id) if avec_360 else None
        donnees.save()
        return m

    def suivre(self, equipe=inv.ADVERSAIRE):
        return AdversaireSuivi.objects.create(equipe=self.equipes[equipe],
                                              competition=self.competition,
                                              importe_le=timezone.now())

    def creer_fiche(self, suivi, matchs, valeurs=None, version=fiche.VERSION_FICHE):
        return FicheAdversaire.objects.create(
            suivi=suivi, version_fiche=version, matchs=[m.pk for m in matchs],
            nb_matchs_calcules=len(matchs), valeurs=valeurs or valeurs_inventees())

    def url(self, suivi):
        return reverse("fiche", args=[suivi.id])

    # ── Accès ────────────────────────────────────────────────────────────────
    def test_anonyme_redirige_vers_la_connexion(self):
        suivi = self.suivre()
        self.client.logout()
        reponse = self.client.get(self.url(suivi))
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse["Location"].startswith(reverse("login")))

    def test_sans_groupe_403(self):
        suivi = self.suivre()
        self.client.force_login(get_user_model().objects.create_user("sans-role-fiche", password="x"))
        self.assertEqual(self.client.get(self.url(suivi)).status_code, 403)

    def test_roles_autorises(self):
        suivi = self.suivre()
        analyste = get_user_model().objects.create_user("analyste-fiche", password="x")
        analyste.groups.add(Group.objects.get(name="analyste"))
        chef = get_user_model().objects.create_superuser("chef-fiche", password="x")
        for compte in (self.compte, analyste, chef):
            with self.subTest(compte=compte.username):
                self.client.force_login(compte)
                self.assertEqual(self.client.get(self.url(suivi)).status_code, 200)

    def test_suivi_inconnu_404(self):
        self.assertEqual(self.client.get(reverse("fiche", args=[999999])).status_code, 404)
        self.assertEqual(self.client.get("/adversaire/abc/").status_code, 404)

    # ── Contenu ──────────────────────────────────────────────────────────────
    def test_fiche_absente_message_et_commande(self):
        suivi = self.suivre()
        reponse = self.client.get(self.url(suivi))
        self.assertContains(reponse, "La fiche n'est pas encore calculée")
        self.assertContains(reponse, "python manage.py recalculer_indicateurs")
        self.assertNotContains(reponse, 'id="resultats"')

    def test_fiche_a_jour_blocs_affiches(self):
        suivi = self.suivre()
        m = self.match(date(2026, 10, 1))
        self.creer_fiche(suivi, [m])
        reponse = self.client.get(self.url(suivi))
        self.assertFalse(reponse.context["perimee"])
        self.assertNotContains(reponse, "Fiche à recalculer")
        for ancre in ('id="resultats"', 'id="systeme"', 'id="joueurs"'):
            self.assertContains(reponse, ancre)
        self.assertContains(reponse, "4-2-3-1")
        self.assertContains(reponse, "1 match sur 1")
        self.assertContains(reponse, "1 match sur 1 avec 360")
        self.assertContains(reponse, "Contre-attaque")  # buts et xG par type d'action
        self.assertContains(reponse, '<span class="res res-V">V</span>', html=True)

    def test_absents_selon_que_le_dernier_match_est_calcule(self):
        suivi = self.suivre()
        m = self.match(date(2026, 10, 1))
        fiche_stockee = self.creer_fiche(suivi, [m])
        self.assertContains(self.client.get(self.url(suivi)), "Aucun titulaire habituel absent")
        valeurs = valeurs_inventees()
        valeurs["systeme"]["dernier_match_calcule"] = False
        FicheAdversaire.objects.filter(pk=fiche_stockee.pk).update(valeurs=valeurs)
        reponse = self.client.get(self.url(suivi))
        self.assertContains(reponse, "Dernier match non calculé")
        self.assertNotContains(reponse, "Aucun titulaire habituel absent")

    def test_fiche_perimee_bandeau(self):
        suivi = self.suivre()
        ancien = self.match(date(2026, 9, 1))
        self.creer_fiche(suivi, [ancien])
        self.match(date(2026, 10, 1))  # nouveau match importé depuis
        reponse = self.client.get(self.url(suivi))
        self.assertTrue(reponse.context["perimee"])
        self.assertContains(reponse, "Fiche à recalculer")

    def test_version_de_fiche_perimee_bandeau(self):
        suivi = self.suivre()
        m = self.match(date(2026, 10, 1))
        self.creer_fiche(suivi, [m], version=fiche.VERSION_FICHE + 1)
        self.assertContains(self.client.get(self.url(suivi)), "Fiche à recalculer")

    def test_xi_sur_le_terrain_svg(self):
        suivi = self.suivre()
        m = self.match(date(2026, 10, 1))
        valeurs = valeurs_inventees()
        # Le gardien (position_id 1) est placé en (6, 40), l'attaquant (23) en (98, 40).
        self.creer_fiche(suivi, [m], valeurs)
        reponse = self.client.get(self.url(suivi))
        self.assertContains(reponse, '<svg class="terrain"')
        self.assertContains(reponse, 'cx="6" cy="40"')
        self.assertContains(reponse, 'cx="98" cy="40"')

    def test_nom_hostile_echappe(self):
        suivi = self.suivre()
        m = self.match(date(2026, 10, 1))
        valeurs = valeurs_inventees()
        for joueur in valeurs["systeme"]["xi_probable"] + valeurs["joueurs"]["cles"]:
            joueur["nom"] = NOM_HOSTILE
        self.creer_fiche(suivi, [m], valeurs)
        reponse = self.client.get(self.url(suivi))
        contenu = reponse.content.decode()
        self.assertNotIn("<script>", contenu)
        self.assertIn("&lt;script&gt;", contenu)

    def test_aucun_attribut_style_ni_gestionnaire(self):
        suivi = self.suivre()
        self.creer_fiche(suivi, [self.match(date(2026, 10, 1))])
        contenu = self.client.get(self.url(suivi)).content.decode()
        self.assertNotRegex(contenu, r"\sstyle\s*=")
        self.assertNotRegex(contenu, r"\son[a-z]+\s*=")

    def test_lien_depuis_la_page_adversaire(self):
        suivi = self.suivre()
        self.assertContains(self.client.get(reverse("accueil")), f'href="{self.url(suivi)}"')

    # ── Requêtes ─────────────────────────────────────────────────────────────
    def test_requetes_fixes_sans_brut_ni_indicateurs_par_match(self):
        suivi = self.suivre()
        m = self.match(date(2026, 9, 1), avec_360=True)
        self.creer_fiche(suivi, [m])
        with CaptureQueriesContext(connection) as un_match:
            self.client.get(self.url(suivi))
        matchs = [m] + [self.match(date(2026, 9, 10 + k), 104, inv.ADVERSAIRE, avec_360=bool(k % 2))
                        for k in range(4)]
        FicheAdversaire.objects.filter(suivi=suivi).update(matchs=[x.pk for x in reversed(matchs)])
        with self.assertNumQueries(len(un_match)):
            reponse = self.client.get(self.url(suivi))
        self.assertEqual(len(reponse.context["lignes"]), 5)
        self.assertFalse(reponse.context["perimee"])
        sql = " ".join(q["sql"] for q in un_match)
        self.assertNotIn("evenements_z", sql)
        self.assertNotIn("compositions_z", sql)
        sans_annotation = re.sub(r"frames_360_z[`\"]?\s+IS\s+NOT\s+NULL", "", sql, flags=re.IGNORECASE)
        self.assertNotIn("frames_360_z", sans_annotation)
        self.assertNotIn("indicateursmatchequipe", sql.lower())
        self.assertNotIn("NULLS LAST", sql.upper())

    def test_aucune_fiche_d_un_autre_suivi(self):
        suivi = self.suivre()
        autre = self.suivre(103)
        self.creer_fiche(autre, [self.match(date(2026, 10, 1), 103, 104)])
        reponse = self.client.get(self.url(suivi))
        self.assertIsNone(reponse.context["fiche"])
        self.assertContains(reponse, "La fiche n'est pas encore calculée")


class FichePeriodeTest(TestCase):
    """Les matchs les plus récents d'abord, comme à l'import."""

    def test_ordre_identique_a_l_import(self):
        from adversaire.importation import matchs_du_suivi
        competition = Competition.objects.create(sb_competition_id=1, sb_season_id=2,
                                                 nom="Ligue Inventée", saison="2026/2027")
        a = Equipe.objects.create(sb_team_id=inv.ADVERSAIRE, nom="A")
        b = Equipe.objects.create(sb_team_id=102, nom="B")
        suivi = AdversaireSuivi.objects.create(equipe=a, competition=competition,
                                               importe_le=timezone.now())
        jour = date(2026, 10, 1)
        for k in range(3):
            Match.objects.create(sb_match_id=10 + k, competition=competition,
                                 date=jour + timedelta(days=k), domicile=a, exterieur=b)
        self.assertEqual([m.sb_match_id for m in matchs_du_suivi(suivi)], [12, 11, 10])
