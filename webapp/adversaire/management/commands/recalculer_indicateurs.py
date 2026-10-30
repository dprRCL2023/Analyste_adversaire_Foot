"""Recalcul des indicateurs depuis le brut stocké (étape 3b), SANS réseau.

    python manage.py recalculer_indicateurs            # lignes périmées ou manquantes
    python manage.py recalculer_indicateurs --tous     # tous les matchs
    python manage.py recalculer_indicateurs --match M  # un match (id StatsBomb)

À lancer après une évolution des formules (`VERSION_CALCUL`). Match par
match : les identifiants d'abord, puis un seul brut en mémoire à la fois.
Aucune table saisie par le staff n'est touchée. Les sorties ne portent que
des identifiants de match et des types d'erreur.
"""
from django.core.management.base import BaseCommand, CommandError

from adversaire import importation
from adversaire.models import Match


def _positif(texte):
    n = int(texte)
    if n <= 0:
        raise ValueError
    return n


class Command(BaseCommand):
    help = "Recalcule les indicateurs des matchs importés depuis le brut stocké (sans réseau)."

    def add_arguments(self, parser):
        quoi = parser.add_mutually_exclusive_group()
        quoi.add_argument("--tous", action="store_true",
                          help="Recalculer tous les matchs, même à jour.")
        quoi.add_argument("--match", type=_positif, help="Identifiant StatsBomb d'un match.")

    def handle(self, *args, **opts):
        if opts["match"]:
            pks = list(Match.objects.filter(sb_match_id=opts["match"])
                       .values_list("pk", flat=True))
            if not pks:
                raise CommandError(f"Match {opts['match']} absent de la base.")
        elif opts["tous"]:
            pks = list(Match.objects.order_by("pk").values_list("pk", flat=True))
        else:
            pks = importation.matchs_a_recalculer()

        recalcules = echecs = sans_brut = 0
        for pk in pks:
            match = Match.objects.select_related("domicile", "exterieur").get(pk=pk)
            try:
                importation.recalculer_match(match)
            except importation.BrutAbsent:
                sans_brut += 1
                self.stderr.write(f"Sans brut : match {match.sb_match_id}")
            except importation.ErreurCalcul as e:
                echecs += 1
                self.stderr.write(f"Échec du calcul : match {match.sb_match_id} ({e})")
            else:
                recalcules += 1
        self.stdout.write(self.style.SUCCESS(
            f"{recalcules} match(s) recalculé(s), {echecs} en échec, {sans_brut} sans brut."))
