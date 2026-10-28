"""Point d'entrée WSGI de l'appli Analyste (gunicorn)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "analyste_site.settings")

application = get_wsgi_application()
