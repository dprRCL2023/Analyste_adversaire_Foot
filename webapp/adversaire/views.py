from django.shortcuts import render


def accueil(request):
    """Page d'accueil. Connexion exigée par LoginRequiredMiddleware.

    Étape 1 (squelette) : aucune donnée encore. La liste des adversaires
    importés arrive avec les modèles et l'import StatsBomb (étape 2).
    """
    return render(request, "adversaire/accueil.html")
