(() => {
  'use strict';
  const root = document.querySelector('#history');
  const calendar = root.querySelector('#history-calendar');
  const games = root.querySelector('#history-games');
  const today = new Date();
  let month = new Date(today.getFullYear(), today.getMonth(), 1);
  let selected = today.getDate();
  let sequence = 0;
  let days = new Map();
  const format = (date, options) => date.toLocaleDateString('it-IT', options);
  const fullDate = date => format(date, {weekday:'long', day:'numeric', month:'long', year:'numeric'});
  function element(tag, value) {
    const el = document.createElement(tag);
    el.textContent = value;
    return el;
  }
  function showDay(day) {
    selected = day;
    calendar.querySelectorAll('button').forEach(b => b.setAttribute('aria-pressed', String(Number(b.dataset.day) === day)));
    const date = new Date(month.getFullYear(), month.getMonth(), day);
    root.querySelector('#history-day').textContent = fullDate(date);
    games.replaceChildren();
    const matches = days.get(day) || [];
    if (!matches.length) games.append(element('p', 'Nessuna partita registrata.'));
    for (const match of matches) {
      const sides = match.sides.map(side => side.join(' + '));
      const card = document.createElement('article');
      card.append(element('small', match.label), element('h3', sides.join(' contro ')),
        element('p', match.winner === null ? 'Pareggio' : 'Vittoria di ' + sides[match.winner]));
      games.append(card);
    }
  }
  async function load() {
    const request = ++sequence;
    const end = new Date(month.getFullYear(), month.getMonth()+1, 1);
    root.querySelector('#history-month').textContent = format(month,{month:'long',year:'numeric'});
    root.querySelector('#history-day').textContent = '';
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
      showDay(selected || Math.max(1,...days.keys()));
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
