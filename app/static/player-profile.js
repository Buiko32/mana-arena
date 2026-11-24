(() => {
  'use strict';
  const $ = selector => document.querySelector(selector);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
  }[char]));
  async function api(path, options = {}) {
    const headers = new Headers(options.headers);
    if (session?.csrf_token && options.method) headers.set('X-Player-CSRF', session.csrf_token);
    const response = await fetch(path, {...options, headers});
    const data = await response.json();
    if (!response.ok) throw Error(data.detail?.message || 'Operazione non riuscita.');
    return data;
  }
  const profileContent = $('#profile-content');
  let session = null;
  function hueHex(h){const hue=((Number(h)||0)%360)/360, sat=.64, light=.48;const f=n=>{const k=(n+hue*12)%12;const a=sat*Math.min(light,1-light);return Math.round(255*(light-a*Math.max(-1,Math.min(k-3,9-k,1)))).toString(16).padStart(2,'0');};return `#${f(0)}${f(8)}${f(4)}`;}
  async function renderProfile(notice=''){
    const editor = $('#profile-editor');
    if (!editor) return;
    const p = await api('/api/player/profile');
    const color=/^#[0-9a-f]{6}$/i.test(p.avatar_color||'')?p.avatar_color:hueHex(p.avatar_hue);
    editor.innerHTML=`<div class="profile-panel"><div class="profile-preview">${p.photo_url?`<div><span>Foto attuale</span><img src="${esc(p.photo_url)}" alt="Immagine del profilo"></div>`:''}<div><span>Segnaposto</span><span id="profile-avatar-preview">${window.renderPlayerAvatar({...p,photo_url:null})}</span></div></div><form id="profile-color-form" class="profile-form"><label>Colore del segnaposto <input type="color" name="picker" value="${esc(color)}"></label><label>Codice colore <input name="color" value="${esc(color)}" pattern="#[0-9a-fA-F]{6}" maxlength="7" required spellcheck="false"></label><button class="button secondary" type="submit">Salva colore</button></form><form id="profile-photo-form" class="profile-form"><label>Immagine del profilo <input type="file" name="file" accept="image/jpeg,image/png,image/webp" required></label><button class="button primary" type="submit">Carica immagine</button></form>${p.photo_url?'<button type="button" id="profile-remove-photo" class="button quiet">Usa il segnaposto</button>':''}<p id="profile-status" role="status">${esc(notice)}</p></div>`;
    const colorForm=$('#profile-color-form'), picker=colorForm.elements.picker, input=colorForm.elements.color;
    const preview=color=>$('#profile-avatar-preview .avatar')?.style.setProperty('--avatar-color',color);picker.addEventListener('input',()=>{input.value=picker.value;preview(picker.value);});input.addEventListener('input',()=>{if(/^#[0-9a-f]{6}$/i.test(input.value)){picker.value=input.value;preview(input.value);}});
    colorForm.addEventListener('submit',async event=>{event.preventDefault();try{await api('/api/player/profile',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({avatar_color:input.value})});await refreshEditedProfile('Colore aggiornato.');}catch(err){if($('#profile-status'))$('#profile-status').textContent=err.message;}});
    $('#profile-photo-form').addEventListener('submit',async event=>{event.preventDefault();try{const data=new FormData();data.append('file',event.currentTarget.elements.file.files[0]);await api('/api/player/profile/photo',{method:'POST',body:data});await refreshEditedProfile('Immagine aggiornata.');}catch(err){if($('#profile-status'))$('#profile-status').textContent=err.message;}});
    $('#profile-remove-photo')?.addEventListener('click',async()=>{try{await api('/api/player/profile/photo',{method:'DELETE'});await refreshEditedProfile('Segnaposto ripristinato.');}catch(err){if($('#profile-status'))$('#profile-status').textContent=err.message;}});
  }

  const profileDate = new Intl.DateTimeFormat('it-IT', {dateStyle:'medium',timeZone:'Europe/Rome'});
  const utcTime = date => {
    const iso = date.replace(' ', 'T');
    return Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : iso.length === 10 ? iso : iso + 'Z');
  };
  const historyDate = new Intl.DateTimeFormat('it-IT', {dateStyle:'medium',timeStyle:'short',timeZone:'Europe/Rome'});
  const dayKey = new Intl.DateTimeFormat('sv-SE', {year:'numeric',month:'2-digit',day:'2-digit',timeZone:'Europe/Rome'});
  const labelDate = date => profileDate.format(utcTime(date));

  function dailyClosingValues(history) {
    const days = new Map();
    for (const point of history) {
      const day = dayKey.format(utcTime(point.date));
      days.set(day, {...point, date:day});
    }
    return [...days.values()];
  }
  const statsUrl = id => `/api/public/players/${encodeURIComponent(id)}/statistics`;

  function profileChart(history, key, title) {
    let low = Infinity, high = -Infinity;
    for (const point of history) {
      low = Math.min(low, point[key]);
      high = Math.max(high, point[key]);
    }
    const start = utcTime(history[0].date), end = utcTime(history.at(-1).date);
    const x = point => start === end ? 150 : 5 + 290 * (utcTime(point.date) - start) / (end - start);
    const y = point => {
      if (high === low) return 50;
      const ratio = (point[key] - low) / (high - low);
      return 7 + 86 * (key === 'rank' ? ratio : 1 - ratio);
    };
    const [top,bottom] = key === 'rank' ? [low,high] : [high,low];
    const line = history.slice(1).map(point => `L${x(point)} ${y(point)}`).join(' ');
    return `<figure class="profile-chart"><figcaption>${title}</figcaption>
      <div class="profile-chart-body"><div class="profile-chart-scale"><span>${top}</span>${top === bottom ? '' : `<span>${bottom}</span>`}</div>
      <svg viewBox="0 0 300 100" preserveAspectRatio="none" role="group" aria-label="${title}">
        <path class="profile-chart-grid" d="M0 7H300 M0 50H300 M0 93H300"/>
        <path class="profile-chart-line" d="M${x(history[0])} ${y(history[0])} ${line}"/>
        ${history.map(point => {
          const value = key === 'rank' ? 'Posizione #' + point.rank : point.rating + ' Elo';
          const label = `${value} · ${labelDate(point.date)}`;
          return `<circle cx="${x(point)}" cy="${y(point)}" r="2.5" tabindex="0" role="img" aria-label="${esc(label)}"><title>${esc(label)}</title></circle>`;
        }).join('')}
      </svg></div>
      <div class="profile-chart-dates"><span>${labelDate(history[0].date)}</span>${start === end ? '' : `<span>${labelDate(history.at(-1).date)}</span>`}</div></figure>`;
  }

  function opponentList(opponents) {
    return opponents.map(player => `<span class="profile-opponent">${window.renderPlayerAvatar(player)}<strong>${esc(player.name)}</strong></span>`).join('');
  }

  function recentResult(match, index) {
    const won = match.result === 'win';
    const label = won ? 'Vittoria' : 'Sconfitta';
    const tooltip = `profile-result-${index}`;
    const delta = match.elo_delta;
    const points = Number.isInteger(delta)
      ? `<strong class="profile-result-elo match-card-elo" data-direction="${Math.sign(delta)}">${delta > 0 ? '+' : delta < 0 ? '−' : ''}${Math.abs(delta)} Elo</strong>`
      : '';
    const date = match.date ? `<span class="muted">${historyDate.format(utcTime(match.date))}</span>` : '';
    return `<li><button type="button" class="${match.result}" aria-label="${label}" aria-describedby="${tooltip}">${won ? 'V' : 'S'}</button>
      <div class="profile-result-tooltip" id="${tooltip}" role="tooltip"><strong>${won ? 'Vittoria contro' : 'Sconfitta contro'}</strong>
      ${opponentList(match.opponents)}<div class="profile-result-meta">${date}${points}</div></div></li>`;
  }

  function headToHead(records = []) {
    const title = '<h3 id="profile-head-to-head">Testa a testa · Duelli Elo</h3>';
    if (!records.length) return title + '<p class="muted">Nessun duello valido per l’Elo.</p>';
    const percent = new Intl.NumberFormat('it-IT', {style:'percent', maximumFractionDigits:1});
    const rows = records.map(({opponent, wins, losses}) => `<tr>
      <th scope="row">${opponentList([opponent])}</th><td>${wins}–${losses}</td>
      <td>${percent.format(wins / (wins + losses))}</td></tr>`).join('');
    return `${title}<div class="table-wrap profile-head-to-head"><table aria-labelledby="profile-head-to-head">
      <thead><tr><th scope="col">Avversario</th><th scope="col">V–S</th><th scope="col">% vittorie</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  function renderStatistics(stats) {
    const {player,history,best_win:bestWin,recent_results:results} = stats;
    const winRate = player.matches ? `${new Intl.NumberFormat('it-IT',{style:'percent',maximumFractionDigits:1}).format(player.wins/player.matches)}` : '—';
    const metrics = [
      ['Elo', player.rating], ['Posizione', `#${player.rank}`],
      ['Record', `${player.wins} V · ${player.losses} S`], ['Vittorie', winRate],
      ['Picco Elo', stats.peak_rating], ['Miglior posizione', `#${stats.best_rank}`],
      ['Serie di vittorie attuale', stats.win_streak], ['Serie di vittorie più lunga', stats.longest_win_streak],
    ];
    const daily = dailyClosingValues(history);
    const historyContent = daily.length ? `
      <div class="profile-charts">${profileChart(daily,'rating','Elo nel tempo')}${profileChart(daily,'rank','Posizione nel tempo')}</div>
      <details class="profile-history"><summary>Variazioni durante la giornata</summary>
        <div class="table-wrap"><table><thead><tr><th>Data e ora</th><th>Elo</th><th>Posizione</th></tr></thead><tbody>
        ${history.slice().reverse().map(point=>`<tr><td>${historyDate.format(utcTime(point.date))}</td><td>${point.rating}</td><td>#${point.rank}</td></tr>`).join('')}</tbody></table></div>
      </details>` : '<p class="muted">Lo storico appare dopo la prima partita valida per l’Elo.</p>';
    document.title = `${player.name} · Mana Arena`;
    $('#profile-heading').innerHTML = `${window.renderPlayerAvatar(player)}<h1>${esc(player.name)}</h1>`;
    $('#profile-summary').innerHTML = `
      <dl class="profile-metrics">${metrics.map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>
      ${historyContent}
      ${results?.length ? `<h3>Ultime 5 partite Elo</h3><ol class="profile-form-results">${results.map(recentResult).join('')}</ol>` : ''}
      ${bestWin ? `<section class="profile-best-win"><div><h3>Miglior vittoria</h3>
        <div class="profile-best-opponents">${bestWin.opponent_profiles ? opponentList(bestWin.opponent_profiles) : esc(bestWin.opponents.join(' e '))}</div></div>
        <strong class="profile-best-gain">+${bestWin.gain} Elo</strong></section>` : ''}
      ${headToHead(stats.head_to_head)}`;
    const recent = $('.profile-form-results');
    recent?.addEventListener('click', event => event.target.closest('button')?.focus());
    recent?.addEventListener('keydown', event => {
      if (event.key === 'Escape') event.target.blur();
    });
  }

  async function refreshEditedProfile(notice) {
    const stats = await api(statsUrl(session.player_id));
    renderStatistics(stats);
    await renderProfile(notice);
  }

  async function openProfile() {
    try {
      const rawId = location.pathname.split('/')[2];
      const explicitId = rawId ? decodeURIComponent(rawId) : '';
      const [viewer, requestedStats] = await Promise.all([
        api('/api/player/session').catch(() => null),
        explicitId ? api(statsUrl(explicitId)) : null,
      ]);
      session = viewer;
      const playerId = explicitId || session?.player_id;
      if (!playerId) {
        profileContent.textContent = 'Torna alla home e scegli il tuo nome per aprire il tuo profilo.';
        return;
      }
      const stats = requestedStats || await api(statsUrl(playerId));
      profileContent.innerHTML = '<div id="profile-heading" class="profile-header"></div>' + (session?.player_id === playerId
        ? '<details class="profile-edit"><summary>Modifica foto e colore</summary><div id="profile-editor"></div></details>' : '') +
        '<div id="profile-summary"></div>';
      renderStatistics(stats);
      const edit = profileContent.querySelector('.profile-edit');
      edit?.addEventListener('toggle', async () => {
        if (!edit.open || $('#profile-editor').firstElementChild) return;
        try { await renderProfile(); }
        catch (error) { $('#profile-editor').textContent = error.message; }
      });
    } catch (error) {
      profileContent.textContent = error.message;
    }
  }

  openProfile();
})();
