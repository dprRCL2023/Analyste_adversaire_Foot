"""
Réglages Django de l'appli Analyste (analyse de l'adversaire du week-end).

Repris des réglages de sécurité de Recrutement (`recrutement/webapp/scouting/
settings.py`), RECOPIÉS et non importés : les deux pôles se déploient
séparément et ne partagent ni code ni base.
"""

import os
from pathlib import Path

from django.core.management.utils import get_random_secret_key

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path):
    """Charge un fichier .env local (sans dépendance externe)."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


# webapp/.env d'abord, puis analyste/.env (celui de docker compose) en
# complément : `setdefault` laisse la priorité au premier, et à l'environnement
# réel avant les deux. Jamais commités (cf. .gitignore racine).
_load_dotenv(BASE_DIR / ".env")
_load_dotenv(BASE_DIR.parent / ".env")


# Désactivé par défaut : DEBUG expose stack traces, SQL et réglages.
DEBUG = os.getenv("DEBUG", "0") == "1"

# ⚠️ Obligatoire hors DEBUG : sous gunicorn, une clé générée à la volée serait
# DIFFÉRENTE dans chaque worker (sessions refusées une fois sur N, cf. le même
# piège côté Recrutement). Mieux vaut refuser de démarrer.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        from django.core.exceptions import ImproperlyConfigured
        raise ImproperlyConfigured(
            "DJANGO_SECRET_KEY est obligatoire hors DEBUG. La générer avec :\n"
            '  python -c "from django.core.management.utils import '
            'get_random_secret_key as g; print(g())"\n'
            "puis la poser dans .env (cf. .env.example).")
    SECRET_KEY = get_random_secret_key()

ALLOWED_HOSTS = [h.strip() for h in
                 os.getenv("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]

# Données StatsBomb et Second Spectrum sous licence : connexion obligatoire sur
# TOUTE l'appli, sans interrupteur pour la couper (pas de REQUIRE_LOGIN ici).


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",      # limitation des tentatives de connexion (anti-brute-force)
    "adversaire",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # CSP (avec nonce), Referrer-Policy, Permissions-Policy. Tout en haut, pour
    # couvrir aussi la redirection de connexion et les pages d'erreur.
    "analyste_site.securite.EnTetesSecurite",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Toute vue exige une session, sauf celles marquées @login_not_required
    # (la page de connexion de Django l'est déjà).
    "django.contrib.auth.middleware.LoginRequiredMiddleware",
    # AxesMiddleware doit rester le DERNIER.
    "axes.middleware.AxesMiddleware",
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "/"
LOGOUT_REDIRECT_URL = "login"

ROOT_URLCONF = "analyste_site.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "analyste_site.wsgi.application"


# Base de données : la SIENNE, jamais celle de Recrutement (`scouting`) ni de
# Performance. MySQL 8 comme Recrutement ; SQLite seulement pour un dépannage.

if os.getenv("DB_ENGINE", "sqlite").lower() == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": os.getenv("DB_NAME", "analyste"),
            "USER": os.getenv("DB_USER", "analyste"),
            "PASSWORD": os.getenv("DB_PASSWORD", ""),
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "3306"),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
            "TEST": {
                "CHARSET": "utf8mb4",
                "COLLATION": "utf8mb4_unicode_ci",
            },
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": os.getenv("DB_SQLITE_PATH", str(BASE_DIR / "db.sqlite3")),
        }
    }


# ── Anti-brute-force (django-axes), comme Recrutement ─────────────────────────
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
AXES_ENABLED = os.getenv("AXES_ENABLED", "1") == "1"
AXES_FAILURE_LIMIT = int(os.getenv("AXES_FAILURE_LIMIT", "5"))
AXES_COOLOFF_TIME = 1  # heures
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]
AXES_RESET_ON_SUCCESS = True
# IP du client : REMOTE_ADDR SEULEMENT par défaut. X-Forwarded-For n'est lu
# que si AXES_PROXY_COUNT (nombre de proxys de confiance devant l'appli) est
# posé EXPRÈS à 1 ou plus, derrière un vrai proxy (cf. securite.ip_client).
# Sous docker compose en local, aucun proxy : laisser à 0.
ANALYSTE_NB_PROXYS = max(0, int(os.getenv("AXES_PROXY_COUNT", "0").strip() or "0"))
AXES_CLIENT_IP_CALLABLE = "analyste_site.securite.ip_client"
# Ceinture et bretelles, si une version d'axes passait par ipware malgré tout.
AXES_IPWARE_META_PRECEDENCE_ORDER = ("REMOTE_ADDR",)


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True


STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
# Manifeste WhiteNoise hors DEBUG seulement : le lanceur de tests force
# DEBUG=False à l'exécution, mais ce choix est fait au CHARGEMENT des réglages
# (DEBUG=1 pour les tests) — la suite tourne donc sans `collectstatic`.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": ("django.contrib.staticfiles.storage.StaticFilesStorage" if DEBUG
                    else "whitenoise.storage.CompressedManifestStaticFilesStorage"),
    },
}

# ⚠️ Aucune route /media/ : l'appli ne sert aucun fichier téléversé.

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CSRF_TRUSTED_ORIGINS = [o.strip() for o in
                        os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]


# ── Fournisseurs de données ───────────────────────────────────────────────────
# Lus UNIQUEMENT depuis l'environnement (.env jamais commité), utilisés côté
# serveur seulement : aucun de ces identifiants n'est envoyé au navigateur ni
# écrit dans un journal. Branchés aux étapes 2 (StatsBomb) et 4 (Second
# Spectrum) ; vides, l'appli démarre quand même.
STATSBOMB_EMAIL = os.getenv("STATSBOMB_EMAIL", "")
STATSBOMB_PASSWORD = os.getenv("STATSBOMB_PASSWORD", "")
STATSBOMB_EMAIL_2 = os.getenv("STATSBOMB_EMAIL_2", "")
STATSBOMB_PASSWORD_2 = os.getenv("STATSBOMB_PASSWORD_2", "")
SECOND_SPECTRUM_CLIENT_ID = os.getenv("SECOND_SPECTRUM_CLIENT_ID", "")
SECOND_SPECTRUM_CLIENT_SECRET = os.getenv("SECOND_SPECTRUM_CLIENT_SECRET", "")
SS_TOKEN_FILE = os.getenv("SS_TOKEN_FILE", "")


# ── Durcissement (hors DEBUG) ─────────────────────────────────────────────────
if not DEBUG:
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"
    # USE_HTTPS=0 seulement pour un essai local en http (docker compose sur le
    # poste de Julien) : mots de passe en clair, jamais une exposition durable.
    if os.getenv("USE_HTTPS", "1") == "1":
        SECURE_SSL_REDIRECT = True
        SESSION_COOKIE_SECURE = True
        CSRF_COOKIE_SECURE = True
        SECURE_HSTS_SECONDS = 31536000
        SECURE_HSTS_INCLUDE_SUBDOMAINS = True
        SECURE_HSTS_PRELOAD = True
        SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
