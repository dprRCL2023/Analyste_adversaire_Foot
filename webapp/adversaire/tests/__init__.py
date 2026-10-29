"""Suite de tests de l'appli Analyste.

Garde-fou commun : AUCUN appel réseau réel. `urllib.request.urlopen` ET
`OpenerDirector.open` (donc tout opener, dont `statsbomb.OUVREUR`) sont
remplacés, pour toute la suite, par une fonction qui fait échouer le test ; un
test qui simule le fournisseur remplace lui-même `OUVREUR.open`
(`mock.patch`), et le garde-fou revient à la fin du test.
"""
import urllib.request

# Gardé pour le seul test qui fait passer une réponse simulée par la vraie
# chaîne de gestionnaires (refus des redirections), sans connexion réelle.
OPEN_REEL = urllib.request.OpenerDirector.open


def _reseau_interdit(*args, **kwargs):
    raise AssertionError("Appel réseau réel interdit dans les tests : simuler OUVREUR.open.")


urllib.request.urlopen = _reseau_interdit
urllib.request.OpenerDirector.open = _reseau_interdit
