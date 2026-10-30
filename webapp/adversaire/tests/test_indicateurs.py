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
                                    "numero": 1, "position_id": None}])

    def test_position_id_depuis_les_compositions(self):
        evenements = [e for e in inv.evenements_riches() if e["type"]["name"] != "Starting XI"]
        compositions = inv.compositions_riches()
        compositions[0]["lineup"][0]["positions"] = [{"position_id": 1, "position": "Goalkeeper",
                                                      "start_reason": "Starting XI"}]
        s = calculer(evenements=evenements, compositions=compositions)["systeme"]
        self.assertEqual(s["xi"][0]["position_id"], 1)


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
        self.assertEqual(indicateurs.VERSION_CALCUL, 2)


# ── Version 2 (étape 3c-1) ───────────────────────────────────────────────────
class ParMotifEtZonesTest(SimpleTestCase):
    def test_buts_et_xg_par_type_d_action(self):
        m = calculer()["resultat"]["par_motif"]
        vide = {"tirs_pour": 0, "buts_pour": 0, "xg_pour": 0.0,
                "tirs_contre": 0, "buts_contre": 0, "xg_contre": 0.0}
        self.assertEqual(m["jeu_courant"], dict(vide, tirs_pour=1, buts_pour=1, xg_pour=0.4))
        self.assertEqual(m["corner"], dict(vide, tirs_pour=1, xg_pour=0.2))
        self.assertEqual(m["coup_franc"], dict(vide, tirs_pour=1, xg_pour=0.05))
        self.assertEqual(m["csc"], dict(vide, buts_pour=1))
        # Contre : tir sans play_pattern → « autre » ; penalty reconnu par le type de tir.
        self.assertEqual(m["autre"], dict(vide, tirs_contre=1, xg_contre=0.1))
        self.assertEqual(m["penalty"], dict(vide, tirs_contre=1, buts_contre=1, xg_contre=0.76))
        self.assertEqual(sorted(m), ["autre", "corner", "coup_franc", "csc", "jeu_courant", "penalty"])

    def test_grille_des_tirs_et_chutes_de_corner(self):
        valeurs = calculer()
        grille = valeurs["attaque"]["grille_tirs_xg"]
        self.assertEqual(len(grille), 96)
        self.assertAlmostEqual(grille[4 * 12 + 10], 0.4)  # tir de (108, 40)
        self.assertAlmostEqual(grille[4 * 12 + 11], 0.2)  # tir de (110, 40)
        self.assertAlmostEqual(grille[4 * 12 + 9], 0.05)  # tir de (95, 40)
        self.assertAlmostEqual(sum(grille), 0.65)
        chutes = valeurs["cpa"]["chutes_corners"]
        self.assertEqual((chutes[4 * 12 + 11], sum(chutes)), (1, 1))  # arrivée (110, 40)


class IndicateursV2Test(SimpleTestCase):
    """Valeurs recalculées à la main sur `evenements_v2()` / `frames_v2()`."""

    def setUp(self):
        self.v = indicateurs.calculer(inv.evenements_v2(), inv.compositions_riches(),
                                      inv.frames_v2(), A)

    def test_position_id_du_xi(self):
        self.assertEqual([(j["id"], j["position_id"]) for j in self.v["systeme"]["xi"]],
                         [(1, 1), (9, 23)])

    def test_xg_et_buts_par_tranche_de_15_minutes(self):
        d = self.v["danger"]
        self.assertEqual(d["tranches"], indicateurs.TRANCHES_15)
        self.assertEqual(d["xg_pour"], [0.35, 0, 0, 0, 0, 0, 0])
        self.assertEqual(d["buts_pour"], [1, 0, 0, 0, 0, 0, 0])
        # 91e minute de la 2e période : dernière tranche de la mi-temps (75-90+).
        self.assertEqual(d["xg_contre"], [0.3, 0.2, 0, 0, 0, 0.1, 0])
        self.assertEqual(d["buts_contre"], [0] * 7)

    def test_hauteur_des_actions_defensives_selon_le_score(self):
        self.assertEqual(self.v["danger"]["hauteur_par_score"], {
            "egalite": {"actions": 1, "somme_hauteur": 50.0, "hauteur": 50.0},  # avant son but
            "mene": {"actions": 1, "somme_hauteur": 80.0, "hauteur": 80.0},
            "est_mene": {"actions": 0, "somme_hauteur": 0.0, "hauteur": None},
        })

    def test_transitions(self):
        t = self.v["transitions"]
        self.assertEqual(t["fenetre_secondes"], 15)
        self.assertEqual(t["pour"], {"recuperations": 1, "tirs": 1, "xg": 0.25, "buts": 1})
        self.assertEqual(t["contre"], {"recuperations": 1, "tirs": 1, "xg": 0.2, "buts": 0})
        self.assertEqual(t["pertes_vers_tir"], 1)
        self.assertEqual(t["grille_pertes_vers_tir"][1 * 12 + 2], 1)  # perte en (20, 10)

    def test_pression_sur_la_relance(self):
        r = self.v["trois_cent_soixante"]["relance"]
        self.assertEqual(r["passes"], 3)
        self.assertEqual(r["par_pression"], {
            "0": {"passes": 2, "reussies": 1, "vers_l_avant": 2},
            "1": {"passes": 1, "reussies": 1, "vers_l_avant": 1},
            "2+": {"passes": 0, "reussies": 0, "vers_l_avant": 0},
        })

    def test_gardien_sous_pression(self):
        self.assertEqual(self.v["trois_cent_soixante"]["gardien_sous_pression"], {
            "sous_pression": {"passes": 1, "longues": 1, "longues_reussies": 1},
            "hors_pression": {"passes": 0, "longues": 0, "longues_reussies": 0},
        })

    def test_ligne_defensive_selon_le_score(self):
        ligne = self.v["trois_cent_soixante"]["ligne_defensive_par_score"]
        # Ses 4 joueurs les plus reculés : 120 - (90, 85, 80, 75) = 30, 35, 40, 45.
        self.assertEqual(ligne["mene"], {"images": 1, "somme_hauteur": 37.5, "hauteur": 37.5})
        self.assertEqual(ligne["egalite"]["images"], 0)
        self.assertIsNone(ligne["egalite"]["hauteur"])

    def test_presence_dans_la_surface_sur_corner(self):
        t = self.v["trois_cent_soixante"]
        self.assertEqual(t["corners_pour"], {"corners": 1, "attaquants_6m": 1, "defenseurs_6m": 2,
                                             "attaquants_surface": 2, "defenseurs_surface": 3})
        self.assertEqual(t["corners_contre"], {"corners": 1, "attaquants_6m": 1, "defenseurs_6m": 1,
                                               "attaquants_surface": 2, "defenseurs_surface": 2})

    def test_eliminations_et_receptions_entre_les_lignes_par_joueur(self):
        joueurs = {j["id"]: j for j in self.v["joueurs"]["liste"]}
        self.assertEqual((joueurs[9]["passes_5_elimines"], joueurs[9]["receptions_entre_lignes"]),
                         (1, 1))
        self.assertEqual((joueurs[1]["passes_5_elimines"], joueurs[1]["receptions_entre_lignes"]),
                         (0, 0))
        t = self.v["trois_cent_soixante"]
        self.assertEqual((t["passes_5_elimines"], t["receptions_entre_lignes"]), (1, 1))

    def test_sans_360_blocs_a_none(self):
        v = indicateurs.calculer(inv.evenements_v2(), inv.compositions_riches(), None, A)
        self.assertIsNone(v["trois_cent_soixante"])
        self.assertTrue(all(j["passes_5_elimines"] is None and j["receptions_entre_lignes"] is None
                            for j in v["joueurs"]["liste"]))
        # Ce qui ne dépend pas du 360 reste calculé.
        self.assertEqual(v["transitions"]["pour"]["tirs"], 1)

    def test_images_360_incompletes_sans_erreur(self):
        evenements = inv.evenements_v2() + [
            {"id": ["pas", "un", "texte"], "type": {"name": "Pass"}, "team": {"id": A},
             "location": [10.0, 40.0]}]
        frames = [
            {"event_uuid": "v-03", "freeze_frame": [{"location": [1.0]}, {"teammate": "oui"},
                                                    {"location": [12.0, 40.0]}, None]},
            {"event_uuid": ["liste"]},
            {"event_uuid": "v-15"},  # ni zone ni joueurs
            {"event_uuid": "v-05", "freeze_frame": "pas une liste"},
        ]
        t = indicateurs.calculer(evenements, None, frames, A)["trois_cent_soixante"]
        self.assertEqual(t["relance"]["passes"], 0)
        self.assertEqual(t["gardien_sous_pression"]["sous_pression"]["passes"], 0)
        self.assertEqual(t["corners_pour"]["corners"], 0)
        self.assertEqual(t["passes_5_elimines"], 0)

    def test_deterministe_et_json(self):
        autre = indicateurs.calculer(inv.evenements_v2(), inv.compositions_riches(),
                                     inv.frames_v2(), A)
        self.assertEqual(self.v, autre)
        self.assertEqual(json.loads(json.dumps(self.v)), self.v)


# ── Version 2 : règles une à une, sur de petits matchs écrits pour le test ────
def ev(ident, nature, equipe=A, periode=1, minute=0, seconde=0, **champs):
    """Événement inventé minimal ; `champs` complète (location, shot, pass…)."""
    return dict({"id": ident, "type": {"name": nature}, "team": {"id": equipe},
                 "period": periode, "minute": minute, "second": seconde}, **champs)


def tir(ident, equipe=A, periode=1, minute=0, seconde=0, xg=0.1, issue="Off T"):
    return ev(ident, "Shot", equipe, periode, minute, seconde, location=[100.0, 40.0],
              shot={"statsbomb_xg": xg, "outcome": {"name": issue}})


def passe(ident, depart, arrivee, equipe=A, minute=0, seconde=0, sorte=None, issue=None,
          poste=None, longueur=None, joueur=None):
    bloc = {"end_location": list(arrivee)}
    if sorte:
        bloc["type"] = {"name": sorte}
    if issue:
        bloc["outcome"] = {"name": issue}
    if longueur is not None:
        bloc["length"] = longueur
    champs = {"location": list(depart), "pass": bloc}
    if poste:
        champs["position"] = {"name": poste}
    if joueur:
        champs["player"] = {"id": joueur, "name": f"Joueur {joueur}"}
    return ev(ident, "Pass", equipe, 1, minute, seconde, **champs)


def image(uuid, *joueurs):
    """`joueurs` : (x, y, partenaire, gardien, acteur), dans le repère de l'acteur."""
    return {"event_uuid": uuid, "freeze_frame": [
        {"location": [x, y], "teammate": partenaire, "keeper": gardien, "actor": acteur}
        for x, y, partenaire, gardien, acteur in joueurs]}


def adversaire(x, y=40.0, gardien=False):
    return (x, y, False, gardien, False)


def calcul(evenements, frames=None, equipe=A):
    return indicateurs.calculer(evenements, None, frames, equipe)


class DangerReglesTest(SimpleTestCase):
    def test_etat_du_score(self):
        self.assertEqual(indicateurs._etat_score((1, 600), [], [(1, 300)]), "est_mene")
        self.assertEqual(indicateurs._etat_score((1, 600), [(1, 300)], []), "mene")
        # Un but au même instant ne compte pas encore (« juste AVANT »).
        self.assertEqual(indicateurs._etat_score((1, 300), [], [(1, 300)]), "egalite")
        # 2e période après un but de la 1re : l'ordre (période, secondes) est respecté.
        self.assertEqual(indicateurs._etat_score((2, 2700), [], [(1, 2750)]), "est_mene")

    def test_hauteur_des_actions_defensives_quand_il_est_mene(self):
        evenements = [tir("b", B, minute=10, issue="Goal"),
                      ev("p1", "Pressure", minute=5, location=[60.0, 40.0]),
                      ev("p2", "Pressure", minute=20, location=[30.0, 40.0]),
                      ev("p3", "Interception", minute=30, location=[40.0, 40.0])]
        h = calcul(evenements)["danger"]["hauteur_par_score"]
        self.assertEqual(h["egalite"], {"actions": 1, "somme_hauteur": 60.0, "hauteur": 60.0})
        self.assertEqual(h["est_mene"], {"actions": 2, "somme_hauteur": 70.0, "hauteur": 35.0})
        self.assertEqual(h["mene"]["actions"], 0)

    def test_tranches_temps_additionnel_et_prolongations(self):
        evenements = [tir("t1", periode=1, minute=46, xg=0.1),   # 45+1 : 30-45+
                      tir("t2", periode=2, minute=45, xg=0.2),   # coup d'envoi : 45-60
                      tir("t3", periode=2, minute=93, xg=0.4),   # 90+3 : 75-90+
                      tir("t4", periode=3, minute=95, xg=0.05),  # prolongations
                      tir("t5", periode=4, minute=118, xg=0.15),
                      tir("t6", periode=5, minute=121, xg=0.7)]  # tirs au but : exclus
        d = calcul(evenements)["danger"]
        self.assertEqual(d["xg_pour"], [0, 0, 0.1, 0.2, 0, 0.4, 0.2])

    def test_but_contre_son_camp_dans_la_repartition_du_danger(self):
        """« Own Goal For » est rangé chez l'équipe qui en profite : but pour elle,
        sans xG, et il change l'état du score."""
        evenements = [ev("csc", "Own Goal For", periode=2, minute=60),
                      ev("p", "Pressure", periode=2, minute=70, location=[70.0, 40.0])]
        d = calcul(evenements)["danger"]
        self.assertEqual(d["buts_pour"], [0, 0, 0, 0, 1, 0, 0])
        self.assertEqual(d["xg_pour"], [0] * 7)
        self.assertEqual(d["hauteur_par_score"]["mene"]["actions"], 1)
        d = calcul(evenements, equipe=B)["danger"]
        self.assertEqual((d["buts_contre"], d["buts_pour"]), ([0, 0, 0, 0, 1, 0, 0], [0] * 7))


class TransitionsReglesTest(SimpleTestCase):
    def test_meme_mi_temps_exigee(self):
        recuperation = ev("r", "Ball Recovery", periode=1, minute=44, seconde=55,
                          location=[50.0, 40.0])
        # 5 s plus tard en temps de jeu, mais à la reprise : ne compte pas.
        t = calcul([recuperation, tir("s", periode=2, minute=45, seconde=0)])["transitions"]
        self.assertEqual((t["pour"]["recuperations"], t["pour"]["tirs"]), (1, 0))
        t = calcul([recuperation, tir("s", periode=1, minute=45, seconde=0)])["transitions"]
        self.assertEqual(t["pour"]["tirs"], 1)

    def test_fenetre_de_15_s_entre_la_perte_et_le_tir_adverse(self):
        evenements = [ev("d1", "Dispossessed", minute=10, location=[20.0, 10.0]),
                      tir("s1", B, minute=10, seconde=15),             # 15 s pile : compte
                      ev("d2", "Dispossessed", minute=20, location=[30.0, 10.0]),
                      tir("s2", B, minute=20, seconde=16),             # 16 s : trop tard
                      ev("d3", "Miscontrol", minute=30, seconde=10, location=[60.0, 10.0]),
                      tir("s3", B, minute=30, seconde=5)]              # avant la perte
        t = calcul(evenements)["transitions"]
        self.assertEqual(t["pertes_vers_tir"], 1)
        self.assertEqual(t["grille_pertes_vers_tir"][1 * 12 + 2], 1)  # perte en (20, 10)

    def test_tacle_gagne_exige_pour_une_recuperation(self):
        evenements = [
            ev("d1", "Duel", minute=1, location=[50.0, 40.0],
               duel={"type": {"name": "Tackle"}, "outcome": {"name": "Won"}}),
            ev("d2", "Duel", minute=2, location=[50.0, 40.0],
               duel={"type": {"name": "Aerial Lost"}, "outcome": {"name": "Won"}}),
            ev("d3", "Duel", minute=3, location=[50.0, 40.0],
               duel={"type": {"name": "Tackle"}, "outcome": {"name": "Lost In Play"}}),
            ev("i1", "Interception", minute=4, location=[50.0, 40.0],
               interception={"outcome": {"name": "Won"}}),
            ev("i2", "Interception", minute=5, location=[50.0, 40.0],
               interception={"outcome": {"name": "Lost"}}),
            ev("b1", "Ball Recovery", minute=6, location=[50.0, 40.0],
               ball_recovery={"recovery_failure": True}),
        ]
        self.assertEqual(calcul(evenements)["transitions"]["pour"]["recuperations"], 2)

    def test_coups_de_pied_arretes_exclus_des_pertes(self):
        evenements = [passe("c", (119.0, 1.0), (110.0, 40.0), minute=10, sorte="Corner",
                            issue="Incomplete"),
                      tir("s1", B, minute=10, seconde=5),
                      passe("t", (30.0, 0.0), (35.0, 10.0), minute=20, sorte="Throw-in", issue="Out"),
                      tir("s2", B, minute=20, seconde=5),
                      passe("j", (50.0, 40.0), (70.0, 40.0), minute=30, issue="Incomplete"),
                      tir("s3", B, minute=30, seconde=5)]
        t = calcul(evenements)["transitions"]
        self.assertEqual(t["pertes_vers_tir"], 1)
        self.assertEqual(t["grille_pertes_vers_tir"][4 * 12 + 5], 1)  # passe ratée de (50, 40)


class TroisCentSoixanteReglesTest(SimpleTestCase):
    def test_unites_en_yards(self):
        # Terrain StatsBomb en yards : 5 m ≈ 5,47 yd, 32 m ≈ 35 yd.
        self.assertAlmostEqual(indicateurs.RAYON_PRESSION, 5.468, places=3)
        self.assertAlmostEqual(indicateurs.LONG_BALLON, 34.996, places=3)

    def test_rayon_de_pression_de_5_m(self):
        # Adversaire à 5,2 yd (4,75 m) : à moins de 5 m ; à 5,6 yd (5,12 m) : non.
        evenements = [passe("p1", (10.0, 40.0), (30.0, 40.0)),
                      passe("p2", (10.0, 40.0), (30.0, 40.0))]
        frames = [image("p1", adversaire(15.2)), image("p2", adversaire(15.6))]
        r = calcul(evenements, frames)["trois_cent_soixante"]["relance"]["par_pression"]
        self.assertEqual((r["0"]["passes"], r["1"]["passes"]), (1, 1))

    def test_relance_limitee_a_son_premier_tiers(self):
        evenements = [passe("p1", (39.9, 40.0), (60.0, 40.0)),
                      passe("p2", (40.0, 40.0), (60.0, 40.0)),
                      passe("p3", (10.0, 40.0), (60.0, 40.0), sorte="Goal Kick")]
        frames = [image(u, adversaire(80.0)) for u in ("p1", "p2", "p3")]
        self.assertEqual(calcul(evenements, frames)["trois_cent_soixante"]["relance"]["passes"], 1)

    def test_ballon_long_du_gardien_32_m(self):
        # 34 yd = 31,1 m : pas long ; 35,5 yd = 32,5 m : long.
        evenements = [passe("g1", (5.0, 40.0), (39.0, 40.0), poste="Goalkeeper", longueur=34.0),
                      passe("g2", (5.0, 40.0), (40.5, 40.0), poste="Goalkeeper", longueur=35.5)]
        frames = [image("g1", adversaire(80.0)), image("g2", adversaire(80.0))]
        g = calcul(evenements, frames)["trois_cent_soixante"]["gardien_sous_pression"]
        self.assertEqual(g["hors_pression"], {"passes": 2, "longues": 1, "longues_reussies": 1})

    def test_gardien_sous_pression_joueurs_de_champ_seulement(self):
        evenements = [passe("g1", (5.0, 40.0), (50.0, 40.0), poste="Goalkeeper"),
                      passe("g2", (5.0, 40.0), (50.0, 40.0), poste="Goalkeeper")]
        frames = [
            # 2 joueurs de champ + leur gardien dans son tiers : pas « sous pression ».
            image("g1", adversaire(20.0), adversaire(30.0), adversaire(35.0, gardien=True)),
            image("g2", adversaire(20.0), adversaire(30.0), adversaire(38.0)),
        ]
        g = calcul(evenements, frames)["trois_cent_soixante"]["gardien_sous_pression"]
        self.assertEqual((g["sous_pression"]["passes"], g["hors_pression"]["passes"]), (1, 1))

    def test_tireur_du_corner_exclu_des_attaquants(self):
        evenements = [passe("c", (120.0, 0.0), (115.0, 40.0), sorte="Corner")]
        frames = [image("c", (115.0, 38.0, True, False, True),   # le tireur (acteur)
                        (116.0, 45.0, True, False, False),        # attaquant dans les 6 m
                        (117.0, 40.0, True, True, False),         # son gardien : exclu
                        (110.0, 40.0, False, False, False),       # défenseur dans la surface
                        (118.0, 40.0, False, True, False))]       # gardien adverse : exclu
        c = calcul(evenements, frames)["trois_cent_soixante"]["corners_pour"]
        self.assertEqual(c, {"corners": 1, "attaquants_6m": 1, "defenseurs_6m": 0,
                             "attaquants_surface": 1, "defenseurs_surface": 1})

    def test_passe_qui_elimine_5_adversaires(self):
        self.assertEqual(indicateurs.ELIMINES_MIN, 5)
        evenements = [passe("p1", (30.0, 40.0), (90.0, 40.0), joueur=7),
                      passe("p2", (30.0, 40.0), (90.0, 40.0), joueur=7),
                      passe("p3", (30.0, 40.0), (90.0, 40.0), joueur=7, issue="Incomplete")]
        cinq = [adversaire(x) for x in (40.0, 50.0, 60.0, 70.0, 80.0)]
        frames = [image("p1", *cinq),
                  # 4 de champ entre départ et arrivée ; le gardien et celui au-delà ne comptent pas.
                  image("p2", *cinq[:4], adversaire(85.0, gardien=True), adversaire(95.0)),
                  image("p3", *cinq)]  # passe ratée
        v = calcul(evenements, frames)
        self.assertEqual(v["trois_cent_soixante"]["passes_5_elimines"], 1)
        self.assertEqual(next(j for j in v["joueurs"]["liste"] if j["id"] == 7)["passes_5_elimines"], 1)

    def test_receptions_entre_les_lignes(self):
        def reception(ident, x):
            return ev(ident, "Ball Receipt*", location=[x, 40.0], player={"id": 8, "name": "Joueur 8"})

        lignes = [adversaire(x) for x in (60.0, 70.0, 80.0, 30.0, 35.0)]  # 3 devant, 2 derrière
        evenements = [reception("r1", 50.0), reception("r2", 39.0), reception("r3", 50.0),
                      reception("r4", 50.0)]
        frames = [image("r1", *lignes),
                  image("r2", *lignes),                             # dans son tiers
                  image("r3", *lignes[1:]),                         # 2 devant seulement
                  image("r4", *lignes[:4], adversaire(20.0, gardien=True))]  # 1 de champ derrière
        v = calcul(evenements, frames)
        self.assertEqual(v["trois_cent_soixante"]["receptions_entre_lignes"], 1)
        self.assertEqual(indicateurs.ENTRE_LIGNES_DEVANT, 3)
        self.assertEqual(indicateurs.ENTRE_LIGNES_DERRIERE, 2)

    def test_seuls_les_corners_dans_les_zones_de_chute(self):
        evenements = [passe("c", (120.0, 0.0), (110.0, 40.0), sorte="Corner"),
                      passe("f", (80.0, 40.0), (110.0, 40.0), sorte="Free Kick"),
                      passe("j", (80.0, 40.0), (110.0, 40.0))]
        chutes = calcul(evenements)["cpa"]["chutes_corners"]
        self.assertEqual((chutes[4 * 12 + 11], sum(chutes)), (1, 1))
