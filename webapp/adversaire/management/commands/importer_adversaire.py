"""Import StatsBomb de l'adversaire du week-end (ligne de commande seulement).

    # 1) Trouver les identifiants
    python manage.py importer_adversaire --catalogue
    python manage.py importer_adversaire --competition C --saison S --equipes
    # 2) Importer les 5 derniers matchs joués de l'adversaire
    python manage.py importer_adversaire --competition C --saison S --equipe E \\
        [--derniers 5] [--sans-360] [--forcer]

Aucune vue web ne déclenche d'appel au fournisseur. Les sorties n'affichent ni
identifiant de connexion ni nom de joueur.
"""
from django.core.management.base import BaseCommand, CommandError

from adversaire import importation
from adversaire.clients.statsbomb import ErreurStatsBomb, StatsBomb


def _positif(texte):
    n = int(texte)
    if n <= 0:
        raise ValueError
    return n


class Command(BaseCommand):
    help = "Importe depuis StatsBomb les derniers matchs joués d'une équipe adverse."

    def add_arguments(self, parser):
        quoi = parser.add_mutually_exclusive_group()
        quoi.add_argument("--catalogue", action="store_true",
                          help="Lister les compétitions-saisons couvertes, et par quel compte.")
        quoi.add_argument("--equipes", action="store_true",
                          help="Lister les équipes de --competition / --saison.")
        quoi.add_argument("--equipe", type=_positif, help="Identifiant StatsBomb de l'adversaire.")
        parser.add_argument("--competition", type=_positif)
        parser.add_argument("--saison", type=_positif)
        parser.add_argument("--derniers", type=_positif, default=importation.NB_DERNIERS,
                            help="Nombre de matchs joués à importer (5 par défaut).")
        parser.add_argument("--sans-360", action="store_true",
                            help="Ne jamais demander le 360, même disponible.")
        parser.add_argument("--forcer", action="store_true",
                            help="Tout relire, même déjà à jour.")

    def handle(self, *args, **opts):
        if not (opts["catalogue"] or opts["equipes"] or opts["equipe"]):
            raise CommandError("Préciser --catalogue, --equipes ou --equipe <id>.")
        if not opts["catalogue"] and not (opts["competition"] and opts["saison"]):
            raise CommandError("--competition et --saison sont obligatoires ici.")
        try:
            client = StatsBomb()
            if opts["catalogue"]:
                self._catalogue(client)
            elif opts["equipes"]:
                self._equipes(client, opts["competition"], opts["saison"])
            else:
                self._importer(client, opts)
        except ErreurStatsBomb as e:
            # Messages sans identifiant (cf. clients/statsbomb.py).
            raise CommandError(str(e)) from None

    def _catalogue(self, client):
        lignes = []
        for c in client.catalogue():
            if not c.get("match_available"):
                continue
            cle = (c.get("competition_id"), c.get("season_id"))
            lignes.append((str(c.get("competition_name") or ""), str(c.get("season_name") or ""),
                           cle, client.par_saison.get(cle, 0)))
        if not lignes:
            self.stdout.write("Aucune compétition disponible pour vos comptes.")
            return
        for nom, saison, (cid, sid), compte in sorted(lignes):
            self.stdout.write(f"--competition {cid} --saison {sid}  {nom} {saison}"
                              f"  (compte {compte + 1})")

    def _equipes(self, client, competition, saison):
        equipes = importation.equipes_de(client.matchs(competition, saison))
        if not equipes:
            self.stdout.write("Aucune équipe trouvée.")
            return
        for ident, nom in sorted(equipes.items(), key=lambda e: (e[1], e[0])):
            self.stdout.write(f"--equipe {ident}  {nom}")

    def _importer(self, client, opts):
        bilan = importation.importer_adversaire(
            client, opts["competition"], opts["saison"], opts["equipe"],
            n=opts["derniers"], forcer=opts["forcer"], sans_360=opts["sans_360"],
            journal=self.stdout.write)
        for erreur in bilan.erreurs:
            self.stderr.write(f"Ignoré : {erreur}")
        self.stdout.write(self.style.SUCCESS(
            f"{bilan.retenus} match(s) retenu(s) : {bilan.telecharges} téléchargé(s), "
            f"{bilan.a_jour} déjà à jour ; {bilan.avec_360} avec 360, "
            f"{bilan.sans_360} sans 360 ; {len(bilan.erreurs)} ignoré(s)."))
        if not bilan.suivi:
            self.stderr.write("Équipe absente des matchs de cette saison : "
                              "elle n'apparaîtra pas sur la page « Adversaire du week-end ».")
