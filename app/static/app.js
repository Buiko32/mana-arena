(() => {
  'use strict';
  const $ = (s, root = document) => root.querySelector(s);
  const $$ = (s, root = document) => [...root.querySelectorAll(s)];
  const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
  const MASCOT_NAMES = Object.freeze(['white', 'blue', 'black', 'red', 'green']);
  function initMascot() {
    const image = $('#arena-mascot');
    if (!image) return;
    const name = MASCOT_NAMES[Math.floor(Math.random() * MASCOT_NAMES.length)];
    const motion = matchMedia('(prefers-reduced-motion: reduce)');
    const update = () => { image.src = `/static/mascots/${name}${motion.matches ? '-still.png' : '.gif'}?v=20261117-poses`; image.hidden = false; };
    update();
    motion.addEventListener?.('change', update);
  }
  const state = {csrf:'', user:'', players:[], tournaments:[], current:null, detailTab:'standings', managerTab:'entrants', publicId:'', busy:false, timer:0, editing:false, visible:true, publicListSnapshot:'', publicDetailSnapshot:'', managerSnapshot:'', publicDetailData:null, publicRoundView:'graph', adminRoundView:'graph', publicRoundOffset:0, adminRoundOffset:0, publicSelectedPlayer:'', adminSelectedPlayer:''};
  const identityKey='mana-arena-player-id';
  function savedPlayer(){try{return localStorage.getItem(identityKey)||'';}catch{return '';}}
  window.manaArenaPlayerId = savedPlayer;
  function savePlayer(id){try{if(id)localStorage.setItem(identityKey,id);else localStorage.removeItem(identityKey);}catch{}}
  let publicRanking = [], rankingSnapshot = null, profileSnapshot = null;
  function renderProfileButton(rows) {
    const button = $('#profile-open');
    const player = rows.find(person => person.id === savedPlayer());
    const snapshot = JSON.stringify(player ? [player.id,player.name,player.photo_url,player.avatar_hue,player.avatar_color] : null);
    if (snapshot === profileSnapshot) return;
    profileSnapshot = snapshot;
    button.innerHTML = player ? avatar(player.name,player.photo_url,player.avatar_hue,player.avatar_color) : 'Profilo';
    button.href = player ? `/profilo/${encodeURIComponent(player.id)}` : '/profilo';
    const label = player ? `Apri il profilo di ${player.name}` : 'Apri il tuo profilo';
    button.setAttribute('aria-label',label);
    button.title = label;
  }
  function rankingMovement(movement, key) {
    const change = movement?.[key];
    if (!change) return '';
    const up = change > 0;
    const magnitude = Math.abs(change);
    const unit = key === 'rating' ? 'Elo' : magnitude === 1 ? 'posizione' : 'posizioni';
    const label = `${up ? '+' : '−'}${magnitude} ${unit} · ${movement.date}`;
    return ` <span class="ranking-movement ${up ? 'is-up' : 'is-down'}" role="img" aria-label="${esc(label)}" title="${esc(label)}"><span aria-hidden="true">${up ? '↑' : '↓'}</span>${magnitude}</span>`;
  }

  function renderRanking(rows){
    publicRanking = rows;
    renderProfileButton(rows);
    const select=$('#player-identity'),current=savedPlayer(),active=document.activeElement===select;
    if(!active){select.innerHTML='<option value="">Seleziona il tuo nome</option>'+rows.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');select.value=rows.some(p=>p.id===current)?current:'';}
    const snapshot = JSON.stringify([current, rows]);
    if (snapshot === rankingSnapshot) return;
    rankingSnapshot = snapshot;
    $('#ranking-list').innerHTML=rows.length?`<div class="table-wrap"><table class="global-table"><thead><tr><th>Pos.</th><th>Giocatore</th><th>Elo</th><th>Partite</th><th>V–S</th><th>Frammenti Mana</th></tr></thead><tbody>${rows.map(p=>`<tr${p.id===current?' class="ranking-own-row"':''}><td class="rank">${p.rank}${rankingMovement(p.movement, 'rank')}</td><td><a class="ranking-player" href="/profilo/${encodeURIComponent(p.id)}" aria-label="Apri profilo di ${esc(p.name)}">${avatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)}<span class="ranking-name" title="${esc(p.name)}">${esc(p.name)}</span></a></td><td><strong>${p.rating}</strong>${rankingMovement(p.movement, 'rating')}</td><td>${p.matches}</td><td>${p.wins}–${p.losses}</td><td><span class="mana-balance"><span class="mana-fragment-icon" aria-hidden="true"></span>${p.mana_fragments ?? 0}</span></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">La classifica apparirà dopo l’aggiunta dei giocatori.</p>';
  }
  const errorBox = id => $(id);
  function message(text, kind='success') { const node=document.createElement('div');node.className=`toast ${kind==='error'?'error':''}`;node.textContent=text;$('#toast-region').append(node);setTimeout(()=>node.remove(),4200); }
  function showError(text, where='#admin-error') { const box=$(where); if(!box)return;box.textContent=text;box.hidden=!text; }
  function clearError(where='#admin-error'){showError('',where);}
  function captureFocus(root){
    const active=document.activeElement;
    if(!root?.contains(active))return null;
    for(const attribute of ['data-public-open','data-reveal-home','data-personal-result','data-detail-tab','data-manage','data-manager-tab','data-round-view','data-round-window','data-round-player','data-result','data-manager-action','data-withdraw','data-export']){
      if(active.hasAttribute?.(attribute))return {attribute,value:active.getAttribute(attribute),scope:active.getAttribute('data-round-scope')};
    }
    return active.id?{id:active.id}:null;
  }
  function restoreFocus(root,key){
    if(!root||!key)return;
    const target=key.id?document.getElementById(key.id):$$(`[${key.attribute}]`,root).find(node=>node.getAttribute(key.attribute)===key.value&&(key.scope===null||node.getAttribute('data-round-scope')===key.scope));
    target?.focus({preventScroll:true});
  }
  function apiError(data, fallback) { const d=data?.detail; return typeof d==='string'?d:(d?.message||fallback||'Si è verificato un errore.'); }
  async function request(path, options={}) {
    const headers = new Headers(options.headers || {}); const method=(options.method||'GET').toUpperCase();
    if(state.csrf && !['GET','HEAD','OPTIONS'].includes(method)) headers.set('X-CSRF-Token',state.csrf);
    const response=await fetch(path,{...options,headers,credentials:'same-origin'});
    const type=response.headers.get('content-type')||'';
    if(!response.ok){let body={};try{body=type.includes('json')?await response.json():{detail:{message:await response.text()}}}catch{};const error=new Error(apiError(body,`Richiesta non riuscita (${response.status}).`));error.status=response.status;error.detail=body.detail;throw error;}
    if(response.status===204)return null;
    return type.includes('json')?response.json():response;
  }
  async function loadDevices() {
    const host = $('#devices-list');
    host.textContent = 'Caricamento…';
    try {
      const devices = await request('/api/admin/devices');
      const format = value => { const date = new Date(typeof value === 'number' ? value * 1000 : value); return Number.isNaN(date.getTime()) ? String(value ?? '—') : date.toLocaleString('it-IT'); };
      host.innerHTML = devices.length ? devices.map(device => `<div class="admin-event-row"><div><h3>${esc(device.label || 'Dispositivo')}</h3><p class="muted">Autorizzato: ${esc(format(device.created_at))} · Scadenza: ${esc(format(device.expires_at))}</p></div><button type="button" class="button danger" data-revoke-device="${esc(device.id)}">Revoca</button></div>`).join('') : '<p class="muted">Nessun dispositivo autorizzato.</p>';
    } catch (error) { host.textContent = error.message; }
  }
  function formObject(form){return Object.fromEntries(new FormData(form).entries());}
  function busy(button, value){if(button){button.disabled=value;button.dataset.oldText ||= button.textContent;button.textContent=value?'Salvataggio…':button.dataset.oldText;}}
  function configFromForm(form){const v=formObject(form);return {name:v.name.trim(),date:v.date||null,description:v.description.trim(),structure:v.structure,game_format:state.current?.config?.game_format||'Magic',match_length:Number(v.match_length),rounds:v.structure==='swiss'?Number(v.rounds):null,loss_threshold:v.structure==='adapted'?Number(v.loss_threshold):null,league_mode:v.structure==='league'?v.league_mode:null,seeding:v.seeding,allow_draws:form.elements.allow_draws.checked,points:{win:Number(form.elements['points.win'].value),draw:Number(form.elements['points.draw'].value),loss:Number(form.elements['points.loss'].value),bye:Number(form.elements['points.bye'].value)},tiebreakers:$$('#tiebreak-list li').filter(li=>$('input[type=checkbox]',li).checked).map(li=>li.dataset.tiebreak)};}
  function configSummary(c){return `${esc(({swiss:'Svizzera',adapted:'Svizzera adattata',league:'Girone',single_elim:'Eliminazione diretta'})[c.structure]||c.structure)} · BO${esc(c.match_length)}${c.structure==='swiss'?` · ${esc(c.rounds)} turni`:''}${c.structure==='adapted'?` · ${esc(c.loss_threshold)} sconfitte`:''}${c.structure==='league'?` · ${esc(c.league_mode==='double'?'andata e ritorno':'solo andata')}`:''}`;}

  const dateLabel = value => {if(!value)return 'Data da definire';const d=new Date(`${value}T12:00:00`);return Number.isNaN(d.getTime())?esc(value):new Intl.DateTimeFormat('it-IT',{day:'2-digit',month:'short',year:'numeric'}).format(d);};
  function avatar(name, photo_url, avatar_hue, avatar_color) {
    return window.renderPlayerAvatar({photo_url, avatar_hue, avatar_color});
  }
  window.manaPlayerAvatar=avatar;
  window.manaAdminRequest = request;

  async function loadTrueSkill() {
    const target = $('#trueskill-results');
    target.textContent = 'Caricamento…';
    try {
      const rows = await request('/api/admin/trueskill');
      if (!state.user) return;
      const number = new Intl.NumberFormat('it-IT', {minimumFractionDigits:2, maximumFractionDigits:2});
      target.innerHTML = rows.length ? `<div class="table-wrap"><table aria-label="Confronto TrueSkill ed Elo">
        <thead><tr><th>Pos. TrueSkill</th><th>Giocatore</th><th>Punteggio TrueSkill</th><th>Abilità</th><th>Incertezza</th><th>Partite</th><th>Pos. Elo</th><th>Elo</th></tr></thead>
        <tbody>${rows.map(row => `<tr><td>${row.rank ?? '—'}</td><th scope="row">${esc(row.name)}</th><td>${row.matches ? number.format(row.score) : '—'}</td><td>${number.format(row.mu)}</td><td>${number.format(row.sigma)}</td><td>${row.matches}</td><td>${row.elo_rank}</td><td>${row.elo}</td></tr>`).join('')}</tbody>
        </table></div>` : '<p>Nessun giocatore attivo.</p>';
    } catch (error) {
      target.textContent = error.message;
    }
  }
  function setView(view){
    const publicPage = view === 'public';
    const liveOpen = publicPage && (location.hash === '#live' || location.hash.startsWith('#live/'));
    const historyOpen = publicPage && location.hash === '#history';
    if (publicPage && location.hash === '#life') {
      location.replace('/segnavita#life');
      return;
    }
    const ranking = publicPage && location.hash === '#global-ranking';
    const home = publicPage && !ranking && !historyOpen && !liveOpen;
    $('#live').hidden = !liveOpen;
    $('#history').hidden = !historyOpen;
    $('#admin-view').hidden = publicPage;
    $('#public-view').hidden = !home && !ranking;
    $('#global-ranking').hidden = !ranking;
    $('#tournament-home').hidden = !home;
    $$('[data-view-link]').forEach(a => {
      const selected = a.dataset.viewLink === 'admin' ? !publicPage : home;
      a.setAttribute('aria-current', selected ? 'page' : 'false');
    });
    for(const [selector, active] of [['[data-live-link]',liveOpen],['[data-history-link]',historyOpen],['[data-ranking-link]',ranking]]){
      $(selector).setAttribute('aria-current', active ? 'page' : 'false');
    }
    if(liveOpen) window.openLiveGames();
    else if(historyOpen) window.openMatchHistory();
    else if(!publicPage) refreshAdmin().catch(e=>showError(e.message));
    else refreshPublic().catch(e=>showError(e.message,'#public-error'));
  }
  let personalSnapshot = '';
  function renderPersonalMatches(tournaments, ranking) {
    const section = $('#personal-matches'), host = $('#personal-match-list');
    const items = savedPlayer() && !identityPending ? tournaments.filter(t => t.my_match) : [];
    const markup = items.map(t => {
      const match = t.my_match, opponent = ranking.find(p => p.id === match.opponent_id);
      const table = match.table != null ? ` · Tavolo ${esc(match.table)}` : '';
      let action = '';
      if (t.reveal_locked) action = `<button type="button" class="button primary" data-reveal-home="${esc(t.id)}">Scopri incontro</button>`;
      else if (opponent && !match.completed && !match.bye) action = `<button type="button" class="button primary" data-personal-result="${esc(match.match_id)}">Inserisci risultato</button>`;
      const status = match.bye ? 'Passi il turno' : match.completed ? 'Incontro concluso' : '';
      return `<article class="personal-match"><div><h3><span aria-hidden="true">🏆</span> ${esc(t.config.name)}</h3><p class="muted">Turno ${esc(match.round)}${table}</p>${opponent ? `<p class="personal-opponent">${avatar(opponent.name,opponent.photo_url,opponent.avatar_hue,opponent.avatar_color)}<strong>${esc(opponent.name)}</strong></p>` : ''}${status ? `<p class="muted">${status}</p>` : ''}</div>${action}</article>`;
    }).join('');
    section.hidden = !markup;
    if (markup === personalSnapshot) return;
    const focus = captureFocus(host);
    personalSnapshot = markup;
    host.innerHTML = markup;
    restoreFocus(host,focus);
  }
  let publicRequest = 0;
  async function refreshPublic() {
    if (!state.visible || state.editing || state.busy || $('#confirm-dialog').open || $('#player-results-dialog').open || $('#reveal-dialog').open) return;
    const serial = ++publicRequest, identity = savedPlayer();
    try {
      const [list,ranking] = await Promise.all([request('/api/public/tournaments'),request('/api/public/ranking')]);
      if (serial !== publicRequest || identityPending || identity !== savedPlayer()) return;
      renderRanking(ranking);
      renderPersonalMatches(list,ranking);
      const snapshot = JSON.stringify(list);
      state.tournaments = list;
      $('#public-error').hidden = true;
      if (snapshot !== state.publicListSnapshot) {
        state.publicListSnapshot = snapshot;
        renderPublicList();
      }
      if (state.publicId) {
        if (list.some(t => t.id === state.publicId)) await loadPublicDetail(state.publicId,true);
        else { state.publicId=''; state.publicDetailSnapshot=''; $('#public-detail').hidden=true; }
      }
    } catch (e) { showError(e.message,'#public-error'); }
  }
  function renderPublicList(){const host=$('#tournament-list'),focus=captureFocus(host);$('#tournament-count').textContent=String(state.tournaments.length);if(!state.tournaments.length){host.innerHTML='<article class="empty-card"><strong>Nessun torneo in corso</strong><span>Quando l’organizzatore avvierà un torneo, lo troverai qui.</span></article>';return;}
    host.innerHTML=state.tournaments.map((t,i)=>{const c=t.config||{};return `<article class="tournament-card ${t.status==='active'?'is-active-tournament':''}" style="animation-delay:${Math.min(i*45,250)}ms"><div class="card-top"><span class="state-tag ${t.status==='completed'?'completed':''}">${t.status==='completed'?'Concluso':'In corso'}</span></div><h3><span aria-hidden="true">🏆</span> ${esc(c.name||'Torneo senza nome')}</h3><p class="card-meta">${dateLabel(c.date)} <span aria-hidden="true">·</span> ${configSummary(c)}</p><div class="card-bottom">${t.reveal_available&&t.reveal_locked?`<button type="button" class="open-card" data-reveal-home="${esc(t.id)}">Scopri il tuo incontro <span aria-hidden="true">→</span></button>`:`<button class="open-card" data-public-open="${esc(t.id)}">Apri torneo <span aria-hidden="true">→</span></button>`}</div></article>`;}).join('');restoreFocus(host,focus);}
  let identityPending=false;
  async function loadPublicDetail(id, quiet=false){const identity=savedPlayer();try{const t=await request(`/api/public/tournaments/${encodeURIComponent(id)}`);if(identityPending||identity!==savedPlayer())return;state.publicId=id;state.publicDetailData=t;const snapshot=JSON.stringify(t);if(!quiet||snapshot!==state.publicDetailSnapshot){state.publicDetailSnapshot=snapshot;renderPublicDetail(t);}if(!quiet)$('#public-detail').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth',block:'start'});return t;}catch(e){showError(e.message,'#public-error');}}
  function renderPublicDetail(t){const host=$('#public-detail'),focus=captureFocus(host), people=new Map((t.entrants||[]).map(p=>[p.id,p]));host.hidden=false;const c=t.config||{};
    const tabs=[['standings','Classifica'],['rounds','Turni'],['players','Partecipanti']];const statuses=new Map((t.standings||[]).map(row=>[row.player_id,row.eliminated]));let body='';
    if(state.detailTab==='players') body=`<div class="mascot-section-heading"><h3>Partecipanti · ${(t.entrants||[]).length}</h3></div><div class="player-strip">${(t.entrants||[]).map(p=>`<div class="player-chip ${p.id===savedPlayer()?'is-me':''} ${statuses.get(p.id)?'is-eliminated':''}">${avatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)}<span>${esc(p.name)}</span>${p.withdrawn?'<span class="badge">Ritirato</span>':t.status==='active'?`<span class="player-status ${statuses.get(p.id)?'eliminated':'in-play'}">${statuses.get(p.id)?'Eliminato':'In gioco'}</span>`:''}</div>`).join('')||'<p class="muted">Nessun partecipante.</p>'}</div>`;
    else if(state.detailTab==='rounds') body=renderRoundPanel({scope:'public',structure:c.structure,rounds:t.rounds||[],entrants:t.entrants||[],matches:t.matches||[],tournament:t,people});
    else body=renderStandings(t.standings||[],people,c,t.status);
    host.innerHTML=`<div class="detail-head"><div><h2><span aria-hidden="true">🏆</span> ${esc(c.name||'Torneo')}</h2><p class="muted">${dateLabel(c.date)} · ${configSummary(c)} · ${t.status==='completed'?'Concluso':'In corso'}</p>${c.description?`<p class="muted">${esc(c.description)}</p>`:''}</div><div class="detail-actions">${t.reveal_locked ? '<button type="button" class="button primary" data-reveal-open>Scopri incontro</button>' : ''}<button type="button" class="button quiet" data-close-detail>Chiudi</button></div></div><div class="stats-row"><span class="stat-chip">${(t.entrants||[]).length} partecipanti</span>${t.champion_id?`<span class="stat-chip">Campione: ${esc(people.get(t.champion_id)?.name||'—')}</span>`:''}</div><div class="detail-tabs" role="tablist" aria-label="Dettaglio torneo">${tabs.map(([key,label])=>`<button type="button" role="tab" aria-selected="${state.detailTab===key}" data-detail-tab="${key}">${label}</button>`).join('')}</div><div class="detail-body">${body}</div>`;syncTournamentLive();restoreFocus(host,focus);scheduleGraphConnections();}
  document.addEventListener('live-games-updated', () => syncTournamentLive());
  function syncTournamentLive() {
    const actions = $('#public-detail .detail-actions');
    const tournament = state.publicDetailData;
    if (!actions || !tournament) return;
    const existing = actions.querySelector('[data-tournament-live]');
    const href = window.tournamentLiveHref(tournament.id);
    if (!href) { existing?.remove(); return; }
    if (existing) { existing.href = href; return; }
    const link = document.createElement('a');
    link.className = 'button quiet';
    link.dataset.tournamentLive = '';
    link.href = href;
    link.textContent = 'Segui in diretta';
    actions.prepend(link);
  }
  let revealTimer=0, revealSession=0;
  async function openReveal(){
    const t=state.publicDetailData, dialog=$('#reveal-dialog');if(!t)return;
    if(!savedPlayer()){message('Seleziona il tuo nome in alto.','error');return;}
    const serial=++revealSession, output=$('#reveal-result'),stage=$('#reveal-stage'),label=$('#reveal-opponent-label');output.textContent='';label.hidden=true;stage.classList.remove('is-revealing');dialog.showModal();
    void stage.offsetWidth;stage.classList.add('is-revealing');
    const show=async()=>{try{
      if(!dialog.open||serial!==revealSession)return;
      const player=await request('/api/player/session');
      if(!dialog.open||serial!==revealSession)return;
      const result=await request(`/api/player/tournaments/${encodeURIComponent(t.id)}/reveal`,{method:'POST',headers:{'X-Player-CSRF':player.csrf_token}});
      if(!dialog.open||serial!==revealSession)return;
      label.hidden=!result.opponent;
      output.innerHTML=result.opponent?`${avatar(result.opponent.name,result.opponent.photo_url,result.opponent.avatar_hue,result.opponent.avatar_color)}<strong>${esc(result.opponent.name)}</strong>`:(result.bye?'<strong>Passi il turno</strong>':'<strong>Nessun incontro per questo turno</strong>');
      loadPublicDetail(t.id,true);
    }catch(e){if(dialog.open&&serial===revealSession)output.textContent=e.message;}};
    if(matchMedia('(prefers-reduced-motion: reduce)').matches)show();else revealTimer=setTimeout(show,1800);
  }
  function closeReveal(){revealSession++;clearTimeout(revealTimer);const dialog=$('#reveal-dialog');if(dialog.open)dialog.close();}
  function renderStandings(rows,people,c,status){const tb=(rows||[]).map(r=>{const p=people.get(r.player_id)||{};return `<tr class="${r.player_id===savedPlayer()?'is-me':''} ${r.eliminated?'is-eliminated':''}"><td class="rank">${esc(r.rank)}</td><td>${esc(r.seed??'—')}</td><td><span class="player-chip">${avatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)} ${esc(p.name||'Giocatore')} ${status==='active'?`<span class="player-status ${r.eliminated?'eliminated':'in-play'}">${r.eliminated?'Eliminato':'In gioco'}</span>`:''}</span></td><td>${esc(r.points)}</td><td>${esc(r.wins)}–${esc(r.losses)}</td></tr>`;}).join('');return `<div class="mascot-section-heading"><h3>Classifica</h3></div><div class="table-wrap"><table><thead><tr><th>Pos.</th><th>Seed</th><th>Giocatore</th><th>Punti</th><th>V–S</th></tr></thead><tbody>${tb||'<tr><td colspan="5">Classifica non ancora disponibile.</td></tr>'}</tbody></table></div>`;}
  function isVoid(match){return Boolean(match.void)||match.result==='void';}
  function renderMatchCard({label, kind, sides, winner, score, status, elo_deltas, action = ''}) {
    const teams = sides.map((side,index) => `<div class="match-card-team${winner === index ? ' match-card-winner' : ''}">${side.map((player, position) => {
      const name = typeof player === 'string' ? player : player.name;
      const photo = typeof player === 'string' ? '' : avatar(name,player.photo_url,player.avatar_hue,player.avatar_color);
      const elo = elo_deltas?.[index]?.[position];
      const delta = Number.isInteger(elo) ? `<span class="match-card-elo" data-direction="${Math.sign(elo)}">${elo > 0 ? '+' : ''}${elo} Elo</span>` : '';
      return `<div class="match-card-player${player?.id===savedPlayer() ? ' match-card-you' : ''}">${photo}<span>${esc(name)}</span>${delta}</div>`;
    }).join('')}${winner === index ? '<strong class="match-card-outcome">Vittoria</strong>' : ''}</div>`);
    return `<article class="match-card"><div class="match-card-heading"><strong>${kind === 'tournament' ? '<span aria-hidden="true">🏆</span> ' : ''}${esc(label)}</strong>${status ? `<span class="muted">${esc(status)}</span>` : ''}</div><div class="match-card-teams">${teams[0]}<span class="match-card-score" aria-label="${score ? 'Punteggio' : 'Contro'}">${score ? esc(score.join('–')) : 'VS'}</span>${teams[1]}</div>${action}</article>`;
  }
  window.manaMatchCard = renderMatchCard;
  const roundOverrides = new Map();
  function renderRoundMatch(match, people, action = '') {
    const voidMatch = isVoid(match);
    const outcomes = {p1_win:0,p2_win:1,p1_forfeit:1,p2_forfeit:0};
    const hasScore = !voidMatch && !match.bye && !match.score_unknown &&
      ['p1_win','p2_win','draw'].includes(match.result) && match.p1_games != null && match.p2_games != null;
    const status = voidMatch ? 'Annullato' : match.bye ? 'Passa il turno' :
      ({draw:'Pareggio',p1_forfeit:'Forfait',p2_forfeit:'Forfait'}[match.result] || (match.result ? '' : 'In attesa'));
    return renderMatchCard({
      label: `Tavolo ${match.table ?? '—'}${match.bracket_slot !== undefined ? ` · ${match.bracket_slot}` : ''}`,
      sides: [[people.get(match.p1) || 'Giocatore'], [people.get(match.p2) || (match.bye ? 'Bye' : 'Slot libero')]],
      winner: voidMatch || match.bye ? null : outcomes[match.result],
      score: hasScore ? [match.p1_games,match.p2_games] : null, status, action,
    });
  }
  function renderRounds(rounds,people,tournament) {
    const current = tournament.current_round;
    const locked = tournament.reveal_locked ? `<div class="round-card locked-round"><strong>Turno ${esc(current)}</strong><p class="muted">Scopri il tuo incontro per vedere gli abbinamenti.</p></div>` : '';
    if (!rounds.length && !locked) return '<p class="muted">Nessun turno disponibile.</p>';
    return `<div class="round-list">${locked}${[...rounds].sort((a,b) => b.round - a.round).map(round => {
      const key = `${tournament.id}/${round.round}`, isCurrent = round.round === current;
      const open = roundOverrides.get(key) ?? isCurrent;
      return `<details class="round-card round-disclosure" ${open ? 'open' : ''}><summary id="round-summary-${esc(round.round)}" class="round-title" data-round-disclosure="${esc(key)}"><strong>Turno ${esc(round.round)}${isCurrent && tournament.status === 'active' ? ' · Corrente' : ''}</strong><span class="badge">${round.matches.length} ${round.matches.length === 1 ? 'tavolo' : 'tavoli'}</span></summary><div class="round-matches">${round.matches.map(match => renderRoundMatch(match,people)).join('')}</div></details>`;
    }).join('')}</div>`;
  }
  function renderRoundPanel({scope,structure,rounds,entrants,matches,tournament,people}){
    const publicScope=scope==='public';
    const view=publicScope?state.publicRoundView:state.adminRoundView;
    const offset=publicScope?state.publicRoundOffset:state.adminRoundOffset;
    const selectedPlayerId=publicScope?state.publicSelectedPlayer:state.adminSelectedPlayer;
    const listMarkup=publicScope?renderRounds(rounds,people,tournament):renderAdminRounds(rounds,people,tournament);
    let graphMarkup='';
    let windowData=null;
    if(structure==='single_elim'){
      graphMarkup=renderEliminationGraph(ManaBrackets.buildEliminationBracket(entrants,matches),tournament,scope,people);
    }else{
      const model=ManaBrackets.buildRoundProgression(rounds,entrants,{selectedPlayerId,offset,windowSize:4});
      windowData=model;
      graphMarkup=renderProgressionGraph(model,tournament,scope,people,selectedPlayerId);
    }
    const showPlayerPath=view==='graph'&&structure!=='single_elim'&&entrants.length>0;
    const controls=`<div class="round-panel-controls"><div class="round-view-switch" role="group" aria-label="Vista dei turni"><button type="button" data-round-view="graph" data-round-scope="${scope}" aria-pressed="${view==='graph'}">Tabellone</button><button type="button" data-round-view="list" data-round-scope="${scope}" aria-pressed="${view==='list'}">Elenco</button></div>${showPlayerPath?`<label class="round-player-select">Percorso<select data-round-player data-round-scope="${scope}" aria-label="Evidenzia il percorso di un giocatore"><option value="">Tutti</option>${entrants.map(player=>`<option value="${esc(player.id)}" ${player.id===selectedPlayerId?'selected':''}>${esc(player.name)}</option>`).join('')}</select></label>`:''}${view==='graph'&&windowData&&windowData.totalRounds>4?`<div class="round-window-controls"><button type="button" class="button quiet small-button" data-round-window="prev" data-round-scope="${scope}" ${windowData.previousOffset===null?'disabled':''}>← Precedenti</button><span>Turni ${windowData.rounds[0]?.round??0}–${windowData.rounds.at(-1)?.round??0} di ${windowData.totalRounds}</span><button type="button" class="button quiet small-button" data-round-window="next" data-round-scope="${scope}" ${windowData.nextOffset===null?'disabled':''}>Successivi →</button></div>`:''}</div>`;
    return `<section class="round-panel">${controls}${view==='graph'?graphMarkup:listMarkup}</section>`;
  }
  function bracketRoundTitle(round,total){
    const remaining=Math.round(2 ** (total-round+1));
    const labels={2:'Finale',4:'Semifinali',8:'Quarti',16:'Ottavi',32:'Sedicesimi',64:'Trentaduesimi'};
    return labels[remaining]||`Turno ${round}`;
  }
  function graphPlayerRow(player,games,match,side,feeders=[]){
    let name=player?.name;
    if(!name&&match.placeholder&&feeders[side])name=`Vincente turno ${feeders[side].round} · tavolo ${feeders[side].slot+1}`;
    if(!name&&match.status==='bye'&&side===1)name='Bye';
    if(!name)name=match.placeholder?'Attesa':'Slot vuoto';
    const won=Boolean(player&&match.winnerId===player.id);
    const forfeited=match.result===(side===0?'p1_forfeit':'p2_forfeit');
    const score=player?(match.status==='pending'||match.status==='bye'||match.status==='void'||match.apiMatch?.score_unknown?'—':games):'';
    return `<div class="graph-player-row ${won?'is-winner':''} ${player?.id===savedPlayer()?'is-me':''} ${forfeited?'is-forfeit':''}"${player?.name?` title="${esc(player.name)}"`:''}>${player?avatar(player.name,player.photo_url,player.avatar_hue,player.avatar_color):'<span class="graph-avatar-placeholder" aria-hidden="true">·</span>'}<span class="graph-player-name">${esc(name)}</span><span class="graph-score">${esc(score)}</span></div>`;
  }
  function graphMatchStatus(match,people){
    if(match.status==='void')return 'Annullato';
    if(match.status==='bye')return `Avanza ${people.get(match.apiMatch?.p1)?.name||'il partecipante'}`;
    if(match.status==='forfeit-p1')return `Forfait di ${people.get(match.apiMatch?.p1)?.name||'P1'}`;
    if(match.status==='forfeit-p2')return `Forfait di ${people.get(match.apiMatch?.p2)?.name||'P2'}`;
    if(match.status==='complete')return match.winnerId?`Vince ${people.get(match.winnerId)?.name||'il vincitore'}`:'Concluso';
    return match.placeholder?'Da disputare':'In attesa';
  }
  function renderGraphMatch(match,people,{scope='public',tournament=null,feeders=match.feeders||[],heading=''}={}){
    const apiMatch=match.apiMatch;
    const allowAction=scope==='admin'&&tournament?.status==='active'&&match.matchId&&match.status!=='void'&&match.status!=='bye'&&Boolean(apiMatch?.result);
    const selected=Boolean(match.containsSelectedPlayer);
    return `<article class="graph-match-card ${esc(match.status)} ${selected?'in-player-path':''} ${match.selectedPlayerOutcome==='lost'?'path-loss':''}" data-graph-key="${esc(match.key)}"${match.matchId?` data-real-match="${esc(match.matchId)}"`:''}><div class="graph-match-heading"><span>${heading||`Turno ${esc(match.round)} · Tavolo ${esc(apiMatch?.table??match.slot+1)}`}</span>${match.placeholder?'<span class="badge">Previsto</span>':''}</div>${graphPlayerRow(match.p1,match.p1Games,match,0,feeders)}${graphPlayerRow(match.p2,match.p2Games,match,1,feeders)}<div class="graph-match-footer"><span>${esc(graphMatchStatus(match,people))}</span>${allowAction?`<button type="button" class="button ${apiMatch?.result?'quiet':'secondary'} small-button" data-result="${esc(match.matchId)}">${apiMatch?.result?'Correggi':'Registra esito'}</button>`:''}</div></article>`;
  }
  function renderEliminationGraph(model,tournament,scope,people){
    if(!model.capacity)return '<p class="muted">Nessun incontro.</p>';
    const edgeKeys=model.edges.map(edge=>({from:`r${edge.from.round}s${edge.from.slot}`,to:`r${edge.to.round}s${edge.to.slot}`}));
    const headerHeight=64;
    const columns=model.rounds.map(column=>{
      const title=bracketRoundTitle(column.round,model.totalRounds);
      const cards=column.matches.map(match=>{
        const feeders=match.round===1?[]:[{round:match.round-1,slot:match.slot*2},{round:match.round-1,slot:match.slot*2+1}];
        return renderGraphMatch(match,people,{scope,tournament,feeders,heading:match.placeholder?`Incontro ${match.slot+1}`:`Tavolo ${esc(match.apiMatch?.table??match.slot+1)}`});
      }).join('');
      return `<section class="elimination-column" style="left:${(column.round-1)*324}px"><h4>${title}</h4>${cards}</section>`;
    }).join('');
    const width=model.totalRounds*324-64;
    return `<div class="graph-scroll" role="region" aria-label="Tabellone a eliminazione diretta" tabindex="0"><p class="graph-scroll-hint">Scorri per seguire il tabellone</p><div class="elimination-canvas graph-canvas" data-graph-canvas data-bracket-capacity="${model.capacity}" data-graph-edges="${esc(JSON.stringify(edgeKeys))}" style="width:${width}px"><svg class="graph-connectors" aria-hidden="true" focusable="false"></svg>${columns}</div></div>`;
  }
  function renderProgressionGraph(model,tournament,scope,people,selectedPlayerId){
    if(!model.totalRounds)return '<p class="muted">I turni appariranno all’avvio del torneo.</p>';
    const edgeKeys=model.path.edges.map(edge=>({from:`match-${edge.from}`,to:`match-${edge.to}`}));
    const columns=model.rounds.map(round=>`<section class="progression-column"><h4>Turno ${esc(round.round)}</h4>${round.matches.map(match=>renderGraphMatch(match,people,{scope,tournament,heading:`Tavolo ${esc(match.apiMatch?.table??match.slot??'—')}`})).join('')}</section>`).join('');
    return `<div class="graph-scroll progression-scroll" role="region" aria-label="Progressione dei turni" tabindex="0"><p class="graph-scroll-hint">${selectedPlayerId?'Percorso del giocatore evidenziato':'Scorri per consultare turni e tavoli'}</p><div class="progression-canvas graph-canvas" data-graph-canvas data-graph-edges="${esc(JSON.stringify(edgeKeys))}"><svg class="graph-connectors" aria-hidden="true" focusable="false"></svg>${columns}</div></div>`;
  }
  function scheduleGraphConnections(){requestAnimationFrame(()=>document.querySelectorAll('[data-graph-canvas]').forEach(canvas=>{if(canvas.closest('[hidden]'))return;if(canvas.classList.contains('elimination-canvas')&&!layoutEliminationCanvas(canvas))return;drawGraphConnections(canvas);}));}
  function layoutEliminationCanvas(canvas){
    const cards=$$('[data-graph-key]',canvas);
    if(!cards.length)return false;
    const cardHeights=cards.map(card=>card.getBoundingClientRect().height);
    if(cardHeights.some(height=>height<=0))return false;
    const layout=ManaBrackets.layoutEliminationBracket(Number(canvas.dataset.bracketCapacity),cardHeights);
    if(!layout.positions.length)return false;
    const cardsByKey=new Map(cards.map(card=>[card.dataset.graphKey,card]));
    for(const position of layout.positions){
      const card=cardsByKey.get(position.key);
      if(card)card.style.top=`${position.center}px`;
    }
    canvas.style.height=`${layout.height}px`;
    canvas.dataset.layoutReady='true';
    return true;
  }
  function drawGraphConnections(canvas){
    const svg=$('svg.graph-connectors',canvas);
    if(!svg)return;
    svg.replaceChildren();
    const width=canvas.scrollWidth,height=canvas.scrollHeight;
    svg.setAttribute('width',String(width));svg.setAttribute('height',String(height));svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    let edges=[];try{edges=JSON.parse(canvas.getAttribute('data-graph-edges')||'[]');}catch{return;}
    const canvasRect=canvas.getBoundingClientRect();
    for(const edge of edges){
      const from=$$('[data-graph-key]',canvas).find(node=>node.dataset.graphKey===edge.from);
      const to=$$('[data-graph-key]',canvas).find(node=>node.dataset.graphKey===edge.to);
      if(!from||!to)continue;
      const a=from.getBoundingClientRect(),b=to.getBoundingClientRect();
      const x1=a.right-canvasRect.left,y1=a.top+a.height/2-canvasRect.top;
      const x2=b.left-canvasRect.left,y2=b.top+b.height/2-canvasRect.top;
      const curve=Math.max(24,(x2-x1)*.45);
      const path=document.createElementNS('http://www.w3.org/2000/svg','path');
      path.setAttribute('d',`M ${x1} ${y1} C ${x1+curve} ${y1}, ${x2-curve} ${y2}, ${x2} ${y2}`);
      path.setAttribute('class','graph-edge');svg.append(path);
    }
  }
  async function getSession(){try{const s=await request('/api/admin/session');state.user=s.username;state.csrf=s.csrf_token;showWorkspace();return true;}catch(e){if(e.status===401){state.user='';state.csrf='';showLogin();return false;}throw e;}}
  function showLogin(){ $('#trueskill-results').replaceChildren(); $('#login-panel').hidden=false;$('#admin-workspace').hidden=true; }
  function showWorkspace(){ $('#login-panel').hidden=true;$('#admin-workspace').hidden=false;$('#admin-identity').textContent=state.user; }
  async function refreshAdmin(){if(!state.visible||state.editing||state.busy||$('#confirm-dialog').open||$('#player-results-dialog').open||$('#reveal-dialog').open)return;clearError();try{if(!state.user&&!await getSession())return;const [ps,ts]=await Promise.all([request('/api/admin/players'),request('/api/admin/tournaments')]);state.players=ps;state.tournaments=ts;showWorkspace();renderPlayers();renderAdminTournaments();if(state.current?.id){const latest=ts.find(t=>t.id===state.current.id);if(latest)await openManager(latest.id,true);else {state.current=null;state.managerSnapshot='';}}}catch(e){if(e.status===401){state.user='';state.csrf='';showLogin();}else showError(e.message);}}
  function renderAdminTournaments(){const host=$('#admin-tournament-list'),focus=captureFocus(host);if(!state.tournaments.length){host.innerHTML='<p class="muted">Nessun torneo.</p>';return;}const labels={draft:'Bozza',active:'In corso',completed:'Concluso',archived:'Archiviato'};host.innerHTML=state.tournaments.map(t=>`<article class="admin-event-row"><div><h3><span aria-hidden="true">🏆</span> ${esc(t.config?.name||'Torneo senza nome')}</h3><div class="admin-event-info"><span class="state-tag ${t.status==='completed'?'completed':''}">${labels[t.status]||esc(t.status)}</span><span class="muted">${configSummary(t.config||{})}</span></div></div><div class="admin-event-actions"><button class="button secondary small-button" data-manage="${esc(t.id)}">Gestisci</button></div></article>`).join('');restoreFocus(host,focus);}
  function renderPlayers(){const host=$('#players-list');if(!state.players.length){host.innerHTML='<p class="muted">Nessun profilo attivo.</p>';return;}host.innerHTML=state.players.map(p=>`<article class="player-list-row">${avatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)}<h3>${esc(p.name)}</h3><div class="player-actions">${!p.photo_url?`<label class="avatar-color">Colore<input type="range" min="0" max="360" value="${p.avatar_hue??215}" data-avatar-hue="${esc(p.id)}" aria-label="Colore mascotte per ${esc(p.name)}"></label>`:''}<label class="photo-upload">Foto<input type="file" accept="image/jpeg,image/png,image/webp" data-photo="${esc(p.id)}" aria-label="Carica foto per ${esc(p.name)}"></label><button class="button quiet small-button" data-edit-player="${esc(p.id)}">Rinomina</button><button class="button danger small-button" data-delete-player="${esc(p.id)}">Rimuovi</button></div></article>`).join('');}
  function setManagerTab(tab){state.managerTab=tab;$$('[data-manager-tab]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.managerTab===tab)));const host=$('.manager-content');if(host&&state.current){host.innerHTML=managerContent(state.current,tab);scheduleGraphConnections();}}
  async function openManager(id,quiet=false){const t=await request(`/api/admin/tournaments/${encodeURIComponent(id)}`),snapshot=JSON.stringify(t);if(quiet&&state.current?.id===id&&snapshot===state.managerSnapshot)return;state.current=t;state.managerSnapshot=snapshot;$('#tournament-manager').hidden=false;$('#tournament-form').hidden=true;renderManager();if(!quiet)$('#tournament-manager').scrollIntoView({behavior:'smooth',block:'start'});}
  function renderManager(){const t=state.current;if(!t)return;const host=$('#tournament-manager'),focus=captureFocus(host),labels={draft:'Bozza',active:'In corso',completed:'Concluso',archived:'Archiviato'};host.hidden=false;host.innerHTML=`<div class="manager-top"><div><h2><span aria-hidden="true">🏆</span> ${esc(t.config?.name||'Torneo')}</h2><span class="state-tag">${labels[t.status]||esc(t.status)} · ${configSummary(t.frozen_config||t.config||{})}</span></div><div class="manager-actions"><button class="button quiet small-button" data-manager-action="edit-config">Regole</button>${t.status==='draft'?'<button class="button danger small-button" data-manager-action="delete">Elimina bozza</button>':''}${t.status!=='archived'?'<button class="button quiet small-button" data-manager-action="archive">Archivia</button>':'<button class="button secondary small-button" data-manager-action="reopen">Riapri</button>'}</div></div><div class="manager-subnav" role="toolbar" aria-label="Gestione torneo">${[['entrants','Iscritti'],['matches','Turni e risultati'],['audit','Cronologia']].map(([k,l])=>`<button type="button" data-manager-tab="${k}" aria-pressed="${state.managerTab===k}">${l}</button>`).join('')}</div><div class="manager-content">${managerContent(t,state.managerTab)}</div>`;syncTournamentLive();restoreFocus(host,focus);scheduleGraphConnections();}
  function managerContent(t,tab){const entrants=t.entrants||[],matches=t.matches||[],people=new Map(entrants.map(p=>[p.id,p]));if(tab==='audit')return `<div class="manager-box"><div class="mascot-section-heading"><h3>Cronologia</h3></div><button class="button secondary" data-load-audit>Carica</button><div id="audit-content" class="audit-list"></div></div>`;
    if(tab==='matches')return `<div class="manager-box"><div class="manager-table-actions">${t.status==='active'&&t.frozen_config?.structure!=='league'?'<button class="button primary" data-manager-action="next-round">Crea turno successivo</button>':''}${t.status==='active'?'<button class="button danger" data-manager-action="reset">Ripristina bozza…</button>':''}${t.status==='completed'?'<button class="button secondary" data-manager-action="reopen">Riapri torneo</button>':''}<label>Esporta CSV<select data-export-kind aria-label="Tipo CSV"><option value="standings">Classifica</option><option value="entrants">Iscritti</option><option value="matches">Incontri</option></select></label><button class="button quiet" data-export>Scarica CSV</button></div>${matches.length?renderRoundPanel({scope:'admin',structure:(t.frozen_config||t.config||{}).structure,rounds:t.rounds||groupRounds(matches),entrants:t.entrants||[],matches,tournament:t,people}):'<p class="muted">Nessun turno generato.</p>'}</div>`;
    const manager=t.status==='draft'?`<div class="manager-box"><div class="mascot-section-heading"><h3>Iscritti e seed</h3></div><div class="entrant-select">${state.players.filter(p=>!entrants.some(e=>e.id===p.id)).map(p=>`<label class="entrant-option"><input type="checkbox" name="entrant-choice" value="${esc(p.id)}"> ${esc(p.name)}</label>`).join('')||'<p class="muted">Nessun profilo disponibile.</p>'}</div><button class="button secondary" data-add-entrants>Aggiungi selezionati</button>${entrants.length?`<h3>Seed · ${entrants.length}/64</h3>${entrants.map(p=>`<label class="seed-row"><span>${esc(p.name)}</span><input type="number" min="1" max="64" value="${esc(p.seed??'')}" aria-label="Seed per ${esc(p.name)}" data-seed-player="${esc(p.id)}"><button class="button danger small-button" data-remove-entrant="${esc(p.id)}">Togli</button></label>`).join('')}`:'<p class="muted">Aggiungi almeno due profili.</p>'}<div class="manager-table-actions"><button class="button quiet" data-manager-action="save-seeds">Salva seed</button><button class="button secondary" data-seed-ranking>Usa classifica globale</button><button class="button primary" data-manager-action="start" ${entrants.length<2?'disabled':''}>Riepilogo e avvia</button></div></div>`:`<div class="manager-box"><div class="mascot-section-heading"><h3>Partecipanti · ${entrants.length}</h3></div><div class="player-strip">${entrants.map(p=>`<div class="player-chip">${avatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)}<span>${esc(p.name)}</span>${p.withdrawn?'<span class="badge">Ritirato</span>':''}</div>`).join('')}</div><h3>Riepilogo</h3><p class="muted">${configSummary(t.frozen_config||t.config||{})}</p><p class="hint">Il calendario all’italiana si genera all’avvio. Crea i turni per Svizzera o eliminazione.</p></div>`;
    return manager+(t.status==='draft'?'<div class="manager-box"><button class="button quiet" data-manager-action="delete">Elimina bozza</button></div>':'');}
  function groupRounds(matches){const groups=new Map();for(const m of matches){if(!groups.has(m.round))groups.set(m.round,[]);groups.get(m.round).push(m);}return [...groups].sort((a,b)=>a[0]-b[0]).map(([round,ms])=>({round,matches:ms}));}
  function renderAdminRounds(rounds,people,tournament){
    return `<div class="round-list">${[...rounds].sort((a,b) => b.round - a.round).map(round =>
      `<section class="round-card"><div class="round-title"><strong>Turno ${esc(round.round)}</strong><span class="badge">${round.matches.length} tavoli</span></div><div class="round-matches">${round.matches.map(match => {
        const action = !match.bye && !isVoid(match) && match.result && tournament.status === 'active'
          ? `<button type="button" class="button quiet small-button" data-result="${esc(match.id)}">Correggi risultato</button>` : '';
        return renderRoundMatch(match,people,action);
      }).join('')}</div></section>`).join('')}</div>`;
  }
  let dialogQueue=Promise.resolve();
  function queuedDialog({title,message,impact,confirmText='Conferma',danger=true,confirmValue,onOpen}){
    const pending=dialogQueue.then(()=>new Promise(resolve=>{
      const dialog=$('#confirm-dialog'),ok=$('#confirm-ok'),cancel=$('#confirm-cancel'),close=$('[data-dialog-close]',dialog),host=$('#confirm-impact');
      $('#confirm-title').textContent=title;
      $('#confirm-message').textContent=message;
      host.replaceChildren();
      if(impact)host.append(impact);
      ok.textContent=confirmText;
      ok.className=`button ${danger?'danger':'primary'}`;
      let result=null;
      const finish=value=>{if(!dialog.open)return;result=value;dialog.close();};
      const confirm=()=>{const value=confirmValue();if(value!==undefined)finish(value);};
      const cancelAction=()=>finish(null);
      const nativeCancel=()=>{result=null;};
      const closed=()=>{
        ok.removeEventListener('click',confirm);
        cancel.removeEventListener('click',cancelAction);
        close.removeEventListener('click',cancelAction);
        dialog.removeEventListener('cancel',nativeCancel);
        resolve(result);
      };
      ok.addEventListener('click',confirm);
      cancel.addEventListener('click',cancelAction);
      close.addEventListener('click',cancelAction);
      dialog.addEventListener('cancel',nativeCancel);
      dialog.addEventListener('close',closed,{once:true});
      dialog.showModal();
      onOpen?.({dialog,finish});
    }));
    dialogQueue=pending.then(()=>undefined,()=>undefined);
    return pending;
  }
  function safeTextDialog(title,text,impact=[],danger=true){
    const list=document.createElement('div');
    for(const line of impact){const p=document.createElement('p');p.textContent=line;list.append(p);}
    return queuedDialog({title,message:text,impact:list,confirmText:'Conferma',danger,confirmValue:()=>true});
  }
  function confirmResetDialog(scope,counts){
    const descriptions={results:'Elimina tutti gli incontri e risultati; ogni torneo torna in bozza. Iscritti e profili restano.',tournaments:'Elimina tutti i tornei, iscrizioni e risultati. Profili e foto restano.',all:'Elimina tutti i tornei, risultati, profili giocatore e foto.'};
    const box=document.createElement('div'), detail=document.createElement('p'), label=document.createElement('label'), input=document.createElement('input');
    detail.textContent=`${counts.tournaments} tornei · ${counts.matches} incontri · ${counts.players} profili · ${counts.photos} foto. L’account organizzatore resta.`;box.append(detail);
    label.textContent='Scrivi ELIMINA per confermare';input.autocomplete='off';label.append(input);box.append(label);
    return queuedDialog({title:'Conferma eliminazione',message:descriptions[scope]+' Scarica prima un backup se vuoi conservarli.',impact:box,confirmText:'Elimina dati',danger:true,confirmValue:()=>{if(input.value!=='ELIMINA'){input.setCustomValidity('Scrivi ELIMINA per confermare.');input.reportValidity();input.setCustomValidity('');return undefined;}return true;},onOpen:()=>input.focus()});
  }
  function renameDialog(current){
    const label=document.createElement('label');
    label.textContent='Nome giocatore';
    const input=document.createElement('input');
    input.value=current;input.required=true;input.maxLength=100;label.append(input);
    return queuedDialog({title:'Rinomina giocatore',message:'Aggiorna il nome del profilo.',impact:label,confirmText:'Salva nome',danger:false,confirmValue:()=>{if(!input.reportValidity())return undefined;const name=input.value.trim();if(!name){input.setCustomValidity('Inserisci un nome.');input.reportValidity();input.setCustomValidity('');return undefined;}return name;},onOpen:()=>input.focus()});
  }
  function roundProgress(t){const rounds=groupRounds(t.matches||[]);const open=rounds.filter(r=>r.matches.some(m=>!m.result&&!m.void));return {rounds,open};}
  function nextRoundAllowed(t){
    const c=t.frozen_config||t.config||{};
    const {rounds,open}=roundProgress(t);
    const roundCount=rounds.length;
    if(c.structure==='league')return {ok:false,roundCount,reason:'Il calendario del girone è già stato generato all’avvio.'};
    if(open.length)return {ok:false,roundCount,reason:`Completa prima il turno ${open[0].round}.`};
    if(c.structure==='swiss'&&roundCount>=Number(c.rounds))return {ok:false,roundCount,reason:'Hai già generato tutti i turni della Svizzera configurati.'};
    return {ok:true,roundCount,reason:roundCount?'Crea il turno successivo.':'Dopo l’avvio, il primo turno si crea con questa azione.'};
  }
  async function handleAction(action){const t=state.current;if(!t)return;
    if(action==='edit-config'){if(t.status==='completed'||t.status==='archived'){if(t.status==='archived'){await message('Riapri il torneo prima di modificare le regole.','error');return;}const yes=await safeTextDialog('Riapri il torneo','Il torneo concluso tornerà attivo. Poi potrai modificare le regole con una seconda conferma.',['La riapertura è registrata nella cronologia.']);if(!yes)return;await request(`/api/admin/tournaments/${t.id}/reopen`,{method:'POST'});await openManager(t.id,true);}
      $('#tournament-form').hidden=false;fillConfigForm(state.current.config||{});$('#tournament-form-title').textContent='Modifica regole';return;}
    if(action==='start'){const ready=t.entrants||[];if(ready.length<2){message('Servono almeno due partecipanti.','error');return;}const c=t.config;const tiebreakOrder=[...(c.tiebreakers||[]).map(x=>({omw:'OMW % (punti avversari)',wins:'vittorie',game_diff:'differenza game'}[x]||x)),'seed'];const summary=[`${ready.length} partecipanti`,configSummary(c),`Seed: ${c.seeding==='random'?'casuale all’avvio':'manuale'}`,`Punti: vittoria ${c.points.win}, patta ${c.points.draw}, sconfitta ${c.points.loss}, bye ${c.points.bye}`,`Patte: ${c.allow_draws?'ammesse':'non ammesse'}`,`Spareggi nell’ordine: ${tiebreakOrder.join(' → ')}`];if(c.structure==='league')summary.push('Il calendario completo sarà creato all’avvio.');if(c.structure==='swiss'||c.structure==='adapted')summary.push('Il primo turno dovrà essere creato subito dopo l’avvio.');const yes=await safeTextDialog('Riepilogo prima dell’avvio','Avviando, le regole si congelano e il torneo diventa pubblico.',summary,false);if(!yes)return;await request(`/api/admin/tournaments/${t.id}/start`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await openManager(t.id,true);if(['swiss','adapted'].includes(c.structure)){const create=await safeTextDialog('Primo turno pronto','L’avvio non crea il primo turno per questa struttura. Vuoi generarlo adesso?',['I partecipanti verranno abbinati secondo i seed.']);if(create){await request(`/api/admin/tournaments/${t.id}/rounds/next`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await openManager(t.id,true);}}message('Torneo avviato.');return;}
    if(action==='next-round'){const gate=nextRoundAllowed(t);if(!gate.ok){message(gate.reason,'error');return;}const ok=await safeTextDialog('Crea turno successivo',gate.reason,[`Turni già presenti: ${gate.roundCount}`],false);if(!ok)return;await request(`/api/admin/tournaments/${t.id}/rounds/next`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await openManager(t.id,true);message('Turno creato.');return;}
    if(action==='save-seeds'){
      const inputs=$$('[data-seed-player]');
      const seeds=inputs.map(input=>Number(input.value));
      if(inputs.some((input,index)=>!Number.isInteger(seeds[index])||seeds[index]<1||seeds[index]>64)){
        inputs.find((input,index)=>!Number.isInteger(seeds[index])||seeds[index]<1||seeds[index]>64)?.focus();
        throw new Error('Ogni seed deve essere un numero da 1 a 64.');
      }
      if(new Set(seeds).size!==seeds.length){
        const duplicate=seeds.find((seed,index)=>seeds.indexOf(seed)!==index);
        inputs[seeds.indexOf(duplicate)]?.focus();
        throw new Error('Ogni partecipante deve avere un seed univoco. Nessuna modifica è stata inviata.');
      }
      for(const [index,input] of inputs.entries())await request(`/api/admin/tournaments/${t.id}/entrants/${encodeURIComponent(input.dataset.seedPlayer)}`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({seed:seeds[index]})});
      state.editing=false;
      await openManager(t.id,true);message('Seed salvati.');return;
    }
    if(action==='reset'){const yes=await safeTextDialog('Ripristina come bozza','Tutti i turni e i risultati saranno eliminati; iscritti e cronologia resteranno.',[`${(t.matches||[]).length} incontri rimossi`]);if(!yes)return;await request(`/api/admin/tournaments/${t.id}/reset`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});await openManager(t.id,true);message('Torneo riportato in bozza.');return;}
    if(action==='archive'){const yes=await safeTextDialog('Archivia torneo','Il torneo sparirà dalla vista pubblica. Potrai riaprirlo in seguito.',[t.config.name]);if(!yes)return;await request(`/api/admin/tournaments/${t.id}/archive`,{method:'POST'});state.current=null;await refreshAdmin();message('Torneo archiviato.');return;}
    if(action==='reopen'){const yes=await safeTextDialog('Riapri torneo','Il torneo tornerà allo stato salvato prima dell’archiviazione, oppure attivo se già concluso.');if(!yes)return;await request(`/api/admin/tournaments/${t.id}/reopen`,{method:'POST'});await openManager(t.id,true);message('Torneo riaperto.');return;}
    if(action==='delete'){const yes=await safeTextDialog('Elimina bozza','La bozza e le sue iscrizioni verranno eliminate.');if(!yes)return;await request(`/api/admin/tournaments/${t.id}`,{method:'DELETE'});state.current=null;await refreshAdmin();message('Bozza eliminata.');return;}
  }
  async function showResultForm(match){
    const p1=state.current.entrants.find(player=>player.id===match.p1);
    const p2=state.current.entrants.find(player=>player.id===match.p2);
    const config=state.current.frozen_config||state.current.config;
    const target=config.match_length===3?2:1;
    const impact=document.createElement('div');
    impact.className='impact-list';
    impact.innerHTML=`<form id="result-form" class="result-form"><label>Esito<select name="result"><option value="p1_win">Vince ${esc(p1?.name||'Giocatore 1')}</option><option value="p2_win">Vince ${esc(p2?.name||'Giocatore 2')}</option></select></label><label>Game ${esc(p1?.name||'Giocatore 1')}<input name="p1_games" type="number" min="0" max="3" value="${target}"></label><label>Game ${esc(p2?.name||'Giocatore 2')}<input name="p2_games" type="number" min="0" max="3" value="0"></label><button class="visually-hidden" type="submit" tabindex="-1" aria-hidden="true">Salva risultato</button></form>`;
    let form;
    const values=()=>{
      if(!form.reportValidity())return undefined;
      const value=formObject(form);
      return {result:value.result,p1_games:Number(value.p1_games),p2_games:Number(value.p2_games)};
    };
    return queuedDialog({
      title:'Registra risultato',
      message:`Turno ${match.round} · tavolo ${match.table}: ${p1?.name||'Giocatore'} contro ${p2?.name||'Giocatore'}`,
      impact,
      confirmText:'Salva risultato',
      danger:false,
      confirmValue:values,
      onOpen:({finish})=>{
        form=$('#result-form');
        form.elements.result.addEventListener('change',()=>{
          const outcome=form.elements.result.value;
          form.elements.p1_games.value=outcome.startsWith('p')&&outcome.endsWith('forfeit')?0:outcome==='p2_win'?0:outcome==='draw'?1:target;
          form.elements.p2_games.value=outcome.startsWith('p')&&outcome.endsWith('forfeit')?0:outcome==='p1_win'?0:outcome==='draw'?1:target;
        });
        form.addEventListener('submit',event=>{event.preventDefault();const value=values();if(value!==undefined)finish(value);});
      }
    });
  }
  async function recordResult(match){const correction=Boolean(match.result);if(correction){const preview=await request(`/api/admin/tournaments/${state.current.id}/rollback`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({match_id:match.id})}).catch(e=>e);if(!(preview instanceof Error)&&!preview?.detail){message('Impossibile verificare l’impatto della correzione.','error');return;}const d=preview.detail||{};const isLeague=(state.current.frozen_config||state.current.config).structure==='league';const ok=await safeTextDialog('Conferma correzione','La correzione invaliderà risultati dipendenti.',[`Turni coinvolti: ${(d.affected_rounds||[]).join(', ')||match.round}`,`Risultati da azzerare: ${d.affected_results??'da verificare'}`,isLeague?'Il calendario resta presente: registra il risultato corretto nell’incontro azzerato.':'Dopo il rollback, rigenera il turno e registra il risultato corretto.','La cronologia conserva i risultati precedenti.']);if(!ok)return;await request(`/api/admin/tournaments/${state.current.id}/rollback`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({match_id:match.id,confirm:true})});await openManager(state.current.id,true);message(isLeague?'Turno ripristinato. Registra il risultato corretto nell’incontro aperto.':'Turno ripristinato. Rigenera il turno e registra il risultato corretto.');return;}
    const values=await showResultForm(match,false);if(!values)return;const outcome=await request(`/api/admin/tournaments/${state.current.id}/matches/${encodeURIComponent(match.id)}/result`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(values)});await openManager(state.current.id,true);message('Risultato registrato.');window.showEloResult?.(outcome.elo_changes);}
  async function withdraw(id){const p=state.current.entrants.find(x=>x.id===id);const yes=await safeTextDialog('Ritira partecipante',`${p?.name||'Il giocatore'} non sarà abbinato nei turni futuri.`,['Gli incontri aperti consentiti possono essere registrati come forfait.']);if(!yes)return;const result=await request(`/api/admin/tournaments/${state.current.id}/withdrawals`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({player_id:id})});await openManager(state.current.id,true);message(`Ritiro registrato. Turno effettivo: ${result.effective_round??'—'}.`);}
  async function addEntrants(){const ids=$$('input[name="entrant-choice"]:checked').map(el=>el.value);if(!ids.length){message('Seleziona almeno un giocatore.','error');return;}await request(`/api/admin/tournaments/${state.current.id}/entrants`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({player_ids:ids})});state.editing=false;await openManager(state.current.id,true);message('Partecipanti aggiunti.');}
  function fillConfigForm(c){const f=$('#tournament-form');f.reset();for(const [key,value] of Object.entries({name:c.name||'',date:c.date||'',description:c.description||'',structure:c.structure||'swiss',game_format:c.game_format||'Modern',match_length:String(c.match_length||1),rounds:c.rounds??3,loss_threshold:c.loss_threshold??2,league_mode:c.league_mode||'single',seeding:c.seeding||'manual'})){if(f.elements[key])f.elements[key].value=value;}f.elements.allow_draws.checked=Boolean(c.allow_draws);for(const k of ['win','draw','loss','bye'])f.elements[`points.${k}`].value=c.points?.[k]??({win:3,draw:1,loss:0,bye:3}[k]);const order=c.tiebreakers||['omw','wins','game_diff'];const ol=$('#tiebreak-list');[...ol.children].sort((a,b)=>order.indexOf(a.dataset.tiebreak)-order.indexOf(b.dataset.tiebreak)).forEach(li=>ol.append(li));$$('#tiebreak-list li').forEach(li=>$('input[type=checkbox]',li).checked=order.includes(li.dataset.tiebreak));syncConfigFields();}
  function syncConfigFields(){const f=$('#tournament-form'),s=f.elements.structure.value;$('#rounds-field').hidden=s!=='swiss';$('#threshold-field').hidden=s!=='adapted';$('#league-field').hidden=s!=='league';const incompatible=['adapted','single_elim'].includes(s);f.elements.allow_draws.disabled=incompatible;if(incompatible)f.elements.allow_draws.checked=false;$('#draw-hint').textContent=s==='adapted'?'Svizzera adattata: patte non ammesse.':s==='single_elim'?'Eliminazione diretta: patte non ammesse.':'Il punteggio patta deve restare tra vittoria e sconfitta.';}
  function fillTie(order){const ol=$('#tiebreak-list');[...ol.children].sort((a,b)=>order.indexOf(a.dataset.tiebreak)-order.indexOf(b.dataset.tiebreak)).forEach(li=>ol.append(li));$$('#tiebreak-list li').forEach(li=>$('input[type=checkbox]',li).checked=order.includes(li.dataset.tiebreak));}
  async function exportCsv(){const kind=$('[data-export-kind]').value;const response=await request(`/api/admin/tournaments/${state.current.id}/export.csv?kind=${encodeURIComponent(kind)}`);const url=URL.createObjectURL(await response.blob());const a=document.createElement('a');a.href=url;a.download=`mana-arena-${kind}.csv`;document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);}
  async function clickHandler(e){const target=e.target.closest('button,summary[data-round-disclosure],[data-public-open],[data-photo],[data-seed-player]');if(!target)return;
    if(target.matches('[data-view-link]')){e.preventDefault();history.replaceState(null,'',`#${target.dataset.viewLink}-view`);setView(target.dataset.viewLink);return;}
    if(target.matches('[data-personal-result]')){
      document.dispatchEvent(new CustomEvent('player-result-open',{detail:target.dataset.personalResult}));
      return;
    }
    if(target.matches('[data-round-disclosure]')){
      roundOverrides.set(target.dataset.roundDisclosure,!target.parentElement.open);
      return;
    }
    if(target.matches('[data-reveal-home]')){
      state.detailTab='rounds';state.publicRoundView='list';
      if (await loadPublicDetail(target.dataset.revealHome,true)) await openReveal();
      return;
    }
    if(target.matches('[data-public-open]')){state.detailTab='rounds';state.publicRoundView='list';await loadPublicDetail(target.dataset.publicOpen);return;}
    if(target.matches('[data-detail-tab]')){state.detailTab=target.dataset.detailTab;state.publicDetailSnapshot='';await loadPublicDetail(state.publicId,true);return;}
    if(target.matches('[data-reveal-open]')){openReveal();return;}
    if(target.id==='reveal-close'){closeReveal();return;}
    if(target.matches('[data-close-detail]')){$('#public-detail').hidden=true;state.publicId='';return;}
    if(target.matches('[data-revoke-device]')){if(!confirm('Revocare l’accesso a questo dispositivo?'))return;target.disabled=true;await request(`/api/admin/devices/${encodeURIComponent(target.dataset.revokeDevice)}`,{method:'DELETE'});await loadDevices();message('Accesso revocato.');return;}
    if(target.matches('[data-admin-tab]')){$$('[data-admin-tab]').forEach(b=>b.setAttribute('aria-selected',String(b===target)));$$('[role=tabpanel].admin-section').forEach(s=>s.hidden=s.id!==`admin-${target.dataset.adminTab}`);if(target.dataset.adminTab==='events')scheduleGraphConnections();if(target.dataset.adminTab==='friendlies')window.openAdminFriendlies();if(target.dataset.adminTab==='devices')await loadDevices();if(target.dataset.adminTab==='trueskill')await loadTrueSkill();return;}
    if(target.matches('[data-manager-tab]')){setManagerTab(target.dataset.managerTab);return;}
    if(target.matches('[data-round-view]')){const scope=target.dataset.roundScope;if(scope==='public')state.publicRoundView=target.dataset.roundView;else state.adminRoundView=target.dataset.roundView;if(scope==='public'&&state.publicDetailData)renderPublicDetail(state.publicDetailData);else if(scope==='admin')renderManager();return;}
    if(target.matches('[data-round-window]')){const scope=target.dataset.roundScope,groups=scope==='public'?(state.publicDetailData?.rounds||[]):(state.current?.rounds||[]),entrants=scope==='public'?(state.publicDetailData?.entrants||[]):(state.current?.entrants||[]),offset=scope==='public'?state.publicRoundOffset:state.adminRoundOffset,model=ManaBrackets.buildRoundProgression(groups,entrants,{offset,windowSize:4}),next=target.dataset.roundWindow==='next'?model.nextOffset:model.previousOffset;if(next===null)return;if(scope==='public'){state.publicRoundOffset=next;if(state.publicDetailData)renderPublicDetail(state.publicDetailData);}else{state.adminRoundOffset=next;renderManager();}return;}
    if(target.id==='new-tournament'){state.current=null;$('#tournament-form-title').textContent='Nuovo torneo';fillConfigForm({});$('#tournament-form').hidden=false;$('#tournament-manager').hidden=true;$('#tournament-form').scrollIntoView({behavior:'smooth'});return;}
    if(target.matches('[data-close-form]')){$(`#${target.dataset.closeForm}`).hidden=true;state.editing=false;return;}
    if(target.matches('[data-manage]')){state.managerTab='entrants';await openManager(target.dataset.manage);return;}
    if(target.matches('[data-manager-action]')){await handleAction(target.dataset.managerAction);return;}
    if(target.matches('[data-reset-scope]')){const scope=target.dataset.resetScope,counts=await request('/api/admin/reset-data/preview');const yes=await confirmResetDialog(scope,counts);if(!yes)return;const result=await request('/api/admin/reset-data',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({scope,confirmation:'ELIMINA'})});state.current=null;state.managerSnapshot='';if(scope==='all')savePlayer('');$('#tournament-manager').hidden=true;await refreshAdmin();message(result.photos_not_deleted?`Dati eliminati, ma ${result.photos_not_deleted} foto non sono state rimosse.`:'Dati selezionati eliminati.',result.photos_not_deleted?'error':'success');return;}
    if(target.matches('[data-seed-ranking]')){const yes=await safeTextDialog('Applica classifica globale','I seed della bozza saranno riordinati secondo l’Elo attuale.',['Il metodo sarà impostato su manuale; potrai modificare i seed prima di avviare.'],false);if(!yes)return;await request(`/api/admin/tournaments/${encodeURIComponent(state.current.id)}/seed-from-ranking`,{method:'POST'});state.managerSnapshot='';await openManager(state.current.id,true);message('Seed aggiornati dalla classifica globale.');return;}
    if(target.matches('[data-add-entrants]')){await addEntrants();return;}
    if(target.matches('[data-remove-entrant]')){const yes=await safeTextDialog('Rimuovi iscrizione','Il profilo rimarrà disponibile nell’albo giocatori.');if(yes){await request(`/api/admin/tournaments/${state.current.id}/entrants/${encodeURIComponent(target.dataset.removeEntrant)}`,{method:'DELETE'});await openManager(state.current.id,true);}return;}
    if(target.matches('[data-withdraw]')){await withdraw(target.dataset.withdraw);return;}
    if(target.matches('[data-result]')){const m=state.current.matches.find(x=>x.id===target.dataset.result);if(m)await recordResult(m);return;}
    if(target.matches('[data-load-audit]')){const events=await request(`/api/admin/tournaments/${state.current.id}/audit`);$('#audit-content').innerHTML=events.map(a=>`<div class="audit-item"><strong>${esc(a.action)}</strong><time>${esc(new Date(a.created_at).toLocaleString('it-IT'))}</time><pre>${esc(JSON.stringify(a.detail,null,2))}</pre></div>`).join('')||'<p class="muted">Nessun evento.</p>';return;}
    if(target.matches('[data-export]')){await exportCsv();return;}
    if(target.id==='logout-button'){await request('/api/admin/logout',{method:'POST'});state.user='';state.csrf='';showLogin();message('Sessione terminata.');return;}
    if(target.id==='password-toggle'){$('#password-form').hidden=!$('#password-form').hidden;return;}
    if(target.id==='backup-download'){const r=await request('/api/admin/backup');const url=URL.createObjectURL(await r.blob());const a=document.createElement('a');a.href=url;a.download='mana-arena-backup.zip';document.body.append(a);a.click();a.remove();URL.revokeObjectURL(url);return;}
    if(target.matches('[data-edit-player]')){const p=state.players.find(x=>x.id===target.dataset.editPlayer);const name=await renameDialog(p?.name||'');if(!name)return;await request(`/api/admin/players/${encodeURIComponent(p.id)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});await refreshAdmin();message('Profilo aggiornato.');return;}
    if(target.matches('[data-delete-player]')){const p=state.players.find(x=>x.id===target.dataset.deletePlayer);const yes=await safeTextDialog('Rimuovi profilo',`Rimuovere ${p?.name||'il giocatore'} dall’albo? Se ha riferimenti storici, il server lo impedirà.`);if(!yes)return;await request(`/api/admin/players/${encodeURIComponent(p.id)}`,{method:'DELETE'});await refreshAdmin();message('Profilo rimosso.');return;}
    if(target.matches('[data-photo]')&&target.files?.[0]){const fd=new FormData();fd.append('file',target.files[0]);await request(`/api/admin/players/${encodeURIComponent(target.dataset.photo)}/photo`,{method:'POST',body:fd});await refreshAdmin();message('Foto caricata.');return;}
  }
  async function submitHandler(e){const f=e.target;if(!f.matches('form')||!['login-form','player-form','tournament-form','password-form','restore-form'].includes(f.id))return;e.preventDefault();const button=$('button[type=submit]',f);if(state.busy)return;state.busy=true;state.editing=false;busy(button,true);clearError();try{
      if(f.id==='login-form'){const x=formObject(f);const s=await request('/api/admin/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)});state.user=s.username;state.csrf=s.csrf_token;f.reset();showWorkspace();await refreshAdmin();message('Accesso effettuato.');}
      else if(f.id==='player-form'){const x=formObject(f);await request('/api/admin/players',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:x.name.trim()})});f.reset();await refreshAdmin();message('Giocatore aggiunto.');}
      else if(f.id==='tournament-form'){const config=configFromForm(f);if(state.current?.status==='active'){try{await request(`/api/admin/tournaments/${state.current.id}/config`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({config})});}catch(err){if(err.status!==409||!err.detail?.requires_confirmation)throw err;const yes=await safeTextDialog('Rollback completo richiesto','Cambiare le regole invaliderà l’intera fase giocata e riporterà il torneo in bozza.',[`Turni coinvolti: ${(err.detail.affected_rounds||[]).join(', ')}`,`Risultati coinvolti: ${err.detail.affected_results??'—'}`,'Iscritti mantenuti; turni e ritiri azzerati.']);if(!yes)throw new Error('Modifica annullata.');await request(`/api/admin/tournaments/${state.current.id}/config?confirm_rollback=true`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({config})});}}
        else if(state.current?.id){if(['completed','archived'].includes(state.current.status))throw new Error('Riapri esplicitamente il torneo prima di cambiare le regole.');await request(`/api/admin/tournaments/${state.current.id}/config`,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({config})});}
        else {const created=await request('/api/admin/tournaments',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config})});state.current=created;}
        f.hidden=true;state.editing=false;await refreshAdmin();if(state.current?.id)await openManager(state.current.id,true);message('Impostazioni salvate.');}
      else if(f.id==='password-form'){const x=formObject(f);await request('/api/admin/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(x)});state.user='';state.csrf='';f.reset();f.hidden=true;showLogin();message('Password aggiornata. Accedi di nuovo.');}
      else if(f.id==='restore-form'){const yes=await safeTextDialog('Sostituisci i dati dell’Arena','Il backup sostituirà tornei, giocatori e foto attuali. L’operazione viene verificata prima dell’attivazione.',[f.elements.file.files[0]?.name||'Archivio selezionato','Le sessioni admin verranno revocate.']);if(!yes)return;const fd=new FormData();fd.append('file',f.elements.file.files[0]);await request('/api/admin/restore?confirm=true',{method:'POST',body:fd});state.user='';state.csrf='';showLogin();message('Ripristino completato. Accedi di nuovo.');}
    }catch(err){if(err.status===401){state.user='';state.csrf='';showLogin();}showError(err.message);if(f.id==='tournament-form')state.editing=true;}
    finally{state.busy=false;busy(button,false);}}
  function init(){initMascot();document.addEventListener('player-result-saved',()=>{state.publicListSnapshot='';state.publicDetailSnapshot='';if(!$('#history').hidden)window.openMatchHistory();else if(!$('#admin-view').hidden)refreshAdmin();else refreshPublic();});document.addEventListener('player-profile-changed',()=>{state.publicListSnapshot='';state.publicDetailSnapshot='';if(location.hash==='#admin'||location.hash==='#admin-view'){refreshAdmin();request('/api/public/ranking').then(renderRanking).catch(()=>{});}else refreshPublic();});document.addEventListener('player-identity-changing',()=>{identityPending=true;renderPersonalMatches([],[]);renderProfileButton([]);if(state.publicId){state.publicDetailData=null;state.publicDetailSnapshot='';$('#public-detail').hidden=true;}});document.addEventListener('reveal-tournament',async event=>{state.detailTab='rounds';state.publicRoundView='list';if(await loadPublicDetail(event.detail,true))await openReveal();});document.addEventListener('player-identity-changed',()=>{state.publicListSnapshot='';identityPending=false;renderProfileButton(publicRanking);if(state.publicId){state.publicDetailData=null;state.publicDetailSnapshot='';$('#public-detail').hidden=true;}refreshPublic();});$('#reveal-dialog').addEventListener('close',()=>{revealSession++;clearTimeout(revealTimer);refreshPublic();});document.addEventListener('click',e=>{if(e.target.closest('[data-ranking-link]')&&$('#public-view').hidden)setView('public');});document.addEventListener('click',e=>clickHandler(e).catch(err=>{showError(err.message);message(err.message,'error');}));document.addEventListener('submit',e=>submitHandler(e));$('#tournament-form').addEventListener('input',()=>{state.editing=true;});$('#tournament-form').addEventListener('change',e=>{if(e.target.name==='structure')syncConfigFields();state.editing=true;});$('#player-form').addEventListener('input',()=>state.editing=true);$('#player-form').addEventListener('change',()=>state.editing=true);$('#password-form').addEventListener('input',()=>state.editing=true);$('#restore-form').addEventListener('change',()=>state.editing=true);$('#tiebreak-list').addEventListener('click',e=>{const b=e.target.closest('button');if(!b)return;const li=b.closest('li'),siblings=[...li.parentElement.children],i=siblings.indexOf(li),direction=b.getAttribute('aria-label').includes('alto')?-1:1,next=siblings[i+direction];if(next){if(direction<0)li.parentElement.insertBefore(li,next);else li.parentElement.insertBefore(next,li);}state.editing=true;});document.addEventListener('input',e=>{if(e.target.matches('[data-avatar-hue]'))e.target.closest('.player-list-row').querySelector('.avatar').style.setProperty('--avatar-hue',e.target.value);});document.addEventListener('change',e=>{if(e.target.matches('[data-avatar-hue]')){const input=e.target;request(`/api/admin/players/${encodeURIComponent(input.dataset.avatarHue)}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({avatar_hue:Number(input.value)})}).then(()=>refreshAdmin()).catch(err=>{showError(err.message);refreshAdmin();});}if(e.target.matches('[data-photo]')&&e.target.files?.[0])clickHandler({target:e.target}).catch(err=>showError(err.message));});document.addEventListener('click',e=>{if(e.target.matches('[data-seed-player]'))state.editing=true;});window.addEventListener('hashchange',()=>setView((location.hash==='#admin'||location.hash==='#admin-view')?'admin':'public'));document.addEventListener('visibilitychange',()=>{state.visible=document.visibilityState==='visible';if(state.timer)clearInterval(state.timer);if(state.visible)state.timer=setInterval(()=>pollIfIdle(),5000);});$('#tournament-form').addEventListener('submit',()=>state.editing=false);const view=(location.hash==='#admin'||location.hash==='#admin-view')?'admin':'public';setView(view);if(state.timer)clearInterval(state.timer);state.timer=setInterval(()=>pollIfIdle(),5000);}
  function pollIfIdle(){if(location.hash==='#life')return;if(document.visibilityState!=='visible'||state.editing||state.busy||$('#confirm-dialog').open||$('#player-results-dialog').open||$('#reveal-dialog').open)return;if(location.hash==='#admin'||location.hash==='#admin-view')refreshAdmin().catch(e=>showError(e.message));else refreshPublic();}
  init();
  const pauseMascotsWhenHidden=()=>document.documentElement.classList.toggle('page-hidden',document.hidden);
  pauseMascotsWhenHidden();
  document.addEventListener('visibilitychange',pauseMascotsWhenHidden);
  $('#login-form').addEventListener('input',()=>state.editing=true);
  $('#login-form').addEventListener('change',()=>state.editing=true);
  document.addEventListener('change',event=>{
    if(!event.target.matches('[data-round-player]'))return;
    const scope=event.target.dataset.roundScope;
    if(scope==='public'){
      state.publicSelectedPlayer=event.target.value;
      if(state.publicDetailData)renderPublicDetail(state.publicDetailData);
    }else{
      state.adminSelectedPlayer=event.target.value;
      renderManager();
    }
  });
  window.addEventListener('resize',scheduleGraphConnections);
  document.addEventListener('input',event=>{if(event.target.matches('input[name="entrant-choice"],[data-seed-player]'))state.editing=true;});
  document.addEventListener('change',event=>{if(event.target.matches('input[name="entrant-choice"],[data-seed-player]'))state.editing=true;});
})();
