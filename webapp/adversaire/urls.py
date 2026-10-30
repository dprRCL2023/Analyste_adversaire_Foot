from django.urls import path

from . import views

urlpatterns = [
    path("", views.accueil, name="accueil"),
    path("adversaire/<int:suivi_id>/", views.fiche, name="fiche"),
]
