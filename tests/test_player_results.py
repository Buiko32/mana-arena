from fastapi.testclient import TestClient
from test_api import app_factory, admin_headers, tournament_with_players, start


def test_players_confirm_results_and_cannot_confirm_their_own(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        headers = admin_headers(admin)
        tid, players = tournament_with_players(admin, headers)
        assert start(admin, tid, headers).status_code == 200
        assert admin.post(f'/api/admin/tournaments/{tid}/rounds/next', json={}, headers=headers).status_code == 200
        match = admin.get(f'/api/public/tournaments/{tid}').json()['matches'][0]
        tokens = []
        for client, person in zip((a, b), players):
            credential = admin.post(f"/api/admin/players/{person['id']}/credential", headers=headers)
            assert credential.status_code == 200
            response = client.post('/api/player/login', json={'player_id': person['id'], 'password': credential.json()['password']})
            assert response.status_code == 200
            tokens.append({'X-Player-CSRF': response.json()['csrf_token']})
        proposal = a.post('/api/player/results', headers=tokens[0], json={'tournament_id': tid, 'match_id': match['id'], 'winner_id': players[0]['id']})
        assert proposal.status_code == 201
        pid = proposal.json()['id']
        assert a.post(f'/api/player/results/{pid}/confirm', headers=tokens[0]).status_code == 403
        assert admin.get(f'/api/public/tournaments/{tid}').json()['matches'][0].get('result') is None
        assert b.post(f'/api/player/results/{pid}/confirm', headers=tokens[1]).status_code == 200
        assert admin.get(f'/api/public/tournaments/{tid}').json()['matches'][0]['result'] in ('p1_win', 'p2_win')


def test_friendly_requires_auth_csrf_and_opponent_confirmation(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as first, TestClient(app) as second:
        headers = admin_headers(admin)
        _, people = tournament_with_players(admin, headers)
        tokens = []
        for client, person in zip((first, second), people):
            password = admin.post(f"/api/admin/players/{person['id']}/credential", headers=headers).json()['password']
            login = client.post('/api/player/login', json={'player_id': person['id'], 'password': password})
            tokens.append({'X-Player-CSRF': login.json()['csrf_token']})
        body = {'opponent_id': people[1]['id'], 'winner_id': people[0]['id']}
        assert first.post('/api/player/results', json=body).status_code == 403
        created = first.post('/api/player/results', json=body, headers=tokens[0])
        assert created.status_code == 201
        rid = created.json()['id']
        assert second.post(f'/api/player/results/{rid}/confirm', headers=tokens[1]).status_code == 200
        assert second.post(f'/api/player/results/{rid}/confirm', headers=tokens[1]).status_code == 409
        assert first.get('/api/player/results').status_code == 200
        assert admin.post('/api/admin/reset-data', json={'scope':'all','confirmation':'ELIMINA'}, headers=headers).status_code == 200


def test_confirmed_friendly_counts_toward_global_ranking(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        headers = admin_headers(admin)
        _, people = tournament_with_players(admin, headers)
        tokens = []
        for client, person in zip((a,b), people):
            password = admin.post(f"/api/admin/players/{person['id']}/credential", headers=headers).json()['password']
            tokens.append({'X-Player-CSRF':client.post('/api/player/login', json={'player_id':person['id'],'password':password}).json()['csrf_token']})
        rid = a.post('/api/player/results', headers=tokens[0], json={'opponent_id':people[1]['id'],'winner_id':people[0]['id']}).json()['id']
        assert all(p['matches'] == 0 for p in admin.get('/api/public/ranking').json())
        assert b.post(f'/api/player/results/{rid}/confirm', headers=tokens[1]).status_code == 200
        rows={p['id']:p for p in admin.get('/api/public/ranking').json()}
        assert rows[people[0]['id']]['wins']==1
        assert rows[people[1]['id']]['losses']==1


def test_organizer_cannot_bypass_player_confirmation(tmp_path):
    from app.main import create_app
    from test_api import PASSWORD, SECRET
    app=create_app(data_dir=tmp_path, admin_username='organizer', admin_password=PASSWORD, session_secret=SECRET)
    with TestClient(app) as client:
        headers=admin_headers(client)
        tid, _ = tournament_with_players(client, headers)
        assert start(client, tid, headers).status_code == 200
        match=client.post(f'/api/admin/tournaments/{tid}/rounds/next', json={}, headers=headers).json()['matches'][0]
        response=client.put(f"/api/admin/tournaments/{tid}/matches/{match['id']}/result", headers=headers, json={'result':'p1_win','p1_games':1,'p2_games':0})
        assert response.status_code == 403


def test_rollback_invalidates_pending_proposal(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        headers=admin_headers(admin)
        tid, people=tournament_with_players(admin, headers)
        start(admin, tid, headers)
        match=admin.post(f'/api/admin/tournaments/{tid}/rounds/next', json={}, headers=headers).json()['matches'][0]
        tokens=[]
        for client, person in zip((a,b),people):
            password=admin.post(f"/api/admin/players/{person['id']}/credential", headers=headers).json()['password']
            tokens.append({'X-Player-CSRF':client.post('/api/player/login',json={'player_id':person['id'],'password':password}).json()['csrf_token']})
        rid=a.post('/api/player/results',headers=tokens[0],json={'tournament_id':tid,'match_id':match['id'],'winner_id':people[0]['id']}).json()['id']
        assert admin.post(f'/api/admin/tournaments/{tid}/rollback',headers=headers,json={'match_id':match['id'],'confirm':True}).status_code==200
        assert b.post(f'/api/player/results/{rid}/confirm',headers=tokens[1]).status_code==409


def test_withdrawal_does_not_create_unconfirmed_forfeit(tmp_path):
    from app.main import create_app
    from test_api import PASSWORD, SECRET
    app=create_app(data_dir=tmp_path, admin_username='organizer', admin_password=PASSWORD, session_secret=SECRET)
    with TestClient(app) as client:
        headers=admin_headers(client)
        tid, people=tournament_with_players(client, headers)
        start(client, tid, headers)
        match=client.post(f'/api/admin/tournaments/{tid}/rounds/next',json={},headers=headers).json()['matches'][0]
        response=client.post(f'/api/admin/tournaments/{tid}/withdrawals',json={'player_id':people[0]['id']},headers=headers)
        assert response.status_code==403
        assert client.get(f'/api/public/tournaments/{tid}').json()['matches'][0].get('result') is None


def test_player_selects_identity_without_a_code(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as first, TestClient(app) as second:
        headers = admin_headers(admin)
        _, people = tournament_with_players(admin, headers)
        sessions = [client.post('/api/player/login', json={'player_id': person['id']}) for client, person in zip((first, second), people)]
        assert all(response.status_code == 200 for response in sessions)
        tokens = [{'X-Player-CSRF': response.json()['csrf_token']} for response in sessions]
        proposed = first.post('/api/player/results', headers=tokens[0], json={'opponent_id':people[1]['id'], 'winner_id':people[0]['id']})
        assert proposed.status_code == 201
        assert second.post(f"/api/player/results/{proposed.json()['id']}/confirm", headers=tokens[1]).status_code == 200


def test_new_players_receive_distinct_available_colors(app_factory):
    from test_api import player
    with TestClient(app_factory()) as client:
        headers = admin_headers(client)
        players = [player(client, headers, f'Colore {i}') for i in range(8)]
        assert len({p['avatar_hue'] for p in players}) == len(players)
