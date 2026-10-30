"""Étape 3c-2 : balises SVG `terrain_grille` et `barres` (templatetags/terrain.py).

Elles ne font que trier et contrôler des valeurs déjà calculées à l'import :
seuls des entiers bornés et des classes d'une liste fermée arrivent dans un
attribut. Valeurs attendues écrites en dur."""
from django.template import Context, Template
from django.test import SimpleTestCase

from adversaire.templatetags import terrain


def rendre(gabarit, **contexte):
    return Template("{% load terrain %}" + gabarit).render(Context(contexte))


def grille_essai():
    niveaux, valeurs = [0] * 96, [0] * 96
    niveaux[13], valeurs[13] = 5, 0.75   # rangée 1, colonne 1
    niveaux[95], valeurs[95] = 1, 0.1    # rangée 7, colonne 11
    return {"valeurs": valeurs, "niveaux": niveaux, "min_non_vide": 0.1, "max": 0.75}


class TerrainGrilleTest(SimpleTestCase):
    def test_96_cases_placees_et_classees(self):
        html = rendre('{% terrain_grille g "Essai" %}', g=grille_essai())
        self.assertEqual(html.count('<rect class="case niv-'), 96)
        self.assertEqual(html.count('<rect class="case niv-0"'), 94)
        self.assertIn('<rect class="case niv-5" x="10" y="10" width="10" height="10">', html)
        self.assertIn('<rect class="case niv-1" x="110" y="70" width="10" height="10">', html)
        self.assertIn("<title>0,75</title>", html)  # texte localisé (fr), 2 décimales fixes
        self.assertIn("<title>0,10</title>", html)
        self.assertIn("de 0,10 à 0,75", html)       # légende calculée à l'import
        self.assertNotIn("Aucune donnée", html)

    def test_grille_de_comptes_sans_decimale(self):
        grille = {"valeurs": [0] * 95 + [3], "niveaux": [0] * 95 + [5], "min_non_vide": 1, "max": 3}
        html = rendre('{% terrain_grille g "Comptes" 0 %}', g=grille)
        self.assertIn('<rect class="case niv-5" x="110" y="70" width="10" height="10"><title>3</title>', html)
        self.assertIn("de 1 à 3", html)

    def test_grille_xg_a_valeurs_entieres_deux_decimales(self):
        # Avec floatformat:"-2", 1 et 2 s'écriraient « 1 » et « 2 » : 2 décimales fixes.
        niveaux, valeurs = [0] * 96, [0] * 96
        niveaux[0], valeurs[0] = 3, 1
        niveaux[1], valeurs[1] = 5, 2
        grille = {"valeurs": valeurs, "niveaux": niveaux, "min_non_vide": 1, "max": 2}
        html = rendre('{% terrain_grille g "xG" 2 %}', g=grille)
        self.assertIn("<title>1,00</title>", html)
        self.assertIn("<title>2,00</title>", html)
        self.assertIn("de 1,00 à 2,00", html)

    def test_decimales_hors_liste_ramenees_a_2(self):
        for decimales in (5, -1, "1", True, None):
            with self.subTest(decimales=decimales):
                self.assertEqual(terrain.terrain_grille(grille_essai(), "Essai", decimales)["decimales"], 2)
                self.assertEqual(terrain.barres([], "", decimales)["decimales"], 2)

    def test_aucun_nombre_a_virgule_dans_un_attribut(self):
        html = rendre('{% terrain_grille g "Essai" %}', g=grille_essai())
        self.assertNotRegex(html, r'="[^"]*\d,\d[^"]*"')
        self.assertNotRegex(html, r"\sstyle\s*=")

    def test_cases_calculees_par_la_balise(self):
        cases = terrain.terrain_grille(grille_essai(), "Essai")["cases"]
        self.assertEqual(len(cases), 96)
        self.assertEqual(cases[13], {"x": 10, "y": 10, "niveau": 5, "valeur": 0.75})
        self.assertEqual(cases[95], {"x": 110, "y": 70, "niveau": 1, "valeur": 0.1})

    def test_grille_mal_formee_ou_vide_aucune_donnee(self):
        mauvais_niveau = grille_essai()
        mauvais_niveau["niveaux"][0] = 9
        texte = grille_essai()
        texte["niveaux"][0] = "<b>"
        booleen = grille_essai()
        booleen["niveaux"][0] = True
        for grille in (None, "abc", {}, {"niveaux": [0] * 95}, {"niveaux": [0] * 96},
                       mauvais_niveau, texte, booleen):
            with self.subTest(grille=grille):
                html = rendre('{% terrain_grille g "Essai" %}', g=grille)
                self.assertIn("Aucune donnée", html)
                self.assertNotIn('<rect class="case', html)
                self.assertNotIn("<b>", html)

    def test_valeur_inattendue_jamais_injectee(self):
        grille = grille_essai()
        grille["valeurs"][13] = "<b>x</b>"
        grille["max"] = float("inf")
        html = rendre('{% terrain_grille g "Essai" %}', g=grille)
        self.assertNotIn("<b>", html)
        self.assertIn('<rect class="case niv-5" x="10" y="10" width="10" height="10"><title>—</title>', html)
        self.assertIn("de 0,10 à —", html)


class BarresTest(SimpleTestCase):
    def lignes(self):
        return [
            {"libelle": "0-15", "series": [{"classe": "pour", "valeur": 0.2, "largeur": 40},
                                           {"classe": "contre", "valeur": 0.1, "largeur": 150}]},
            {"libelle": "15-30", "series": [{"classe": "pour", "valeur": 0, "largeur": -5},
                                            {"classe": 'x" onclick="alert(1)', "valeur": 1, "largeur": 50},
                                            {"classe": "contre", "valeur": "x", "largeur": 12.5}]},
            {"libelle": "vide", "series": [{"classe": "autre", "largeur": 10}]},
            "pas une ligne",
        ]

    def test_controle_des_series(self):
        resultat = terrain.barres(self.lignes())
        self.assertEqual(
            [(l["libelle"], [(s["classe"], s["largeur"], s["valeur"]) for s in l["series"]])
             for l in resultat["lignes"]],
            [("0-15", [("pour", 40, 0.2), ("contre", 100, 0.1)]),
             ("15-30", [("pour", 0, 0), ("contre", 0, None)])])

    def test_rendu(self):
        html = rendre('{% barres l " %" 1 %}', l=self.lignes())
        self.assertIn('<rect class="barre-pour" x="0" y="0" width="40" height="6"/>', html)
        self.assertIn('<rect class="barre-contre" x="0" y="0" width="100" height="6"/>', html)
        self.assertIn('<span class="valeur-pour">0,2 %</span>', html)  # pourcentage : 1 décimale fixe
        self.assertIn('<span class="valeur-contre">0,1 %</span>', html)
        self.assertIn('<span class="valeur-pour">0,0 %</span>', html)
        self.assertIn('<span class="valeur-contre">—</span>', html)
        self.assertNotIn("onclick", html)
        self.assertNotIn('<th scope="row">vide</th>', html)  # aucune série valide : ligne écartée

    def test_rendu_xg_deux_decimales(self):
        html = rendre('{% barres l "" 2 %}', l=self.lignes())
        self.assertIn('<span class="valeur-pour">0,20</span>', html)
        self.assertIn('<span class="valeur-contre">0,10</span>', html)
        self.assertIn('<span class="valeur-pour">0,00</span>', html)

    def test_aucune_ligne(self):
        self.assertIn("Aucune donnée", rendre("{% barres l %}", l=None))
