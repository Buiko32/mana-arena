import json
import sqlite3
import pytest
from fastapi import HTTPException
from app.life import Game, _save, _validate
from test_api import app_factory


def test_life_record_retry_validation_and_statistics(tmp_path, monkeypatch):
    monkeypatch.setenv("LIFE_STATS_DIR", str(tmp_path))
    raw = {
        "format": "duel", "session_id": "a7dd37e6-ecf7-4a70-bbae-cc42c28d491c",
        "started_at": "2026-11-19T12:00:00Z", "ended_at": "2026-11-19T12:02:00Z",
        "starting_life": 20,
        "players": [{"name": "Anna", "members": [{"id": "p1", "name": "Anna"}], "initial_life": 20, "final_life": 18}, {"name": "Luca", "members": [{"id": None, "name": "Luca"}], "initial_life": 30, "final_life": 30}],
        "changes": [{"at": "2026-11-19T12:01:00Z", "player": "Anna", "delta": -2, "life": 18}],
        "winner": "Luca",
    }
    game = Game.model_validate(raw)
    _validate(game)
    assert _save(game) is False
    assert _save(game) is True
    with sqlite3.connect(tmp_path / 'life-stats.sqlite3') as conn:
        payload = json.loads(conn.execute('SELECT payload FROM games').fetchone()[0])
        assert conn.execute('SELECT count(*) FROM games').fetchone()[0] == 1
    assert payload['statistics']['Anna']['lost'] == 2
    assert payload['players'][0]['members'][0]['id'] == 'p1'
    assert payload['changes'][0]['life'] == 18
    raw['winner'] = 'Anna'
    with pytest.raises(HTTPException) as exc:
        _save(Game.model_validate(raw))
    assert exc.value.status_code == 409
    raw['players'][0]['final_life'] = 19
    with pytest.raises(ValueError):
        _validate(Game.model_validate(raw))


def test_life_game_creates_one_proposal_even_after_deletion(app_factory):
    from fastapi.testclient import TestClient
    from test_api import admin_headers, tournament_with_players
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as player, TestClient(app) as opponent:
        headers=admin_headers(admin)
        _, people=tournament_with_players(admin,headers)
        login=player.post('/api/player/login',json={'player_id':people[0]['id']}).json()
        token={'X-Player-CSRF':login['csrf_token']}
        raw={'format':'duel','session_id':'7a86cbf9-4367-4104-bb28-91fa765a5bd6',
             'started_at':'2026-11-19T12:00:00Z','ended_at':'2026-11-19T12:02:00Z','starting_life':20,
             'players':[{'name':p['name'],'members':[{'id':p['id'],'name':p['name']}],'initial_life':20,'final_life':20} for p in people],
             'changes':[],'winner':people[1]['name'],
             'result_target':{'kind':'friendly','proposer_id':people[0]['id']}}
        assert player.post('/api/life/games',json=raw).json()['result_status']=='unauthorized'
        response=player.post('/api/life/games',json=raw,headers=token).json()
        assert response['duplicate'] and response['result_status']=='confirmed'
        assert player.post('/api/life/games',json=raw,headers=token).json()['result_id']==response['result_id']
        login=opponent.post('/api/player/login',json={'player_id':people[1]['id']}).json()
        assert opponent.post('/api/player/results/'+response['result_id']+'/confirm',headers={'X-Player-CSRF':login['csrf_token']}).status_code==409
        from app.history import month_matches
        with app.state.db.transaction() as conn:
            history=month_matches(conn,'2026-11-19 00:00:00','2026-11-20 00:00:00')
            assert history[0]['at']=='2026-11-19T12:02:00Z'
        assert next(p for p in admin.get('/api/public/ranking').json() if p['id']==people[1]['id'])['wins']==1
        assert admin.delete('/api/admin/friendlies/single/'+response['result_id'],headers=headers).status_code==200
        assert player.post('/api/life/games',json=raw,headers=token).json()['result_status']=='unavailable'
        with app.state.db.transaction() as conn:
            assert conn.execute('SELECT count(*) FROM result_proposals').fetchone()[0]==0


def test_life_team_registers_immediately(app_factory):
    from fastapi.testclient import TestClient
    from test_api import admin_headers, tournament_with_players
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as player:
        headers=admin_headers(admin)
        _, people=tournament_with_players(admin,headers)
        extra=admin.post('/api/admin/players',json={'name':'Terzo'},headers=headers).json()
        token={'X-Player-CSRF':player.post('/api/player/login',json={'player_id':people[0]['id']}).json()['csrf_token']}
        sides=[[people[0],extra],[people[1]]]
        body={'format':'team','session_id':'6507b73e-bd49-4ad5-b4c6-b03508816732',
              'started_at':'2026-11-19T12:00:00Z','ended_at':'2026-11-19T12:02:00Z','starting_life':20,
              'players':[{'name':f'Squadra {i}','members':[{'id':p['id'],'name':p['name']} for p in side],'initial_life':20,'final_life':20} for i,side in enumerate(sides)],
              'changes':[],'winner':'Squadra 1','result_target':{'kind':'friendly','proposer_id':people[0]['id']}}
        result=player.post('/api/life/games',json=body,headers=token).json()
        assert result['result_status']=='confirmed'
        assert player.get('/api/player/team-results').json()==[]


def test_life_unavailable_tournament_keeps_archive(app_factory):
    from fastapi.testclient import TestClient
    from test_api import admin_headers, tournament_with_players
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as player:
        headers=admin_headers(admin)
        tid, people=tournament_with_players(admin,headers)
        token={'X-Player-CSRF':player.post('/api/player/login',json={'player_id':people[0]['id']}).json()['csrf_token']}
        raw={'format':'duel','session_id':'355129bd-b617-43c0-91d3-e1713628df9d',
             'started_at':'2026-11-19T12:00:00Z','ended_at':'2026-11-19T12:02:00Z','starting_life':20,
             'players':[{'name':p['name'],'members':[{'id':p['id'],'name':p['name']}],'initial_life':20,'final_life':20} for p in people],
             'changes':[],'winner':people[0]['name'],
             'result_target':{'kind':'tournament','proposer_id':people[0]['id'],'tournament_id':tid,'match_id':'missing'}}
        result=player.post('/api/life/games',json=raw,headers=token).json()
        assert result['saved'] and result['result_status']=='unavailable'
        with app.state.db.transaction() as conn:
            assert conn.execute('SELECT count(*) FROM result_proposals').fetchone()[0]==0


def test_life_concurrent_retries_make_only_one_proposal(app_factory):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    from test_api import admin_headers, tournament_with_players
    app=app_factory()
    with TestClient(app) as admin, TestClient(app) as player:
        headers=admin_headers(admin); _,people=tournament_with_players(admin,headers)
        token={'X-Player-CSRF':player.post('/api/player/login',json={'player_id':people[0]['id']}).json()['csrf_token']}
        raw={'format':'duel','session_id':'a7ff46c2-80b1-49e9-8b7c-f8da27c47b41','started_at':'2026-11-18T12:00:00Z','ended_at':'2026-11-18T12:02:00Z','starting_life':20,
             'players':[{'name':p['name'],'members':[{'id':p['id'],'name':p['name']}],'initial_life':20,'final_life':20} for p in people],
             'changes':[],'winner':people[0]['name'],'result_target':{'kind':'friendly','proposer_id':people[0]['id']}}
        with ThreadPoolExecutor(max_workers=4) as pool:
            responses=list(pool.map(lambda _:player.post('/api/life/games',json=raw,headers=token),range(4)))
        assert all(r.status_code==200 for r in responses)
        assert len({r.json()['result_id'] for r in responses})==1
        with app.state.db.transaction() as conn:
            assert conn.execute('SELECT count(*) FROM result_proposals').fetchone()[0]==1


@pytest.mark.parametrize(('error','expected'), [
    (PermissionError(13, 'denied'), 'permessi'),
    (OSError(30, 'read only'), 'sola lettura'),
    (OSError(28, 'full'), 'spazio'),
    (FileNotFoundError(2, 'missing'), 'percorso'),
    (sqlite3.OperationalError('database is locked'), 'occupato'),
    (sqlite3.OperationalError('unable to open database file'), 'percorso'),
])
def test_life_storage_errors_offer_specific_recovery(error, expected):
    from app.life import _storage_error_message
    assert expected in _storage_error_message(error).lower()


def test_life_http_storage_error_logs_path_without_payload(app_factory, monkeypatch, caplog):
    from fastapi.testclient import TestClient
    from importlib import import_module
    life_module=import_module("app.life")
    app=app_factory()
    def deny(*args):
        raise PermissionError(13, 'denied')
    monkeypatch.setattr(life_module, '_save', deny)
    body={'format':'duel','session_id':'7a86cbf9-4367-4104-bb28-91fa765a5bd6',
          'started_at':'2026-11-19T12:00:00Z','ended_at':'2026-11-19T12:02:00Z','starting_life':20,
          'players':[{'name':n,'members':[{'name':n}],'initial_life':20,'final_life':20} for n in ['PrivateName','Other']],
          'changes':[],'winner':'Other'}
    with TestClient(app) as client:
        response=client.post('/api/life/games',json=body)
    assert response.status_code==503 and 'permessi' in response.json()['detail'].lower()
    assert 'life-stats.sqlite3' in caplog.text and 'errno=13' in caplog.text
    assert 'PrivateName' not in caplog.text
