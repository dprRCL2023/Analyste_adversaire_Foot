# Étape 3a : adversaires suivis, et groupes « analyste » / « staff » (rôles).

import django.db.models.deletion
from django.db import migrations, models

GROUPES = ("analyste", "staff")


def creer_groupes(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    for nom in GROUPES:
        Group.objects.get_or_create(name=nom)


class Migration(migrations.Migration):

    dependencies = [
        ("adversaire", "0001_initial"),
        ("auth", "0012_alter_user_first_name_max_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdversaireSuivi",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True,
                                           serialize=False, verbose_name="ID")),
                ("cree_le", models.DateTimeField(auto_now_add=True)),
                ("importe_le", models.DateTimeField()),
                ("competition", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="suivis", to="adversaire.competition")),
                ("equipe", models.ForeignKey(
                    on_delete=django.db.models.deletion.PROTECT,
                    related_name="suivis", to="adversaire.equipe")),
            ],
            options={
                "verbose_name": "adversaire suivi",
                "verbose_name_plural": "adversaires suivis",
                "ordering": ["-importe_le", "-id"],
                "constraints": [
                    models.UniqueConstraint(fields=("equipe", "competition"),
                                            name="suivi_equipe_competition_unique"),
                ],
            },
        ),
        # Retour arrière sans effet : les groupes (et leurs membres) restent.
        migrations.RunPython(creer_groupes, migrations.RunPython.noop),
    ]
