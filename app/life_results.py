"""Bridge a persisted life session to the player result registration."""
import hashlib
import json
import secrets
import uuid
from datetime import timezone

from .db import Database, MUTATION_LOCK
from .service import elo_snapshot, elo_changes
from .tournament_results import record_tournament_winner


def submit_result(db, game, cookie, csrf):
    target = game.result_target
    if not target:
        return {'result_status': 'stats_only'}
    digest = hashlib.sha256(cookie.encode()).hexdigest()
    with MUTATION_LOCK, db.transaction() as conn:
        session = conn.execute("SELECT s.* FROM player_sessions s JOIN players p ON p.id=s.player_id WHERE s.token_hash=? AND s.expires_at>strftime('%s','now') AND p.active=1", (digest,)).fetchone()
        if not session or session['player_id'] != target.proposer_id or not secrets.compare_digest(csrf, session['csrf']):
            return {'result_status': 'unauthorized'}
        previous = conn.execute('SELECT kind,result_id FROM life_result_links WHERE session_id=?', (str(game.session_id),)).fetchone()
        if previous:
            table = 'team_results' if previous['kind'] == 'team' else 'result_proposals'
            row = conn.execute(f'SELECT status FROM {table} WHERE id=?', (previous['result_id'],)).fetchone() if previous['result_id'] else None
            if not row or row['status'] not in ('pending', 'confirmed'):
                return {'result_status':'unavailable'}
            return {'result_status':row['status'], 'result_id':previous['result_id']}
        def unavailable():
            conn.execute('INSERT INTO life_result_links VALUES (?,?,NULL)', (str(game.session_id),'none'))
            return {'result_status':'unavailable'}
        sides = [[member.id for member in side.members] for side in game.players]
        if game.format not in ('duel','team') or game.winner is None or len(sides)!=2 or any(not pid for side in sides for pid in side):
            return unavailable()
        ids = sides[0]+sides[1]
        if len(set(ids))!=len(ids) or any(not conn.execute('SELECT 1 FROM players WHERE id=? AND active=1',(pid,)).fetchone() for pid in ids):
            return unavailable()
        if target.proposer_id not in ids:
            return unavailable()
        winner_index = next(i for i, side in enumerate(game.players) if side.name == game.winner)
        if target.kind == 'tournament':
            if game.format != 'duel' or not target.tournament_id or not target.match_id:
                return unavailable()
            tournament = Database.get_tournament(conn,target.tournament_id)
            if not tournament or tournament['status'] != 'active': return unavailable()
            matches = Database.get_matches(conn,target.tournament_id)
            pending = [m['round'] for m in matches if not m.get('result') and not m.get('void')]
            current = min(pending) if pending else None
            match = next((m for m in matches if m['id']==target.match_id and m['round']==current),None)
            if not match or match.get('result') or match.get('void') or match.get('bye') or {match.get('p1'),match.get('p2')} != set(ids): return unavailable()
        elif target.kind != 'friendly':
            return unavailable()
        first = 0 if target.proposer_id in sides[0] else 1
        rid = str(uuid.uuid4())
        played_at = game.ended_at.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        if game.format == 'team' and (target.kind != 'friendly' or any(not 1 <= len(side) <= 3 for side in sides)):
            return unavailable()
        before = elo_snapshot(conn, ids)
        if game.format == 'team':
            conn.execute("INSERT INTO team_results(id,proposer_id,winning_team,created_at,status) VALUES(?,?,?,?,'confirmed')",(rid,target.proposer_id,'a' if winner_index==first else 'b',played_at))
            conn.executemany('INSERT INTO team_result_players(result_id,player_id,team) VALUES(?,?,?)',[(rid,pid,'a' if index==first else 'b') for index,side in enumerate(sides) for pid in side])
            Database.audit(conn, None, 'team_friendly_result_recorded', {'match_id': rid})
            kind='team'
        else:
            opponent = sides[1-first][0]
            winner = sides[winner_index][0]
            tid = target.tournament_id if target.kind == 'tournament' else None
            mid = target.match_id if tid else None
            conn.execute("INSERT INTO result_proposals(id,proposer_id,opponent_id,tournament_id,match_id,winner_id,created_at,status) VALUES(?,?,?,?,?,?,?,'confirmed')",
                         (rid,target.proposer_id,opponent,tid,mid,winner,played_at))
            if tid:
                conn.execute("UPDATE result_proposals SET status='invalidated' WHERE tournament_id=? AND match_id=? AND status='pending'", (tid, mid))
                record_tournament_winner(conn, tournament, matches, match, winner)
            else:
                Database.audit(conn, None, 'friendly_result_recorded', {'match_id': rid})
            kind='single'
        conn.execute('INSERT INTO life_result_links VALUES (?,?,?)',(str(game.session_id),kind,rid))
        return {'result_status':'confirmed','result_id':rid,'elo_changes':elo_changes(conn, before)}
