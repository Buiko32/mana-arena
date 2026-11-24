(() => {
  'use strict';
  const $ = s => document.querySelector(s), esc = x => String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let session=null, players=[], matches=[];
  const dialog=$('#player-results-dialog'), content=$('#player-results-content'), select=$('#player-identity');
  async function api(path,options={}) {const headers=new Headers(options.headers||{});if(session?.csrf_token)headers.set('X-Player-CSRF',session.csrf_token);const r=await fetch(path,{...options,headers,credentials:'same-origin'});const data=await r.json();if(!r.ok){const error=Error(data.detail?.message||'Operazione non riuscita.');error.status=r.status;throw error;}return data;}
  const post=(path,data={})=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  async function getSession() {
    const previous = select.value;
    try {
      session = await api('/api/player/session');
      localStorage.setItem('mana-arena-player-id',session.player_id);
      if (![...select.options].some(option => option.value === session.player_id)) select.add(new Option(session.name,session.player_id));
      select.value = session.player_id;
    } catch (error) {
      if (error.status !== 401) throw error;
      session = null;
      try { localStorage.removeItem('mana-arena-player-id'); } catch {}
      select.value = '';
    }
    if (previous !== select.value) document.dispatchEvent(new Event('player-identity-changed'));
  }

  let resultSaved=false;
  function updateSummary(form, label, sides, winner, valid) {
    const summary=form.querySelector('.result-review');
    summary.innerHTML=`<p class="result-review-kind">${esc(label)}</p><p class="result-review-sides">${sides.map(side=>esc(side.join(' + ')||'Da selezionare')).join(' <span>contro</span> ')}</p><p class="result-review-winner">Vincitore: <strong>${esc(winner||'Da selezionare')}</strong></p>`;
    form.querySelector('button[type="submit"]').disabled=!valid||Boolean(form.dataset.saving);
  }
  async function saveResult(form, path, payload, errorSelector) {
    if (form.dataset.saving) return;
    form.dataset.saving = 'true';
    const enabledControls = [...form.elements].filter(control => !control.disabled);
    enabledControls.forEach(control => { control.disabled = true; });
    try {
      const outcome = await post(path, payload);
      if (dialog.open) {
        resultSaved = true;
        if (form.isConnected) content.innerHTML = '<p role="status">Risultato registrato.</p>';
      } else {
        document.dispatchEvent(new Event('player-result-saved'));
      }
      window.showEloResult?.(outcome.elo_changes);
    } catch (err) {
      form.querySelector(errorSelector).textContent = err.message;
      delete form.dataset.saving;
      enabledControls.forEach(control => { control.disabled = false; });
    }
  }
  async function render(matchId = null) {
    if (!session) {
      content.innerHTML = '<p>Seleziona il tuo nome in alto per accedere.</p>';
      return;
    }
    const [ranking,ownMatches] = await Promise.all([api('/api/public/ranking'),api('/api/player/matches')]);
    if (!dialog.open) return;
    players = ranking;
    matches = ownMatches;
    $('#player-results-title').textContent = 'Inserisci risultato';
    content.innerHTML = `<div class="results-panel">
      <div class="result-type-switch" role="group" aria-label="Tipo di partita">
        <button type="button" class="button secondary" data-result-type="tournament" aria-pressed="false"><span aria-hidden="true">🏆</span> Torneo</button>
        <button type="button" class="button secondary" data-result-type="friendly" aria-pressed="false">Amichevole</button>
      </div>
      <div id="result-form-slot"></div>
    </div>`;
    const buttons = [...content.querySelectorAll('[data-result-type]')];
    function selectType(type, selectedMatch = null) {
      if (content.querySelector('form[data-saving]')) return;
      buttons.forEach(button => button.setAttribute('aria-pressed',String(button.dataset.resultType === type)));
      if (type === 'tournament') renderTournamentForm(selectedMatch);
      else renderTeamForm();
    }
    buttons.forEach(button => button.addEventListener('click',() => selectType(button.dataset.resultType)));
    if (matchId) selectType('tournament',matchId);
    else buttons[0].focus();
  }
  function renderTournamentForm(matchId) {
    const host = $('#result-form-slot');
    const name = id => players.find(p => p.id === id)?.name || 'Giocatore';
    const available = matches.filter(match => match.revealed);
    const locked = matches.filter(match => !match.revealed);
    const revealButtons = locked.map(match => `<button type="button" class="button secondary" data-result-reveal="${esc(match.tournament.id)}">Scopri incontro · ${esc(match.tournament.config.name)}</button>`).join('');
    host.innerHTML = `${locked.length ? `<div class="results-panel result-reveals">${revealButtons}</div>` : ''}
      ${available.length ? `<form id="new-player-result" class="results-panel">
        <label>Partita di torneo<select name="match" required><option value="">Seleziona una partita</option>
          ${available.map(match => `<option value="${esc(match.id)}">🏆 ${esc(match.tournament.config.name)} · ${esc(name(match.p1))} – ${esc(name(match.p2))}</option>`).join('')}
        </select></label>
        <label>Chi ha vinto?<select name="winner" required></select></label>
        <div class="result-review" aria-live="polite"></div><p id="player-results-error" class="results-error" role="alert"></p>
        <button class="button primary" type="submit" disabled>Salva risultato</button></form>`
      : locked.length ? '' : '<p class="muted" role="status">Nessun incontro di torneo da registrare.</p>'}`;
    host.querySelectorAll('[data-result-reveal]').forEach(button => button.addEventListener('click',() => {
      dialog.close();
      document.dispatchEvent(new CustomEvent('reveal-tournament',{detail:button.dataset.resultReveal}));
    }));
    const form = $('#new-player-result');
    if (!form) return;
    const matchField = form.elements.match, winnerField = form.elements.winner;
    const selectedMatch = () => available.find(match => match.id === matchField.value);
    function updateSummaryFields() {
      const match = selectedMatch(), winner = winnerField.value;
      updateSummary(form, match ? '🏆 Partita di torneo · ' + match.tournament.config.name : 'Partita di torneo',
        match ? [[name(match.p1)],[name(match.p2)]] : [[],[]], winner ? name(winner) : '', Boolean(match && winner));
    }
    function updateWinnerOptions() {
      const match = selectedMatch();
      winnerField.replaceChildren(new Option('Seleziona chi ha vinto',''));
      if (match) for (const id of [match.p1,match.p2]) winnerField.add(new Option(name(id),id));
      updateSummaryFields();
    }
    matchField.value = available.some(match => match.id === matchId) ? matchId : '';
    matchField.addEventListener('change',updateWinnerOptions);
    winnerField.addEventListener('change',updateSummaryFields);
    updateWinnerOptions();
    if (matchField.value) winnerField.focus();
    form.addEventListener('submit',event => {
      event.preventDefault();
      const match = selectedMatch(), winner = winnerField.value;
      if (match && winner) saveResult(form,'/api/player/results',
        {tournament_id:match.tournament.id,match_id:match.id,winner_id:winner},'#player-results-error');
    });
  }
  function renderTeamForm(){
    const choice=(p,locked=false)=>`<label class="team-choice"><span class="team-choice-person">${window.manaPlayerAvatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)}<span>${esc(p.name)}${locked?' (tu)':''}</span></span><input type="checkbox" value="${esc(p.id)}" ${locked?'checked disabled':''}></label>`;
    const options=players.filter(p=>p.id!==session.player_id).map(p=>choice(p)).join('');
    const own=players.find(p=>p.id===session.player_id)||{id:session.player_id,name:session.name};
    $('#result-form-slot').innerHTML=`<form id="team-result-form" class="results-panel"><p>Puoi giocare da solo o scegliere fino a due compagni. Seleziona da uno a tre avversari.</p><fieldset><legend>La tua squadra</legend><div class="team-choices">${choice(own,true)}<div id="team-a" class="team-choices">${options}</div></div></fieldset><fieldset><legend>Avversari</legend><div id="team-b" class="team-choices">${options}</div></fieldset><label>Chi ha vinto?<select name="winner" required></select></label><div class="result-review" aria-live="polite"></div><p id="team-result-error" class="results-error" role="alert"></p><button class="button primary" type="submit" disabled>Salva risultato</button></form>`;
    const teamForm=$('#team-result-form');
    const selected = key => [...teamForm.querySelectorAll(`#team-${key} input:checked`)].map(input => input.value);
    const winner = teamForm.elements.winner;
    function updateWinnerOptions() {
      const previousWinner = winner.value;
      const groups = {a: [session.player_id, ...selected('a')], b: selected('b')};
      winner.replaceChildren(new Option('Seleziona chi ha vinto', ''));
      for (const [side, ids] of Object.entries(groups)) {
        if (ids.length) {
          const names = ids.map(id => players.find(p => p.id === id)?.name || session.name);
          winner.add(new Option(names.join(' e '), side));
        }
      }
      if ([...winner.options].some(option => option.value === previousWinner)) {
        winner.value = previousWinner;
      }
    }
    const names=ids=>ids.map(id=>players.find(p=>p.id===id)?.name||session.name);
    function updateTeamSummary(){const a=[session.player_id,...selected('a')],b=selected('b'),win=winner.value,valid=a.length<=3&&b.length>=1&&b.length<=3&&Boolean(win);updateSummary(teamForm,'Amichevole',[names(a),names(b)],win?names(win==='a'?a:b).join(' + '):'',valid);}
    updateWinnerOptions();updateTeamSummary();
    winner.addEventListener('change',updateTeamSummary);
    teamForm.addEventListener('change',event=>{
      const input=event.target;
      if(!input.matches('#team-a input[type="checkbox"], #team-b input[type="checkbox"]'))return;
      const other=input.closest('#team-a')?$('#team-b'):$('#team-a');
      const counterpart=[...other.querySelectorAll('input[type="checkbox"]')].find(item=>item.value===input.value);
      if(counterpart){counterpart.disabled=input.checked;counterpart.closest('.team-choice').classList.toggle('is-unavailable',input.checked);}
      updateWinnerOptions();updateTeamSummary();
    });
    teamForm.addEventListener('submit',event=>{event.preventDefault();const a=[session.player_id,...selected('a')],b=selected('b'),win=winner.value;if(a.length>3||b.length<1||b.length>3||!win||new Set([...a,...b]).size!==a.length+b.length){$('#team-result-error').textContent='Scegli da 1 a 3 giocatori distinti per parte e il vincitore.';return;}saveResult(teamForm,'/api/player/team-results',{team_a:a,team_b:b,winner:win},'#team-result-error');});
  }
  const profileDialog=$('#profile-dialog'), profileContent=$('#profile-content');
  let profileRequest = 0;
  function hueHex(h){const hue=((Number(h)||0)%360)/360, sat=.64, light=.48;const f=n=>{const k=(n+hue*12)%12;const a=sat*Math.min(light,1-light);return Math.round(255*(light-a*Math.max(-1,Math.min(k-3,9-k,1)))).toString(16).padStart(2,'0');};return `#${f(0)}${f(8)}${f(4)}`;}
  async function renderProfile(notice=''){
    const editor = $('#profile-editor');
    if (!editor) return;
    const token = profileRequest;
    const p = await api('/api/player/profile');
    if (token !== profileRequest || !profileDialog.open) return;
    const color=/^#[0-9a-f]{6}$/i.test(p.avatar_color||'')?p.avatar_color:hueHex(p.avatar_hue);
    editor.innerHTML=`<div class="profile-panel"><div class="profile-preview">${p.photo_url?`<div><span>Foto attuale</span><img src="${esc(p.photo_url)}" alt="Immagine del profilo"></div>`:''}<div><span>Segnaposto</span><span id="profile-avatar-preview">${window.manaPlayerAvatar(p.name,null,p.avatar_hue,p.avatar_color)}</span></div></div><form id="profile-color-form" class="profile-form"><label>Colore del segnaposto <input type="color" name="picker" value="${esc(color)}"></label><label>Codice colore <input name="color" value="${esc(color)}" pattern="#[0-9a-fA-F]{6}" maxlength="7" required spellcheck="false"></label><button class="button secondary" type="submit">Salva colore</button></form><form id="profile-photo-form" class="profile-form"><label>Immagine del profilo <input type="file" name="file" accept="image/jpeg,image/png,image/webp" required></label><button class="button primary" type="submit">Carica immagine</button></form>${p.photo_url?'<button type="button" id="profile-remove-photo" class="button quiet">Usa il segnaposto</button>':''}<p id="profile-status" role="status">${esc(notice)}</p></div>`;
    const colorForm=$('#profile-color-form'), picker=colorForm.elements.picker, input=colorForm.elements.color;
    const preview=color=>$('#profile-avatar-preview .avatar')?.style.setProperty('--avatar-color',color);picker.addEventListener('input',()=>{input.value=picker.value;preview(picker.value);});input.addEventListener('input',()=>{if(/^#[0-9a-f]{6}$/i.test(input.value)){picker.value=input.value;preview(input.value);}});
    colorForm.addEventListener('submit',async event=>{event.preventDefault();try{await api('/api/player/profile',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({avatar_color:input.value})});document.dispatchEvent(new Event('player-profile-changed'));await refreshEditedProfile('Colore aggiornato.', token);}catch(err){if(token===profileRequest && $('#profile-status'))$('#profile-status').textContent=err.message;}});
    $('#profile-photo-form').addEventListener('submit',async event=>{event.preventDefault();try{const data=new FormData();data.append('file',event.currentTarget.elements.file.files[0]);await api('/api/player/profile/photo',{method:'POST',body:data});document.dispatchEvent(new Event('player-profile-changed'));await refreshEditedProfile('Immagine aggiornata.', token);}catch(err){if(token===profileRequest && $('#profile-status'))$('#profile-status').textContent=err.message;}});
    $('#profile-remove-photo')?.addEventListener('click',async()=>{try{await api('/api/player/profile/photo',{method:'DELETE'});document.dispatchEvent(new Event('player-profile-changed'));await refreshEditedProfile('Segnaposto ripristinato.', token);}catch(err){if(token===profileRequest && $('#profile-status'))$('#profile-status').textContent=err.message;}});
  }

  const profileDate = new Intl.DateTimeFormat('it-IT', {dateStyle:'medium',timeZone:'Europe/Rome'});
  const utcTime = date => {
    const iso = date.replace(' ', 'T');
    return Date.parse(/(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso) ? iso : iso.length === 10 ? iso : iso + 'Z');
  };
  const labelDate = date => profileDate.format(utcTime(date));
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
    const line = history.slice(1).map(point => `H${x(point)} V${y(point)}`).join(' ');
    return `<figure class="profile-chart"><figcaption>${title}</figcaption>
      <div class="profile-chart-body"><div class="profile-chart-scale"><span>${top}</span>${top === bottom ? '' : `<span>${bottom}</span>`}</div>
      <svg viewBox="0 0 300 100" preserveAspectRatio="none" aria-hidden="true">
        <path class="profile-chart-grid" d="M0 7H300 M0 50H300 M0 93H300"/>
        <path class="profile-chart-line" d="M${x(history[0])} ${y(history[0])} ${line}"/>
        <circle cx="${x(history.at(-1))}" cy="${y(history.at(-1))}" r="3"/>
      </svg></div>
      <div class="profile-chart-dates"><span>${labelDate(history[0].date)}</span>${start === end ? '' : `<span>${labelDate(history.at(-1).date)}</span>`}</div></figure>`;
  }

  function renderStatistics(stats) {
    const {player,history,best_win:bestWin,recent_form:form} = stats;
    const winRate = player.matches ? `${new Intl.NumberFormat('it-IT',{style:'percent',maximumFractionDigits:1}).format(player.wins/player.matches)}` : '—';
    const metrics = [
      ['Elo', player.rating], ['Posizione', `#${player.rank}`],
      ['Record', `${player.wins} V · ${player.losses} S`], ['Vittorie', winRate],
      ['Picco Elo', stats.peak_rating], ['Miglior posizione', `#${stats.best_rank}`],
      ['Serie di vittorie attuale', stats.win_streak], ['Serie di vittorie più lunga', stats.longest_win_streak],
    ];
    const historyContent = history.length ? `
      <div class="profile-charts">${profileChart(history,'rating','Elo nel tempo')}${profileChart(history,'rank','Posizione nel tempo')}</div>
      <p class="muted">Storico ricostruito dalle partite valide per l’Elo, con la posizione calcolata sui profili attivi oggi.</p>
      <details class="profile-history"><summary>Valori dello storico</summary>
        <div class="table-wrap"><table><thead><tr><th>Data</th><th>Elo</th><th>Posizione</th></tr></thead><tbody>
        ${history.slice().reverse().map(point=>`<tr><td>${labelDate(point.date)}</td><td>${point.rating}</td><td>#${point.rank}</td></tr>`).join('')}</tbody></table></div>
      </details>` : '<p class="muted">Lo storico appare dopo la prima partita valida per l’Elo.</p>';
    $('#profile-heading').innerHTML = `${window.renderPlayerAvatar(player)}<h3>${esc(player.name)}</h3>`;
    $('#profile-summary').innerHTML = `
      <dl class="profile-metrics">${metrics.map(([label,value])=>`<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl>
      ${form.length ? `<h3>Ultime partite</h3><ol class="profile-form-results">${form.map(result=>`<li class="${result}" aria-label="${result==='win'?'Vittoria':'Sconfitta'}">${result==='win'?'V':'S'}</li>`).join('')}</ol>` : ''}
      ${bestWin ? `<h3>Miglior vittoria</h3><p class="profile-best-win"><strong>${bestWin.opponents.map(esc).join(' e ')}</strong> · ${labelDate(bestWin.date)}<br><span class="muted">+${bestWin.gain} Elo guadagnati · divario prima della partita ${bestWin.gap>0?'+':''}${bestWin.gap} Elo</span></p>` : ''}
      ${historyContent}`;
  }

  async function refreshEditedProfile(notice, token) {
    if (token !== profileRequest || !profileDialog.open) return;
    const stats = await api(statsUrl(session.player_id));
    if (token !== profileRequest || !profileDialog.open) return;
    renderStatistics(stats);
    await renderProfile(notice);
  }

  async function openProfile(playerId) {
    const token = ++profileRequest;
    profileContent.textContent = 'Caricamento…';
    if (!profileDialog.open) profileDialog.showModal();
    try {
      if (!playerId) {
        await getSession();
        playerId = session?.player_id;
      }
      if (token !== profileRequest || !profileDialog.open) return;
      if (!playerId) {
        profileContent.textContent = 'Seleziona il tuo nome in alto per aprire il tuo profilo.';
        return;
      }
      const stats = await api(statsUrl(playerId));
      if (token !== profileRequest || !profileDialog.open) return;
      profileContent.innerHTML = '<div id="profile-heading" class="profile-header"></div>' + (session?.player_id === playerId
        ? '<details class="profile-edit"><summary>Modifica foto e colore</summary><div id="profile-editor"></div></details>' : '') +
        '<div id="profile-summary"></div>';
      renderStatistics(stats);
      const edit = profileContent.querySelector('.profile-edit');
      edit?.addEventListener('toggle', async () => {
        if (!edit.open || $('#profile-editor')?.firstElementChild) return;
        try { await renderProfile(); }
        catch (error) {
          if (token === profileRequest && profileDialog.open) $('#profile-editor').textContent = error.message;
        }
      });
    } catch (error) {
      if (token === profileRequest && profileDialog.open) profileContent.textContent = error.message;
    }
  }

  $('#profile-open').addEventListener('click',()=>openProfile());
  document.addEventListener('click',event=>{
    const button = event.target.closest?.('[data-player-profile]');
    if (button) openProfile(button.dataset.playerProfile);
  });
  document.addEventListener('player-identity-changing',()=>{ if (profileDialog.open) profileDialog.close(); });
  $('#profile-close').addEventListener('click',()=>profileDialog.close());
  async function openResults(matchId = null) {
    if (dialog.open) return;
    content.textContent = 'Caricamento…';
    dialog.showModal();
    try { await getSession(); await render(matchId); }
    catch (e) { content.textContent = e.message; }
  }
  $('#results-open').addEventListener('click',() => openResults());
  document.addEventListener('player-result-open',event => openResults(event.detail));
  $('#player-results-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{if(resultSaved){resultSaved=false;document.dispatchEvent(new Event('player-result-saved'));}const id=session?.player_id||'';select.value=id;try{if(id)localStorage.setItem('mana-arena-player-id',id);else localStorage.removeItem('mana-arena-player-id');}catch{}});
  select.addEventListener('change',async()=>{const id=select.value;document.dispatchEvent(new Event('player-identity-changing'));try{if(!id){if(session)await post('/api/player/logout');session=null;localStorage.removeItem('mana-arena-player-id');}else{session=await post('/api/player/login',{player_id:id});localStorage.setItem('mana-arena-player-id',id);}document.dispatchEvent(new Event('player-identity-changed'));}catch(err){select.value=session?.player_id||'';document.dispatchEvent(new Event('player-identity-changed'));window.alert(err.message);}});
  const welcome=$('#identity-welcome'), welcomeForm=$('#identity-welcome-form'), welcomePlayers=$('#identity-welcome-players'), welcomeMessage=$('#identity-welcome-message'), welcomeSubmit=$('#identity-welcome-submit');
  async function showIdentityWelcome(){
    if(!welcome || window.location?.hash === '#life')return;
    if(!welcome.open)welcome.showModal();
    try{
      const list=await api('/api/public/ranking');
      welcomePlayers.innerHTML=list.map(p=>`<label class="identity-player"><span class="identity-player-info">${window.manaPlayerAvatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)}<span>${esc(p.name)}</span></span><input type="radio" name="welcome-player" value="${esc(p.id)}" required></label>`).join('');
      welcomeSubmit.disabled=true;
      welcomeMessage.textContent=list.length?'':'Non ci sono ancora giocatori. Puoi proseguire e aggiungerli nell’area organizzatore.';
    }catch{welcomePlayers.innerHTML='';welcomeSubmit.disabled=true;welcomeMessage.textContent='Impossibile caricare i giocatori. Riprova ricaricando la pagina o prosegui senza scegliere.';}
  }
  if(welcome){
    $('#identity-welcome-skip').addEventListener('click',()=>welcome.close());
    welcomePlayers.addEventListener('change',event=>{if(event.target.matches('input[name="welcome-player"]'))welcomeSubmit.disabled=false;});
    welcomeForm.addEventListener('submit',async event=>{
      event.preventDefault();const id=welcomeForm.querySelector('input[name="welcome-player"]:checked')?.value;if(!id)return;
      welcomeSubmit.disabled=true;welcomeMessage.textContent='';
      try{
        document.dispatchEvent(new Event('player-identity-changing'));
        session=await post('/api/player/login',{player_id:id});
        try{localStorage.setItem('mana-arena-player-id',id);}catch{}
        if(![...select.options].some(o=>o.value===id))select.add(new Option(session.name,id));
        select.value=id;document.dispatchEvent(new Event('player-identity-changed'));
        welcome.close();
      }catch(err){document.dispatchEvent(new Event('player-identity-changed'));welcomeMessage.textContent=err.message||'Impossibile selezionare il giocatore.';}
      finally{welcomeSubmit.disabled=false;}
    });
  }
  let rememberedPlayer='';try{rememberedPlayer=localStorage.getItem('mana-arena-player-id')||'';}catch{}
  getSession().then(async()=>{
    if(!session&&rememberedPlayer){
      try{session=await post('/api/player/login',{player_id:rememberedPlayer});try{localStorage.setItem('mana-arena-player-id',session.player_id);}catch{}if(![...select.options].some(o=>o.value===session.player_id))select.add(new Option(session.name,session.player_id));select.value=session.player_id;document.dispatchEvent(new Event('player-identity-changed'));}catch{}
    }
    if(!session)await showIdentityWelcome();
  }).catch(()=>showIdentityWelcome());
})();
