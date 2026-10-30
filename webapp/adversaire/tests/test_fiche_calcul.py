"""Étape 3c-1 : synthèse des derniers matchs (`analyse/fiche.py`), calcul PUR.

Sans base ni réseau. Les valeurs par match sont écrites à la main (seules les
clés utiles au test) : chaque résultat attendu se recalcule de tête."""
import json

from django.test import SimpleTestCase

from adversaire.analyse import fiche, indicateurs

from . import donnees_inventees as inv


def element(i, pour, contre=None, a_domicile=True):
    """Match n° i (0 = le plus récent)."""
    return {"match": 100 - i, "date": f"2026-10-{25 - i:02d}", "a_domicile": a_domicile,
            "pour": pour, "contre": {} if contre is None else contre}


def joueur(ident, minutes, **valeurs):
    return dict({"id": ident, "nom": f"Joueur {ident}", "minutes": minutes}, **valeurs)


def titulaire(ident, position_id=None, poste=None):
    return {"id": ident, "nom": f"Joueur {ident}", "numero": ident, "poste": poste,
            "position_id": position_id}


class ResultatsTest(SimpleTestCase):
    def test_ppda_rapport_des_sommes(self):
        valeurs = fiche.construire([
            element(0, {"defense": {"ppda_passes_adverses": 10, "ppda_actions": 2}}),
            element(1, {"defense": {"ppda_passes_adverses": 30, "ppda_actions": 10}}),
        ])
        # 40 / 12, et non la moyenne des ratios (5 + 3) / 2 = 4.
        self.assertEqual(valeurs["resultats"]["total"]["ppda"], 3.33)

    def test_domicile_exterieur_separes(self):
        valeurs = fiche.construire([
            element(0, {"resultat": {"xg_pour": 1.0, "tirs_pour": 4, "buts_pour": 2}}),
            element(1, {"resultat": {"xg_pour": 0.5, "tirs_pour": 1, "buts_pour": 0}},
                    a_domicile=False),
        ])
        r = valeurs["resultats"]
        self.assertEqual((r["domicile"]["matchs"], r["domicile"]["xg_pour"]), (1, 1.0))
        self.assertEqual((r["exterieur"]["matchs"], r["exterieur"]["xg_pour"]), (1, 0.5))
        self.assertEqual(r["total"]["xg_pour"], 1.5)
        self.assertEqual(r["total"]["xg_pour_par_match"], 0.75)
        self.assertEqual(r["total"]["xg_par_tir_pour"], 0.3)  # 1.5 / 5 tirs
        self.assertEqual(r["total"]["buts_pour"], 2)

    def test_possession_sur_les_totaux(self):
        valeurs = fiche.construire([
            element(0, {"attaque": {"passes": 300}}, {"attaque": {"passes": 100}}),
            element(1, {"attaque": {"passes": 100}}, {"attaque": {"passes": 300}}),
        ])
        self.assertEqual(valeurs["resultats"]["total"]["possession_pct"], 50.0)
        self.assertEqual(valeurs["attaque"]["possession_pct"], 50.0)

    def test_par_motif_additionne_et_ordonne(self):
        motifs = {"corner": {"tirs_pour": 2, "buts_pour": 1, "xg_pour": 0.3},
                  "jeu_courant": {"tirs_contre": 1, "xg_contre": 0.1}}
        valeurs = fiche.construire([element(0, {"resultat": {"par_motif": motifs}}),
                                    element(1, {"resultat": {"par_motif": motifs}})])
        lignes = valeurs["resultats"]["par_motif"]
        self.assertEqual([m["motif"] for m in lignes], ["jeu_courant", "corner"])
        self.assertEqual((lignes[1]["libelle"], lignes[1]["tirs_pour"], lignes[1]["buts_pour"],
                          lignes[1]["xg_pour"]), ("Corner", 4, 2, 0.6))
        self.assertEqual(lignes[0]["xg_contre"], 0.2)


class SystemeTest(SimpleTestCase):
    def setUp(self):
        xi = [[1, 2, 3], [1, 2, 4], [1, 4, 5, 6], [1, 4, 5, 7], [2, 4]]
        minutes = [{1: 90, 2: 90, 3: 90}, {1: 90, 2: 90, 4: 90},
                   {1: 90, 4: 90, 5: 90, 6: 30}, {1: 90, 4: 90, 5: 90, 7: 80}, {2: 90, 4: 90}]
        formations = ["4231", "433", "433", "4231", "442"]
        postes_4 = {1: (4, "Center Back"), 2: (5, "Left Center Back"),
                    3: (5, "Left Center Back"), 4: (4, "Center Back")}
        self.elements = []
        for i in range(5):
            titulaires = [titulaire(j, *(postes_4[i] if j == 4 else ((1, "Goalkeeper") if j == 1 else (None, None))))
                          for j in xi[i]]
            self.elements.append(element(i, {
                "systeme": {"formation_depart": formations[i], "xi": titulaires,
                            "changements": [{"minute": 60, "formation": "442"}] if i == 0 else []},
                "joueurs": {"liste": [joueur(j, m) for j, m in minutes[i].items()]},
            }))
        self.s = fiche.construire(self.elements)["systeme"]

    def test_formation_la_plus_frequente_egalite_la_plus_recente(self):
        # 4231 et 433 deux fois chacune : 4231 est la plus récente.
        self.assertEqual(self.s["formation_probable"], "4-2-3-1")
        self.assertEqual(self.s["formations"], [{"formation": "4-2-3-1", "matchs": 2},
                                                {"formation": "4-3-3", "matchs": 2},
                                                {"formation": "4-4-2", "matchs": 1}])
        self.assertEqual(self.s["changements"], [{"date": "2026-10-25", "minute": 60,
                                                  "formation": "4-4-2"}])

    def test_xi_probable_et_departages(self):
        # Titularisations 1:4, 4:4, 2:3, 5:2 puis 3, 7, 6 à 1 : 1 avant 4 (titulaire au
        # dernier match), 3 avant 7 (idem), 7 avant 6 (80 minutes contre 30).
        self.assertEqual([j["id"] for j in self.s["xi_probable"]], [1, 4, 2, 5, 3, 7, 6])
        par_id = {j["id"]: j for j in self.s["xi_probable"]}
        self.assertEqual((par_id[1]["titularisations"], par_id[1]["minutes"]), (4, 360))
        # Poste du 4 : 4 et 5 deux fois chacun, le plus récent (4) l'emporte.
        self.assertEqual((par_id[4]["position_id"], par_id[4]["poste"]), (4, "Center Back"))
        # Placés sur le terrain d'après position_id ; sans poste : pas de coordonnées.
        self.assertEqual((par_id[1]["x"], par_id[1]["y"]), fiche.POSITIONS[1])
        self.assertEqual((par_id[4]["x"], par_id[4]["y"]), fiche.POSITIONS[4])
        self.assertIsNone(par_id[2]["x"])

    def test_absents_du_dernier_match(self):
        # 1 : titulaire 3 fois sur les 4 précédents mais a joué ; 4 : 4 fois, 0 minute.
        self.assertEqual(self.s["absents"], [{"id": 4, "nom": "Joueur 4", "numero": 4,
                                              "titularisations_precedentes": 4}])
        self.assertEqual(self.s["matchs_precedents"], 4)
        self.assertTrue(self.s["dernier_match_calcule"])

    def test_dernier_match_non_calcule_ni_absents_ni_departage(self):
        """Le plus récent n'a pas ses lignes : le 1er match CALCULÉ n'est pas le
        dernier match joué, il ne sert ni aux absents ni au départage."""
        non_calcule = element(0, {"systeme": {"xi": [titulaire(9)]}})
        non_calcule["contre"] = None
        elements = [non_calcule,
                    # 1 : 0 minute ici, titulaire 3 fois avant ; 2 : titulaire ici, 10 minutes.
                    element(1, {"systeme": {"xi": [titulaire(2)]},
                                "joueurs": {"liste": [joueur(2, 10)]}})]
        for i in (2, 3, 4):
            elements.append(element(i, {"systeme": {"xi": [titulaire(1), titulaire(3)]},
                                        "joueurs": {"liste": [joueur(1, 90), joueur(3, 5)]}}))
        s = fiche.construire(elements)["systeme"]
        self.assertFalse(s["dernier_match_calcule"])
        self.assertEqual((s["absents"], s["matchs_precedents"]), ([], 0))
        # 1 et 3 : trois titularisations chacun, départagés aux minutes ; 2 : une seule.
        # Le 9, titulaire du match non calculé, n'apparaît pas.
        self.assertEqual([j["id"] for j in s["xi_probable"]], [1, 3, 2])

    def test_departage_sans_dernier_match_aux_minutes(self):
        non_calcule = element(0, {})
        non_calcule["contre"] = None
        elements = [non_calcule,
                    element(1, {"systeme": {"xi": [titulaire(1)]}, "joueurs": {"liste": [joueur(1, 10)]}}),
                    element(2, {"systeme": {"xi": [titulaire(2)]}, "joueurs": {"liste": [joueur(2, 90)]}})]
        # Une titularisation chacun : 2 (90 minutes) passe devant 1, titulaire du
        # premier match calculé mais pas du dernier match joué.
        self.assertEqual([j["id"] for j in fiche.construire(elements)["systeme"]["xi_probable"]], [2, 1])
        # Même données avec le dernier match calculé : 1, titulaire au dernier match, d'abord.
        self.assertEqual([j["id"] for j in fiche.construire(elements[1:])["systeme"]["xi_probable"]],
                         [1, 2])

    def test_minutes(self):
        self.assertEqual([(j["id"], j["minutes"], j["matchs"]) for j in self.s["minutes"]][:3],
                         [(1, 360, 4), (4, 360, 4), (2, 270, 3)])

    def test_deux_joueurs_au_meme_poste_decales(self):
        valeurs = fiche.construire([element(0, {"systeme": {"xi": [
            titulaire(1, 4, "Center Back"), titulaire(2, 4, "Center Back")]}})])
        xi = valeurs["systeme"]["xi_probable"]
        self.assertNotEqual((xi[0]["x"], xi[0]["y"]), (xi[1]["x"], xi[1]["y"]))


class JoueursClesTest(SimpleTestCase):
    def test_obv_par_90_peu_joue_et_minutes_des_seuls_matchs_avec_obv(self):
        valeurs = fiche.construire([
            element(0, {"joueurs": {"obv_disponible": True, "liste": [
                joueur(8, 60, obv_cree=0.2, obv_concede=0.1, xg=0.1, actions_progressives=2),
                joueur(9, 90, obv_cree=0.3, obv_concede=0.0, xg=0.0, actions_progressives=1)]}}),
            element(1, {"joueurs": {"obv_disponible": False, "liste": [
                joueur(9, 90, obv_cree=None, obv_concede=None, xg=0.5, actions_progressives=0)]}}),
        ])
        j = valeurs["joueurs"]
        self.assertTrue(j["obv_disponible"])
        cles = j["cles"]
        # 0.3 / 90 min (le match sans OBV ne compte pas) contre 0.2 × 90 / 60 = 0.3 : à
        # égalité, le plus de minutes d'abord.
        self.assertEqual([c["id"] for c in cles], [9, 8])
        self.assertEqual((cles[0]["obv_cree_90"], cles[0]["minutes"], cles[0]["peu_joue"]),
                         (0.3, 180, False))
        self.assertEqual((cles[1]["obv_cree_90"], cles[1]["obv_concede_90"], cles[1]["peu_joue"]),
                         (0.3, 0.15, True))
        self.assertAlmostEqual(cles[0]["xg"], 0.5)

    def test_sans_obv_repli_sur_l_xg(self):
        valeurs = fiche.construire([element(0, {"joueurs": {"obv_disponible": False, "liste": [
            joueur(8, 90, xg=0.1, actions_progressives=5),
            joueur(9, 90, xg=0.4, actions_progressives=0),
            joueur(10, 0, xg=0.9)]}})])
        j = valeurs["joueurs"]
        self.assertFalse(j["obv_disponible"])
        self.assertEqual([c["id"] for c in j["cles"]], [9, 8])  # 0 minute : écarté
        self.assertIsNone(j["cles"][0]["obv_cree_90"])

    def test_listes_360_par_joueur(self):
        valeurs = fiche.construire([
            element(0, {"joueurs": {"liste": [joueur(8, 90, passes_5_elimines=2,
                                                     receptions_entre_lignes=0),
                                              joueur(9, 90, passes_5_elimines=None)]}}),
            element(1, {"joueurs": {"liste": [joueur(8, 90, passes_5_elimines=1,
                                                     receptions_entre_lignes=3)]}}),
        ])
        j = valeurs["joueurs"]
        self.assertEqual([(c["id"], c["passes_5_elimines"]) for c in j["eliminateurs"]], [(8, 3)])
        self.assertEqual([(c["id"], c["receptions_entre_lignes"]) for c in j["entre_les_lignes"]],
                         [(8, 3)])


class TroisCentSoixanteTest(SimpleTestCase):
    def bloc(self):
        return {
            "relance": {"passes": 4, "par_pression": {
                "0": {"passes": 2, "reussies": 2, "vers_l_avant": 1},
                "1": {"passes": 1, "reussies": 0, "vers_l_avant": 1},
                "2+": {"passes": 1, "reussies": 1, "vers_l_avant": 0}}},
            "gardien_sous_pression": {"sous_pression": {"passes": 2, "longues": 1, "longues_reussies": 0},
                                      "hors_pression": {"passes": 1, "longues": 0, "longues_reussies": 0}},
            "ligne_defensive_par_score": {"mene": {"images": 2, "somme_hauteur": 70.0}},
            "corners_pour": {"corners": 2, "attaquants_6m": 3, "defenseurs_6m": 4,
                             "attaquants_surface": 9, "defenseurs_surface": 12},
            "corners_contre": {"corners": 0},
            "passes_5_elimines": 1,
            "receptions_entre_lignes": 2,
        }

    def test_n_matchs_sur_5_avec_360(self):
        elements = [element(i, {"trois_cent_soixante": self.bloc() if i in (1, 3) else None})
                    for i in range(5)]
        valeurs = fiche.construire(elements)
        self.assertEqual((valeurs["nb_matchs_calcules"], valeurs["matchs_avec_360"]), (5, 2))
        t = valeurs["trois_cent_soixante"]
        self.assertEqual(t["matchs_avec_360"], 2)
        self.assertEqual((t["relance"]["passes"], t["relance"]["sans_adversaire_pct"]), (8, 50.0))
        palier_0 = t["relance"]["paliers"][0]
        self.assertEqual((palier_0["pression"], palier_0["reussite_pct"], palier_0["vers_l_avant_pct"]),
                         ("0", 100.0, 50.0))
        sous_pression = t["gardien"][0]
        self.assertEqual((sous_pression["situation"], sous_pression["part_longues_pct"],
                          sous_pression["reussite_longues_pct"]), ("sous_pression", 50.0, 0.0))
        mene = t["ligne_defensive_par_score"][0]
        self.assertEqual((mene["etat"], mene["images"], mene["hauteur"]), ("mene", 4, 35.0))
        self.assertEqual(t["corners_pour"], {"corners": 4, "attaquants_6m": 1.5, "defenseurs_6m": 2.0,
                                             "attaquants_surface": 4.5, "defenseurs_surface": 6.0})
        self.assertIsNone(t["corners_contre"]["attaquants_6m"])  # aucun corner concédé
        self.assertEqual(t["receptions_entre_lignes_par_match"], 2.0)

    def test_sans_360(self):
        valeurs = fiche.construire([element(0, {"trois_cent_soixante": None})])
        self.assertEqual(valeurs["matchs_avec_360"], 0)
        self.assertIsNone(valeurs["trois_cent_soixante"])


class RobustesseTest(SimpleTestCase):
    def test_matchs_sans_lignes_ecartes(self):
        elements = [element(i, {"resultat": {"buts_pour": 1}}) for i in range(4)]
        elements.append({"match": 1, "date": "2026-09-01", "a_domicile": True, "pour": None,
                         "contre": {}})
        valeurs = fiche.construire(elements)
        self.assertEqual((valeurs["nb_matchs"], valeurs["nb_matchs_calcules"]), (5, 4))
        self.assertEqual(valeurs["resultats"]["total"]["buts_pour"], 4)

    def test_entrees_absentes_ou_mal_formees(self):
        for elements in (None, [], [None, 3], [{}],
                         [element(0, {"systeme": "x", "joueurs": {"liste": [None, {"id": "9"}]},
                                      "danger": {"xg_pour": "x"}, "attaque": {"grille_progression": [1]}})]):
            with self.subTest(elements=elements):
                valeurs = fiche.construire(elements)
                self.assertEqual(valeurs["version_fiche"], fiche.VERSION_FICHE)
                self.assertIsNone(valeurs["resultats"]["total"]["ppda"])
                self.assertEqual(valeurs["systeme"]["xi_probable"], [])

    def test_niveaux_des_grilles(self):
        self.assertEqual(fiche.niveaux([0] * 96), [0] * 96)
        self.assertEqual(fiche.niveaux([0, 2, 1, 2, 4]), [0, 3, 1, 3, 5])
        self.assertEqual(fiche.niveaux([0, 7]), [0, 5])
        grille = fiche.construire([element(0, {"attaque": {"grille_progression": [0] * 95 + [3]}}),
                                   element(1, {"attaque": {"grille_progression": [0] * 95 + [1]}})])
        progression = grille["attaque"]["grille_progression"]
        self.assertEqual((progression["valeurs"][95], progression["niveaux"][95]), (4, 5))

    def test_depuis_de_vrais_indicateurs_et_deterministe(self):
        """Chaîne complète sur le match inventé : indicateurs → fiche."""
        elements = [{"match": 1, "date": "2026-10-01", "a_domicile": True,
                     "pour": indicateurs.calculer(inv.evenements_v2(), inv.compositions_riches(),
                                                  inv.frames_v2(), inv.ADVERSAIRE),
                     "contre": indicateurs.calculer(inv.evenements_v2(), inv.compositions_riches(),
                                                    inv.frames_v2(), 102)}]
        valeurs = fiche.construire(elements)
        self.assertEqual(valeurs, fiche.construire(elements))
        self.assertEqual(json.loads(json.dumps(valeurs)), valeurs)
        self.assertEqual(valeurs["systeme"]["formation_probable"], "4-2-3-1")
        self.assertEqual(valeurs["transitions"]["pour"]["tirs"], 1)
        self.assertEqual(valeurs["danger"]["tranches"][0]["xg_pour"], 0.35)
        self.assertEqual(valeurs["defense"]["hauteur_actions_defensives"], 65.0)  # (50 + 80) / 2
        self.assertEqual(valeurs["trois_cent_soixante"]["corners_pour"]["defenseurs_surface"], 3.0)
        self.assertEqual(valeurs["cpa"]["chutes_corners"]["valeurs"][4 * 12 + 11], 1)
