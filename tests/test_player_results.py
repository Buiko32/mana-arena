from test_support import TestClient
from test_api import app_factory, admin_headers, tournament_with_players, start


def test_tournament_result_registers_immediately_and_legacy_confirm_is_rejected(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        headers = admin_headers(admin)
        tid, players = tournament_with_players(admin, headers)
        assert start(admin, tid, headers).status_code == 200
        assert admin.post(f'/api/admin/tournaments/{tid}/rounds/next', json={}, headers=headers).status_code == 200
        match = admin.get(f'/api/admin/tournaments/{tid}',headers=headers).json()['matches'][0]
        tokens = []
        for client, person in zip((a, b), players):
            credential = admin.post(f"/api/admin/players/{person['id']}/credential", headers=headers)
            assert credential.status_code == 200
            response = client.post('/api/player/login', json={'player_id': person['id'], 'password': credential.json()['password']})
            assert response.status_code == 200
            tokens.append({'X-Player-CSRF': response.json()['csrf_token']})
            assert client.post(f'/api/player/tournaments/{tid}/reveal', headers=tokens[-1]).status_code == 200
        proposal = a.post('/api/player/results', headers=tokens[0], json={'tournament_id': tid, 'match_id': match['id'], 'winner_id': players[0]['id']})
        assert proposal.status_code == 201
        pid = proposal.json()['id']
        matches = admin.get(f'/api/admin/tournaments/{tid}', headers=headers).json()['matches']
        stored = next(m for m in matches if m['id'] == match['id'])
        assert stored['result'] == ('p1_win' if stored['p1'] == players[0]['id'] else 'p2_win')
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        history = a.get('/api/public/history', params={
            'start': (now - timedelta(days=1)).isoformat(), 'end': (now + timedelta(days=1)).isoformat(),
        }).json()
        assert len(history) == 1 and history[0]['kind'] == 'tournament'
        assert history[0]['label'] == 'Serata Mana · Turno 1'
        assert history[0]['score'] == [stored['p1_games'], stored['p2_games']]
        assert b.post(f'/api/player/results/{pid}/confirm', headers=tokens[1]).status_code == 409
        duplicate = b.post('/api/player/results', headers=tokens[1], json={'tournament_id': tid, 'match_id': match['id'], 'winner_id': players[1]['id']})
        assert duplicate.status_code == 409


def test_friendly_registers_immediately_and_legacy_confirm_is_closed(app_factory):
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
        assert second.post(f'/api/player/results/{rid}/confirm', headers=tokens[1]).status_code == 409
        assert first.get('/api/player/results').status_code == 200
        assert admin.post('/api/admin/reset-data', json={'scope':'all','confirmation':'ELIMINA'}, headers=headers).status_code == 200


def test_friendly_updates_global_ranking_immediately(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        headers = admin_headers(admin)
        _, people = tournament_with_players(admin, headers)
        tokens = []
        for client, person in zip((a,b), people):
            password = admin.post(f"/api/admin/players/{person['id']}/credential", headers=headers).json()['password']
            tokens.append({'X-Player-CSRF':client.post('/api/player/login', json={'player_id':person['id'],'password':password}).json()['csrf_token']})
        result = a.post('/api/player/results', headers=tokens[0], json={'opponent_id':people[1]['id'],'winner_id':people[0]['id']})
        assert result.status_code == 201 and result.json().get('id')
        rows={p['id']:p for p in admin.get('/api/public/ranking').json()}
        assert rows[people[0]['id']]['wins']==1
        assert rows[people[1]['id']]['losses']==1


def test_organizer_cannot_bypass_player_registration(tmp_path):
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


def test_rollback_invalidates_registered_result(app_factory):
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
            assert client.post(f'/api/player/tournaments/{tid}/reveal',headers=tokens[-1]).status_code==200
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
        assert client.get(f'/api/admin/tournaments/{tid}',headers=headers).json()['matches'][0].get('result') is None


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
        assert second.post(f"/api/player/results/{proposed.json()['id']}/confirm", headers=tokens[1]).status_code == 409


def test_new_players_receive_distinct_available_colors(app_factory):
    from test_api import player
    with TestClient(app_factory()) as client:
        headers = admin_headers(client)
        players = [player(client, headers, f'Colore {i}') for i in range(8)]
        assert len({p['avatar_hue'] for p in players}) == len(players)


def test_next_round_hidden_until_player_reveals(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        headers=admin_headers(admin)
        tid, people=tournament_with_players(admin, headers)
        assert start(admin, tid, headers).status_code==200
        assert admin.post(f'/api/admin/tournaments/{tid}/rounds/next',headers=headers,json={}).status_code==200
        for client,person in zip((a,b),people):
            login=client.post('/api/player/login',json={'player_id':person['id']})
            assert login.status_code==200
        detail = a.get(f'/api/public/tournaments/{tid}').json()
        assert detail['current_round'] == 1 and detail['matches'] == []
        listing = a.get('/api/public/tournaments')
        assert listing.headers['cache-control'] == 'private, no-store'
        assert 'Cookie' in listing.headers['vary']
        assert listing.json()[0]['my_match'] == {'round': 1}
        assert admin.get('/api/public/tournaments').json()[0]['my_match'] is None
        assert a.get('/api/player/matches').json()[0]['revealed'] is False
        session=a.get('/api/player/session').json()
        reveal=a.post(f'/api/player/tournaments/{tid}/reveal',headers={'X-Player-CSRF':session['csrf_token']})
        assert reveal.status_code==200
        assert reveal.json()['opponent']['id']==people[1]['id']
        personal = a.get('/api/public/tournaments').json()[0]['my_match']
        assert personal['opponent_id'] == people[1]['id'] and personal['table'] == 1
        match = a.get('/api/player/matches').json()[0]
        assert match['revealed'] is True and match['id'] == personal['match_id']
        assert b.get('/api/public/tournaments').json()[0]['my_match'] == {'round': 1}
        assert len(a.get(f'/api/public/tournaments/{tid}').json()['matches'])==1
        assert b.get(f'/api/public/tournaments/{tid}').json()['matches']==[]


def test_future_league_rounds_stay_hidden_after_reveal(app_factory):
    from test_api import config
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as player:
        headers=admin_headers(admin)
        tid, people=tournament_with_players(admin,headers,4,config(structure='league',rounds=None,league_mode='single'))
        assert start(admin,tid,headers).status_code==200
        session=player.post('/api/player/login',json={'player_id':people[0]['id']}).json()
        assert player.get(f'/api/public/tournaments/{tid}').json()['matches']==[]
        assert player.post(f'/api/player/tournaments/{tid}/reveal',headers={'X-Player-CSRF':session['csrf_token']}).status_code==200
        shown=player.get(f'/api/public/tournaments/{tid}').json()['matches']
        assert shown and {m['round'] for m in shown}=={1}


def test_player_responses_are_not_cacheable(app_factory):
    app=app_factory()
    with TestClient(app) as client:
        headers=admin_headers(client)
        tid, people=tournament_with_players(client,headers)
        start(client,tid,headers)
        client.post(f'/api/admin/tournaments/{tid}/rounds/next',headers=headers,json={})
        client.post('/api/player/login',json={'player_id':people[0]['id']})
        for path in (f'/api/public/tournaments/{tid}',f'/api/public/tournaments/{tid}/rounds','/api/player/results'):
            response=client.get(path)
            assert 'no-store' in response.headers['cache-control']
            assert 'Cookie' in response.headers['vary']


def test_unrevealed_players_can_register_result_immediately(app_factory):
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        headers=admin_headers(admin)
        tid, people=tournament_with_players(admin,headers)
        start(admin,tid,headers)
        match=admin.post(f'/api/admin/tournaments/{tid}/rounds/next',headers=headers,json={}).json()['matches'][0]
        tokens=[]
        for client, person in zip((a,b),people):
            login=client.post('/api/player/login',json={'player_id':person['id']}).json()
            tokens.append({'X-Player-CSRF':login['csrf_token']})
        body={'tournament_id':tid,'match_id':match['id'],'winner_id':people[0]['id']}
        assert len(a.get('/api/player/matches').json()) == 1
        proposal = a.post('/api/player/results',json=body,headers=tokens[0])
        assert proposal.status_code==201
        assert b.post(f"/api/player/results/{proposal.json()['id']}/confirm",headers=tokens[1]).status_code==409
        tournament = admin.get(f'/api/admin/tournaments/{tid}',headers=headers).json()
        assert max(m['round'] for m in tournament['matches']) == 2


def test_new_round_is_hidden_until_bye_player_reveals(app_factory):
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as bye_player:
        headers=admin_headers(admin)
        tid,people=tournament_with_players(admin,headers,3)
        start(admin,tid,headers)
        matches=admin.post(f'/api/admin/tournaments/{tid}/rounds/next',headers=headers,json={}).json()['matches']
        bye=next(m for m in matches if m.get('bye'))
        played=next(m for m in matches if not m.get('bye'))
        session=bye_player.post('/api/player/login',json={'player_id':bye['p1']}).json()
        assert admin.put(f"/api/admin/tournaments/{tid}/matches/{played['id']}/result",headers=headers,json={'result':'p1_win','p1_games':1,'p2_games':0}).status_code==200
        visible = bye_player.get(f'/api/public/tournaments/{tid}').json()['matches']
        assert visible and all(m['round'] == 1 for m in visible)
        response=bye_player.post(f'/api/player/tournaments/{tid}/reveal',headers={'X-Player-CSRF':session['csrf_token']})
        assert response.status_code==200 and response.json()['round'] == 2


def test_player_without_match_can_unlock_view(app_factory):
    from test_api import player
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as spectator:
        headers=admin_headers(admin)
        tid, _=tournament_with_players(admin,headers)
        observer=player(admin,headers,'Spettatore')
        start(admin,tid,headers)
        admin.post(f'/api/admin/tournaments/{tid}/rounds/next',headers=headers,json={})
        login=spectator.post('/api/player/login',json={'player_id':observer['id']}).json()
        assert spectator.get(f'/api/public/tournaments/{tid}').json()['matches']==[]
        response=spectator.post(f'/api/player/tournaments/{tid}/reveal',headers={'X-Player-CSRF':login['csrf_token']})
        assert response.status_code==200 and response.json()['unpaired']
        assert spectator.get(f'/api/public/tournaments/{tid}').json()['matches']


def test_completed_final_hides_champion_until_reveal(app_factory):
    from test_api import config
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as player:
        headers=admin_headers(admin)
        tid, people=tournament_with_players(admin,headers,2,config(rounds=1))
        start(admin,tid,headers)
        match=admin.post(f'/api/admin/tournaments/{tid}/rounds/next',headers=headers,json={}).json()['matches'][0]
        assert admin.put(f"/api/admin/tournaments/{tid}/matches/{match['id']}/result",headers=headers,json={'result':'p1_win','p1_games':1,'p2_games':0}).status_code==200
        session=player.post('/api/player/login',json={'player_id':people[0]['id']}).json()
        detail=player.get(f'/api/public/tournaments/{tid}').json()
        assert detail['matches']==[] and detail['champion_id'] is None
        assert player.get('/api/public/tournaments').json()[0]['champion_id'] is None
        assert player.post(f'/api/player/tournaments/{tid}/reveal',headers={'X-Player-CSRF':session['csrf_token']}).status_code==200
        assert player.get(f'/api/public/tournaments/{tid}').json()['champion_id']==match['p1']


def test_player_can_update_own_avatar_color_and_photo(app_factory):
    from io import BytesIO
    from PIL import Image
    from test_api import player
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as user:
        headers=admin_headers(admin)
        alice=player(admin,headers,'Alice')
        bob=player(admin,headers,'Bob')
        session=user.post('/api/player/login',json={'player_id':alice['id']}).json()
        csrf={'X-Player-CSRF':session['csrf_token']}
        assert user.patch('/api/player/profile',json={'avatar_color':'#FF8800'}).status_code==403
        changed=user.patch('/api/player/profile',headers=csrf,json={'avatar_color':'#FF8800'})
        assert changed.status_code==200 and changed.json()['avatar_color']=='#ff8800'
        assert user.patch('/api/player/profile',headers=csrf,json={'avatar_color':'red'}).status_code==422
        assert user.patch('/api/player/profile',headers=csrf,json={'player_id':bob['id'],'avatar_color':'#00ff00'}).status_code==422
        photo=BytesIO();Image.new('RGB',(8,8),'red').save(photo,format='PNG')
        uploaded=user.post('/api/player/profile/photo',headers=csrf,files={'file':('avatar.png',photo.getvalue(),'image/png')})
        assert uploaded.status_code==200
        assert user.get(uploaded.json()['photo_url']).status_code==200
        assert user.delete('/api/player/profile/photo',headers=csrf).status_code==200
        ranking={p['id']:p for p in user.get('/api/public/ranking').json()}
        assert ranking[alice['id']]['avatar_color']=='#ff8800' and ranking[alice['id']]['photo_url'] is None
        assert ranking[bob['id']]['avatar_color'] is None


def test_invalid_profile_photo_preserves_previous_image_and_backup_restores_color(app_factory):
    from io import BytesIO
    from PIL import Image
    from test_api import player
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as user:
        headers=admin_headers(admin)
        person=player(admin,headers,'Alice')
        session=user.post('/api/player/login',json={'player_id':person['id']}).json()
        csrf={'X-Player-CSRF':session['csrf_token']}
        assert user.patch('/api/player/profile',headers=csrf,json={'avatar_color':'#123456'}).status_code==200
        photo=BytesIO();Image.new('RGB',(8,8),'green').save(photo,format='PNG')
        url=user.post('/api/player/profile/photo',headers=csrf,files={'file':('ok.png',photo.getvalue(),'image/png')}).json()['photo_url']
        assert user.post('/api/player/profile/photo',headers=csrf,files={'file':('bad.png',b'bad','image/png')}).status_code==422
        assert user.get('/api/player/profile').json()['photo_url']==url
        archive=admin.get('/api/admin/backup',headers=headers).content
        assert user.patch('/api/player/profile',headers=csrf,json={'avatar_color':'#ffffff'}).status_code==200
        restored=admin.post('/api/admin/restore?confirm=true',headers=headers,files={'file':('backup.zip',archive,'application/zip')})
        assert restored.status_code==200
        assert user.get('/api/player/profile').status_code==401
        user.post('/api/player/login',json={'player_id':person['id']})
        profile=user.get('/api/player/profile').json()
        assert profile['avatar_color']=='#123456' and profile['photo_url']==url

def test_admin_can_edit_and_delete_confirmed_friendly(app_factory):
    app = app_factory()
    with TestClient(app) as admin, TestClient(app) as a, TestClient(app) as b:
        headers = admin_headers(admin)
        _, people = tournament_with_players(admin, headers)
        tokens = []
        for client, person in zip((a, b), people):
            password = admin.post(f"/api/admin/players/{person['id']}/credential", headers=headers).json()['password']
            tokens.append({'X-Player-CSRF': client.post('/api/player/login', json={'player_id':person['id'],'password':password}).json()['csrf_token']})
        rid = a.post('/api/player/results', headers=tokens[0], json={'opponent_id':people[1]['id'],'winner_id':people[0]['id']}).json()['id']
        b.post(f'/api/player/results/{rid}/confirm', headers=tokens[1])
        assert admin.get('/api/admin/friendlies', headers=headers).status_code == 422
        response = admin.patch(f'/api/admin/friendlies/single/{rid}', headers=headers, json={'at':'2024-03-05T12:30:00Z','winner_id':people[1]['id']})
        assert response.status_code == 200
        listed = admin.get('/api/admin/friendlies?start=2024-03-01T00:00:00Z&end=2024-04-01T00:00:00Z', headers=headers).json()
        assert len(listed) == 1 and listed[0]['id'] == rid and listed[0]['winner'] == 'b'
        assert admin.get('/api/admin/friendlies?start=2024-02-01T00:00:00Z&end=2024-03-01T00:00:00Z', headers=headers).json() == []
        assert next(p for p in admin.get('/api/public/ranking').json() if p['id']==people[1]['id'])['wins'] == 1
        assert admin.delete(f'/api/admin/friendlies/single/{rid}', headers=headers).status_code == 200
        assert all(p['matches'] == 0 for p in admin.get('/api/public/ranking').json())
