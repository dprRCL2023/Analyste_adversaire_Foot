# Étape 3c-1 : synthèse des derniers matchs d'un adversaire, calculée à l'import.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("adversaire", "0003_indicateurs"),
    ]

    operations = [
        migrations.CreateModel(
            name="FicheAdversaire",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("version_fiche", models.PositiveIntegerField()),
                ("matchs", models.JSONField(default=list)),
                ("nb_matchs_calcules", models.PositiveSmallIntegerField(default=0)),
                ("valeurs", models.JSONField(default=dict)),
                ("calcule_le", models.DateTimeField(auto_now=True)),
                ("suivi", models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="fiche", to="adversaire.adversairesuivi")),
            ],
            options={
                "verbose_name": "fiche adversaire",
                "verbose_name_plural": "fiches adversaires",
            },
        ),
    ]
