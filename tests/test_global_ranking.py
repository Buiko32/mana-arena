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
    assert rows["a"]["matches"] == 1 and rows["b"]["matches"] == 1
    assert rows["a"]["wins"] == 1 and rows["b"]["draws"] == 0
    assert rows["a"]["rating"] == 1520 and rows["b"]["rating"] == 1480
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
    games = [{"p1":"a", "p2":"b", "result":"p1_win" if i % 2 == 0 else "p2_win"} for i in range(30)] + [{"p1":"a", "p2":"b", "result":"p1_win"}]
    after = {r["id"]: r for r in calculate_global_elo(people, games)}
    assert after["a"]["rating"] == 1500 and after["b"]["rating"] == 1500


def test_draws_do_not_affect_games_ratings_or_k_factor():
    players = [{"id":"a", "name":"Ada"}, {"id":"b", "name":"Bruno"}]
    win = {"p1":"a", "p2":"b", "result":"p1_win"}
    draw = {"p1":"a", "p2":"b", "result":"draw"}
    assert calculate_global_elo(players, [draw] * 50 + [win]) == calculate_global_elo(players, [win])


def test_team_elo_rewards_outnumbered_players_and_limits_loss():
    players=[{'id':str(i),'name':str(i)} for i in range(5)]
    game={'team_a':['0','1'],'team_b':['2','3','4'],'winner':'a'}
    ranks={p['id']:p for p in calculate_global_elo(players,[game])}
    assert [ranks[str(i)]['rating'] for i in range(5)]==[1528,1528,1472,1472,1472]
    game['winner']='b'
    ranks={p['id']:p for p in calculate_global_elo(players,[game])}
    assert [ranks[str(i)]['rating'] for i in range(5)]==[1488,1488,1512,1512,1512]


def test_team_elo_uses_each_players_individual_rating():
    players=[{'id':str(i),'name':str(i)} for i in range(4)]
    warmup=[{'p1':'0','p2':'1','result':'p1_win'}]
    before={p['id']:p for p in calculate_global_elo(players,warmup)}
    game={'team_a':['0','1'],'team_b':['2','3'],'winner':'a'}
    after={p['id']:p for p in calculate_global_elo(players,warmup+[game])}
    assert after['1']['rating']-before['1']['rating'] > after['0']['rating']-before['0']['rating']
    assert after['0']['rating']>before['0']['rating'] and after['1']['rating']>before['1']['rating']


def test_team_elo_lower_rated_player_loses_less_and_orientation_is_symmetric():
    players=[{'id':str(i),'name':str(i)} for i in range(5)]
    warmup=[{'p1':'0','p2':'1','result':'p1_win'}]
    before={p['id']:p for p in calculate_global_elo(players,warmup)}
    game={'team_a':['0','1'],'team_b':['2','3','4'],'winner':'b'}
    after={p['id']:p for p in calculate_global_elo(players,warmup+[game])}
    assert before['1']['rating']-after['1']['rating'] < before['0']['rating']-after['0']['rating']
    reversed_game={'team_a':game['team_b'],'team_b':game['team_a'],'winner':'a'}
    assert calculate_global_elo(players,warmup+[game])==calculate_global_elo(players,warmup+[reversed_game])
