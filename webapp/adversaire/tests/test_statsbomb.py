"""Étape 2 : client StatsBomb, avec un faux `OUVREUR.open` (aucun appel réseau)."""
import base64
import gzip
import http.client
import io
import json
import traceback
import urllib.error
import urllib.request
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from adversaire.clients import statsbomb
from adversaire.clients.statsbomb import (API, ErreurStatsBomb, IdentifiantsManquants,
                                          StatsBomb, entete, repartir)

from . import OPEN_REEL
from . import donnees_inventees as inv

# Seul point de sortie réseau du client, simulé dans chaque test.
OUVRIR = "adversaire.clients.statsbomb.OUVREUR.open"

COMPTE_1 = ("analyste-un@exemple.invalid", "mot-de-passe-invente-1")
COMPTE_2 = ("analyste-deux@exemple.invalid", "mot-de-passe-invente-2")


class FausseReponse:
    def __init__(self, objet=None, compresse=False, corps=None):
        corps = corps if corps is not None else json.dumps(objet).encode()
        self._corps = gzip.compress(corps) if compresse else corps
        self.headers = {"Content-Encoding": "gzip"} if compresse else {}

    def read(self, n=-1):
        return self._corps if n is None or n < 0 else self._corps[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def erreur_http(code):
    return urllib.error.HTTPError(f"{API}/x", code, "erreur", {}, io.BytesIO(b""))


def client(*comptes):
    attentes = []
    return StatsBomb(list(comptes) or [COMPTE_1], attendre=attentes.append), attentes


class FausseRedirection:
    """Réponse 302 telle que la rend HTTPSHandler, vers un hôte étranger."""
    code = status = 302
    msg = reason = "Found"

    def __init__(self):
        self.entetes = http.client.HTTPMessage()
        self.entetes["Location"] = "https://hote-etranger.invalid/vol"
        self.ferme = False

    def info(self):
        return self.entetes

    def read(self, n=-1):
        return b""

    def close(self):
        self.ferme = True


class GardeFouReseauTest(SimpleTestCase):
    def test_appel_reel_interdit_dans_la_suite(self):
        sb, _ = client()
        with self.assertRaises(AssertionError):
            sb.catalogue()

    def test_tout_opener_et_urlopen_interdits(self):
        requete = urllib.request.Request(f"{API}/v4/competitions")
        for ouvrir in (urllib.request.urlopen, statsbomb.OUVREUR.open,
                       urllib.request.build_opener().open):
            with self.subTest(ouvrir=ouvrir), self.assertRaises(AssertionError):
                ouvrir(requete, timeout=1)


class RedirectionTest(SimpleTestCase):
    def test_302_refusee_sans_second_appel(self):
        """La réponse simulée traverse la VRAIE chaîne de gestionnaires de
        l'opener : le 302 n'est pas suivi, aucun nouvel essai, aucune attente."""
        sb, attentes = client()
        reponse = FausseRedirection()
        with mock.patch.object(urllib.request.OpenerDirector, "open", OPEN_REEL), \
                mock.patch.object(urllib.request.HTTPSHandler, "https_open",
                                  return_value=reponse) as faux:
            with self.assertRaisesMessage(ErreurStatsBomb, "redirection 302 refusée") as ctx:
                sb.catalogue()
        self.assertEqual(faux.call_count, 1)
        self.assertEqual(attentes, [])
        self.assertTrue(reponse.ferme)
        self.assertNotIn("hote-etranger", str(ctx.exception))

    def test_aucun_gestionnaire_de_redirection_par_defaut(self):
        redirections = [h for h in statsbomb.OUVREUR.handlers
                        if isinstance(h, urllib.request.HTTPRedirectHandler)]
        self.assertEqual([type(h) for h in redirections], [statsbomb._SansRedirection])


class LectureTest(SimpleTestCase):
    def test_entete_basic(self):
        attendu = "Basic " + base64.b64encode(b"a@b.invalid:c").decode()
        self.assertEqual(entete(("a@b.invalid", "c")), attendu)

    def test_gzip_decompresse_et_bonne_adresse(self):
        sb, _ = client()
        with mock.patch(OUVRIR,
                        return_value=FausseReponse(inv.catalogue(), compresse=True)) as faux:
            self.assertEqual(sb.catalogue(), inv.catalogue())
        requete = faux.call_args.args[0]
        self.assertEqual(requete.full_url, f"{API}/v4/competitions")
        self.assertEqual(requete.get_header("Authorization"), entete(COMPTE_1))
        self.assertEqual(requete.get_header("Accept-encoding"), "gzip")
        # Contexte SSL par défaut (vérification active) : aucun context= passé.
        self.assertNotIn("context", faux.call_args.kwargs)

    def test_json_non_compresse_accepte(self):
        sb, _ = client()
        with mock.patch(OUVRIR, return_value=FausseReponse(inv.catalogue())):
            self.assertEqual(len(sb.catalogue()), 1)

    def test_reprise_sur_503_puis_succes(self):
        sb, attentes = client()
        with mock.patch(OUVRIR,
                        side_effect=[erreur_http(503), FausseReponse(inv.catalogue())]) as faux:
            sb.catalogue()
        self.assertEqual(faux.call_count, 2)
        self.assertEqual(attentes, [5])

    def test_reprise_sur_coupure_reseau(self):
        sb, attentes = client()
        with mock.patch(OUVRIR,
                        side_effect=[urllib.error.URLError("coupure"),
                                     FausseReponse(inv.catalogue())]):
            sb.catalogue()
        self.assertEqual(attentes, [5])

    def test_reprise_sur_lecture_interrompue(self):
        sb, attentes = client()
        with mock.patch(OUVRIR,
                        side_effect=[http.client.IncompleteRead(b"[1,"),
                                     FausseReponse(inv.catalogue())]):
            sb.catalogue()
        self.assertEqual(attentes, [5])

    def test_abandon_apres_trois_essais(self):
        sb, attentes = client()
        with mock.patch(OUVRIR, side_effect=[erreur_http(429)] * 3) as faux:
            with self.assertRaises(ErreurStatsBomb):
                sb.catalogue()
        self.assertEqual(faux.call_count, 3)
        self.assertEqual(attentes, [5, 10])

    def test_aucune_reprise_sur_401(self):
        sb, attentes = client()
        with mock.patch(OUVRIR, side_effect=[erreur_http(401)]) as faux:
            with self.assertRaisesMessage(ErreurStatsBomb, "401"):
                sb.catalogue()
        self.assertEqual(faux.call_count, 1)
        self.assertEqual(attentes, [])

    def test_plafond_de_decompression(self):
        sb, _ = client()
        enorme = FausseReponse(corps=b"[" + b"0," * 5000 + b"0]", compresse=True)
        with mock.patch.object(statsbomb, "PLAFOND", 1000), \
                mock.patch(OUVRIR, return_value=enorme):
            with self.assertRaisesMessage(ErreurStatsBomb, "trop volumineuse"):
                sb.catalogue()

    def test_plafond_de_reponse_brute(self):
        sb, _ = client()
        enorme = FausseReponse(corps=b"[" + b"0," * 5000 + b"0]")
        with mock.patch.object(statsbomb, "PLAFOND", 1000), \
                mock.patch(OUVRIR, return_value=enorme):
            with self.assertRaisesMessage(ErreurStatsBomb, "trop volumineuse"):
                sb.catalogue()

    def test_json_illisible(self):
        sb, _ = client()
        with mock.patch(OUVRIR, return_value=FausseReponse(corps=b"<html>")):
            with self.assertRaises(ErreurStatsBomb):
                sb.catalogue()

    def test_identifiant_non_entier_refuse(self):
        sb, _ = client()
        for valeur in ("1/../../autre", "abc", -3, 0, True):
            with self.subTest(valeur=valeur), self.assertRaises(ValueError):
                sb.evenements(valeur)


class AucunIdentifiantDansLesErreursTest(SimpleTestCase):
    def _secrets(self):
        return (COMPTE_1[0], COMPTE_1[1], entete(COMPTE_1).split()[1])

    def _verifier(self, erreur):
        texte = "".join(traceback.format_exception(erreur)) + repr(erreur) + str(erreur)
        for secret in self._secrets():
            self.assertNotIn(secret, texte)

    def test_erreur_http(self):
        sb, _ = client()
        with mock.patch(OUVRIR, side_effect=[erreur_http(403)]):
            with self.assertRaises(ErreurStatsBomb) as ctx:
                sb.catalogue()
        self._verifier(ctx.exception)

    def test_erreur_reseau(self):
        sb, _ = client()
        with mock.patch(OUVRIR, side_effect=[TimeoutError()] * 3):
            with self.assertRaises(ErreurStatsBomb) as ctx:
                sb.catalogue()
        self._verifier(ctx.exception)

    def test_repr_du_client(self):
        sb, _ = client()
        for secret in self._secrets():
            self.assertNotIn(secret, repr(sb))


class RoutageTest(SimpleTestCase):
    def test_repartir_prefere_le_compte_avec_matchs(self):
        sans = [{"competition_id": 1, "season_id": 2, "match_available": None}]
        avec = [{"competition_id": 1, "season_id": 2, "match_available": "2026-10-01"},
                {"competition_id": 3, "season_id": 4, "match_available": "2026-10-01"}]
        _, route = repartir([sans, avec])
        self.assertEqual(route, {(1, 2): 1, (3, 4): 1})
        _, route = repartir([avec, avec])
        self.assertEqual(route[(1, 2)], 0)

    def test_appels_par_match_avec_le_compte_de_la_competition(self):
        sb, _ = client(COMPTE_1, COMPTE_2)
        par_compte = {entete(COMPTE_1): [], entete(COMPTE_2): inv.catalogue()}
        vus = []

        def faux_urlopen(requete, timeout=None):
            vus.append((requete.full_url, requete.get_header("Authorization")))
            if requete.full_url.endswith("/v4/competitions"):
                return FausseReponse(par_compte[requete.get_header("Authorization")])
            if "/matches" in requete.full_url:
                return FausseReponse([inv.match(501, "2026-10-10")])
            return FausseReponse([])

        with mock.patch(OUVRIR, side_effect=faux_urlopen):
            self.assertEqual(sb.compte_saison(inv.COMPETITION, inv.SAISON), 1)
            sb.matchs(inv.COMPETITION, inv.SAISON)
            sb.evenements(501)
            sb.compositions(501)
            sb.frames_360(501)
        self.assertEqual(vus[-4:], [
            (f"{API}/v6/competitions/{inv.COMPETITION}/seasons/{inv.SAISON}/matches",
             entete(COMPTE_2)),
            (f"{API}/v8/events/501", entete(COMPTE_2)),
            (f"{API}/v4/lineups/501", entete(COMPTE_2)),
            (f"{API}/v3/360-frames/501", entete(COMPTE_2)),
        ])

    def test_competition_hors_licence(self):
        sb, _ = client()
        with mock.patch(OUVRIR, return_value=FausseReponse([])):
            with self.assertRaisesMessage(ErreurStatsBomb, "absente du catalogue"):
                sb.matchs(inv.COMPETITION, inv.SAISON)


class IdentifiantsTest(TestCase):
    def test_paire_incomplete_ignoree(self):
        with self.settings(STATSBOMB_EMAIL=COMPTE_1[0], STATSBOMB_PASSWORD="",
                           STATSBOMB_EMAIL_2=COMPTE_2[0], STATSBOMB_PASSWORD_2=COMPTE_2[1]):
            self.assertEqual(statsbomb.comptes(), [COMPTE_2])

    def test_arret_propre_sans_identifiants(self):
        with self.settings(STATSBOMB_EMAIL="", STATSBOMB_PASSWORD="",
                           STATSBOMB_EMAIL_2="", STATSBOMB_PASSWORD_2=""):
            with self.assertRaises(IdentifiantsManquants):
                StatsBomb()
            with self.assertRaisesMessage(CommandError, "Identifiants StatsBomb manquants"):
                call_command("importer_adversaire", "--catalogue", stdout=io.StringIO())

    def test_ouvreur_est_un_opener_de_urllib(self):
        """Le client passe par un OpenerDirector, dont le garde-fou remplace `open`."""
        self.assertIsInstance(statsbomb.OUVREUR, urllib.request.OpenerDirector)
