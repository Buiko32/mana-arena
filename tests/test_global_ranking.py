from app.service import calculate_global_elo


def test_elo_replays_played_matches_in_order_and_ignores_nonplayed_results():
    players = [{"id": "a", "name": "Ada"}, {"id": "b", "name": "Bruno"}, {"id": "c", "name": "Carla"}]
    matches = [
        {"p1": "a", "p2": "b", "result": "p1_win"},
        {"p1": "b", "p2": "a", "result": "draw"},
        {"p1": "c", "p2": None, "result": "bye"},
        {"p1": "a", "p2": "c", "result": "p1_forfeit"},
        {"p1": "a", "p2": "c", "result": "void"},
    ]
    rows = {row["id"]: row for row in calculate_global_elo(players, matches)}
    assert rows["a"]["matches"] == 2 and rows["b"]["matches"] == 2
    assert rows["a"]["wins"] == 1 and rows["b"]["draws"] == 1
    assert rows["a"]["rating"] > 1500 > rows["b"]["rating"]
    assert rows["c"]["rating"] == 1500 and rows["c"]["matches"] == 0


def test_unknown_players_or_incomplete_results_do_not_change_ratings():
    players = [{"id":"a", "name":"Ada"}]
    rows = calculate_global_elo(players, [{"p1":"a", "p2":"missing", "result":"p1_win"}, {"p1":"a", "p2":None, "result":None}])
    assert rows[0]["rating"] == 1500 and rows[0]["matches"] == 0



def test_k_is_40_initially_and_20_after_30_played_matches():
    people = [{"id":"a", "name":"Ada"}, {"id":"b", "name":"Bruno"}]
    first = {r["id"]: r for r in calculate_global_elo(people, [{"p1":"a", "p2":"b", "result":"p1_win"}])}
    assert first["a"]["rating"] == 1520
    games = [{"p1":"a", "p2":"b", "result":"draw"}] * 30 + [{"p1":"a", "p2":"b", "result":"p1_win"}]
    after = {r["id"]: r for r in calculate_global_elo(people, games)}
    assert after["a"]["rating"] == 1510 and after["b"]["rating"] == 1490
