"""Mana bets and settlement against official results."""
import json
from datetime import datetime, timezone

from .db import Database


def quote(probability):
    return min(300, max(105, 9000 // probability))


def tournament_market(conn, tournament_id, match_id):
    row = conn.execute("""
        SELECT m.data_json, t.status, t.config_json FROM matches m
        JOIN tournaments t ON t.id=m.tournament_id
        WHERE m.id=? AND m.tournament_id=?
    """, (match_id, tournament_id)).fetchone()
    if not row:
        return None
    match = json.loads(row["data_json"])
    match.update(status=row["status"], tournament_name=json.loads(row["config_json"])["name"])
    return match


def settlement(conn, bet):
    if bet["tournament_id"]:
        match = tournament_market(conn, bet["tournament_id"], bet["match_id"])
        if not match or match.get("void") or match.get("bye") or [match.get("p1"), match.get("p2")] != [bet["p1"], bet["p2"]]:
            return "refunded"
        outcome = match.get("result")
        if outcome in ("p1_win", "p2_win"):
            winner = bet["p1"] if outcome == "p1_win" else bet["p2"]
            return "won" if winner == bet["selection"] else "lost"
        if outcome or match["status"] != "active":
            return "refunded"
        return "pending"
    result = conn.execute("""
        SELECT l.kind, r.proposer_id, r.opponent_id, r.winner_id, r.status
        FROM life_result_links l LEFT JOIN result_proposals r ON r.id=l.result_id
        WHERE l.session_id=?
    """, (bet["match_id"],)).fetchone()
    if result:
        if result["kind"] == "single" and result["status"] == "confirmed" and (
            {result["proposer_id"], result["opponent_id"]} == {bet["p1"], bet["p2"]}
        ):
            return "won" if result["winner_id"] == bet["selection"] else "lost"
        return "refunded"
    if bet["created_at"] <= int(datetime.now(timezone.utc).timestamp()) - 86400:
        return "refunded"
    return "pending"


def settle_mana_bets(conn):
    for bet in conn.execute("SELECT * FROM mana_bets WHERE status='pending'").fetchall():
        status = settlement(conn, bet)
        if status != "pending":
            conn.execute("UPDATE mana_bets SET status=? WHERE id=?", (status, bet["id"]))


def balance_changes(conn):
    settle_mana_bets(conn)
    rows = conn.execute("""
        SELECT bettor, SUM(CASE status WHEN 'refunded' THEN 0
            WHEN 'won' THEN amount * multiplier / 100 - amount ELSE -amount END) AS delta
        FROM mana_bets GROUP BY bettor
    """)
    return {row["bettor"]: row["delta"] for row in rows}


def public_market(conn, market, ids, prediction, viewer):
    quotes = [quote(chance) for chance in prediction]
    totals = dict(conn.execute("""
        SELECT selection, SUM(amount) FROM mana_bets WHERE market=? AND status!='refunded'
        GROUP BY selection
    """, (market,)))
    own = conn.execute("SELECT selection, amount, multiplier, status FROM mana_bets WHERE market=? AND bettor=? AND status!='refunded' ORDER BY id DESC LIMIT 1",
                       (market, viewer)).fetchone()
    return {"id": market, "players": ids, "quotes": quotes, "totals": [totals.get(pid, 0) for pid in ids],
            "own": dict(own) if own else None, "participant": viewer in ids}
