"""Client StatsBomb de l'appli Analyste (urllib + gzip, sans `requests`).

RECOPIÉ et adapté de Recrutement (`players/sb_comptes.py` et
`players/pipeline_matchs.StatsBomb`), jamais importé : les pôles ne partagent
pas de code.

Deux comptes (`STATSBOMB_EMAIL`/`_PASSWORD` et `_2`), qui ne couvrent pas les
mêmes championnats. ⚠️ Un compte qui demande une compétition hors de sa
licence reçoit une liste VIDE, sans erreur : le compte se choisit donc d'après
le catalogue `/v4/competitions` de chacun, jamais par essai-erreur.

Sécurité :
- vérification SSL TOUJOURS active (pas d'interrupteur, contrairement à
  Recrutement) ;
- l'en-tête `Authorization` n'apparaît dans aucun message d'erreur : les
  erreurs ne portent que le chemin et le code HTTP ;
- chemins construits uniquement à partir d'entiers, hôte constant ;
- AUCUNE redirection suivie (`OUVREUR`) : `urlopen` suivrait un 30x et
  renverrait l'en-tête `Authorization` vers l'hôte désigné, quel qu'il soit ;
- réponse et décompression bornées (`PLAFOND`).
"""
import base64
import http.client
import json
import time
import urllib.error
import urllib.request
import zlib

from django.conf import settings

API = "https://data.statsbombservices.com/api"
SUFFIXES = ("", "_2")
# Taille maximale d'une réponse, compressée comme décompressée (200 Mo) : les
# événements d'un match pèsent ~8 Mo en clair, une réponse anormale ne doit
# pas saturer la mémoire.
PLAFOND = 200 * 1024 * 1024
DELAI = 180          # secondes par requête
ESSAIS = 3
CODES_REPRISE = (429, 500, 502, 503, 504)


class ErreurStatsBomb(Exception):
    """Erreur d'appel : le message ne contient jamais d'identifiant."""


class IdentifiantsManquants(ErreurStatsBomb):
    pass


def _chemin(url):
    return url[len(API):] if url.startswith(API) else "?"


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    """Remplace le gestionnaire de redirections par défaut : toute réponse 30x
    lève ErreurStatsBomb, que `_lire` ne rattrape pas (aucun nouvel essai). Ni
    l'adresse cible ni l'en-tête ne figurent dans le message."""

    def _refuser(self, req, fp, code, msg, headers):
        fp.close()
        raise ErreurStatsBomb(f"redirection {code} refusée sur {_chemin(req.full_url)}")

    http_error_301 = http_error_302 = http_error_303 = _refuser
    http_error_307 = http_error_308 = _refuser


# Seul point de sortie réseau du client. HTTPSHandler par défaut : certificat
# et nom d'hôte vérifiés.
OUVREUR = urllib.request.build_opener(_SansRedirection())


def comptes():
    """[(email, mot de passe)] dans l'ordre de priorité, lus depuis les
    réglages (donc l'environnement). Une paire incomplète est ignorée."""
    sortie = []
    for s in SUFFIXES:
        email = getattr(settings, f"STATSBOMB_EMAIL{s}", "") or ""
        mdp = getattr(settings, f"STATSBOMB_PASSWORD{s}", "") or ""
        if email and mdp:
            sortie.append((email, mdp))
    return sortie


def entete(compte):
    return "Basic " + base64.b64encode(f"{compte[0]}:{compte[1]}".encode()).decode()


def repartir(catalogues):
    """`catalogues` : le catalogue `/v4/competitions` de chaque compte, dans
    l'ordre de `comptes()`. Rend (catalogue fusionné, {(competition_id,
    season_id): indice du compte}).

    Une saison présente chez plusieurs comptes va au premier qui a des matchs
    disponibles (`match_available`), sinon au premier tout court."""
    fusion, route = {}, {}
    for i, cat in enumerate(catalogues):
        for c in cat or []:
            try:
                cle = (int(c["competition_id"]), int(c["season_id"]))
            except (KeyError, TypeError, ValueError):
                continue
            if cle not in fusion or (c.get("match_available")
                                     and not fusion[cle].get("match_available")):
                fusion[cle] = c
                route[cle] = i
    return list(fusion.values()), route


def _degonfler(brut):
    """Décompression gzip bornée à PLAFOND octets."""
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        sortie = d.decompress(brut, PLAFOND + 1)
    except zlib.error:
        raise ErreurStatsBomb("réponse gzip illisible") from None
    if len(sortie) > PLAFOND or d.unconsumed_tail:
        raise ErreurStatsBomb("réponse trop volumineuse")
    return sortie


def _id(valeur):
    """Entier strictement positif, sinon ValueError : seul ce qui sort d'ici
    entre dans un chemin d'adresse."""
    if isinstance(valeur, bool):
        raise ValueError("identifiant invalide")
    n = int(valeur)
    if n <= 0:
        raise ValueError("identifiant invalide")
    return n


class StatsBomb:
    """Client multi-comptes : chaque appel part avec le compte dont la licence
    couvre la compétition (catalogue), ou, pour un appel par match, avec le
    compte de la compétition dont la liste de matchs a été lue (`matchs()`)."""

    def __init__(self, liste_comptes=None, attendre=time.sleep):
        cpts = comptes() if liste_comptes is None else list(liste_comptes)
        if not cpts:
            raise IdentifiantsManquants(
                "Identifiants StatsBomb manquants : renseigner STATSBOMB_EMAIL et "
                "STATSBOMB_PASSWORD (et éventuellement _2) dans analyste/.env.")
        # Les identifiants ne sont gardés que dans les en-têtes, jamais affichés.
        self._entetes = [{"Authorization": entete(c), "Accept-Encoding": "gzip"}
                         for c in cpts]
        self._attendre = attendre
        self.par_saison = {}    # (competition_id, season_id) -> indice du compte
        self.par_match = {}     # match_id -> indice du compte
        self._catalogue = None

    def __repr__(self):
        return f"<StatsBomb {len(self._entetes)} compte(s)>"

    @property
    def nb_comptes(self):
        return len(self._entetes)

    # ── Bas niveau ──────────────────────────────────────────────────────────
    def _lire(self, chemin, compte):
        """GET JSON avec reprise sur 429/5xx et coupure réseau (ESSAIS essais,
        attente croissante). Aucune reprise sur 401/403/404 ni sur une
        redirection (refusée par `OUVREUR`)."""
        for i in range(ESSAIS):
            dernier = i == ESSAIS - 1
            try:
                req = urllib.request.Request(f"{API}{chemin}", headers=self._entetes[compte])
                with OUVREUR.open(req, timeout=DELAI) as r:
                    brut = r.read(PLAFOND + 1)
                    if len(brut) > PLAFOND:
                        raise ErreurStatsBomb(f"réponse trop volumineuse sur {chemin}")
                    if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                        brut = _degonfler(brut)
                try:
                    return json.loads(brut)
                except ValueError:
                    raise ErreurStatsBomb(f"JSON illisible sur {chemin}") from None
            except urllib.error.HTTPError as e:
                code = e.code
                e.close()
                if code not in CODES_REPRISE or dernier:
                    raise ErreurStatsBomb(f"StatsBomb a répondu {code} sur {chemin}") from None
            # http.client.HTTPException : coupure en pleine lecture du corps
            # (IncompleteRead…), qui n'hérite pas d'OSError.
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError,
                    http.client.HTTPException) as e:
                if dernier:
                    raise ErreurStatsBomb(
                        f"StatsBomb injoignable sur {chemin} ({type(e).__name__})") from None
            self._attendre(5 * (i + 1))

    # ── Catalogue et routage ────────────────────────────────────────────────
    def catalogue(self):
        """Catalogue fusionné de tous les comptes ; renseigne le routage."""
        if self._catalogue is None:
            cats = [self._lire("/v4/competitions", i) for i in range(len(self._entetes))]
            self._catalogue, self.par_saison = repartir(
                [c if isinstance(c, list) else [] for c in cats])
        return self._catalogue

    def compte_saison(self, competition_id, season_id):
        """Indice du compte qui couvre (compétition, saison), None sinon."""
        self.catalogue()
        return self.par_saison.get((_id(competition_id), _id(season_id)))

    # ── Appels ──────────────────────────────────────────────────────────────
    def matchs(self, competition_id, season_id):
        """Tous les matchs (Matches v6) de la compétition-saison, bruts. Les
        appels par match qui suivront partiront avec le même compte."""
        c, s = _id(competition_id), _id(season_id)
        compte = self.compte_saison(c, s)
        if compte is None:
            raise ErreurStatsBomb(f"compétition {c} saison {s} absente du catalogue de vos comptes")
        donnees = self._lire(f"/v6/competitions/{c}/seasons/{s}/matches", compte)
        if not isinstance(donnees, list):
            raise ErreurStatsBomb("liste de matchs inattendue")
        for m in donnees:
            try:
                self.par_match[_id(m["match_id"])] = compte
            except (KeyError, TypeError, ValueError):
                continue
        return donnees

    def _par_match(self, version, ressource, match_id):
        m = _id(match_id)
        return self._lire(f"/{version}/{ressource}/{m}", self.par_match.get(m, 0))

    def evenements(self, match_id):
        return self._par_match("v8", "events", match_id)

    def compositions(self, match_id):
        return self._par_match("v4", "lineups", match_id)

    def frames_360(self, match_id):
        """360 Frames v3 : à n'appeler que si `match_status_360` vaut « available »."""
        return self._par_match("v3", "360-frames", match_id)
