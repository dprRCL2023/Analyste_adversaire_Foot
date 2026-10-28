from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("connexion/", auth_views.LoginView.as_view(
        template_name="registration/login.html"), name="login"),
    path("deconnexion/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("adversaire.urls")),
]

# ⚠️ AUCUNE route /media/, ni en dev ni en prod : l'appli ne sert aucun fichier
# téléversé, et les données des fournisseurs ne sortent que par des vues
# protégées par la connexion.
