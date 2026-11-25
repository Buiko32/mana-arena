(() => {
  'use strict';
  const link = document.querySelector('[data-live-link]');
  const section = document.querySelector('#live');
  const status = section.querySelector('#live-status');
  const list = section.querySelector('#live-games');
  const back = section.querySelector('#live-back');
  const labels = {duel:'Duello 1v1', team:'Squadre', ffa:'Tutti contro tutti'};
  let refreshing = false, layout, games, unavailable = false;

  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  }

  const label = game => `${game.kind === 'tournament' ? 'Torneo' : 'Amichevole'} · ${labels[game.format]}`;
  let players = new Map();
  fetch('/api/public/ranking').then(response => response.ok ? response.json() : [])
    .then(rows => {
      players = new Map(rows.map(player => [player.id, player]));
      layout = undefined;
      if (!section.hidden) render();
    }).catch(() => {});

  function renderSide(side, detail) {
    const row = element('div', undefined, 'history-side live-side');
    const members = element('div', undefined, 'live-members');
    for (const member of side.members) {
      const person = element('div', undefined, 'history-player live-player');
      person.innerHTML = window.renderPlayerAvatar(players.get(member.id) || {});
      person.append(element('strong', member.name));
      members.append(person);
    }
    const score = element('div', undefined, 'live-score');
    const change = element('span', undefined, 'live-change');
    change.setAttribute('aria-hidden', 'true');
    score.append(element('strong', undefined, 'live-total'), change);
    row.append(members, score);
    if (detail) row.append(element('span', `Vita iniziale · ${side.initial} PV`, 'muted'),
      element('span', undefined, 'live-odds muted'));
    return row;
  }

  function renderSummary(game) {
    const card = element('a', undefined, 'history-match live-summary');
    card.href = '#live/' + encodeURIComponent(game.id);
    card.append(element('strong', label(game)), renderSides(game, false), element('span', 'Apri partita →', 'live-open'));
    return card;
  }

  function renderDetail(game) {
    const card = element('article', undefined, 'history-match live-detail');
    const heading = element('h2', label(game));
    card.append(heading, renderSides(game, true));
    const estimate = element('p', 'Stima basata su vita residua, Elo e scontri diretti.', 'live-estimate muted');
    card.append(estimate);
    return card;
  }

  function renderSides(game, detail) {
    const sides = element('div', undefined, 'live-sides');
    sides.append(...game.sides.map(side => renderSide(side, detail)));
    return sides;
  }

  function updateScores(card, game) {
    card.querySelectorAll('.live-side').forEach((row, index) => {
      const {life} = game.sides[index];
      const total = row.querySelector('.live-total');
      const delta = life - Number(total.dataset.life);
      if (Number.isFinite(delta) && delta !== 0) {
        const change = row.querySelector('.live-change');
        change.textContent = delta > 0 ? '+' + delta : '−' + Math.abs(delta);
        row.removeAttribute('data-change');
        void change.offsetWidth;
        row.dataset.change = delta > 0 ? 'gain' : 'loss';
      }
      if (delta !== 0) {
        total.dataset.life = life;
        total.textContent = `${life} PV`;
      }
      const odds = row.querySelector('.live-odds');
      if (odds) {
        odds.hidden = !game.prediction;
        odds.textContent = game.prediction ? `${game.prediction[index]}% di vittoria stimata` : '';
      }
    });
    const estimate = card.querySelector('.live-estimate');
    if (estimate) estimate.hidden = !game.prediction;
  }

  function render() {
    const id = location.hash.startsWith('#live/') ? location.hash.slice(6) : '';
    back.hidden = !id;
    const game = games?.find(item => encodeURIComponent(item.id) === id);
    const visible = id ? (game ? [game] : []) : (games || []);
    const nextLayout = JSON.stringify([id, visible.map(({id, kind, format, sides}) => ({
      id, kind, format, sides: sides.map(({members, initial}) => ({members, initial}))
    }))]);
    const message = unavailable ? (visible.length ? 'Aggiornamento interrotto. Ultimi valori ricevuti.' : 'Aggiornamento live non disponibile.')
      : !games ? 'Caricamento partite…'
      : visible.length ? '' : id ? 'Questa partita non è più in diretta.' : 'Nessuna partita in corso.';
    if (status.textContent !== message) status.textContent = message;
    status.hidden = !message;
    if (nextLayout !== layout) {
      layout = nextLayout;
      list.replaceChildren(...visible.map(id ? renderDetail : renderSummary));
    }
    visible.forEach((item, index) => updateScores(list.children[index], item));
  }

  async function refresh() {
    if (refreshing) return;
    refreshing = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 6500);
    try {
      const response = await fetch('/api/public/live', {signal: controller.signal});
      if (!response.ok) throw Error();
      games = await response.json();
      unavailable = false;
      link.hidden = !games.length && !location.hash.startsWith('#live');
    } catch {
      unavailable = true;
    } finally {
      clearTimeout(timeout);
      refreshing = false;
      if (!section.hidden) render();
      document.dispatchEvent(new Event('live-games-updated'));
    }
  }

  window.openLiveGames = () => {
    link.hidden = false;
    render();
    refresh();
  };
  window.tournamentLiveHref = tournamentId => {
    const game = games?.find(item => item.kind === 'tournament' && item.tournament_id === tournamentId);
    return game ? '#live/' + encodeURIComponent(game.id) : '';
  };
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
