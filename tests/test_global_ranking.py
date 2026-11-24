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


def test_database_ranking_replays_all_game_types_by_shared_dates(tmp_path):
    from app.db import Database
    from app.service import global_elo_from_db, calculate_global_elo
    from app.history import month_matches
    db = Database(tmp_path)
    db.initialize()
    with db.transaction() as conn:
        for pid in 'abcd':
            conn.execute('INSERT INTO players(id,name) VALUES (?,?)',(pid,pid))
        conn.execute("INSERT INTO tournaments(id,config_json,status,created_at) VALUES ('t',?, 'active','2024-01-01 00:00:00')", ('{"name":"T"}',))
        conn.execute("INSERT INTO matches(id,tournament_id,round_no,table_no,data_json) VALUES ('m','t',1,1,?)", ('{"p1":"a","p2":"b","result":"p1_win"}',))
        conn.execute("INSERT INTO audit(tournament_id,action,detail_json,created_at) VALUES ('t','match_result_recorded',?, '2024-01-02 12:00:00')", ('{"match_id":"m"}',))
        conn.execute("INSERT INTO result_proposals(id,proposer_id,opponent_id,winner_id,status,created_at) VALUES ('f','a','c','c','confirmed','2024-01-03 12:00:00')")
        conn.execute("INSERT INTO team_results(id,proposer_id,winning_team,status,created_at) VALUES ('g','a','b','confirmed','2024-01-01 12:00:00')")
        for pid, team in [('a','a'),('b','a'),('c','b'),('d','b')]:
            conn.execute('INSERT INTO team_result_players(result_id,player_id,team) VALUES (?,?,?)',('g',pid,team))
        actual={r['id']:r['rating'] for r in global_elo_from_db(conn)}
        expected=calculate_global_elo([{'id':p,'name':p} for p in 'abcd'],[
            {'team_a':['a','b'],'team_b':['c','d'],'winner':'b'},
            {'p1':'a','p2':'b','result':'p1_win'}, {'p1':'a','p2':'c','result':'p2_win'}])
        assert actual == {r['id']:r['rating'] for r in expected}
        from app.service import player_statistics
        records = player_statistics(conn, 'a')['head_to_head']
        assert [(row['opponent']['id'], row['wins'], row['losses']) for row in records] == [('b', 1, 0), ('c', 0, 1)]
        history=month_matches(conn,'2024-01-01 00:00:00','2024-02-01 00:00:00')
        assert [g['at'] for g in history] == ['2024-01-03T12:00:00Z','2024-01-02T12:00:00Z','2024-01-01T12:00:00Z']

def test_player_statistics_track_rank_changes_and_only_count_confirmed_games(tmp_path):
    from app.db import Database
    from app.service import global_elo_from_db, player_statistics

    db = Database(tmp_path)
    db.initialize()
    with db.transaction() as conn:
        for pid in 'abcd':
            conn.execute('INSERT INTO players(id,name) VALUES (?,?)', (pid, pid))
        games = [
            ('1', 'b', 'c', 'b', 'confirmed', '2026-01-01 12:00:00'),
            ('2', 'a', 'd', 'a', 'confirmed', '2026-01-02 12:00:00'),
            ('3', 'b', 'd', 'b', 'confirmed', '2026-01-03 12:00:00'),
            ('4', 'a', 'b', 'a', 'confirmed', '2026-01-04 12:00:00'),
            ('5', 'a', 'c', 'c', 'pending', '2026-01-05 12:00:00'),
        ]
        conn.executemany("INSERT INTO result_proposals(id,proposer_id,opponent_id,winner_id,status,created_at) VALUES (?,?,?,?,?,?)", games)
        stats = player_statistics(conn, 'a')
        points = stats['history']
        assert [point['rank'] for point in points] == [1, 2, 1]
        assert points[0]['rating'] == points[1]['rating'] == 1520
        assert stats['recent_form'] == ['win', 'win']
        assert stats['recent_results'][0]['elo_delta'] == 20
        assert stats['recent_results'][0]['date'] == '2026-01-02 12:00:00'
        assert stats['recent_results'][0]['opponents'][0]['name'] == 'd'
        assert stats['win_streak'] == stats['longest_win_streak'] == 2
        assert stats['best_win']['opponents'] == ['b']
        assert stats['best_win']['gap'] > 0
        assert stats['peak_rating'] == points[-1]['rating']
        assert stats['player']['rating'] == global_elo_from_db(conn)[0]['rating']
        assert player_statistics(conn, 'missing') is None


def test_ranking_movement_uses_latest_played_day_in_rome(tmp_path):
    from app.db import Database
    from app.service import global_elo_from_db, player_statistics

    db = Database(tmp_path)
    db.initialize()
    with db.transaction() as conn:
        for pid in 'abcde':
            conn.execute('INSERT INTO players(id,name) VALUES (?,?)', (pid, pid))
        conn.execute("UPDATE players SET photo='abcd.jpg' WHERE id='b'")
        games = [
            ('1', 'a', 'b', 'b', 'confirmed', '2026-07-01 22:30:00'),
            ('2', 'a', 'b', 'b', 'confirmed', '2026-07-02 12:00:00'),
            ('3', 'c', 'd', 'c', 'confirmed', '2026-07-05 12:00:00'),
            ('4', 'c', 'd', 'c', 'confirmed', '2026-07-05 13:00:00'),
            ('5', 'c', 'd', 'c', 'confirmed', '2026-07-05 14:00:00'),
            ('6', 'a', 'e', 'e', 'pending', '2026-07-06 12:00:00'),
        ]
        conn.executemany("INSERT INTO result_proposals(id,proposer_id,opponent_id,winner_id,status,created_at) VALUES (?,?,?,?,?,?)", games)
        ranking = {row['id']: row for row in global_elo_from_db(conn, include_movement=True)}

        assert ranking['b']['rank'] == 2
        assert ranking['b']['movement'] == {'date': '2026-07-02', 'rank': 1, 'rating': 38}
        assert ranking['a']['movement'] == {'date': '2026-07-02', 'rank': -4, 'rating': -38}
        assert ranking['c']['movement']['date'] == '2026-07-05'
        assert ranking['e']['movement'] is None
        stats = player_statistics(conn, 'a')
        assert stats['recent_results'][1]['result'] == 'loss'
        assert [match['elo_delta'] for match in stats['recent_results']] == [-20, -18]
        assert stats['recent_results'][1]['opponents'][0]['photo_url'] == '/photos/abcd.jpg'
        assert [(row['opponent']['id'], row['wins'], row['losses']) for row in stats['head_to_head']] == [('b', 0, 2)]
