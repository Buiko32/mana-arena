import pytest

from app.engine import (
    apply_result,
    calculate_standings,
    make_round,
    make_schedule,
    validate_config,
)


def config(**changes):
    value = {
        "name": "Torneo",
        "date": None,
        "description": "",
        "structure": "swiss",
        "game_format": "Standard",
        "match_length": 1,
        "rounds": 3,
        "loss_threshold": None,
        "league_mode": None,
        "seeding": "manual",
        "allow_draws": False,
        "points": {"win": 3, "draw": 1, "loss": 0, "bye": 3},
        "tiebreakers": ["omw", "wins", "game_diff"],
    }
    value.update(changes)
    return value


def entrants(count):
    return [{"id": f"p{i}", "seed": i} for i in range(1, count + 1)]


def played(cfg, round_no, table, p1, p2, result="p1_win", p1_games=1, p2_games=0):
    return apply_result(
        cfg,
        {"round": round_no, "table": table, "p1": p1, "p2": p2, "bye": False},
        {"result": result, "p1_games": p1_games, "p2_games": p2_games},
    )


def test_come_excel_preset_is_adapted_bo1_with_two_losses():
    preset = config(
        structure="adapted",
        match_length=1,
        rounds=None,
        loss_threshold=2,
        allow_draws=False,
        points={"win": 1, "draw": 0, "loss": 0, "bye": 1},
    )
    validate_config(preset)
    assert preset["structure"] == "adapted"
    assert preset["match_length"] == 1
    assert preset["loss_threshold"] == 2
    assert preset["points"] == {"win": 1, "draw": 0, "loss": 0, "bye": 1}
    assert preset["allow_draws"] is False


@pytest.mark.parametrize(
    "changes",
    [
        {"structure": "unknown"},
        {"structure": "swiss", "rounds": 0},
        {"structure": "swiss", "rounds": 33},
        {"structure": "adapted", "loss_threshold": 1},
        {"structure": "adapted", "loss_threshold": 6},
        {"match_length": 2},
        {"points": {"win": 11, "draw": 0, "loss": 0, "bye": 1}},
        {"tiebreakers": ["wins", "wins"]},
        {"structure": "single_elim", "allow_draws": True},
        {"structure": "league", "league_mode": "triple"},
        {"date": "30/10/2026"},
    ],
)
def test_validate_config_rejects_incompatible_settings(changes):
    with pytest.raises(ValueError):
        validate_config(config(**changes))


@pytest.mark.parametrize(
    "points,allow_draws",
    [
        ({"win": 2, "draw": 0, "loss": 2, "bye": 1}, False),
        ({"win": 2, "draw": 1, "loss": 2, "bye": 1}, True),
        ({"win": 2, "draw": 3, "loss": 1, "bye": 1}, True),
    ],
)
def test_validate_config_rejects_inconsistent_point_order(points, allow_draws):
    with pytest.raises(ValueError):
        validate_config(config(points=points, allow_draws=allow_draws))


def test_validate_config_allows_draws_only_when_format_can_finish_tied():
    cfg = config(allow_draws=True)
    validate_config(cfg)
    with pytest.raises(ValueError):
        validate_config(config(structure="adapted", loss_threshold=2, allow_draws=True))


@pytest.mark.parametrize(
    "cfg,score,valid",
    [
        (config(), {"result": "p1_win", "p1_games": 1, "p2_games": 0}, True),
        (config(), {"result": "p1_win", "p1_games": 0, "p2_games": 1}, False),
        (config(), {"result": "draw", "p1_games": 0, "p2_games": 0}, False),
        (config(allow_draws=True), {"result": "draw", "p1_games": 0, "p2_games": 0}, True),
        (config(match_length=3), {"result": "p1_win", "p1_games": 2, "p2_games": 1}, True),
        (config(match_length=3), {"result": "p1_win", "p1_games": 2, "p2_games": 2}, False),
        (config(match_length=3), {"result": "p1_win", "p1_games": 3, "p2_games": 0}, False),
        (config(), {"result": "p1_forfeit", "p1_games": 0, "p2_games": 0}, True),
    ],
)
def test_apply_result_validates_scores_and_configures_points(cfg, score, valid):
    match = {"round": 1, "table": 1, "p1": "p1", "p2": "p2", "bye": False}
    if not valid:
        with pytest.raises(ValueError):
            apply_result(cfg, match, score)
        return
    result = apply_result(cfg, match, score)
    assert result["p1_points"] >= 0
    assert result["p2_points"] >= 0
    assert result["p1_losses"] + result["p2_losses"] == (0 if score["result"] == "draw" else 1)
    if score["result"] == "p1_forfeit":
        assert (result["p1_games"], result["p2_games"]) == (0, 1)


def test_swiss_pairing_uses_standings_and_avoids_rematches_when_possible():
    cfg = config()
    field = entrants(4)
    previous = [played(cfg, 1, 1, "p1", "p2"), played(cfg, 1, 2, "p3", "p4")]
    result = make_round(cfg, field, previous, 2)
    pairs = {frozenset((m["p1"], m["p2"])) for m in result["matches"]}
    assert pairs == {frozenset(("p1", "p3")), frozenset(("p2", "p4"))}
    assert result["warnings"] == []


def test_pairing_reports_unavoidable_rematch_and_keeps_every_entrant():
    cfg = config()
    field = entrants(2)
    history = [played(cfg, n, 1, "p1", "p2") for n in (1, 2)]
    result = make_round(cfg, field, history, 3)
    assert len(result["matches"]) == 1
    assert {result["matches"][0]["p1"], result["matches"][0]["p2"]} == {"p1", "p2"}
    assert result["warnings"]


def test_pairing_64_entrants_with_realistic_round_history_is_complete_and_bounded():
    cfg = config(rounds=7)
    field = entrants(64)
    history = []
    pairs_seen = set()
    for round_no in range(1, 7):
        current = make_round(cfg, field, history, round_no)
        assert len(current["matches"]) == 32
        assert not current["warnings"]
        current_pairs = {frozenset((m["p1"], m["p2"])) for m in current["matches"]}
        assert pairs_seen.isdisjoint(current_pairs)
        pairs_seen.update(current_pairs)
        history.extend(played(cfg, round_no, m["table"], m["p1"], m["p2"]) for m in current["matches"])


def test_odd_field_bye_is_fair_and_not_a_played_win():
    cfg = config(points={"win": 3, "draw": 1, "loss": 0, "bye": 2})
    field = entrants(3)
    first = make_round(cfg, field, [], 1)
    bye1 = next(m for m in first["matches"] if m["bye"])
    final_bye = apply_result(cfg, bye1, {"result": "bye", "p1_games": 0, "p2_games": 0})
    assert final_bye["p1_points"] == 2
    assert final_bye["p1_wins"] == 0
    second = make_round(cfg, field, [final_bye], 2)
    bye2 = next(m for m in second["matches"] if m["bye"])
    assert bye2["p1"] != bye1["p1"]


def test_adapted_players_at_loss_threshold_are_removed_from_next_pairing():
    cfg = config(structure="adapted", rounds=None, loss_threshold=2, points={"win": 1, "draw": 0, "loss": 0, "bye": 1})
    field = entrants(4)
    hist = [
        played(cfg, 1, 1, "p1", "p2", "p1_win"),
        played(cfg, 1, 2, "p3", "p4", "p2_win", p1_games=0, p2_games=1),
        played(cfg, 2, 1, "p1", "p3", "p1_win"),
        played(cfg, 2, 2, "p2", "p4", "p2_win", p1_games=0, p2_games=1),
    ]
    nxt = make_round(cfg, field, hist, 3)
    ids = {p for m in nxt["matches"] for p in (m["p1"], m["p2"]) if p is not None}
    assert "p2" not in ids
    assert "p3" not in ids


def test_adapted_pairing_prefers_same_loss_count_over_closer_points():
    cfg = config(structure="adapted", rounds=None, loss_threshold=3)
    field = entrants(5)
    field[4]["withdrawn"] = True
    history = [
        played(cfg, 1, 1, "p1", "p5", "p1_win"),
        played(cfg, 2, 1, "p1", "p5", "p1_win"),
        played(cfg, 3, 1, "p2", "p5", "p1_win"),
        played(cfg, 4, 1, "p2", "p5", "p1_win"),
        played(cfg, 5, 1, "p2", "p5", "p2_win", p1_games=0, p2_games=1),
        played(cfg, 6, 1, "p2", "p5", "p1_win"),
        played(cfg, 7, 1, "p3", "p5", "p1_win"),
        played(cfg, 8, 1, "p4", "p5", "p2_win", p1_games=0, p2_games=1),
    ]
    result = make_round(cfg, field, history, 9)
    assert {frozenset((m["p1"], m["p2"])) for m in result["matches"]} == {
        frozenset(("p1", "p3")),
        frozenset(("p2", "p4")),
    }


def test_pairing_keeps_results_against_eliminated_players_in_standings_and_pairing():
    cfg = config(structure="adapted", rounds=None, loss_threshold=2)
    field = entrants(6)
    history = [
        played(cfg, 1, 1, "p1", "p3", "p1_win"),
        played(cfg, 1, 2, "p2", "p4", "p1_win"),
        played(cfg, 2, 1, "p1", "p5", "p1_win"),
        played(cfg, 2, 2, "p4", "p6", "p1_win"),
        played(cfg, 3, 1, "p1", "p5", "p1_win"),
        played(cfg, 3, 2, "p4", "p6", "p1_win"),
        played(cfg, 4, 1, "p1", "p6", "p2_win", p1_games=0, p2_games=1),
        played(cfg, 4, 2, "p2", "p5", "p2_win", p1_games=0, p2_games=1),
    ]
    standings = {row["player_id"]: row for row in calculate_standings(cfg, field, history)}
    assert standings["p5"]["eliminated"] and standings["p5"]["losses"] == 2
    assert standings["p6"]["eliminated"] and standings["p6"]["losses"] == 2
    result = make_round(cfg, field, history, 5)
    assert {frozenset((m["p1"], m["p2"])) for m in result["matches"]} == {
        frozenset(("p1", "p4")),
        frozenset(("p2", "p3")),
    }


def test_adapted_threshold_schedule_terminates_for_two_through_nine_entrants():
    for count in range(2, 10):
        for seed_variant in range(8):
            cfg = config(structure="adapted", rounds=None, loss_threshold=2, points={"win": 1, "draw": 0, "loss": 0, "bye": 1})
            field = entrants(count)
            for index, entrant in enumerate(field):
                entrant["seed"] = (index + seed_variant) % count + 1
            history = []
            for round_no in range(1, count * 4 + 1):
                scheduled = make_round(cfg, field, history, round_no)
                assert len(scheduled["matches"]) <= count // 2 + 1
                if not scheduled["matches"]:
                    live = sum(not row["eliminated"] for row in calculate_standings(cfg, field, history))
                    assert live <= 1
                    break
                for match in scheduled["matches"]:
                    if match["bye"]:
                        history.append(apply_result(cfg, match, {"result": "bye", "p1_games": 0, "p2_games": 0}))
                    elif (seed_variant + round_no + match["table"]) % 2:
                        history.append(played(cfg, round_no, match["table"], match["p1"], match["p2"]))
                    else:
                        history.append(played(cfg, round_no, match["table"], match["p1"], match["p2"], "p2_win", p1_games=0, p2_games=1))
                rows = calculate_standings(cfg, field, history)
                if sum(not row["eliminated"] for row in rows) <= 1:
                    break
            else:
                pytest.fail(f"adapted tournament did not terminate for {count} entrants, seed variant {seed_variant}")


def test_league_single_and_double_round_robin_schedules():
    field = entrants(4)
    single = make_schedule(config(structure="league", rounds=None, league_mode="single"), field)
    double = make_schedule(config(structure="league", rounds=None, league_mode="double"), field)
    assert len(single["matches"]) == 6
    assert len({frozenset((m["p1"], m["p2"])) for m in single["matches"]}) == 6
    assert len(double["matches"]) == 12
    assert single["matches"] == make_schedule(config(structure="league", rounds=None, league_mode="single"), field)["matches"]


def test_league_odd_field_has_calendar_byes_without_points():
    cfg = config(structure="league", rounds=None, league_mode="single")
    schedule = make_schedule(cfg, entrants(3))
    assert len(schedule["matches"]) == 6
    assert sum(m["bye"] for m in schedule["matches"]) == 3
    byes = [apply_result(cfg, m, {"result": "bye", "p1_games": 0, "p2_games": 0}) for m in schedule["matches"] if m["bye"]]
    assert all(m["p1_points"] == 0 for m in byes)


def test_single_elimination_initial_byes_and_progression_do_not_reintroduce_losers():
    cfg = config(structure="single_elim", rounds=None)
    field = entrants(5)
    initial = make_schedule(cfg, field)
    assert len(initial["matches"]) == 4
    byes = [m for m in initial["matches"] if m["bye"]]
    assert len(byes) == 3
    completed = [apply_result(cfg, m, {"result": "bye", "p1_games": 0, "p2_games": 0}) if m["bye"] else apply_result(cfg, m, {"result": "p1_win", "p1_games": 1, "p2_games": 0}) for m in initial["matches"]]
    nxt = make_round(cfg, field, completed, 2)
    assert len(nxt["matches"]) == 2
    losers = {m["p2"] for m in completed if not m["bye"]}
    assert not any(loser in {m["p1"], m["p2"]} for loser in losers for m in nxt["matches"])


def test_single_elimination_preserves_eight_player_bracket_slots():
    cfg = config(structure="single_elim", rounds=None)
    field = entrants(8)
    initial = make_schedule(cfg, field)["matches"]
    by_slot = {m["bracket_slot"]: m for m in initial}
    assert [(by_slot[i]["p1"], by_slot[i]["p2"]) for i in range(4)] == [
        ("p1", "p8"), ("p4", "p5"), ("p2", "p7"), ("p3", "p6")
    ]
    completed = [
        apply_result(cfg, by_slot[i], {"result": "p1_win", "p1_games": 1, "p2_games": 0})
        for i in range(4)
    ]
    following = make_round(cfg, field, completed, 2)["matches"]
    assert [(m["p1"], m["p2"]) for m in following] == [("p1", "p4"), ("p2", "p3")]


def test_single_elimination_withdrawals_forfeit_in_place_and_propagate_empty_slots():
    cfg = config(structure="single_elim", rounds=None)
    field = entrants(8)
    field[0]["withdrawn"] = True  # p1 forfeits its slot to p8
    field[3]["withdrawn"] = True  # p4 forfeits its slot to p5
    field[1]["withdrawn"] = True  # p2 and p7 leave an empty branch
    field[6]["withdrawn"] = True
    initial = make_schedule(cfg, field)["matches"]
    slot = {m["bracket_slot"]: m for m in initial}
    assert (slot[0]["p1"], slot[0]["p2"], slot[0]["result"]) == ("p1", "p8", "p1_forfeit")
    assert (slot[1]["p1"], slot[1]["p2"], slot[1]["result"]) == ("p4", "p5", "p1_forfeit")
    assert slot[2]["p1"] is None and slot[2]["p2"] is None and slot[2]["result"] == "void"
    completed = [slot[0], slot[1], slot[2], apply_result(cfg, slot[3], {"result": "p1_win", "p1_games": 1, "p2_games": 0})]
    following = make_round(cfg, field, completed, 2)["matches"]
    assert [(m["p1"], m["p2"], m.get("result")) for m in following] == [
        ("p8", "p5", None), ("p3", None, "bye")
    ]


def test_single_elimination_empty_and_single_entrant_samples_have_no_phantom_matches():
    cfg = config(structure="single_elim", rounds=None)
    assert make_schedule(cfg, []) == {"matches": [], "warnings": []}
    assert make_schedule(cfg, entrants(1)) == {"matches": [], "warnings": []}
    assert make_round(cfg, [], [], 1) == {"matches": [], "warnings": []}


def test_withdrawn_entrants_stay_in_standings_but_not_future_pairings():
    cfg = config()
    field = entrants(3)
    field[0]["withdrawn"] = True
    round_one = make_round(cfg, field, [], 1)
    ids = {p for m in round_one["matches"] for p in (m["p1"], m["p2"]) if p is not None}
    assert "p1" not in ids
    rows = calculate_standings(cfg, field, [])
    assert {r["player_id"] for r in rows} == {"p1", "p2", "p3"}


def test_standings_points_game_difference_and_omw_are_computed():
    cfg = config()
    field = entrants(3)
    history = [played(cfg, 1, 1, "p1", "p2", p1_games=1, p2_games=0)]
    history.append(apply_result(cfg, {"round": 1, "table": 2, "p1": "p3", "p2": None, "bye": True}, {"result": "bye", "p1_games": 0, "p2_games": 0}))
    rows = {row["player_id"]: row for row in calculate_standings(cfg, field, history)}
    assert rows["p1"]["points"] == 3
    assert rows["p1"]["game_diff"] == 1
    assert rows["p3"]["wins"] == 0
    assert rows["p3"]["byes"] == 1
    assert rows["p1"]["omw"] == 0
    assert rows["p3"]["omw"] == 0


def test_omw_excludes_opponent_bye_points_and_has_no_minimum_floor():
    cfg = config()
    field = entrants(3)
    field[2]["withdrawn"] = True
    bye = apply_result(cfg, {"round": 1, "table": 1, "p1": "p2", "p2": None, "bye": True}, {"result": "bye", "p1_games": 0, "p2_games": 0})
    game = played(cfg, 2, 1, "p1", "p2", "p1_win", p1_games=1, p2_games=0)
    rows = {row["player_id"]: row for row in calculate_standings(cfg, field, [bye, game])}
    assert rows["p1"]["omw"] == 0


def test_omw_uses_custom_match_points_without_a_minimum_floor():
    cfg = config(points={"win": 5, "draw": 2, "loss": 0, "bye": 10})
    field = entrants(4)
    history = [
        played(cfg, 1, 1, "p1", "p2", "p1_win"),
        played(cfg, 1, 2, "p3", "p4", "p1_win"),
        played(cfg, 2, 1, "p1", "p3", "p1_win"),
        played(cfg, 2, 2, "p2", "p4", "p1_win", p1_games=1, p2_games=0),
    ]
    rows = {row["player_id"]: row for row in calculate_standings(cfg, field, history)}
    assert rows["p1"]["omw"] == 0.5


@pytest.mark.parametrize("malformed", [[], {}, {"unexpected": "object"}])
def test_apply_result_rejects_malformed_outcome_with_value_error(malformed):
    match = {"round": 1, "table": 1, "p1": "p1", "p2": "p2", "bye": False}
    with pytest.raises(ValueError):
        apply_result(config(), match, {"result": malformed, "p1_games": 1, "p2_games": 0})


@pytest.mark.parametrize("outcome", ["p1_forfeit", "p2_forfeit"])
def test_forfeit_awards_points_and_a_loss_without_played_win_or_games(outcome):
    cfg = config()
    field = entrants(2)
    match = apply_result(
        cfg,
        {"round": 1, "table": 1, "p1": "p1", "p2": "p2", "bye": False},
        {"result": outcome, "p1_games": 0, "p2_games": 0},
    )
    rows = {row["player_id"]: row for row in calculate_standings(cfg, field, [match])}
    assert sum(row["wins"] for row in rows.values()) == 0
    assert sum(row["losses"] for row in rows.values()) == 1
    assert sum(row["game_diff"] for row in rows.values()) == 0
    assert rows["p1"]["points"] + rows["p2"]["points"] == cfg["points"]["win"] + cfg["points"]["loss"]


def test_empty_and_completed_round_requests_return_empty_matches():
    cfg = config()
    assert make_round(cfg, [], [], 1) == {"matches": [], "warnings": []}
    field = entrants(2)
    first = make_round(cfg, field, [], 1)
    complete = [played(cfg, 1, m["table"], m["p1"], m["p2"]) for m in first["matches"]]
    assert make_round(cfg, field, complete, 1)["matches"] == []

