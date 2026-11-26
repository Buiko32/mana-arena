(() => {
  'use strict';
  const root = document.querySelector('#history');
  const calendar = root.querySelector('#history-calendar');
  const games = root.querySelector('#history-games');
  const count = root.querySelector('#history-count');
  const mine = root.querySelector('#history-mine');
  const opponent = root.querySelector('#history-opponent');
  const matchFormat = root.querySelector('#history-format');
  const filterHint = root.querySelector('#history-filter-hint');
  const today = new Date();
  let month = new Date(today.getFullYear(), today.getMonth(), 1);
  let selected = today.getDate();
  let sequence = 0;
  let days = new Map();
  let loaded = false;
  const format = (date, options) => date.toLocaleDateString('it-IT', options);
  const fullDate = date => format(date, {weekday:'long', day:'numeric', month:'long', year:'numeric'});
  function element(tag, value = '', className = '') {
    const el = document.createElement(tag);
    el.textContent = value;
    if (className) el.className = className;
    return el;
  }
  function renderMatch(match) {
    const card = element('article', '', 'history-match');
    const teamMatch = match.format === 'team';
    const label = teamMatch ? match.label + ' · Squadre' : match.label;
    const heading = element('header', '', 'history-match-heading');
    const title = element('h3');
    if (match.kind === 'tournament') {
      const trophy = element('span', '🏆');
      trophy.setAttribute('role', 'img');
      trophy.setAttribute('aria-label', 'Partita di torneo');
      trophy.title = 'Partita di torneo';
      title.append(trophy, ' ');
    }
    title.append(label);
    heading.append(title);
    if (match.winner === null) heading.append(element('span', 'Pareggio', 'history-outcome'));
    const layout = element('div', '', 'history-match-layout');
    if (match.kind === 'ffa') layout.classList.add('history-ffa');
    match.sides.forEach((side, index) => {
      if (index === 1 && match.kind !== 'ffa') {
        const separator = element('div', '', 'history-match-separator');
        if (match.score) {
          const score = element('strong', match.score.join('–'));
          score.setAttribute('aria-label', 'Punteggio ' + match.score.join(' a '));
          separator.append(score);
        } else {
          separator.setAttribute('aria-hidden', 'true');
        }
        layout.append(separator);
      }
      const team = element('div', '', 'history-side');
      if (match.kind === 'ffa') team.append(element('span', (index + 1) + '° posto', 'history-outcome'));
      for (const [position, name] of side.entries()) {
        const row = element('div', '', 'history-player');
        const profile = match.player_profiles?.[index]?.[position] ?? {};
        row.insertAdjacentHTML('beforeend', window.renderPlayerAvatar(profile));
        const details = element('div', '', 'history-player-details');
        details.append(element('strong', name));
        if (match.kind !== 'ffa' && match.winner === index && position === 0) {
          details.append(element('span', 'Vittoria', 'history-outcome history-winner'));
        }
        const delta = match.elo_deltas?.[index]?.[position];
        if (Number.isInteger(delta)) {
          const points = element('span', (delta > 0 ? '+' : delta < 0 ? '−' : '') + Math.abs(delta) + ' Elo', 'match-card-elo');
          points.dataset.direction = Math.sign(delta);
          details.append(points);
        }
        row.append(details);
        team.append(row);
      }
      layout.append(team);
    });
    card.append(heading, layout);
    return card;
  }

  function showDay(day, searchMonth = false) {
    selected = day;
    calendar.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.day) === day)));
    const date = new Date(month.getFullYear(), month.getMonth(), day);
    const player = window.manaArenaPlayerId();
    const query = opponent.value.trim().toLocaleLowerCase('it-IT');
    const active = mine.checked || query || matchFormat.value;
    const wholeMonth = active && searchMonth;
    filterHint.hidden = !mine.checked || Boolean(player);
    root.querySelector('#history-day').textContent = wholeMonth ? 'Risultati del mese' : fullDate(date);
    if (wholeMonth) calendar.querySelector('[aria-pressed="true"]')?.setAttribute('aria-pressed', 'false');
    games.replaceChildren();
    const source = wholeMonth ? [...days.values()].flat() : days.get(day) || [];
    const matches = source.filter(match => {
      const ownSide = player ? match.player_ids?.findIndex(side => side.includes(player)) ?? -1 : -1;
      return (!matchFormat.value || match.format === matchFormat.value) &&
        (!mine.checked || ownSide >= 0) &&
        (!query || match.sides.some((side, index) => index !== ownSide &&
          side.some(name => name.toLocaleLowerCase('it-IT').includes(query))));
    });
    count.textContent = matches.length === 1 ? '1 partita' : matches.length + ' partite';
    if (!matches.length) games.append(element('p', active ? 'Nessuna partita corrisponde ai filtri.' : 'Nessuna partita registrata.', 'muted'));
    else games.append(...matches.map(match => {
      const card = renderMatch(match);
      if (wholeMonth) card.querySelector('header').append(element('span', formatDate(match.at), 'muted'));
      return card;
    }));
  }
  const formatDate = stamp => format(new Date(stamp), {day:'numeric', month:'short'});
  const applyFilters = () => { if (loaded) showDay(selected, true); };
  mine.addEventListener('change', applyFilters);
  opponent.addEventListener('input', applyFilters);
  matchFormat.addEventListener('change', applyFilters);
  document.addEventListener('player-identity-changed', applyFilters);
  async function load() {
    loaded = false;
    const request = ++sequence;
    const end = new Date(month.getFullYear(), month.getMonth()+1, 1);
    root.querySelector('#history-month').textContent = format(month,{month:'long',year:'numeric'});
    root.querySelector('#history-day').textContent = '';
    count.textContent = '';
    calendar.replaceChildren();
    games.textContent = 'Caricamento partite…';
    try {
      const params = new URLSearchParams({start:month.toISOString(),end:end.toISOString()});
      const response = await fetch('/api/public/history?' + params);
      if (!response.ok) throw Error();
      const matches = await response.json();
      if (request !== sequence) return;
      days = new Map();
      for (const match of matches) {
        const day = new Date(match.at).getDate();
        if (!days.has(day)) days.set(day, []);
        days.get(day).push(match);
      }
      for (const name of ['Lun','Mar','Mer','Gio','Ven','Sab','Dom']) calendar.append(element('span',name));
      for (let day=1; day<=new Date(end.getFullYear(),end.getMonth(),0).getDate(); day++) {
        const count = days.get(day)?.length || 0;
        const button = element('button',day);
        button.type = 'button';
        button.dataset.day = day;
        if (day===1) button.style.gridColumnStart = (month.getDay()+6)%7+1;
        const date = new Date(month.getFullYear(),month.getMonth(),day);
        button.setAttribute('aria-label',fullDate(date) + ', ' + count + (count===1?' partita':' partite'));
        const marks = element('span','●'.repeat(Math.min(count,3)) + (count>3?' +'+(count-3):''));
        marks.setAttribute('aria-hidden','true');
        button.append(marks);
        calendar.append(button);
      }
      loaded = true;
      showDay(selected || Math.max(1,...days.keys()), true);
    } catch {
      if (request !== sequence) return;
      games.textContent = 'Impossibile caricare le partite. ';
      const retry = element('button','Riprova');
      retry.onclick = load;
      games.append(retry);
    }
  }
  root.addEventListener('click', event => {
    const b = event.target.closest('button');
    if (b?.dataset.day) showDay(Number(b.dataset.day));
    if (b?.dataset.month) {
      month = new Date(month.getFullYear(),month.getMonth()+Number(b.dataset.month),1);
      selected = null;
      load();
    }
  });
  window.openMatchHistory = load;
})();
