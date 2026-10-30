const test = require('node:test');
const assert = require('node:assert/strict');
const {
  buildEliminationBracket,
  layoutEliminationBracket,
  buildRoundProgression,
  escapeHtml,
} = require('../app/static/brackets.js');

function entrants(count) {
  return Array.from({ length: count }, (_, index) => ({
    id: `p${index + 1}`,
    name: `Player ${index + 1}`,
    seed: index + 1,
  }));
}

function firstRoundMatches(count, overrides = []) {
  const capacity = 2 ** Math.ceil(Math.log2(count));
  const slotCount = capacity / 2;
  const base = Array.from({ length: slotCount }, (_, slot) => ({
    id: `round1-${slot}`,
    round: 1,
    table: slot + 1,
    bracket_slot: slot,
    p1: `p${slot * 2 + 1}`,
    p2: `p${slot * 2 + 2}`,
    p1_games: 1,
    p2_games: 0,
    result: null,
    bye: false,
  }));
  for (const override of overrides) {
    base[override.bracket_slot] = { ...base[override.bracket_slot], ...override };
  }
  return base;
}

test('single-elimination model builds 2, 4, 6 and 8 entrant brackets', () => {
  for (const count of [2, 4, 6, 8]) {
    const model = buildEliminationBracket(entrants(count), firstRoundMatches(count));
    const capacity = 2 ** Math.ceil(Math.log2(count));
    assert.equal(model.capacity, capacity);
    assert.equal(model.rounds.length, Math.ceil(Math.log2(count)));
    assert.equal(model.rounds[0].matches.length, capacity / 2);
    assert.equal(model.rounds.at(-1).matches.length, 1);
  }
});

test('zero bracket slot selects the real opening match and preserves its ID', () => {
  const first = firstRoundMatches(4, [{ bracket_slot: 0, id: 'real-zero', result: 'p1_win' }]);
  const model = buildEliminationBracket(entrants(4), first);
  assert.equal(model.rounds[0].matches[0].matchId, 'real-zero');
  assert.equal(model.rounds[0].matches[0].slot, 0);
  assert.equal(model.rounds[0].matches[0].winnerId, 'p1');
});

test('seeded opening byes and voids stay distinct; future cards keep both feeder edges', () => {
  const first = firstRoundMatches(5, [
    { bracket_slot: 0, result: 'bye', bye: true, p2: null, p1: 'p1', p1_games: 0, p2_games: 0 },
    { bracket_slot: 1, result: 'void', p1: null, p2: null },
  ]);
  const model = buildEliminationBracket(entrants(5), first);
  assert.equal(model.rounds[0].matches[0].status, 'bye');
  assert.equal(model.rounds[0].matches[1].status, 'void');
  const future = model.rounds[1].matches[0];
  assert.equal(future.placeholder, true);
  assert.deepEqual(future.feeders, [{ round: 1, slot: 0 }, { round: 1, slot: 1 }]);
  assert.deepEqual(model.edges.filter(edge => edge.to.round === 2 && edge.to.slot === 0), [
    { from: { round: 1, slot: 0 }, to: { round: 2, slot: 0 } },
    { from: { round: 1, slot: 1 }, to: { round: 2, slot: 0 } },
  ]);
  assert.equal(model.rounds[1].matches[0].matchId, null);
});

test('withdrawal voids retain fixed topology through later placeholders', () => {
  const first = firstRoundMatches(6, [{ bracket_slot: 0, result: 'void', p1: null, p2: null }]);
  const second = [
    { id: 'round2-zero', round: 2, bracket_slot: 0, p1: null, p2: null, result: 'void' },
    { id: 'round2-one', round: 2, bracket_slot: 1, p1: 'p5', p2: 'p6', result: null },
  ];
  const model = buildEliminationBracket(entrants(6), [...first, ...second]);
  assert.equal(model.rounds[1].matches[0].status, 'void');
  assert.equal(model.rounds[2].matches[0].placeholder, true);
  assert.deepEqual(model.rounds[2].matches[0].feeders, [{ round: 2, slot: 0 }, { round: 2, slot: 1 }]);
});

test('non-elimination progression uses only actual rounds and traces a selected player', () => {
  const people = entrants(4);
  const rounds = [
    { round: 1, matches: [{ id: 'r1-a', p1: 'p1', p2: 'p2', result: 'p1_win', p1_games: 1, p2_games: 0 }] },
    { round: 2, matches: [{ id: 'r2-a', p1: 'p3', p2: 'p1', result: 'p3_win', p1_games: 1, p2_games: 0 }] },
  ];
  const model = buildRoundProgression(rounds, people, { selectedPlayerId: 'p1' });
  assert.deepEqual(model.rounds.map(round => round.round), [1, 2]);
  assert.deepEqual(model.path.matchIds, ['r1-a', 'r2-a']);
  assert.deepEqual(model.path.edges, [{ from: 'r1-a', to: 'r2-a' }]);
  assert.deepEqual(model.path.lossMatchIds, ['r2-a']);
  assert.equal(model.edges.length, 0);
});

test('large league progression is bounded to a four-round window', () => {
  const rounds = Array.from({ length: 126 }, (_, index) => ({ round: index + 1, matches: [{ id: `r${index + 1}`, p1: 'p1', p2: 'p2', result: null }] }));
  const model = buildRoundProgression(rounds, entrants(64), { offset: 70, windowSize: 4 });
  assert.equal(model.rounds.length, 4);
  assert.deepEqual(model.rounds.map(round => round.round), [71, 72, 73, 74]);
  assert.equal(model.totalRounds, 126);
  assert.equal(model.nextOffset, 74);
  assert.equal(model.previousOffset, 66);
});

test('elimination layout spaces every column for measured card heights and bounds the canvas', () => {
  for (const [capacity, cardHeights] of [
    [2, [139, 151]],
    [4, [139, 151, 166, 142, 154, 147, 162]],
    [8, [139, 151, 166, 142, 154, 147, 162, 180, 146, 159, 151, 171, 144, 162, 156]],
    [64, Array.from({ length: 127 }, (_, index) => 132 + (index % 11) * 13)],
  ]) {
    const layout = layoutEliminationBracket(capacity, cardHeights, { gap: 18 });
    assert.equal(layout.positions.length, capacity - 1);
    assert.equal(layout.cardHeight, Math.max(...cardHeights));
    assert.ok(layout.height >= Math.max(...layout.positions.map(position => position.top + layout.cardHeight)) + layout.bottomPadding);
    for (let round = 1; round <= layout.totalRounds; round += 1) {
      const centers = layout.positions.filter(position => position.round === round).map(position => position.center);
      for (let index = 1; index < centers.length; index += 1) {
        assert.ok(centers[index] - centers[index - 1] >= layout.cardHeight + layout.gap);
      }
    }
  }
});

test('HTML text escaping covers tag, quote and attribute delimiters', () => {
  assert.equal(escapeHtml(`<img src=x onerror="x"> & 'ok'`), '&lt;img src=x onerror=&quot;x&quot;&gt; &amp; &#39;ok&#39;');
});
