"""Données INVENTÉES au format StatsBomb (Matches v6, Events v8, Lineups v4,
360 Frames v3). Aucune vraie équipe, aucun vrai joueur, aucun vrai identifiant."""

COMPETITION, SAISON = 9001, 9002
ADVERSAIRE = 101
EQUIPES = {
    101: "Les Mouettes Inventées",
    102: "Olympique Imaginaire",
    103: "Racing Fictif",
    104: "Union des Dunes",
    105: "Stade de Nulle Part",
}
JOUEUR_INVENTE = "Joueur Totalement Inventé"


def catalogue(match_available=True):
    return [{"competition_id": COMPETITION, "season_id": SAISON,
             "competition_name": "Ligue Inventée", "season_name": "2026/2027",
             "match_available": "2026-10-20T10:00:00" if match_available else None}]


def match(ident, jour, domicile=ADVERSAIRE, exterieur=102, statut="available",
          statut_360="unscheduled", version="2026-10-01T10:00:00.000",
          version_360=None, kick_off="20:00:00.000", scores=(1, 0)):
    return {
        "match_id": ident,
        "match_date": jour,
        "kick_off": kick_off,
        "competition": {"competition_id": COMPETITION, "competition_name": "Ligue Inventée"},
        "season": {"season_id": SAISON, "season_name": "2026/2027"},
        "home_team": {"home_team_id": domicile, "home_team_name": EQUIPES[domicile],
                      "managers": [{"id": 1, "name": "Entraîneur Inventé"}]},
        "away_team": {"away_team_id": exterieur, "away_team_name": EQUIPES[exterieur]},
        "home_score": scores[0],
        "away_score": scores[1],
        "match_status": statut,
        "match_status_360": statut_360,
        "play_status": "Normal",
        "collection_status": "Complete",
        "last_updated": version,
        "last_updated_360": version_360,
        "match_week": 1,
        "competition_stage": {"id": 1, "name": "Regular Season"},
    }


def evenements(match_id):
    return [
        {"id": f"evt-{match_id}-1", "index": 1, "period": 1, "minute": 0, "second": 0,
         "type": {"id": 35, "name": "Starting XI"},
         "team": {"id": ADVERSAIRE, "name": EQUIPES[ADVERSAIRE]},
         "tactics": {"formation": 442, "lineup": [
             {"player": {"id": 1, "name": JOUEUR_INVENTE}, "jersey_number": 9}]}},
        {"id": f"evt-{match_id}-2", "index": 2, "period": 1, "minute": 3, "second": 12,
         "type": {"id": 30, "name": "Pass"}, "location": [40.0, 30.0],
         "team": {"id": ADVERSAIRE, "name": EQUIPES[ADVERSAIRE]},
         "player": {"id": 1, "name": JOUEUR_INVENTE}},
    ]


def compositions(match_id):
    return [{"team_id": ADVERSAIRE, "team_name": EQUIPES[ADVERSAIRE],
             "lineup": [{"player_id": 1, "player_name": JOUEUR_INVENTE, "jersey_number": 9}]}]


def frames_360(match_id):
    """Trois images, dont les cas limites décrits par la doc 360 v3."""
    return [
        {"event_uuid": f"evt-{match_id}-2",
         "visible_area": [0.0, 0.0, 120.0, 0.0, 120.0, 80.0, 0.0, 0.0],
         "freeze_frame": [
             {"location": [40.0, 30.0], "teammate": True, "actor": True,
              "keeper": False, "player_id": 1},
             {"location": [60.5, 35.2], "teammate": False, "actor": False, "keeper": False},
         ]},
        # Zone visible vide et aucun acteur.
        {"event_uuid": f"evt-{match_id}-3", "visible_area": [],
         "freeze_frame": [{"location": [100.0, 40.0], "teammate": False,
                           "actor": False, "keeper": True}]},
        # Aucun joueur visible, pas de zone du tout.
        {"event_uuid": f"evt-{match_id}-4", "freeze_frame": []},
    ]
