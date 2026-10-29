"""Étape 1 : connexion obligatoire, en-têtes de sécurité, aucun secret versionné."""
import os
import re
import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

WEBAPP = Path(settings.BASE_DIR)


class ConnexionObligatoireTest(TestCase):
    def test_accueil_anonyme_redirige_vers_la_connexion(self):
        reponse = self.client.get("/")
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse["Location"].startswith(reverse("login")))
        self.assertIn("next=/", reponse["Location"])

    def test_page_de_connexion_accessible(self):
        reponse = self.client.get(reverse("login"))
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Connexion")

    def test_accueil_connecte(self):
        compte = get_user_model().objects.create_user("analyste-test", password="x")
        compte.groups.add(Group.objects.get(name="staff"))
        self.client.force_login(compte)
        reponse = self.client.get("/")
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Adversaire du week-end")

    def test_accueil_connecte_sans_role_refuse(self):
        """Étape 3a : refus par défaut d'un compte sans groupe."""
        compte = get_user_model().objects.create_user("sans-role", password="x")
        self.client.force_login(compte)
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_aucune_route_media(self):
        compte = get_user_model().objects.create_user("analyste-test", password="x")
        self.client.force_login(compte)
        self.assertEqual(self.client.get("/media/quelconque.mp4").status_code, 404)


class EnTetesSecuriteTest(TestCase):
    def _csp(self, reponse):
        return reponse["Content-Security-Policy"]

    def test_csp_sur_la_redirection_et_la_connexion(self):
        for url in ("/", reverse("login")):
            with self.subTest(url=url):
                reponse = self.client.get(url)
                csp = self._csp(reponse)
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn("object-src 'none'", csp)
                self.assertEqual(reponse["Referrer-Policy"], "same-origin")
                self.assertIn("camera=()", reponse["Permissions-Policy"])

    def test_csp_stricte_sans_unsafe_inline_ni_tiers(self):
        csp = self._csp(self.client.get(reverse("login")))
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        self.assertNotIn("http", csp)  # aucune origine tierce
        self.assertRegex(csp, r"script-src 'self' 'nonce-[A-Za-z0-9_-]+'")

    def test_nonce_change_a_chaque_requete(self):
        motif = re.compile(r"'nonce-([^']+)'")
        n1 = motif.search(self._csp(self.client.get(reverse("login")))).group(1)
        n2 = motif.search(self._csp(self.client.get(reverse("login")))).group(1)
        self.assertNotEqual(n1, n2)

    def test_gabarits_sans_style_ni_gestionnaire_en_ligne(self):
        """Avec `style-src 'self'`, un attribut style= serait ignoré en silence."""
        gabarits = [p for p in WEBAPP.rglob("*.html") if "staticfiles" not in p.parts]
        self.assertTrue(gabarits)
        for chemin in gabarits:
            texte = chemin.read_text(encoding="utf-8")
            with self.subTest(gabarit=str(chemin.relative_to(WEBAPP))):
                self.assertNotRegex(texte, r"\sstyle\s*=")
                self.assertNotRegex(texte, r"\son[a-z]+\s*=")


class VerrouillageAxesTest(TestCase):
    """django-axes ne doit lire que REMOTE_ADDR tant qu'aucun proxy n'est déclaré."""

    def test_x_forwarded_for_invente_ne_leve_pas_le_verrouillage(self):
        get_user_model().objects.create_user("cible", password="bon-mot-de-passe")
        for i in range(settings.AXES_FAILURE_LIMIT):
            self.client.post(reverse("login"),
                             {"username": "cible", "password": "faux"},
                             HTTP_X_FORWARDED_FOR=f"203.0.113.{i + 1}")
        # Nouvelle IP inventée + bon mot de passe : toujours verrouillé.
        reponse = self.client.post(reverse("login"),
                                   {"username": "cible", "password": "bon-mot-de-passe"},
                                   HTTP_X_FORWARDED_FOR="198.51.100.77")
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertNotEqual(reponse.status_code, 302)

    def test_ip_client_ignore_x_forwarded_for_par_defaut(self):
        from django.test import RequestFactory
        from analyste_site.securite import ip_client
        requete = RequestFactory().get("/", REMOTE_ADDR="10.0.0.5",
                                       HTTP_X_FORWARDED_FOR="203.0.113.9")
        self.assertEqual(ip_client(requete), "10.0.0.5")
        with self.settings(ANALYSTE_NB_PROXYS=1):
            # Derrière un proxy déclaré : l'adresse ajoutée par ce proxy (la
            # plus à droite), pas celle écrite par le client à gauche.
            requete = RequestFactory().get(
                "/", REMOTE_ADDR="10.0.0.5",
                HTTP_X_FORWARDED_FOR="1.2.3.4, 203.0.113.9")
            self.assertEqual(ip_client(requete), "203.0.113.9")


class ReglagesTest(TestCase):
    def test_cle_secrete_obligatoire_hors_debug(self):
        """Sans DJANGO_SECRET_KEY et sans DEBUG, les réglages refusent de charger."""
        env = dict(os.environ, DEBUG="0", DJANGO_SECRET_KEY="",
                   DJANGO_SETTINGS_MODULE="analyste_site.settings")
        resultat = subprocess.run(
            [sys.executable, "-c", "import django; django.setup()"],
            cwd=WEBAPP, env=env, capture_output=True, text=True, timeout=60)
        self.assertNotEqual(resultat.returncode, 0)
        self.assertIn("DJANGO_SECRET_KEY est obligatoire", resultat.stderr)

    def test_env_example_sans_aucune_valeur_secrete(self):
        """Le modèle versionné ne porte que des NOMS de variables sensibles."""
        modele = WEBAPP.parent / ".env.example"
        if not modele.exists():  # image Docker : seul webapp/ est copié
            self.skipTest(".env.example absent")
        sensible = re.compile(r"(PASSWORD|SECRET|TOKEN|EMAIL|CLIENT_ID|KEY)")
        for ligne in modele.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if not ligne or ligne.startswith("#") or "=" not in ligne:
                continue
            cle, _, valeur = ligne.partition("=")
            if sensible.search(cle):
                with self.subTest(cle=cle):
                    self.assertEqual(valeur.strip(), "")
