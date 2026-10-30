/* Pure match-to-graph models. This module has no DOM or API side effects. */
(function publishBracketModels(root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  if (root) root.ManaBrackets = api;
})(typeof globalThis === 'undefined' ? this : globalThis, function createBracketModels() {
  'use strict';

  const MAX_ROUND_WINDOW = 4;

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, character => ({
      '&': '&amp;',
      '<': '&lt;',
      '>': '&gt;',
      '"': '&quot;',
      "'": '&#39;',
    })[character]);
  }

  function capacityFor(entrantCount) {
    if (entrantCount < 2) return 0;
    return 2 ** Math.ceil(Math.log2(entrantCount));
  }

  function layoutEliminationBracket(capacity, cardHeights = [], {
    gap = 18,
    headerHeight = 64,
    bottomPadding = 24,
  } = {}) {
    const totalRounds = Math.log2(capacity);
    if (!Number.isInteger(capacity) || capacity < 2 || !Number.isInteger(totalRounds)) {
      return { capacity: 0, totalRounds: 0, cardHeight: 0, gap, headerHeight, bottomPadding, height: 0, positions: [] };
    }

    const heights = Array.isArray(cardHeights) ? cardHeights : [cardHeights];
    const cardHeight = Math.max(0, ...heights.map(height => Number.isFinite(height) ? height : 0));
    const rowHeight = cardHeight + Math.max(0, gap);
    const positions = [];
    for (let round = 1; round <= totalRounds; round += 1) {
      const matchCount = capacity / (2 ** round);
      const block = 2 ** (round - 1);
      for (let slot = 0; slot < matchCount; slot += 1) {
        const center = headerHeight + (slot * block + block / 2) * rowHeight;
        positions.push({ round, slot, key: `r${round}s${slot}`, center, top: center - cardHeight / 2 });
      }
    }

    const lastCardBottom = positions.reduce((bottom, position) => Math.max(bottom, position.top + cardHeight), 0);
    return {
      capacity,
      totalRounds,
      cardHeight,
      gap: Math.max(0, gap),
      headerHeight,
      bottomPadding,
      height: Math.ceil(lastCardBottom + bottomPadding),
      positions,
    };
  }

  function matchWinner(match) {
    if (!match || match.result === 'void' || match.result === 'draw') return null;
    if (match.result === 'p1_win' || match.result === 'p2_forfeit' || match.result === 'bye') {
      return match.p1 ?? null;
    }
    if (match.result === 'p2_win' || match.result === 'p1_forfeit') return match.p2 ?? null;
    return null;
  }

  function makeParticipant(playerId, people) {
    if (playerId == null) return null;
    const player = people.get(playerId);
    return player ? { ...player } : { id: playerId, name: null, photo_url: null, seed: null };
  }

  function matchStatus(match) {
    if (!match) return 'pending';
    if (match.result === 'void' || match.void) return 'void';
    if (match.bye || match.result === 'bye') return 'bye';
    if (match.result === 'p1_forfeit') return 'forfeit-p1';
    if (match.result === 'p2_forfeit') return 'forfeit-p2';
    if (match.result) return 'complete';
    return 'pending';
  }

  function buildEliminationBracket(entrants = [], matches = []) {
    const people = new Map(entrants.map(player => [player.id, player]));
    const capacity = capacityFor(entrants.length);
    if (!capacity) return { capacity, totalRounds: 0, rounds: [], edges: [] };

    const totalRounds = Math.log2(capacity);
    const byRoundAndSlot = new Map();
    for (const match of matches) {
      if (!Number.isInteger(match.round) || !Number.isInteger(match.bracket_slot)) continue;
      byRoundAndSlot.set(`${match.round}:${match.bracket_slot}`, match);
    }

    const rounds = [];
    const edges = [];
    for (let round = 1; round <= totalRounds; round += 1) {
      const matchCount = capacity / (2 ** round);
      const column = { round, matches: [] };
      for (let slot = 0; slot < matchCount; slot += 1) {
        const apiMatch = byRoundAndSlot.get(`${round}:${slot}`) ?? null;
        const placeholder = apiMatch === null;
        const feeders = round === 1 ? [] : [
          { round: round - 1, slot: slot * 2 },
          { round: round - 1, slot: slot * 2 + 1 },
        ];
        column.matches.push({
          round,
          slot,
          key: `r${round}s${slot}`,
          matchId: apiMatch?.id ?? null,
          apiMatch,
          p1: makeParticipant(apiMatch?.p1, people),
          p2: makeParticipant(apiMatch?.p2, people),
          p1Games: apiMatch?.p1_games ?? 0,
          p2Games: apiMatch?.p2_games ?? 0,
          result: apiMatch?.result ?? null,
          status: matchStatus(apiMatch),
          winnerId: matchWinner(apiMatch),
          placeholder,
          feeders,
        });
        if (round > 1) {
          for (const source of feeders) edges.push({ from: source, to: { round, slot } });
        }
      }
      rounds.push(column);
    }

    return { capacity, totalRounds, rounds, edges };
  }

  function progressionStatus(match, selectedPlayerId) {
    if (!match || match.result === 'void' || match.void) return 'void';
    if (match.bye || match.result === 'bye') return 'bye';
    if (!match.result) return 'pending';
    if (match.result === 'draw') return 'draw';
    const winner = matchWinner(match);
    return winner === selectedPlayerId ? 'won' : 'lost';
  }

  function buildRoundProgression(roundGroups = [], entrants = [], {
    selectedPlayerId = null,
    offset = 0,
    windowSize = MAX_ROUND_WINDOW,
  } = {}) {
    const people = new Map(entrants.map(player => [player.id, player]));
    const orderedRounds = [...roundGroups].sort((left, right) => left.round - right.round);
    const totalRounds = orderedRounds.length;
    const boundedSize = Math.max(1, Math.min(MAX_ROUND_WINDOW, Math.floor(windowSize) || MAX_ROUND_WINDOW));
    const maxOffset = Math.max(0, totalRounds - boundedSize);
    const boundedOffset = Math.max(0, Math.min(maxOffset, Math.floor(offset) || 0));
    const visible = orderedRounds.slice(boundedOffset, boundedOffset + boundedSize);
    const rounds = visible.map(round => ({
      round: round.round,
      matches: (round.matches || []).map(match => {
        const participantIds = [match.p1, match.p2].filter(id => id != null);
        const containsSelectedPlayer = selectedPlayerId != null && participantIds.includes(selectedPlayerId);
        return {
          round: round.round,
          slot: match.bracket_slot ?? match.table ?? null,
          key: `match-${match.id}`,
          matchId: match.id ?? null,
          apiMatch: match,
          p1: makeParticipant(match.p1, people),
          p2: makeParticipant(match.p2, people),
          p1Games: match.p1_games ?? 0,
          p2Games: match.p2_games ?? 0,
          result: match.result ?? null,
          status: matchStatus(match),
          winnerId: matchWinner(match),
          containsSelectedPlayer,
          selectedPlayerOutcome: containsSelectedPlayer ? progressionStatus(match, selectedPlayerId) : null,
        };
      }),
    }));

    const selectedMatches = rounds.flatMap(round => round.matches)
      .filter(match => match.containsSelectedPlayer && match.matchId != null);
    const pathEdges = [];
    for (let index = 1; index < selectedMatches.length; index += 1) {
      const previous = selectedMatches[index - 1];
      const current = selectedMatches[index];
      if (current.round === previous.round + 1) {
        pathEdges.push({ from: previous.matchId, to: current.matchId });
      }
    }

    const endOffset = boundedOffset + visible.length;
    return {
      rounds,
      totalRounds,
      offset: boundedOffset,
      windowSize: boundedSize,
      previousOffset: boundedOffset > 0 ? Math.max(0, boundedOffset - boundedSize) : null,
      nextOffset: endOffset < totalRounds ? endOffset : null,
      path: {
        matchIds: selectedMatches.map(match => match.matchId),
        lossMatchIds: selectedMatches
          .filter(match => match.selectedPlayerOutcome === 'lost')
          .map(match => match.matchId),
        edges: pathEdges,
      },
      edges: [],
    };
  }

  return {
    MAX_ROUND_WINDOW,
    buildEliminationBracket,
    layoutEliminationBracket,
    buildRoundProgression,
    escapeHtml,
  };
});
