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

from unittest import mock

from adversaire import blocs
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


def valeurs_3c2(avec_360=True, nom="Tireur Inventé", comptes=False):
    """Fiche à valeurs CONNUES (deux matchs inventés), calculée comme à l'import.
    `comptes` remplit aussi les grilles de progression, de pertes et de pertes
    suivies d'un tir (vides sinon)."""
    def grille(indice, valeur):
        g = [0] * 96
        g[indice] = valeur
        return g

    recuperations = grille(13, 3)  # rangée 1, colonne 1 : seule case non vide → niv-5
    bloc_360 = {"relance": {"passes": 40, "par_pression": {"0": {"passes": 20, "reussies": 18}}},
                "corners_pour": {"corners": 4, "attaquants_6m": 10}}
    pour_0 = {"defense": {"ppda_passes_adverses": 30, "ppda_actions": 4,
                          "grille_recuperations": recuperations},
              "attaque": {"passes": 300, "couloirs": {"gauche": 10, "axe": 20, "droite": 10},
                          "grille_tirs_xg": grille(50, 0.3)},       # x = 20, y = 40
              "cpa": {"tireurs": [{"id": 7, "nom": nom, "corners": 3, "coups_francs": 1}],
                      "chutes_corners": grille(20, 3)},             # x = 80, y = 10
              "danger": {"xg_pour": [0.4, 0.1], "xg_contre": [0.2, 0, 0.5],
                         "buts_pour": [1], "buts_contre": [0, 2]},
              "transitions": {"pour": {"recuperations": 12, "tirs": 3, "xg": 0.6, "buts": 2},
                              "contre": {"recuperations": 9, "tirs": 2, "xg": 0.3, "buts": 0}},
              "joueurs": {"liste": [{"id": 7, "nom": nom, "minutes": 90, "passes_5_elimines": 2,
                                     "receptions_entre_lignes": 1}]},
              "trois_cent_soixante": bloc_360 if avec_360 else None}
    if comptes:
        pour_0["attaque"]["grille_progression"] = grille(47, 3)       # x = 110, y = 30
        pour_0["defense"]["grille_pertes"] = grille(30, 3)            # x = 60, y = 20
        pour_0["transitions"]["grille_pertes_vers_tir"] = grille(75, 3)  # x = 30, y = 60
    pour_1 = {"defense": {"ppda_passes_adverses": 20, "ppda_actions": 6}, "attaque": {"passes": 200},
              "danger": {"xg_pour": [0.1]},
              "transitions": {"contre": {"tirs": 2, "xg": 0.1, "buts": 1}}}
    # Grilles « contre » lues chez l'équipe rencontrée, retournées à l'import :
    # case 0 → 95 (x = 110, y = 70), case 1 → 94 (x = 100, y = 70).
    contre_0 = {"attaque": {"passes": 200, "grille_tirs_xg": grille(0, 0.4)},
                "cpa": {"chutes_corners": grille(1, 2)}}
    return fiche.construire([
        {"match": 2, "date": "2026-10-08", "a_domicile": True, "pour": pour_0, "contre": contre_0},
        {"match": 1, "date": "2026-10-01", "a_domicile": False, "pour": pour_1,
         "contre": {"attaque": {"passes": 300}}},
        # Match le plus ancien, non calculé : 3 matchs, dont 2 calculés.
        {"match": 0, "date": "2026-09-24", "a_domicile": True, "pour": None, "contre": None},
    ])


def compacter(contenu):
    """Page sans les blancs entre balises, pour comparer des lignes entières."""
    return re.sub(r">\s+<", "><", contenu)


def figure(contenu, libelle):
    """La seule figure de grille dont la légende est `libelle`."""
    debut = contenu.index(f"<figcaption>{libelle}</figcaption>")
    return contenu[debut:contenu.index("</figure>", debut)]


SVG_BARRE = '<svg class="barre" viewBox="0 0 100 6" preserveAspectRatio="none" aria-hidden="true">'


def ligne_barres(libelle, pour, contre):
    """Ligne de barres doubles attendue : (largeur, texte) pour et contre."""
    return (f'<th scope="row">{libelle}</th><td class="barres-svg">'
            f'{SVG_BARRE}<rect class="barre-pour" x="0" y="0" width="{pour[0]}" height="6"/></svg>'
            f'{SVG_BARRE}<rect class="barre-contre" x="0" y="0" width="{contre[0]}" height="6"/></svg>'
            f'</td><td class="barres-valeurs"><span class="valeur-pour">{pour[1]}</span>'
            f'<span class="valeur-contre">{contre[1]}</span></td>')


class PageFicheBlocs3c2Test(TestCase):
    """Étape 3c-2 : blocs Attaque, Défense (danger, transitions, gardien), CPA et
    360, rendus depuis `FicheAdversaire.valeurs`. Valeurs attendues en dur."""

    @classmethod
    def setUpTestData(cls):
        competition = Competition.objects.create(sb_competition_id=inv.COMPETITION, sb_season_id=inv.SAISON,
                                                 nom="Ligue Inventée", saison="2026/2027")
        adversaire = Equipe.objects.create(sb_team_id=inv.ADVERSAIRE, nom="Adversaire Inventé")
        autre = Equipe.objects.create(sb_team_id=102, nom="Autre Inventée")
        cls.suivi = AdversaireSuivi.objects.create(equipe=adversaire, competition=competition,
                                                   importe_le=timezone.now())
        cls.match = Match.objects.create(sb_match_id=5900, competition=competition, date=date(2026, 10, 8),
                                         domicile=adversaire, exterieur=autre, score_domicile=1,
                                         score_exterieur=0, statut="available")
        cls.compte = get_user_model().objects.create_user("staff-3c2", password="x")
        cls.compte.groups.add(Group.objects.get(name="staff"))

    def setUp(self):
        self.client.force_login(self.compte)

    def page(self, valeurs, version=None):
        FicheAdversaire.objects.update_or_create(suivi=self.suivi, defaults={
            "version_fiche": fiche.VERSION_FICHE if version is None else version,
            "matchs": [self.match.pk], "nb_matchs_calcules": 2, "valeurs": valeurs})
        return self.client.get(reverse("fiche", args=[self.suivi.id]))

    def test_blocs_et_valeurs_en_dur(self):
        reponse = self.page(valeurs_3c2())
        self.assertEqual(reponse.status_code, 200)
        for ancre in ('id="attaque"', 'id="defense"', 'id="danger"', 'id="transitions"', 'id="cpa"',
                      'id="trois-cent-soixante"', 'href="#attaque"', 'href="#defense"', 'href="#cpa"',
                      'href="#trois-cent-soixante"'):
            self.assertContains(reponse, ancre)
        # PPDA = (30 + 20) / (4 + 6) ; possession = 500 / 1000.
        self.assertContains(reponse, "<div><dt>PPDA</dt><dd>5,00</dd></div>")
        self.assertContains(reponse, "<div><dt>Possession</dt><dd>50,0 %</dd></div>")
        # Couloirs 25 / 50 / 25 % : barres de 50, 100 et 50.
        self.assertContains(reponse, '<rect class="barre-pour" x="0" y="0" width="100" height="6"/>')
        self.assertContains(reponse, '<rect class="barre-pour" x="0" y="0" width="50" height="6"/>', count=2)
        # Seule case non vide des récupérations : rangée 1, colonne 1.
        self.assertContains(reponse, '<rect class="case niv-5" x="10" y="10" width="10" height="10">')
        self.assertContains(reponse, "Aucune donnée")  # ex. grille de progression vide
        self.assertContains(reponse, "non disponible")  # PSxG absent
        # 360 : 1 match sur 3 (dénominateur = matchs de la fiche, calculés ou non),
        # relance 20 / 40, 10 attaquants sur 4 corners.
        self.assertContains(reponse, '<p class="aide">1 match sur 3 avec 360.')
        self.assertContains(reponse, "<strong>50,0 %</strong>")
        self.assertContains(reponse, "<td>2,5</td>")
        self.assertContains(reponse, "Tireur Inventé : 2")  # passes qui éliminent 5 adversaires

    def test_couloirs_en_pourcentage(self):
        # Couloirs 10 / 20 / 10 sur 40 : 25,0 / 50,0 / 25,0 %, une décimale et l'unité.
        contenu = self.page(valeurs_3c2()).content.decode()
        self.assertEqual(contenu.count('<span class="valeur-pour">25,0 %</span>'), 2)
        self.assertEqual(contenu.count('<span class="valeur-pour">50,0 %</span>'), 1)

    def test_grilles_pour_et_contre(self):
        contenu = self.page(valeurs_3c2(comptes=True)).content.decode()
        case = '<rect class="case niv-5" x="{}" y="{}" width="10" height="10"><title>{}</title></rect>'
        attendues = [
            # (légende, x, y, texte de la case, légende « de … à … »)
            ("xG de ses tirs par zone", 20, 40, "0,30", "de 0,30 à 0,30"),
            ("xG concédé par zone (vers son but)", 110, 70, "0,40", "de 0,40 à 0,40"),
            ("Zones de chute de ses corners", 80, 10, "3", "de 3 à 3"),
            ("Zones de chute des corners adverses (vers son but)", 100, 70, "2", "de 2 à 2"),
            # Grilles de comptes : case entière, légende sans décimale.
            ("Zones de récupération", 10, 10, "3", "de 3 à 3"),
            ("Zones de perte", 60, 20, "3", "de 3 à 3"),
            ("Pertes suivies d'un tir adverse", 30, 60, "3", "de 3 à 3"),
            ("Arrivées des actions progressives", 110, 30, "3", "de 3 à 3"),
        ]
        for libelle, x, y, texte, legende in attendues:
            with self.subTest(libelle=libelle):
                bloc = figure(contenu, libelle)
                self.assertIn(case.format(x, y, texte), bloc)
                self.assertEqual(bloc.count('class="case niv-5"'), 1)
                self.assertEqual(bloc.count('class="case niv-0"'), 95)
                self.assertIn(legende, bloc)

    def test_danger_par_tranche(self):
        contenu = compacter(self.page(valeurs_3c2()).content.decode())
        # xG pour 0,5 / 0,1 / 0 et contre 0,2 / 0 / 0,5 : même échelle, maximum 0,5.
        self.assertIn(ligne_barres("0-15", (100, "0,50"), (40, "0,20")), contenu)
        self.assertIn(ligne_barres("15-30", (20, "0,10"), (0, "0,00")), contenu)
        self.assertIn(ligne_barres("30-45+", (0, "0,00"), (100, "0,50")), contenu)
        # Buts pour puis contre, par tranche.
        self.assertIn('<tr><th scope="row">0-15</th><td>1</td><td>0</td></tr>'
                      '<tr><th scope="row">15-30</th><td>0</td><td>2</td></tr>', contenu)

    def test_transitions_pour_et_contre(self):
        contenu = compacter(self.page(valeurs_3c2()).content.decode())
        # Pour : 12 récupérations, 3 tirs (1,50 par match), 0,6 xG (0,30), 2 buts.
        self.assertIn('<tr><th scope="row">Pour</th><td>12</td><td>3</td><td>1,50</td>'
                      '<td>0,60</td><td>0,30</td><td>2</td></tr>', contenu)
        # Contre : 9, 2 + 2 tirs (2,00), 0,3 + 0,1 xG (0,20), 0 + 1 but.
        self.assertIn('<tr><th scope="row">Contre</th><td>9</td><td>4</td><td>2,00</td>'
                      '<td>0,40</td><td>0,20</td><td>1</td></tr>', contenu)

    def test_bloc_360_masque_sans_360(self):
        reponse = self.page(valeurs_3c2(avec_360=False))
        self.assertNotContains(reponse, 'id="trois-cent-soixante"')
        self.assertNotContains(reponse, 'href="#trois-cent-soixante"')
        self.assertContains(reponse, 'id="attaque"')

    def test_nom_hostile_echappe_dans_les_nouveaux_blocs(self):
        contenu = self.page(valeurs_3c2(nom=NOM_HOSTILE)).content.decode()
        self.assertNotIn("<script>", contenu)
        # Tireur de CPA (tableau) et listes 360 (passes, réceptions entre les lignes).
        self.assertIn("<td>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;</td>", contenu)
        self.assertIn("<li>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; : 2</li>", contenu)
        self.assertIn("<li>&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; : 1</li>", contenu)

    def test_aucun_style_ni_gestionnaire_ni_virgule_dans_un_attribut(self):
        contenu = self.page(valeurs_3c2()).content.decode()
        self.assertNotRegex(contenu, r"\sstyle\s*=")
        self.assertNotRegex(contenu, r"\son[a-z]+\s*=")
        self.assertNotRegex(contenu, r'\s(?:x|y|width|height|cx|cy)="[^"]*,')

    def test_fiche_version_1_bandeau(self):
        self.assertContains(self.page(valeurs_3c2(), version=1), "Fiche à recalculer")

    def test_fiche_a_jour_sans_bandeau(self):
        self.assertNotContains(self.page(valeurs_3c2(), version=2), "Fiche à recalculer")

    def test_sommaire_dans_l_ordre_de_la_liste(self):
        contenu = self.page(valeurs_3c2()).content.decode()
        liens = re.findall(r'<a href="#([a-z-]+)">', contenu)
        self.assertEqual(liens, [b.ancre for b in blocs.BLOCS_FICHE])
        for bloc in blocs.BLOCS_FICHE:
            self.assertIn(f'id="{bloc.ancre}"', contenu)

    def test_sommaire_et_blocs_exacts_dans_l_ordre(self):
        contenu = self.page(valeurs_3c2()).content.decode()
        attendu = [
            ("resultats", "Résultats et forme"),
            ("systeme", "Système et XI probable"),
            ("joueurs", "Joueurs clés"),
            ("attaque", "Attaque"),
            ("defense", "Défense"),
            ("cpa", "Coups de pied arrêtés"),
            ("trois-cent-soixante", "360"),
        ]
        liens = re.findall(r'<a href="#([a-z-]+)">([^<]*)</a>', contenu)
        self.assertEqual(liens, attendu)
        positions = []
        for ancre, _titre in attendu:
            self.assertEqual(contenu.count(f'id="{ancre}"'), 1, ancre)
            positions.append(contenu.index(f'id="{ancre}"'))
        self.assertEqual(positions, sorted(positions))

    def test_retirer_une_entree_retire_le_bloc_et_son_lien(self):
        restantes = [b for b in blocs.BLOCS_FICHE if b.cle != "cpa"]
        with mock.patch.object(blocs, "BLOCS_FICHE", restantes):
            reponse = self.page(valeurs_3c2())
        self.assertEqual(reponse.status_code, 200)
        self.assertNotContains(reponse, 'id="cpa"')
        self.assertNotContains(reponse, 'href="#cpa"')
        self.assertContains(reponse, 'id="attaque"')
        self.assertContains(reponse, 'href="#trois-cent-soixante"')

    def test_liste_vide_aucun_bloc_sans_erreur(self):
        with mock.patch.object(blocs, "BLOCS_FICHE", []):
            reponse = self.page(valeurs_3c2())
        self.assertEqual(reponse.status_code, 200)
        self.assertNotContains(reponse, 'id="resultats"')
        self.assertNotContains(reponse, 'href="#resultats"')

    def test_nombre_de_requetes_independant_des_blocs(self):
        self.page(valeurs_3c2())
        url = reverse("fiche", args=[self.suivi.id])
        with CaptureQueriesContext(connection) as toutes:
            self.client.get(url)
        with mock.patch.object(blocs, "BLOCS_FICHE", blocs.BLOCS_FICHE[:2]):
            with CaptureQueriesContext(connection) as moins:
                self.client.get(url)
        self.assertEqual(len(toutes), len(moins))


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
