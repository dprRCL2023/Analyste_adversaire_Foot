"""Étape 3a : rôles « analyste » / « staff », refus par défaut."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser, Group
from django.test import TestCase
from django.urls import reverse

from adversaire import roles


def compte(nom, *groupes, superutilisateur=False):
    Utilisateur = get_user_model()
    if superutilisateur:
        utilisateur = Utilisateur.objects.create_superuser(nom, password="x")
    else:
        utilisateur = Utilisateur.objects.create_user(nom, password="x")
    for groupe in groupes:
        utilisateur.groups.add(Group.objects.get(name=groupe))
    return get_user_model().objects.get(pk=utilisateur.pk)  # sans cache de groupes


class GroupesTest(TestCase):
    def test_groupes_crees_par_la_migration(self):
        self.assertEqual(sorted(Group.objects.filter(
            name__in=[roles.GROUPE_ANALYSTE, roles.GROUPE_STAFF]).values_list("name", flat=True)),
            ["analyste", "staff"])


class FonctionsDeRoleTest(TestCase):
    def test_anonyme(self):
        self.assertFalse(roles.peut_consulter(AnonymousUser()))
        self.assertFalse(roles.peut_analyser(AnonymousUser()))

    def test_sans_groupe(self):
        u = compte("sans")
        self.assertFalse(roles.peut_consulter(u))
        self.assertFalse(roles.peut_analyser(u))

    def test_staff(self):
        u = compte("staff1", "staff")
        self.assertTrue(roles.peut_consulter(u))
        self.assertFalse(roles.peut_analyser(u))

    def test_analyste(self):
        u = compte("analyste1", "analyste")
        self.assertTrue(roles.peut_consulter(u))
        self.assertTrue(roles.peut_analyser(u))

    def test_superutilisateur(self):
        u = compte("chef", superutilisateur=True)
        self.assertTrue(roles.peut_consulter(u))
        self.assertTrue(roles.peut_analyser(u))

    def test_compte_desactive_refuse(self):
        u = compte("parti", "analyste")
        u.is_active = False
        self.assertFalse(roles.peut_consulter(u))
        self.assertFalse(roles.peut_analyser(u))

    def test_autre_groupe_refuse(self):
        Group.objects.create(name="scout")
        u = compte("scout1", "scout")
        self.assertFalse(roles.peut_consulter(u))


class AccesPageTest(TestCase):
    """Le rôle est vérifié DANS la vue, pas seulement par le masquage d'un lien."""

    def test_anonyme_redirige_vers_la_connexion(self):
        reponse = self.client.get(reverse("accueil"))
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(reponse["Location"].startswith(reverse("login")))

    def test_sans_groupe_403(self):
        self.client.force_login(compte("sans"))
        reponse = self.client.get(reverse("accueil"))
        self.assertEqual(reponse.status_code, 403)
        self.assertContains(reponse, "Accès refusé", status_code=403)

    def test_sans_groupe_403_meme_avec_parametre(self):
        self.client.force_login(compte("sans"))
        self.assertEqual(self.client.get(reverse("accueil"), {"suivi": "1"}).status_code, 403)

    def test_roles_autorises(self):
        for u in (compte("staff1", "staff"), compte("analyste1", "analyste"),
                  compte("chef", superutilisateur=True)):
            with self.subTest(compte=u.username):
                self.client.force_login(u)
                self.assertEqual(self.client.get(reverse("accueil")).status_code, 200)
