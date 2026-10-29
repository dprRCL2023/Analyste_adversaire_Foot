"""Modèles de l'étape 2 : ce qui vient de StatsBomb, rien de saisi à la main.

Toutes ces tables sont RECALCULABLES par `importer_adversaire` : aucune n'est
saisie par la cellule. Le brut (événements, compositions, 360) est gardé
compressé en zlib dans `DonneesMatch`, table à part pour que les listes de
matchs restent légères (même méthode que `AnalyseMatch.donnees_z` côté
Recrutement, recopiée et non importée).
"""
import json
import zlib

from django.db import models


def compresser(objet):
    """Objet JSON → octets zlib (niveau 6 : bon compromis taille / temps)."""
    return zlib.compress(json.dumps(objet, separators=(",", ":")).encode("utf-8"), 6)


def decompresser(octets):
    """Octets zlib → objet JSON ; None si rien n'est stocké."""
    if not octets:
        return None
    return json.loads(zlib.decompress(bytes(octets)).decode("utf-8"))


class Competition(models.Model):
    """Une compétition-saison StatsBomb, avec le compte dont la licence la couvre."""
    sb_competition_id = models.PositiveIntegerField()
    sb_season_id = models.PositiveIntegerField()
    nom = models.CharField(max_length=120)
    saison = models.CharField(max_length=40)
    # Indice du compte StatsBomb (0 = STATSBOMB_EMAIL, 1 = STATSBOMB_EMAIL_2).
    # Jamais l'adresse elle-même : aucun identifiant en base.
    compte = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["sb_competition_id", "sb_season_id"],
                                    name="competition_saison_unique"),
        ]
        ordering = ["nom", "saison"]

    def __str__(self):
        return f"{self.nom} {self.saison}"


class Equipe(models.Model):
    sb_team_id = models.PositiveIntegerField(unique=True)
    nom = models.CharField(max_length=120)

    class Meta:
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Match(models.Model):
    sb_match_id = models.PositiveIntegerField(unique=True)
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE,
                                    related_name="matchs")
    date = models.DateField()
    coup_envoi = models.TimeField(null=True, blank=True)
    domicile = models.ForeignKey(Equipe, on_delete=models.PROTECT,
                                 related_name="matchs_domicile")
    exterieur = models.ForeignKey(Equipe, on_delete=models.PROTECT,
                                  related_name="matchs_exterieur")
    score_domicile = models.PositiveSmallIntegerField(null=True, blank=True)
    score_exterieur = models.PositiveSmallIntegerField(null=True, blank=True)
    journee = models.PositiveSmallIntegerField(null=True, blank=True)
    # `match_status` et `match_status_360` de Matches v6 (« available »…).
    statut = models.CharField(max_length=30, blank=True)
    statut_360 = models.CharField(max_length=30, blank=True)
    importe_le = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["domicile", "date"], name="match_domicile_date"),
            models.Index(fields=["exterieur", "date"], name="match_exterieur_date"),
        ]
        ordering = ["-date", "-sb_match_id"]

    def __str__(self):
        return f"{self.date} {self.domicile} – {self.exterieur}"


class DonneesMatch(models.Model):
    """Brut StatsBomb d'un match, compressé. ⚠️ Lourd (~1 Mo) : ne jamais le
    charger dans une liste (`defer`/`only` sur les champs `*_z`)."""
    match = models.OneToOneField(Match, on_delete=models.CASCADE,
                                 primary_key=True, related_name="donnees")
    evenements_z = models.BinaryField()
    compositions_z = models.BinaryField()
    frames_360_z = models.BinaryField(null=True, blank=True)
    # `last_updated` / `last_updated_360` de Matches v6, tels quels : un
    # changement signifie que StatsBomb a révisé ses données.
    version = models.CharField(max_length=40, blank=True)
    version_360 = models.CharField(max_length=40, blank=True)

    class Meta:
        verbose_name = "données de match"
        verbose_name_plural = "données de match"

    @property
    def evenements(self):
        return decompresser(self.evenements_z) or []

    @evenements.setter
    def evenements(self, valeur):
        self.evenements_z = compresser(valeur)

    @property
    def compositions(self):
        return decompresser(self.compositions_z) or []

    @compositions.setter
    def compositions(self, valeur):
        self.compositions_z = compresser(valeur)

    @property
    def frames_360(self):
        """None si le 360 n'est pas disponible pour ce match (cas normal)."""
        return decompresser(self.frames_360_z)

    @frames_360.setter
    def frames_360(self, valeur):
        self.frames_360_z = None if valeur is None else compresser(valeur)

    def __str__(self):
        return f"Données {self.match_id}"


class AdversaireSuivi(models.Model):
    """Équipe CIBLÉE par `importer_adversaire` dans une compétition-saison.

    Sans elle, rien ne distingue l'adversaire de ses propres adversaires (tous
    dans `Equipe`). Recalculable : la commande la crée ou la met à jour, rien
    ne s'y saisit à la main."""
    equipe = models.ForeignKey(Equipe, on_delete=models.PROTECT, related_name="suivis")
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE,
                                    related_name="suivis")
    cree_le = models.DateTimeField(auto_now_add=True)
    # Date du dernier passage de la commande (même « déjà à jour »).
    importe_le = models.DateTimeField()

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["equipe", "competition"],
                                    name="suivi_equipe_competition_unique"),
        ]
        ordering = ["-importe_le", "-id"]
        verbose_name = "adversaire suivi"
        verbose_name_plural = "adversaires suivis"

    def __str__(self):
        return f"{self.equipe} ({self.competition})"
