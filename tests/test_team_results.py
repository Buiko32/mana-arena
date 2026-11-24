from contextlib import ExitStack
import pytest
from fastapi.testclient import TestClient
from test_api import app_factory, admin_headers, tournament_with_players


@pytest.mark.parametrize('team_size', [1, 2])
def test_team_friendly_registers_immediately_and_updates_each_elo_once(app_factory, team_size):
    app=app_factory()
    with ExitStack() as stack:
        admin=stack.enter_context(TestClient(app))
        clients=[stack.enter_context(TestClient(app)) for _ in range(2 * team_size)]
        headers=admin_headers(admin)
        _, people=tournament_with_players(admin,headers,count=2 * team_size)
        tokens=[]
        for client,person in zip(clients,people):
            pw=admin.post(f"/api/admin/players/{person['id']}/credential",headers=headers).json()['password']
            tokens.append({'X-Player-CSRF':client.post('/api/player/login',json={'player_id':person['id'],'password':pw}).json()['csrf_token']})
        ids=[p['id'] for p in people]
        body={'team_a':ids[:team_size],'team_b':ids[team_size:],'winner':'a'}
        response=clients[0].post('/api/player/team-results',json=body,headers=tokens[0]); assert response.status_code==201, response.text
        rid=response.json()['id']
        confirm = f'/api/player/team-results/{rid}/confirm'
        assert clients[0].post(confirm, headers=tokens[0]).status_code == 403
        for index in range(team_size, 2 * team_size):
            assert clients[index].post(confirm, headers=tokens[index]).status_code == 409
        ranks = {p['id']: p for p in admin.get('/api/public/ranking').json()}
        assert [ranks[i]['rating'] for i in ids] == [1520] * team_size + [1480] * team_size
        assert [ranks[i]['matches'] for i in ids] == [1] * len(ids)

def test_team_validation_rejection_and_backup_roundtrip(app_factory):
    app=app_factory()
    with ExitStack() as stack:
        admin=stack.enter_context(TestClient(app));clients=[stack.enter_context(TestClient(app)) for _ in range(4)]
        headers=admin_headers(admin);_,people=tournament_with_players(admin,headers,count=4);ids=[p['id'] for p in people];tokens=[]
        for client,person in zip(clients,people):
            pw=admin.post(f"/api/admin/players/{person['id']}/credential",headers=headers).json()['password']
            tokens.append({'X-Player-CSRF':client.post('/api/player/login',json={'player_id':person['id'],'password':pw}).json()['csrf_token']})
        assert clients[0].post('/api/player/team-results',headers=tokens[0],json={'team_a':ids[:2],'team_b':[ids[1],ids[2]],'winner':'a'}).status_code==422
        rid=clients[0].post('/api/player/team-results',headers=tokens[0],json={'team_a':ids[:2],'team_b':ids[2:],'winner':'b'}).json()['id']
        assert len(clients[2].get('/api/player/team-results').json())==0
        assert clients[2].post(f'/api/player/team-results/{rid}/reject',headers=tokens[2]).status_code==409
        assert clients[3].post(f'/api/player/team-results/{rid}/confirm',headers=tokens[3]).status_code==409
        assert all(row['matches']==1 for row in admin.get('/api/public/ranking').json())
        backup=admin.get('/api/admin/backup',headers=headers)
        assert backup.status_code==200
        restored=admin.post('/api/admin/restore?confirm=true',headers=headers,files={'file':('backup.zip',backup.content,'application/zip')})
        assert restored.status_code==200, restored.text
