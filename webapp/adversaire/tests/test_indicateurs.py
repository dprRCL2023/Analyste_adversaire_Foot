"""Étape 3b : module de calcul PUR (sans base, sans réseau, données inventées).

Chaque valeur attendue se recalcule à la main sur `evenements_riches()`."""
import copy
import json

from django.test import SimpleTestCase

from adversaire.analyse import indicateurs

from . import donnees_inventees as inv

A, B = inv.ADVERSAIRE, 102


def calculer(equipe=A, evenements=None, compositions=None, frames=None):
    return indicateurs.calculer(
        inv.evenements_riches() if evenements is None else evenements,
        inv.compositions_riches() if compositions is None else compositions,
        frames, equipe)


def sans_cles(evenements, *cles):
    """Copie des événements sans les clés données (à tout niveau du tir)."""
    copie = copy.deepcopy(evenements)
    for e in copie:
        for cle in cles:
            e.pop(cle, None)
            if isinstance(e.get("shot"), dict):
                e["shot"].pop(cle, None)
    return copie


class ResultatTest(SimpleTestCase):
    def test_buts_xg_tirs_pour_et_contre(self):
        r = calculer()["resultat"]
        # Pour : but de r-05 + contre son camp r-20 ; la séance de tirs au but (r-27) est exclue.
        self.assertEqual((r["buts_pour"], r["csc_pour"], r["buts_contre"]), (2, 1, 1))
        self.assertEqual((r["tirs_pour"], r["tirs_cadres_pour"]), (3, 2))
        self.assertAlmostEqual(r["xg_pour"], 0.65)
        self.assertAlmostEqual(r["npxg_pour"], 0.65)
        self.assertAlmostEqual(r["xg_par_tir_pour"], 0.217)
        # Contre : le penalty compte dans l'xG, pas dans le npxG.
        self.assertEqual((r["tirs_contre"], r["tirs_cadres_contre"]), (2, 2))
        self.assertAlmostEqual(r["xg_contre"], 0.86)
        self.assertAlmostEqual(r["npxg_contre"], 0.1)

    def test_vu_de_l_autre_equipe(self):
        r = calculer(B)["resultat"]
        self.assertEqual((r["buts_pour"], r["buts_contre"]), (1, 2))
        self.assertAlmostEqual(r["xg_pour"], 0.86)
        self.assertAlmostEqual(r["xg_contre"], 0.65)

    def test_tirs_au_but_exclus(self):
        sans_seance = [e for e in inv.evenements_riches() if e["period"] != 5]
        self.assertEqual(calculer(evenements=sans_seance), calculer())


class DerouleTest(SimpleTestCase):
    def test_xg_cumules_et_buts(self):
        d = calculer()["deroule"]
        self.assertEqual(len(d["xg_cumule_pour"]), 95)  # minutes 0 à 94
        self.assertEqual(d["xg_cumule_pour"][9], 0.0)
        self.assertAlmostEqual(d["xg_cumule_pour"][10], 0.4)
        self.assertAlmostEqual(d["xg_cumule_pour"][40], 0.6)
        self.assertAlmostEqual(d["xg_cumule_pour"][-1], 0.65)
        self.assertAlmostEqual(d["xg_cumule_contre"][50], 0.86)
        self.assertEqual(d["buts"], [{"minute": 10, "pour": True}, {"minute": 50, "pour": False},
                                     {"minute": 60, "pour": True}])

    def test_menace_obv_par_tranche_de_5_minutes(self):
        d = calculer()["deroule"]
        self.assertEqual(d["menace_source"], "obv")
        self.assertEqual(len(d["menace_pour"]), 19)
        self.assertAlmostEqual(d["menace_pour"][0], 0.05)
        self.assertAlmostEqual(d["menace_pour"][2], 0.3)
        self.assertAlmostEqual(d["menace_contre"][6], 0.02)
        self.assertEqual(sum(d["menace_pour"][3:]), 0)

    def test_repli_sur_l_xg_sans_obv(self):
        evenements = sans_cles(inv.evenements_riches(), "obv_for_net", "obv_against_net")
        valeurs = calculer(evenements=evenements)
        d = valeurs["deroule"]
        self.assertEqual(d["menace_source"], "xg")
        self.assertAlmostEqual(d["menace_pour"][2], 0.4)
        self.assertAlmostEqual(d["menace_pour"][8], 0.2)
        self.assertAlmostEqual(d["menace_pour"][17], 0.05)
        self.assertFalse(valeurs["joueurs"]["obv_disponible"])
        self.assertIsNone(valeurs["joueurs"]["liste"][0]["obv_cree"])


class SystemeTest(SimpleTestCase):
    def test_formation_changements_et_xi(self):
        s = calculer()["systeme"]
        self.assertEqual(s["formation_depart"], "433")
        self.assertEqual(s["changements"], [{"minute": 70, "formation": "442"}])
        self.assertEqual([j["id"] for j in s["xi"]], [1, 10, 9])
        self.assertEqual(s["xi"][2]["poste"], "Center Forward")

    def test_minutes_remplacement_et_expulsion(self):
        minutes = {j["id"]: j["minutes"] for j in calculer()["joueurs"]["liste"]}
        self.assertEqual(minutes, {1: 94, 10: 94, 9: 70, 11: 24})
        minutes = {j["id"]: j["minutes"] for j in calculer(B)["joueurs"]["liste"]}
        self.assertEqual(minutes, {21: 94, 29: 80})  # carton rouge à la 80e

    def test_prolongations(self):
        evenements = [e for e in inv.evenements_riches() if e["id"] != "r-26"]
        evenements.append({"id": "r-fin", "period": 4, "minute": 121, "second": 0,
                           "type": {"name": "Half End"}, "team": {"id": A}})
        minutes = {j["id"]: j["minutes"] for j in calculer(evenements=evenements)["joueurs"]["liste"]}
        self.assertEqual((minutes[1], minutes[11]), (121, 51))

    def test_xi_depuis_les_compositions_sans_starting_xi(self):
        evenements = [e for e in inv.evenements_riches() if e["type"]["name"] != "Starting XI"]
        compositions = inv.compositions_riches()
        compositions[0]["lineup"][0]["positions"] = [{"position": "Goalkeeper",
                                                      "start_reason": "Starting XI"}]
        s = calculer(evenements=evenements, compositions=compositions)["systeme"]
        self.assertIsNone(s["formation_depart"])
        self.assertEqual(s["xi"], [{"id": 1, "nom": "Gardien Inventé", "poste": "Goalkeeper",
                                    "numero": 1}])


class AttaqueTest(SimpleTestCase):
    def test_passes_progression_et_couloirs(self):
        a = calculer()["attaque"]
        self.assertEqual((a["passes"], a["passes_reussies"]), (3, 2))
        self.assertEqual(a["possession"], 0.5)
        self.assertEqual((a["passes_progressives"], a["conduites_progressives"]), (1, 1))
        self.assertEqual((a["entrees_dernier_tiers"], a["entrees_surface"]), (1, 0))
        self.assertEqual(a["centres"], 1)  # le corner n'est pas compté comme centre
        self.assertEqual(a["couloirs"], {"gauche": 0, "axe": 2, "droite": 0})
        self.assertEqual(len(a["grille_progression"]), 96)
        self.assertEqual(a["grille_progression"][4 * 12 + 6], 1)  # arrivée (60, 40)
        self.assertEqual(a["grille_progression"][4 * 12 + 8], 1)  # arrivée (85, 40)
        self.assertEqual(sum(a["grille_progression"]), 2)


class DefenseTest(SimpleTestCase):
    def test_ppda_calcule_a_la_main(self):
        d = calculer()["defense"]
        # 3 passes adverses à x ≤ 72 dans LEUR repère ; nos actions à x ≥ 48 dans
        # le nôtre : le tacle (70) et l'interception (60), pas la faute (30).
        self.assertEqual((d["ppda_passes_adverses"], d["ppda_actions"]), (3, 2))
        self.assertEqual(d["ppda"], 1.5)

    def test_ppda_sans_action_defensive(self):
        d = calculer(B)["defense"]
        self.assertEqual(d["ppda_actions"], 0)
        self.assertIsNone(d["ppda"])

    def test_pressing_hauteur_et_recuperations(self):
        d = calculer()["defense"]
        self.assertEqual((d["pressions"], d["contre_pressing"]), (1, 1))
        self.assertEqual(d["hauteur_actions_defensives"], 63.3)
        self.assertEqual((d["recuperations"], d["recuperations_hautes"]), (2, 1))
        self.assertEqual(sum(d["grille_recuperations"]), 2)
        self.assertEqual(d["pertes"], 0)


class CpaEtGardienTest(SimpleTestCase):
    def test_corner_suivi_d_un_tir_et_coup_franc_direct(self):
        c = calculer()["cpa"]
        self.assertEqual((c["corners"], c["coups_francs"]), (1, 1))
        self.assertEqual(c["pour"]["corner"], {"tirs": 1, "xg": 0.2, "buts": 0})
        self.assertEqual(c["pour"]["coup_franc"], {"tirs": 1, "xg": 0.05, "buts": 0})
        self.assertEqual(c["contre"]["corner"], {"tirs": 0, "xg": 0.0, "buts": 0})
        self.assertEqual(c["tireurs"], [{"id": 10, "nom": "Milieu Inventé", "corners": 1,
                                         "coups_francs": 1}])

    def test_psxg_present(self):
        g = calculer()["gardien"]
        self.assertEqual((g["tirs_cadres_subis"], g["arrets"], g["buts_encaisses_sur_tir"]),
                         (2, 1, 1))
        self.assertAlmostEqual(g["psxg"], 0.95)
        self.assertAlmostEqual(g["psxg_moins_buts"], -0.05)

    def test_saved_to_post_compte_comme_cadre_et_arret(self):
        """« Saved to Post » (libellé StatsBomb) : tir cadré pour et contre, arrêt
        du gardien et PSxG ; la casse du libellé ne change rien."""
        for issue in ("Saved to Post", "Saved To Post", "saved to post"):
            with self.subTest(issue=issue):
                evenements = inv.evenements_riches() + inv.tirs_au_poteau(issue)
                r = calculer(evenements=evenements)["resultat"]
                self.assertEqual((r["tirs_pour"], r["tirs_cadres_pour"]), (4, 3))
                self.assertEqual((r["tirs_contre"], r["tirs_cadres_contre"]), (3, 3))
                # Gardien de 101 : r-16 (arrêt), r-19 (but), p-01 (arrêt, poteau).
                g = calculer(evenements=evenements)["gardien"]
                self.assertEqual((g["tirs_cadres_subis"], g["arrets"], g["buts_encaisses_sur_tir"]),
                                 (3, 2, 1))
                self.assertAlmostEqual(g["psxg"], 1.25)  # 0.05 + 0.9 + 0.3
                self.assertAlmostEqual(g["psxg_moins_buts"], 0.25)
                # Gardien de 102 : r-05 (but), r-25 (arrêt), p-02 (arrêt, sans PSxG).
                g = calculer(B, evenements=evenements)["gardien"]
                self.assertEqual((g["tirs_cadres_subis"], g["arrets"], g["buts_encaisses_sur_tir"]),
                                 (3, 2, 1))
                self.assertAlmostEqual(g["psxg"], 0.6)
                self.assertAlmostEqual(g["psxg_moins_buts"], -0.4)

    def test_psxg_absent(self):
        g = calculer(evenements=sans_cles(inv.evenements_riches(), "shot_execution_xg"))["gardien"]
        self.assertIsNone(g["psxg"])
        self.assertIsNone(g["psxg_moins_buts"])
        self.assertEqual(g["tirs_cadres_subis"], 2)


class JoueursTest(SimpleTestCase):
    def test_obv_xg_et_ordre(self):
        liste = calculer()["joueurs"]["liste"]
        self.assertEqual([j["id"] for j in liste], [1, 10, 9, 11])  # minutes, puis nom
        attaquant = next(j for j in liste if j["id"] == 9)
        self.assertEqual(attaquant["nom"], inv.JOUEUR_INVENTE)
        self.assertAlmostEqual(attaquant["obv_cree"], 0.35)
        self.assertAlmostEqual(attaquant["xg"], 0.6)  # tir au but de la séance exclu
        self.assertEqual(attaquant["actions_progressives"], 1)
        self.assertEqual(next(j for j in liste if j["id"] == 11)["numero"], 14)


class TroisCentSoixanteTest(SimpleTestCase):
    def test_sans_360(self):
        self.assertIsNone(calculer(frames=None)["trois_cent_soixante"])

    def test_images_incompletes_sans_erreur(self):
        t = calculer(frames=inv.frames_360_riches())["trois_cent_soixante"]
        self.assertEqual(t["images"], 4)  # r-03, r-04, r-09, r-10 ; r-06 est adverse
        self.assertEqual(t["images_inexploitables"], 3)  # sans acteur, vide, pas un dict
        self.assertEqual(t["passes_cassant_une_ligne"], 1)
        self.assertEqual(t["defenseurs_cote_but_moyen"], 2.5)  # (4 + 1) / 2

    def test_images_au_format_de_l_etape_2(self):
        """Les images inventées de l'import (sans acteur, zone vide…) passent aussi."""
        t = indicateurs.calculer(inv.evenements(1), inv.compositions(1), inv.frames_360(1), A)
        self.assertEqual(t["trois_cent_soixante"]["images"], 1)


class RobustesseTest(SimpleTestCase):
    def test_donnees_vides_ou_absentes(self):
        for evenements in ([], None, "pas une liste", [None, 3, "x"]):
            with self.subTest(evenements=evenements):
                valeurs = indicateurs.calculer(evenements, None, None, A)
                self.assertEqual(valeurs["resultat"]["tirs_pour"], 0)
                self.assertIsNone(valeurs["resultat"]["xg_par_tir_pour"])
                self.assertIsNone(valeurs["systeme"]["formation_depart"])
                self.assertIsNone(valeurs["attaque"]["possession"])

    def test_evenements_tronques(self):
        tronques = [
            {"type": {"name": "Shot"}, "team": {"id": A}},  # ni tir, ni minute, ni lieu
            {"type": {"name": "Pass"}, "team": {"id": A}, "location": [10.0]},
            {"type": {"name": "Carry"}, "team": {"id": A}, "location": ["a", "b"],
             "carry": {"end_location": None}},
            {"type": "Pass", "team": "101", "minute": "dix"},
            {"type": {"name": "Shot"}, "team": {"id": B},
             "shot": {"statsbomb_xg": float("nan"), "outcome": None}},
            {"type": {"name": "Substitution"}, "team": {"id": A}, "player": None,
             "substitution": {"replacement": {"id": True}}},
            {"type": {"name": "Starting XI"}, "team": {"id": A},
             "tactics": {"formation": "quatre", "lineup": [None, {"player": {"id": "9"}}]}},
        ]
        valeurs = indicateurs.calculer(tronques, [{"team_id": A, "lineup": "x"}], [None], A)
        self.assertEqual(valeurs["resultat"]["tirs_pour"], 1)
        self.assertEqual(valeurs["resultat"]["xg_pour"], 0.0)
        self.assertEqual(valeurs["resultat"]["xg_contre"], 0.0)  # NaN ignoré
        self.assertEqual(valeurs["systeme"]["xi"], [])

    def test_listes_remplacees_par_un_nombre(self):
        """`positions` et `freeze_frame` qui ne sont pas des listes : ignorés, sans erreur."""
        evenements = [{"id": "u-1", "type": {"name": "Pass"}, "team": {"id": A}}]
        compositions = [{"team_id": A, "lineup": [{"player_id": 9, "positions": 3}]}]
        valeurs = indicateurs.calculer(evenements, compositions,
                                       [{"event_uuid": "u-1", "freeze_frame": 7}], A)
        self.assertEqual(valeurs["systeme"]["xi"], [])
        self.assertEqual(valeurs["trois_cent_soixante"]["images"], 1)
        self.assertEqual(valeurs["trois_cent_soixante"]["images_inexploitables"], 1)

    def test_deterministe_et_json(self):
        frames = inv.frames_360_riches()
        premier = calculer(frames=frames)
        self.assertEqual(premier, calculer(frames=frames))
        self.assertEqual(json.loads(json.dumps(premier)), premier)  # aucun type exotique

    def test_version_calcul_dans_les_valeurs(self):
        self.assertEqual(calculer()["version_calcul"], indicateurs.VERSION_CALCUL)
