"""En-têtes de sécurité posés sur chaque réponse (repris de Recrutement).

  - **Content-Security-Policy** avec nonce, plus stricte que celle de
    Recrutement : ni Google Fonts, ni `'unsafe-inline'` dans `style-src`.
    L'appli affiche des noms et des textes venus des fournisseurs (StatsBomb,
    Second Spectrum) : un oubli d'échappement ne doit pas devenir du script.
    ⚠️ Gabarits : aucun attribut `style=` ni gestionnaire `onclick=`, les
    terrains se dessinent en SVG par attributs (`x`, `fill`…), le JS va dans
    `static/` (ou un `<script nonce="{{ request.csp_nonce }}">`).
  - **Referrer-Policy** : l'URL d'une page nomme l'adversaire préparé.
  - **Permissions-Policy** : ni caméra, ni micro, ni géolocalisation.

Aucune source tierce : pas de vidéo dans cette appli (réponse de Julien à Q3).
"""
import secrets


class EnTetesSecurite:
    """Pose la CSP (avec nonce), Referrer-Policy et Permissions-Policy."""

    MODELE = ("default-src 'self'; "
              "base-uri 'self'; "
              "frame-ancestors 'none'; "
              "object-src 'none'; "
              "form-action 'self'; "
              "img-src 'self' data:; "
              "font-src 'self'; "
              "style-src 'self'; "
              "connect-src 'self'; "
              "script-src 'self' 'nonce-{nonce}'")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.csp_nonce = secrets.token_urlsafe(16)
        reponse = self.get_response(request)

        # L'administration de Django porte ses propres scripts et styles en
        # ligne, sans nonce : lui appliquer la CSP la casserait.
        if not request.path.startswith("/admin/"):
            reponse.setdefault("Content-Security-Policy",
                               self.MODELE.format(nonce=request.csp_nonce))

        reponse.setdefault("Referrer-Policy", "same-origin")
        reponse.setdefault("Permissions-Policy",
                           "camera=(), microphone=(), geolocation=(), payment=()")
        return reponse


def ip_client(request):
    """Adresse IP retenue par django-axes (AXES_CLIENT_IP_CALLABLE).

    Par défaut (`ANALYSTE_NB_PROXYS` = 0) : REMOTE_ADDR SEULEMENT. Un en-tête
    X-Forwarded-For est écrit par le client lui-même : le lire sans proxy de
    confiance devant l'appli permettrait de changer d'IP à chaque essai et
    donc d'échapper au verrouillage.

    Derrière un VRAI proxy (variable AXES_PROXY_COUNT=n posée exprès) : on lit
    la n-ième adresse en partant de la DROITE de X-Forwarded-For, celle
    qu'a ajoutée le proxy de confiance le plus éloigné ; ce que le client a
    pu écrire plus à gauche est ignoré.
    """
    from django.conf import settings

    remote = request.META.get("REMOTE_ADDR", "")
    nb_proxys = getattr(settings, "ANALYSTE_NB_PROXYS", 0)
    if nb_proxys <= 0:
        return remote
    adresses = [a.strip() for a in
                request.META.get("HTTP_X_FORWARDED_FOR", "").split(",") if a.strip()]
    if len(adresses) < nb_proxys:
        return remote
    return adresses[-nb_proxys]
