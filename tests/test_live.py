from test_api import app_factory, client, admin_headers, tournament_with_players


def test_live_scores_and_device_ownership(client, monkeypatch):
    from fastapi.testclient import TestClient

    assert client.get("/api/public/live").json() == []
    headers = {"X-Mana-Live": "1"}
    path = "/api/life/live/34c10f2f-ee91-4bc4-a5b1-67f30e2fbec2"
    _, players = tournament_with_players(client, admin_headers(client))
    sides = [{"members": [{"id": player["id"], "name": player["name"]}],
              "initial": 20, "life": 20} for player in players]
    game = {"format": "duel", "kind": "friendly", "sides": sides}
    assert client.put(path, json=game).status_code == 403
    assert client.put(path, json=game, headers=headers).status_code == 204
    live = client.get("/api/public/live").json()
    assert live[0]["prediction"] == [50, 50]
    sides[0]["life"] = 5
    assert client.put(path, json=game, headers=headers).status_code == 204
    live = client.get("/api/public/live").json()[0]
    assert live["sides"][0]["life"] == 5
    assert 0 < live["prediction"][0] < 50
    assert sum(live["prediction"]) == 100
    other = TestClient(client.app)
    try:
        other.post("/api/access/login", json={"password": "Shared-access-passphrase-42"})
        assert other.delete(path, headers=headers).status_code == 404
    finally:
        other.close()
    assert client.delete(path, headers=headers).status_code == 204
    assert client.get("/api/public/live").json() == []


    from app import live as live_module

    assert client.put(path, json=game, headers=headers).status_code == 204
    expired = live_module.monotonic() + live_module.LIVE_TIMEOUT + 1
    monkeypatch.setattr(live_module, "monotonic", lambda: expired)
    assert client.get("/api/public/live").json() == []


def test_scheduled_bet_survives_live_transition_and_settles(client):
    from test_api import start

    headers = admin_headers(client)
    tid, people = tournament_with_players(client, headers, count=4)
    assert start(client, tid, headers).status_code == 200
    assert client.post(f"/api/admin/tournaments/{tid}/rounds/next", json={}, headers=headers).status_code == 200
    market = client.get("/api/public/live-markets").json()["scheduled"][0]
    first, second = market["players"]
    spectator = next(person["id"] for person in people if person["id"] not in market["players"])
    with client.app.state.db.transaction() as conn:
        conn.executemany("""
            INSERT INTO result_proposals(id,proposer_id,opponent_id,winner_id,status,created_at)
            VALUES (?, ?, ?, ?, 'confirmed', '2026-11-25 12:00:00')
        """, [(f"fund-{i}", spectator, second, spectator) for i in range(3)])

    def sign_in(player_id):
        response = client.post("/api/player/login", json={"player_id": player_id})
        assert response.status_code == 200, response.text
        return {"X-Player-CSRF": response.json()["csrf_token"]}

    participant_headers = sign_in(first)
    markets = client.get("/api/public/live-markets").json()
    market = next(row for row in markets["scheduled"] if row["id"] == market["id"])
    payload = {"market": market["id"], "selection": first, "amount": 5, "multiplier": market["quotes"][0]}
    assert market["participant"]
    assert client.post("/api/player/live-bets", json=payload, headers=participant_headers).status_code == 403
    spectator_headers = sign_in(spectator)
    initial = client.get("/api/public/live-markets").json()["viewer"]["balance"]
    assert initial >= 5
    assert client.post("/api/player/live-bets", json={**payload, "amount": (initial // 5 + 1) * 5}, headers=spectator_headers).status_code == 409
    assert client.post("/api/player/live-bets", json={**payload, "amount": 6}, headers=spectator_headers).status_code == 422
    assert client.post("/api/player/live-bets", json={**payload, "multiplier": 105}, headers=spectator_headers).status_code == 409
    wager = client.post("/api/player/live-bets", json=payload, headers=spectator_headers)
    assert wager.status_code == 201, wager.text
    assert client.post("/api/player/live-bets", json=payload, headers=spectator_headers).status_code == 409
    live_id = "5c2ae3b4-cc34-4a81-ac86-0dbc9e209bbd"
    match_id = market["id"].split(":")[-1]
    sign_in(first)
    sides = [{"members": [{"id": pid, "name": next(p["name"] for p in people if p["id"] == pid)}],
              "initial": 20, "life": life} for pid, life in [(second, 20), (first, 7)]]
    game = {"format": "duel", "kind": "tournament", "tournament_id": tid, "match_id": match_id, "sides": sides}
    live_path = f"/api/life/live/{live_id}"
    assert client.put(live_path, json=game, headers={"X-Mana-Live": "1"}).status_code == 204
    sign_in(spectator)
    live = client.get("/api/public/live-markets").json()
    assert live["viewer"]["balance"] == initial - 5
    assert live["active"][live_id]["id"] == market["id"]
    assert live["active"][live_id]["totals"] == [5, 0]
    assert live["active"][live_id]["own"]["multiplier"] == payload["multiplier"]
    assert all(row["id"] != market["id"] for row in live["scheduled"])
    with client.app.state.db.transaction() as conn:
        conn.execute("UPDATE matches SET data_json=json_set(data_json,'$.result','p1_win') WHERE id=?", (match_id,))
    settled = client.get("/api/public/live-markets").json()
    assert settled["tickets"][0]["status"] == "won"
    assert settled["viewer"]["balance"] == initial - 5 + wager.json()["return"]
    assert client.get("/api/public/live-markets").json()["viewer"] == settled["viewer"]

    friendly_id = "68be09dc-8dab-4520-8934-1effe927215c"
    friendly_path = f"/api/life/live/{friendly_id}"
    friendly = {"format": "duel", "kind": "friendly", "sides": sides}
    sign_in(first)
    assert client.put(friendly_path, json=friendly, headers={"X-Mana-Live": "1"}).status_code == 204
    spectator_headers = sign_in(spectator)
    live_market = client.get("/api/public/live-markets").json()["active"][friendly_id]
    bet = {"market": live_market["id"], "selection": second, "amount": 5, "multiplier": live_market["quotes"][0]}
    assert client.post("/api/player/live-bets", json=bet, headers=spectator_headers).status_code == 201
    assert client.post("/api/player/live-bets", json=bet, headers=sign_in(second)).status_code == 403
    assert client.delete(friendly_path + "?cancel=true", headers={"X-Mana-Live": "1"}).status_code == 204
    sign_in(spectator)
    refunded = client.get("/api/public/live-markets").json()
    assert refunded["tickets"][0]["status"] == "refunded"
    assert refunded["viewer"]["balance"] == settled["viewer"]["balance"]

    draw_id = "0e065a15-4f89-4859-9593-5c0caac9c079"
    draw_path = f"/api/life/live/{draw_id}"
    participant = sign_in(first)
    draw_sides = [{**side, "life": 20} for side in sides]
    assert client.put(draw_path, json={**friendly, "sides": draw_sides}, headers={"X-Mana-Live": "1"}).status_code == 204
    spectator_headers = sign_in(spectator)
    draw_market = client.get("/api/public/live-markets").json()["active"][draw_id]
    bet = {"market": draw_market["id"], "selection": first, "amount": 5, "multiplier": draw_market["quotes"][1]}
    assert client.post("/api/player/live-bets", json=bet, headers=spectator_headers).status_code == 201
    participant = sign_in(first)
    result = {
        "format": "duel", "session_id": draw_id, "started_at": "2026-11-25T18:00:00+00:00",
        "ended_at": "2026-11-25T18:10:00+00:00", "starting_life": 20, "changes": [], "winner": None,
        "players": [{"name": side["members"][0]["name"], "members": side["members"],
                     "initial_life": 20, "final_life": 20} for side in sides],
        "result_target": {"kind": "friendly", "proposer_id": first},
    }
    for _ in range(2):
        response = client.post("/api/life/games", json=result, headers=participant)
        assert response.status_code == 200 and response.json()["result_status"] == "stats_only"
    sign_in(spectator)
    drawn = client.get("/api/public/live-markets").json()
    assert drawn["tickets"][0]["status"] == "refunded"
    assert drawn["viewer"]["balance"] == settled["viewer"]["balance"]

def test_head_to_head_weights_recent_wins_and_ignores_non_duels():
    from datetime import datetime, timezone

    from app.live import prematch_prediction, weighted_duel_wins

    now = datetime(2026, 11, 27, tzinfo=timezone.utc)
    recent = {"p1": "a", "p2": "b", "result": "p1_win", "elo_date": "2026-11-27 00:00:00"}
    old = {**recent, "elo_date": "2026-08-29 00:00:00"}
    wins = weighted_duel_wins([recent, old, {**recent, "void": True}, {**recent, "result": "draw"},
                               {**recent, "team_a": ["a"]}, {**recent, "result": "p2_win"}], now=now)
    assert wins == {("a", "b"): 1.5, ("b", "a"): 1}
    ratings = {"a": {"rating": 1500}, "b": {"rating": 1500}}
    assert prematch_prediction(["a", "b"], ratings, {}) == [50, 50]
    assert prematch_prediction(["a", "b"], ratings, wins) == [51, 49]
    assert prematch_prediction(["b", "a"], ratings, wins) == [49, 51]
