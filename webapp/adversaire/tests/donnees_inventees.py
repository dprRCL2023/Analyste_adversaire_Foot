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


# ── Match « riche » pour les indicateurs (étape 3b) ──────────────────────────
# ADVERSAIRE (101) reçoit 102. Chaque valeur attendue est recalculable à la
# main (cf. tests/test_indicateurs.py). Joueurs inventés.
JOUEURS = {
    1: "Gardien Inventé",
    9: JOUEUR_INVENTE,
    10: "Milieu Inventé",
    11: "Remplaçant Inventé",
    21: "Gardien Adverse Inventé",
    29: "Attaquant Adverse Inventé",
}


def _j(ident):
    return {"id": ident, "name": JOUEURS[ident]}


def _ev(ident, nature, equipe, minute, second=0, period=1, **champs):
    e = {"id": ident, "period": period, "minute": minute, "second": second,
         "type": {"name": nature}, "team": {"id": equipe, "name": EQUIPES[equipe]}}
    e.update(champs)
    return e


def _passe(**champs):
    return {"pass": champs}


def evenements_riches():
    a, b = ADVERSAIRE, 102
    return [
        _ev("r-01", "Starting XI", a, 0, tactics={"formation": 433, "lineup": [
            {"player": _j(1), "position": {"name": "Goalkeeper"}, "jersey_number": 1},
            {"player": _j(10), "position": {"name": "Center Midfield"}, "jersey_number": 10},
            {"player": _j(9), "position": {"name": "Center Forward"}, "jersey_number": 9}]}),
        _ev("r-02", "Starting XI", b, 0, tactics={"formation": 4231, "lineup": [
            {"player": _j(21), "position": {"name": "Goalkeeper"}, "jersey_number": 1},
            {"player": _j(29), "position": {"name": "Center Forward"}, "jersey_number": 9}]}),
        # Passe progressive (90 m du but → 60 m), conduite progressive vers le dernier tiers.
        _ev("r-03", "Pass", a, 3, player=_j(10), location=[30.0, 40.0],
            **_passe(end_location=[60.0, 40.0])),
        _ev("r-04", "Carry", a, 4, player=_j(9), location=[60.0, 40.0],
            obv_for_net=0.05, obv_against_net=0.0, carry={"end_location": [85.0, 40.0]}),
        _ev("r-05", "Shot", a, 10, player=_j(9), location=[108.0, 40.0], obv_for_net=0.3,
            play_pattern={"name": "Regular Play"},
            shot={"statsbomb_xg": 0.4, "shot_execution_xg": 0.6, "outcome": {"name": "Goal"},
                  "type": {"name": "Open Play"}}),
        # Passes adverses : deux dans leurs 60 % (x ≤ 72), une au-delà.
        _ev("r-06", "Pass", b, 12, player=_j(29), location=[20.0, 30.0],
            **_passe(end_location=[40.0, 30.0])),
        _ev("r-07", "Pass", b, 13, player=_j(29), location=[50.0, 50.0],
            **_passe(end_location=[55.0, 60.0])),
        _ev("r-08", "Pass", b, 14, player=_j(29), location=[70.0, 40.0],
            **_passe(end_location=[90.0, 40.0])),
        _ev("r-09", "Pressure", a, 12, player=_j(9), location=[100.0, 50.0], counterpress=True),
        _ev("r-10", "Duel", a, 13, player=_j(10), location=[70.0, 50.0],
            duel={"type": {"name": "Tackle"}, "outcome": {"name": "Won"}}),
        _ev("r-11", "Interception", a, 14, player=_j(10), location=[60.0, 30.0]),
        _ev("r-12", "Foul Committed", a, 15, player=_j(10), location=[30.0, 30.0]),
        _ev("r-13", "Ball Recovery", a, 16, player=_j(10), location=[90.0, 20.0]),
        _ev("r-14", "Ball Recovery", a, 17, player=_j(1), location=[30.0, 20.0]),
        _ev("r-15", "Pass", a, 20, player=_j(10), location=[100.0, 5.0],
            **_passe(end_location=[112.0, 38.0], cross=True, outcome={"name": "Incomplete"})),
        _ev("r-16", "Shot", b, 30, player=_j(29), location=[100.0, 40.0], obv_for_net=0.02,
            shot={"statsbomb_xg": 0.1, "shot_execution_xg": 0.05, "outcome": {"name": "Saved"}}),
        _ev("r-17", "Pass", a, 40, player=_j(10), location=[120.0, 0.0],
            **_passe(end_location=[110.0, 40.0], type={"name": "Corner"}, cross=True)),
        _ev("r-18", "Shot", a, 40, 30, player=_j(9), location=[110.0, 40.0],
            play_pattern={"name": "From Corner"},
            shot={"statsbomb_xg": 0.2, "outcome": {"name": "Off T"}}),
        _ev("r-19", "Shot", b, 50, period=2, player=_j(29), location=[108.0, 40.0],
            shot={"statsbomb_xg": 0.76, "shot_execution_xg": 0.9, "type": {"name": "Penalty"},
                  "outcome": {"name": "Goal"}}),
        _ev("r-20", "Own Goal For", a, 60, period=2),
        _ev("r-21", "Own Goal Against", b, 60, period=2),
        _ev("r-22", "Substitution", a, 70, period=2, player=_j(9),
            substitution={"replacement": _j(11)}),
        _ev("r-23", "Tactical Shift", a, 70, period=2, tactics={"formation": 442, "lineup": []}),
        _ev("r-24", "Bad Behaviour", b, 80, period=2, player=_j(29),
            bad_behaviour={"card": {"name": "Red Card"}}),
        _ev("r-25", "Shot", a, 85, period=2, player=_j(10), location=[95.0, 40.0],
            play_pattern={"name": "From Free Kick"},
            shot={"statsbomb_xg": 0.05, "type": {"name": "Free Kick"},
                  "outcome": {"name": "Saved"}}),
        _ev("r-26", "Half End", a, 94, period=2),
        # Séance de tirs au but : exclue de tout.
        _ev("r-27", "Shot", a, 120, period=5, player=_j(9), location=[108.0, 40.0],
            shot={"statsbomb_xg": 0.76, "type": {"name": "Penalty"}, "outcome": {"name": "Goal"}}),
    ]


def tirs_au_poteau(issue="Saved to Post"):
    """Deux tirs arrêtés puis renvoyés par le poteau (libellé StatsBomb exact :
    « Saved to Post », t minuscule), à ajouter à `evenements_riches()` : un
    adverse (102) avec PSxG, un de l'ADVERSAIRE (101) sans PSxG."""
    a, b = ADVERSAIRE, 102
    return [
        _ev("p-01", "Shot", b, 55, period=2, player=_j(29), location=[104.0, 40.0],
            shot={"statsbomb_xg": 0.15, "shot_execution_xg": 0.3, "outcome": {"name": issue}}),
        _ev("p-02", "Shot", a, 65, period=2, player=_j(10), location=[100.0, 40.0],
            shot={"statsbomb_xg": 0.1, "outcome": {"name": issue}}),
    ]


def compositions_riches():
    return [
        {"team_id": ADVERSAIRE, "team_name": EQUIPES[ADVERSAIRE], "lineup": [
            {"player_id": i, "player_name": JOUEURS[i], "jersey_number": n}
            for i, n in ((1, 1), (9, 9), (10, 10), (11, 14))]},
        {"team_id": 102, "team_name": EQUIPES[102], "lineup": [
            {"player_id": i, "player_name": JOUEURS[i], "jersey_number": n}
            for i, n in ((21, 1), (29, 9))]},
    ]


def frames_360_riches():
    """Images 360 v3 du match riche, cas limites compris."""
    return [
        # Indicateurs déjà calculés par StatsBomb.
        {"event_uuid": "r-03", "visible_area": [0.0, 0.0, 120.0, 0.0, 120.0, 80.0, 0.0, 80.0],
         "line_breaking_pass": True, "num_defenders_on_goal_side_of_actor": 4,
         "freeze_frame": []},
        # Sans indicateur : défenseurs côté but comptés sur freeze_frame (1 seul).
        {"event_uuid": "r-04", "visible_area": [0.0, 0.0, 120.0, 0.0, 120.0, 80.0, 0.0, 80.0],
         "freeze_frame": [
             {"location": [60.0, 40.0], "teammate": True, "actor": True, "keeper": False},
             {"location": [70.0, 40.0], "teammate": False, "actor": False, "keeper": False},
             {"location": [50.0, 40.0], "teammate": False, "actor": False, "keeper": False},
             {"location": [118.0, 40.0], "teammate": False, "actor": False, "keeper": True},
             {"location": [90.0, 40.0], "teammate": True, "actor": False, "keeper": False}]},
        # Sans zone visible ni acteur.
        {"event_uuid": "r-09", "freeze_frame": [
            {"location": [100.0, 40.0], "teammate": False, "actor": False, "keeper": False}]},
        # Zone vide, aucun joueur.
        {"event_uuid": "r-10", "visible_area": [], "freeze_frame": []},
        "pas une image",
        # Action de l'autre équipe, puis événement inconnu : ignorés.
        {"event_uuid": "r-06", "freeze_frame": []},
        {"event_uuid": "inconnu", "freeze_frame": []},
    ]


# ── Match « v2 » pour les indicateurs de l'étape 3c-1 ────────────────────────
# ADVERSAIRE (101) reçoit 102. Chaque valeur attendue se recalcule à la main
# (cf. tests/test_indicateurs.py, classe IndicateursV2Test).
def evenements_v2():
    a, b = ADVERSAIRE, 102
    return [
        _ev("v-01", "Starting XI", a, 0, tactics={"formation": 4231, "lineup": [
            {"player": _j(1), "position": {"id": 1, "name": "Goalkeeper"}, "jersey_number": 1},
            {"player": _j(9), "position": {"id": 23, "name": "Striker"}, "jersey_number": 9}]}),
        _ev("v-02", "Starting XI", b, 0, tactics={"formation": 442, "lineup": [
            {"player": _j(21), "position": {"id": 1, "name": "Goalkeeper"}, "jersey_number": 1}]}),
        # Relance longue du gardien, 1 adversaire à 4 yards, 3 dans son tiers.
        _ev("v-03", "Pass", a, 1, player=_j(1), position={"id": 1, "name": "Goalkeeper"},
            location=[10.0, 40.0], **_passe(end_location=[60.0, 40.0])),
        # Passe qui laisse 6 adversaires derrière le ballon (aucun à moins de 5 m).
        _ev("v-05", "Pass", a, 2, player=_j(9), location=[30.0, 40.0],
            **_passe(end_location=[80.0, 40.0])),
        # Réception entre les lignes : 3 adversaires devant, 2 derrière.
        _ev("v-06", "Ball Receipt*", a, 3, player=_j(9), location=[70.0, 40.0]),
        # Passe ratée dans son tiers, suivie d'un tir adverse 10 s plus tard.
        _ev("v-04", "Pass", a, 5, player=_j(9), location=[20.0, 10.0],
            **_passe(end_location=[25.0, 10.0], outcome={"name": "Incomplete"})),
        _ev("v-07", "Shot", b, 5, 10, player=_j(29), location=[100.0, 40.0],
            play_pattern={"name": "From Counter"},
            shot={"statsbomb_xg": 0.3, "outcome": {"name": "Off T"}}),
        # Récupération puis but 12 s après (dans la fenêtre), tir 20 s après (hors fenêtre).
        _ev("v-08", "Ball Recovery", a, 10, player=_j(10), location=[50.0, 40.0]),
        _ev("v-09", "Shot", a, 10, 12, player=_j(9), location=[105.0, 40.0],
            play_pattern={"name": "From Counter"},
            shot={"statsbomb_xg": 0.25, "outcome": {"name": "Goal"}}),
        _ev("v-10", "Shot", a, 10, 20, player=_j(9), location=[100.0, 40.0],
            play_pattern={"name": "Regular Play"},
            shot={"statsbomb_xg": 0.1, "outcome": {"name": "Off T"}}),
        # Récupération adverse puis tir adverse 15 s après (limite incluse).
        _ev("v-11", "Interception", b, 20, player=_j(29), location=[40.0, 40.0],
            interception={"outcome": {"name": "Won"}}),
        _ev("v-12", "Shot", b, 20, 15, player=_j(29), location=[104.0, 40.0],
            shot={"statsbomb_xg": 0.2, "outcome": {"name": "Saved"}}),
        # Passe adverse : image de SA ligne défensive (il mène 1-0).
        _ev("v-14", "Pass", b, 25, player=_j(29), location=[40.0, 40.0],
            **_passe(end_location=[50.0, 40.0])),
        _ev("v-13", "Pressure", a, 30, player=_j(9), location=[80.0, 40.0]),
        _ev("v-15", "Pass", a, 35, player=_j(10), location=[120.0, 0.0],
            **_passe(end_location=[114.0, 40.0], type={"name": "Corner"})),
        _ev("v-16", "Pass", b, 40, player=_j(29), location=[120.0, 80.0],
            **_passe(end_location=[110.0, 40.0], type={"name": "Corner"})),
        _ev("v-17", "Shot", b, 91, period=2, player=_j(29), location=[100.0, 40.0],
            shot={"statsbomb_xg": 0.1, "outcome": {"name": "Off T"}}),
    ]


def _vu(x, y, partenaire, acteur=False, gardien=False):
    """Un joueur visible d'une image 360 (repère de l'équipe de l'acteur)."""
    return {"location": [float(x), float(y)], "teammate": partenaire, "actor": acteur,
            "keeper": gardien}


def _image(uuid, *joueurs):
    return {"event_uuid": uuid,
            "visible_area": [0.0, 0.0, 120.0, 0.0, 120.0, 80.0, 0.0, 80.0],
            "freeze_frame": list(joueurs)}


def frames_v2():
    return [
        _image("v-03", _vu(10, 40, True, acteur=True, gardien=True), _vu(14, 40, False),
               _vu(30, 20, False), _vu(35, 60, False), _vu(110, 40, False, gardien=True)),
        _image("v-04", _vu(20, 10, True, acteur=True), _vu(60, 60, False)),
        _image("v-05", _vu(30, 40, True, acteur=True), _vu(40, 30, False), _vu(45, 40, False),
               _vu(50, 50, False), _vu(55, 35, False), _vu(70, 20, False), _vu(75, 60, False),
               _vu(20, 40, False), _vu(118, 40, False, gardien=True)),
        _image("v-06", _vu(70, 40, True, acteur=True), _vu(80, 30, False), _vu(85, 50, False),
               _vu(95, 40, False), _vu(60, 30, False), _vu(65, 50, False),
               _vu(119, 40, False, gardien=True)),
        # Action adverse : les joueurs de l'ADVERSAIRE sont « teammate: false »,
        # dans le repère adverse (x = 70 adverse = 50 pour lui).
        _image("v-14", _vu(40, 40, True, acteur=True), _vu(70, 40, False), _vu(75, 30, False),
               _vu(80, 50, False), _vu(85, 40, False), _vu(90, 20, False),
               _vu(115, 40, False, gardien=True)),
        # Moins de 4 de ses joueurs visibles : ignorée pour la ligne défensive.
        _image("v-12", _vu(104, 40, True, acteur=True), _vu(110, 40, False), _vu(112, 30, False)),
        _image("v-15", _vu(120, 0, True, acteur=True), _vu(116, 38, True), _vu(110, 40, True),
               _vu(100, 40, True), _vu(117, 42, False), _vu(115, 35, False), _vu(105, 30, False),
               _vu(119, 40, False, gardien=True)),
        _image("v-16", _vu(120, 80, True, acteur=True), _vu(112, 40, True), _vu(116, 45, True),
               _vu(115, 40, False), _vu(108, 50, False), _vu(90, 40, False)),
    ]
