# Étape 2 : compétitions, équipes, matchs et brut StatsBomb compressé.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Competition",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("sb_competition_id", models.PositiveIntegerField()),
                ("sb_season_id", models.PositiveIntegerField()),
                ("nom", models.CharField(max_length=120)),
                ("saison", models.CharField(max_length=40)),
                ("compte", models.PositiveSmallIntegerField(default=0)),
            ],
            options={
                "ordering": ["nom", "saison"],
                "constraints": [
                    models.UniqueConstraint(fields=("sb_competition_id", "sb_season_id"),
                                            name="competition_saison_unique"),
                ],
            },
        ),
        migrations.CreateModel(
            name="Equipe",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("sb_team_id", models.PositiveIntegerField(unique=True)),
                ("nom", models.CharField(max_length=120)),
            ],
            options={
                "ordering": ["nom"],
            },
        ),
        migrations.CreateModel(
            name="Match",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("sb_match_id", models.PositiveIntegerField(unique=True)),
                ("date", models.DateField()),
                ("coup_envoi", models.TimeField(blank=True, null=True)),
                ("score_domicile", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("score_exterieur", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("journee", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("statut", models.CharField(blank=True, max_length=30)),
                ("statut_360", models.CharField(blank=True, max_length=30)),
                ("importe_le", models.DateTimeField(auto_now=True)),
                ("competition", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="matchs", to="adversaire.competition")),
                ("domicile", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="matchs_domicile", to="adversaire.equipe")),
                ("exterieur", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="matchs_exterieur", to="adversaire.equipe")),
            ],
            options={
                "ordering": ["-date", "-sb_match_id"],
                "indexes": [
                    models.Index(fields=["domicile", "date"], name="match_domicile_date"),
                    models.Index(fields=["exterieur", "date"], name="match_exterieur_date"),
                ],
            },
        ),
        migrations.CreateModel(
            name="DonneesMatch",
            fields=[
                ("match", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE, primary_key=True,
                    related_name="donnees", serialize=False, to="adversaire.match")),
                ("evenements_z", models.BinaryField()),
                ("compositions_z", models.BinaryField()),
                ("frames_360_z", models.BinaryField(blank=True, null=True)),
                ("version", models.CharField(blank=True, max_length=40)),
                ("version_360", models.CharField(blank=True, max_length=40)),
            ],
            options={
                "verbose_name": "données de match",
                "verbose_name_plural": "données de match",
            },
        ),
    ]
