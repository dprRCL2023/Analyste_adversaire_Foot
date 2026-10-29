"""Rôles de l'appli Analyste : REFUS PAR DÉFAUT.

- `analyste` : importe, écrira les textes (synthèse, fiches) ;
- `staff` : consulte et exporte.

Un compte connecté sans aucun de ces groupes (ni superutilisateur) reçoit 403
sur toutes les vues métier : c'est la base du « un scout ne voit rien » de
l'Après-match. Les groupes sont créés par la migration 0002 ; on y range les
comptes depuis l'admin Django. Le contrôle se fait DANS la vue (décorateur),
jamais seulement en masquant un lien.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied

GROUPE_ANALYSTE = "analyste"
GROUPE_STAFF = "staff"


def _groupes(user):
    """Noms des groupes du compte, lus une fois par requête (cache sur l'objet)."""
    if not hasattr(user, "_groupes_analyste"):
        user._groupes_analyste = set(user.groups.values_list("name", flat=True))
    return user._groupes_analyste


def peut_consulter(user):
    if not (user.is_authenticated and user.is_active):
        return False
    return user.is_superuser or bool(_groupes(user) & {GROUPE_ANALYSTE, GROUPE_STAFF})


def peut_analyser(user):
    if not (user.is_authenticated and user.is_active):
        return False
    return user.is_superuser or GROUPE_ANALYSTE in _groupes(user)


def role_requis(test):
    """Décorateur de vue : 403 si `test(request.user)` est faux. La connexion
    reste exigée en amont par LoginRequiredMiddleware (anonyme → connexion)."""
    def decorateur(vue):
        @wraps(vue)
        def enveloppe(request, *args, **kwargs):
            if not test(request.user):
                raise PermissionDenied
            return vue(request, *args, **kwargs)
        return enveloppe
    return decorateur
