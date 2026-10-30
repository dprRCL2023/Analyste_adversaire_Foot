# Étape 3b : indicateurs calculés à l'import, une ligne par match et par équipe.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("adversaire", "0002_suivi_roles"),
    ]

    operations = [
        migrations.CreateModel(
            name="IndicateursMatchEquipe",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("version_calcul", models.PositiveIntegerField()),
                ("version_source", models.CharField(blank=True, max_length=90)),
                ("avec_360", models.BooleanField(default=False)),
                ("valeurs", models.JSONField(default=dict)),
                ("calcule_le", models.DateTimeField(auto_now=True)),
                ("equipe", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="indicateurs", to="adversaire.equipe")),
                ("match", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="indicateurs", to="adversaire.match")),
            ],
            options={
                "verbose_name": "indicateurs d'un match",
                "verbose_name_plural": "indicateurs des matchs",
                "indexes": [
                    models.Index(fields=["equipe", "match"], name="indicateurs_equipe_match"),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("match", "equipe"),
                                            name="indicateurs_match_equipe_unique"),
                ],
            },
        ),
    ]
