(() => {
  'use strict';
  const link = document.querySelector('[data-live-link]');
  const section = document.querySelector('#live');
  const status = section.querySelector('#live-status');
  const list = section.querySelector('#live-games');
  const back = section.querySelector('#live-back');
  const labels = {duel:'Duello 1v1', team:'Squadre', ffa:'Tutti contro tutti'};
  let refreshing = false, previous, games, unavailable = false;

  function element(tag, value, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  }

  const label = game => `${game.kind === 'tournament' ? 'Torneo' : 'Amichevole'} · ${labels[game.format]}`;
  const name = side => side.members.map(member => member.name).join(' + ');

  function renderSummary(game) {
    const card = element('a', undefined, 'match-card live-summary');
    card.href = '#live/' + encodeURIComponent(game.id);
    card.append(element('strong', game.sides.map(name).join(' vs ')), element('span', label(game), 'muted'));
    return card;
  }

  function renderDetail(game) {
    const card = element('article', undefined, 'match-card live-detail');
    const heading = element('h2', label(game));
    const sides = element('div', undefined, 'live-sides');
    game.sides.forEach((side, index) => {
      const row = element('div', undefined, 'match-card-team live-side');
      row.append(
        element('strong', name(side)),
        element('span', `${side.life} PV`, 'live-total'),
        element('span', `Vita iniziale · ${side.initial} PV`, 'muted')
      );
      if (game.prediction) {
        row.append(element('strong', `${game.prediction[index]}% di vittoria stimata`, 'live-odds'));
      }
      sides.append(row);
    });
    card.append(heading, sides);
    if (game.prediction) {
      card.append(element('p', 'Stima basata su vita residua, Elo e scontri diretti.', 'muted'));
    }
    return card;
  }

  function render() {
    const id = location.hash.startsWith('#live/') ? location.hash.slice(6) : '';
    back.hidden = !id;
    const game = games?.find(item => encodeURIComponent(item.id) === id);
    const summaries = games?.map(({id, kind, format, sides}) => ({
      id, kind, format, sides: sides.map(({members}) => ({members}))
    }));
    const current = JSON.stringify([id, unavailable, id ? game : summaries]);
    if (current === previous) return;
    previous = current;
    if (unavailable) status.textContent = 'Aggiornamento LIVE non disponibile.';
    else if (!games) status.textContent = 'Caricamento partite…';
    else if (id) status.textContent = game ? '' : 'Questa partita non è più in diretta.';
    else status.textContent = games.length ? '' : 'Nessuna partita in corso.';

    const cards = unavailable ? [] : id ? (game ? [renderDetail(game)] : []) : (summaries || []).map(renderSummary);
    list.replaceChildren(...cards);
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
      link.hidden = !games.length;
    } catch {
      games = undefined;
      unavailable = true;
      link.hidden = true;
    } finally {
      clearTimeout(timeout);
      refreshing = false;
      if (!section.hidden) render();
    }
  }

  window.openLiveGames = () => {
    render();
    refresh();
  };
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 2000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
