import sqlite3

from app.db import Database
from app.service import ManaFragments, global_elo_from_db, player_statistics


def test_fragments_reward_wins_repeatable_streaks_and_completed_tournaments():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE tournaments (champion_id, status, archived_from)")
        conn.executemany("INSERT INTO tournaments VALUES (?, ?, ?)", [
            ("a", "completed", None),
            ("a", "archived", "completed"),
            ("b", "active", None),
        ])
        fragments = ManaFragments(conn)
        player = {"id": "a"}
        for won in [True] * 6 + [False] + [True] * 3:
            fragments.record({}, [(player, 0, won)])

        assert fragments.balance("a") == 118  # 18 wins, 40 streak, 60 tournament
        assert fragments.balance("b") == 0

        fragments.record({"tournament_match": True}, [(player, 0, False)])
        fragments.record({"tournament_match": True}, [(player, 0, True)])
        fragments.record({}, [(player, 0, True)])
        fragments.record({}, [(player, 0, True)])
        assert fragments.balance("a") == 144  # +6 wins and the five-win milestone


def test_ranking_and_profile_replay_the_same_confirmed_rewards(tmp_path):
    db = Database(tmp_path)
    db.initialize()
    with db.transaction() as conn:
        conn.executemany("INSERT INTO players (id, name) VALUES (?, ?)", [
            ("a", "Anna"), ("b", "Bruno"), ("c", "Carlo"),
        ])
        conn.executemany("""
            INSERT INTO result_proposals (id, proposer_id, opponent_id, winner_id, status, created_at)
            VALUES (?, 'a', 'b', 'a', ?, ?)
        """, [
            ("duel", "confirmed", "2026-11-25 16:00:00"),
            ("pending", "pending", "2026-11-25 16:01:00"),
        ])
        conn.execute("""
            INSERT INTO team_results (id, proposer_id, winning_team, status, created_at)
            VALUES ('team', 'a', 'a', 'confirmed', '2026-11-25 16:02:00')
        """)
        conn.executemany("""
            INSERT INTO team_result_players (result_id, player_id, team) VALUES ('team', ?, ?)
        """, [("a", "a"), ("b", "b")])
        conn.execute("""
            INSERT INTO ffa_results (id, placements, created_at)
            VALUES ('ffa', '["a","b","c"]', '2026-11-25 16:03:00')
        """)
        for _ in range(2):
            ranking = global_elo_from_db(conn, include_movement=True)
            assert {row["id"]: row["mana_fragments"] for row in ranking} == {"a": 16, "b": 0, "c": 0}
            assert player_statistics(conn, "a")["player"]["mana_fragments"] == 16
