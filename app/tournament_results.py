"""Tournament progression and winner-only result registration."""

from . import engine
from .db import Database, _dump
from .service import champion_id


def _bump(conn, tid, status=None, config=None, frozen=None, warnings=None, champ_marker=False):
    fields = ["revision=revision+1", "updated_at=CURRENT_TIMESTAMP"]
    params = []
    if status is not None:
        fields.append("status=?"); params.append(status)
    if config is not None:
        fields.append("config_json=?"); params.append(_dump(config))
    if frozen is not None:
        fields.append("frozen_config_json=?"); params.append(_dump(frozen) if frozen else None)
    if warnings is not None:
        fields.append("warnings_json=?"); params.append(_dump(warnings))
    if champ_marker:
        fields.append("champion_id=NULL")
    params.append(tid)
    conn.execute(f"UPDATE tournaments SET {', '.join(fields)} WHERE id=?", params)


def _persist_match_rows(conn, tid, matches):
    Database.save_matches(conn, tid, matches)


def _finalize_if_done(conn, tournament, entrants, matches):
    if tournament["status"] != "active":
        return tournament
    cfg = tournament["frozen_config"] or tournament["config"]
    final_round = max((m["round"] for m in matches), default=0)
    latest = [m for m in matches if m["round"] == final_round]
    completed = False
    champ = None
    reason = "rules_complete"
    if cfg["structure"] == "adapted":
        standings = engine.calculate_standings(cfg, entrants, matches)
        eligible = [row for row in standings if not row["eliminated"]]
    else:
        active_ids = {person["id"] for person in entrants if not person.get("withdrawn")}
        eligible = [person for person in entrants if person["id"] in active_ids]
    if not eligible:
        completed = True
        reason = "no_active_players"
    elif cfg["structure"] == "swiss":
        completed = final_round >= cfg["rounds"] and bool(latest) and all(m.get("result") for m in latest)
        if completed:
            champ = champion_id(cfg, entrants, matches)
    elif cfg["structure"] == "adapted":
        survivors = eligible
        completed = len(survivors) <= 1 and bool(latest) and all(m.get("result") for m in latest)
        champ = survivors[0]["player_id"] if len(survivors) == 1 else None
    elif cfg["structure"] == "single_elim":
        completed = len(latest) == 1 and bool(latest) and all(m.get("result") for m in latest)
        if completed:
            champ = champion_id(cfg, entrants, matches)
    else:
        completed = bool(matches) and all(m.get("result") for m in matches)
        if completed:
            rows = engine.calculate_standings(cfg, entrants, matches)
            champ = next((row["player_id"] for row in rows if row["player_id"] in {person["id"] for person in eligible}), None)
    if completed:
        conn.execute("UPDATE tournaments SET status='completed',champion_id=? WHERE id=?", (champ, tournament["id"]))
        Database.audit(conn, tournament["id"], "tournament_completed", {"champion_id": champ, "reason": reason})
        tournament["status"] = "completed"
        tournament["champion_id"] = champ
    return tournament


def _advance_completed_round(conn, tournament, entrants, matches):
    _finalize_if_done(conn, tournament, entrants, matches)
    cfg = tournament['frozen_config'] or tournament['config']
    if tournament['status'] != 'active' or cfg['structure'] == 'league' or not matches:
        return
    latest = max(m['round'] for m in matches)
    if any(not m.get('result') for m in matches if m['round'] == latest):
        return
    schedule = engine.make_round(cfg, entrants, matches, latest + 1)
    if not schedule['matches']:
        return
    new = [engine.apply_result(cfg, m, {'result':'bye','p1_games':0,'p2_games':0}) if m.get('bye') and not m.get('result') else m for m in schedule['matches']]
    matches.extend(new)
    _persist_match_rows(conn, tournament['id'], matches)
    _bump(conn, tournament['id'], warnings=tournament['warnings'] + schedule['warnings'])
    Database.audit(conn, tournament['id'], 'round_created', {'round':latest + 1, 'matches':len(new), 'warnings':schedule['warnings']})
    _advance_completed_round(conn, tournament, entrants, matches)


def record_tournament_winner(conn, tournament, matches, match, winner):
    tid = tournament['id']
    cfg = tournament['frozen_config'] or tournament['config']
    p1_won = winner == match['p1']
    target = 2 if cfg['match_length'] == 3 else 1
    finalized = engine.apply_result(cfg, match, {
        'result': 'p1_win' if p1_won else 'p2_win',
        'p1_games': target if p1_won else 0,
        'p2_games': 0 if p1_won else target,
    })
    if cfg['match_length'] != 1:
        finalized.update(p1_games=None, p2_games=None, score_unknown=True)
    matches[matches.index(match)] = finalized
    _persist_match_rows(conn, tid, matches)
    _bump(conn, tid)
    Database.audit(conn, tid, 'match_result_recorded', {
        'match_id': match['id'], 'round': match['round'], 'result': finalized['result'],
    })
    _advance_completed_round(conn, tournament, Database.get_entrants(conn, tid), matches)
