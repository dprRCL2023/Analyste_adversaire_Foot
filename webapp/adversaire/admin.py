"""Admin en LECTURE SEULE, pour vérifier un import en attendant la fiche de
l'étape 3. Tout vient de StatsBomb et se recalcule par `importer_adversaire` :
rien ne se saisit ni ne se supprime ici. Le brut compressé n'est jamais affiché."""
from django.contrib import admin
from django.db.models import BooleanField, ExpressionWrapper, Q

from .models import (AdversaireSuivi, Competition, DonneesMatch, Equipe, FicheAdversaire,
                     IndicateursMatchEquipe, Match)


class LectureSeule(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Competition)
class CompetitionAdmin(LectureSeule):
    list_display = ("nom", "saison", "sb_competition_id", "sb_season_id", "compte")


@admin.register(Equipe)
class EquipeAdmin(LectureSeule):
    list_display = ("nom", "sb_team_id")
    search_fields = ("nom",)


@admin.register(Match)
class MatchAdmin(LectureSeule):
    list_display = ("date", "domicile", "score_domicile", "score_exterieur", "exterieur",
                    "competition", "statut_360", "importe_le")
    list_filter = ("competition", "statut_360")
    list_select_related = ("domicile", "exterieur", "competition")
    date_hierarchy = "date"


@admin.register(AdversaireSuivi)
class AdversaireSuiviAdmin(LectureSeule):
    list_display = ("equipe", "competition", "importe_le", "cree_le")
    list_select_related = ("equipe", "competition")


@admin.register(IndicateursMatchEquipe)
class IndicateursMatchEquipeAdmin(LectureSeule):
    """Recalculés par `recalculer_indicateurs` : consultation seule. Le JSON
    (`valeurs`) est différé : chargé seulement par la page de détail qui l'affiche."""
    list_display = ("match", "equipe", "version_calcul", "avec_360", "calcule_le")
    list_filter = ("version_calcul", "avec_360")
    list_select_related = ("match__domicile", "match__exterieur", "equipe")
    fields = ("match", "equipe", "version_calcul", "version_source", "avec_360",
              "calcule_le", "valeurs")

    def get_queryset(self, request):
        return super().get_queryset(request).defer("valeurs")


@admin.register(FicheAdversaire)
class FicheAdversaireAdmin(LectureSeule):
    """Reconstruite par l'import et `recalculer_indicateurs` : consultation
    seule ; `valeurs` différé dans la liste."""
    list_display = ("suivi", "version_fiche", "nb_matchs_calcules", "calcule_le")
    list_select_related = ("suivi__equipe", "suivi__competition")
    fields = ("suivi", "version_fiche", "matchs", "nb_matchs_calcules", "calcule_le", "valeurs")

    def get_queryset(self, request):
        return super().get_queryset(request).defer("valeurs")


@admin.register(DonneesMatch)
class DonneesMatchAdmin(LectureSeule):
    list_display = ("match", "version", "version_360", "a_360")
    # Jamais les colonnes *_z dans la liste ni dans le détail (~1 Mo chacune).
    fields = ("match", "version", "version_360")

    def get_queryset(self, request):
        return (super().get_queryset(request)
                .defer("evenements_z", "compositions_z", "frames_360_z")
                .annotate(avec_360=ExpressionWrapper(Q(frames_360_z__isnull=False),
                                                     output_field=BooleanField()))
                .select_related("match__domicile", "match__exterieur"))

    @admin.display(boolean=True, description="360")
    def a_360(self, obj):
        return obj.avec_360
