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
