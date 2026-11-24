(() => {
  'use strict';
  const root = document.querySelector('#life');
  if (!root) return;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function avatar(member) {
    const name=String(member?.name||'?'), initials=name.trim().split(/\s+/).slice(0,2).map(part=>part[0]||'').join('').toLocaleUpperCase()||'?';
    const url=typeof member?.photo_url==='string' && member.photo_url.startsWith('/') && !member.photo_url.startsWith('//') ? member.photo_url : '';
    return `<span class="life-avatar" aria-hidden="true"><span>${esc(initials)}</span>${url?`<img src="${esc(url)}" alt="" loading="lazy">`:''}</span>`;
  }
  function memberNames(side) { return (side.members||[{name:side.name}]).map(member=>`<span class="life-person">${avatar(member)}<span>${esc(member.name)}</span></span>`).join(''); }
  root.addEventListener('error',event=>{if(event.target.matches?.('.life-avatar img'))event.target.remove();},true);
  let profiles = [], sides = [[], []], game = null, busy = false;
  const key='mana-life-draft-v1', outboxKey='mana-life-outbox-v1', rosterKey='mana-life-roster-v1';
  let draining=null, storageProblem='', setupGeneration=0, sessionPlayer=null, cachedMatches=[], queueMessage='';
  function reportStorage(message){storageProblem=message;const el=root.querySelector('.life-error');if(el)el.textContent=message;}
  function write(key,value){try{localStorage.setItem(key,JSON.stringify(value));return true;}catch(_){reportStorage('Memoria locale non disponibile o piena: libera spazio e riprova. Non chiudere questa pagina.');return false;}}
  function read(key,fallback){try{const raw=localStorage.getItem(key);return raw===null?fallback:JSON.parse(raw);}catch(_){reportStorage('Impossibile leggere la memoria locale. Non chiudere questa pagina.');return fallback;}}
  function persist(){return write(key,game);}
  function restore(){return read(key,null);}
  function outbox(){try{const raw=localStorage.getItem(outboxKey),value=raw===null?[]:JSON.parse(raw);if(!Array.isArray(value))throw Error();return value;}catch(_){reportStorage('Coda locale illeggibile: non chiudere questa pagina.');return null;}}
  function enqueue(payload){const items=outbox();if(!items)return false;const existing=items.find(item=>item.session_id===payload.session_id);if(existing){if(JSON.stringify(existing)===JSON.stringify(payload))return true;reportStorage('La partita è già presente con dati diversi: impossibile sovrascriverla.');return false;}return write(outboxKey,[...items,payload]);}
  function refreshQueueStatus() {
    const status = root.querySelector('[data-queue-status]');
    if (!status) return;
    const items = outbox(), first = items?.[0];
    const retry = root.querySelector('[data-queue-retry]');
    if (retry) retry.hidden = !first;
    const login = root.querySelector('[data-queue-login]');
    if (login) {
      login.hidden = !first?.result_target;
      const proposer = first?.players?.flatMap(player => player.members || []).find(member => member.id === first.result_target?.proposer_id);
      if (proposer) login.textContent = 'Accedi come ' + proposer.name + ' e sincronizza';
    }
    status.textContent = items === null ? 'Coda locale non disponibile.' :
      first ? `${items.length} ${items.length === 1 ? 'partita' : 'partite'} in attesa di sincronizzazione. ${queueMessage}` :
        (queueMessage || 'Tutte le partite sono sincronizzate.');
  }
  async function loginAndDrain(){const item=outbox()?.[0];if(!item?.result_target)return;try{const response=await fetch('/api/player/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({player_id:item.result_target.proposer_id})});if(!response.ok)throw Error('Accesso non riuscito.');document.dispatchEvent(new Event('player-identity-changed'));await drainOutbox();}catch(error){queueMessage=error.message;refreshQueueStatus();}}
  async function proposalHeaders(target) {
    const headers = {'Content-Type':'application/json'};
    if (!target) return headers;
    const current = await fetch('/api/player/session', {credentials:'same-origin'});
    let session = current.ok ? await current.json() : null;
    if (session?.player_id !== target.proposer_id) {
      const login = await fetch('/api/player/login', {
        method:'POST', credentials:'same-origin', headers,
        body:JSON.stringify({player_id:target.proposer_id})
      });
      session = login.ok ? await login.json() : null;
    }
    if (session?.player_id === target.proposer_id) headers['X-Player-CSRF'] = session.csrf_token;
    return headers;
  }
  async function syncFinishedGames() {
    const attempted = new Set();
    const messages = {
      unavailable:'Partita archiviata. Il risultato non è più applicabile.',
      pending:'Risultato precedente ancora in sospeso.',
      confirmed:'Risultato registrato.',
      stats_only:'Partita archiviata senza classifica.'
    };
    while (true) {
      const next = outbox()?.find(item => !attempted.has(item.session_id));
      if (!next) return;
      attempted.add(next.session_id);
      try {
        const headers = await proposalHeaders(next.result_target);
        const response = await fetch('/api/life/games', {
          method:'POST', credentials:'same-origin', headers, body:JSON.stringify(next)
        });
        if (!response.ok) {
          const error = await response.json().catch(() => null);
          const detail = typeof error?.detail === 'string' ? error.detail : error?.detail?.message;
          queueMessage = detail || (response.status === 503
            ? 'Il NAS non riesce ad archiviare la partita. Verifica lo spazio libero e i permessi di scrittura di /data/life.'
            : 'Sincronizzazione non riuscita (HTTP ' + response.status + ').');
          continue;
        }
        const outcome = await response.json();
        if (outcome.result_status === 'unauthorized') {
          queueMessage = 'Partita archiviata. Accedi come proponente per inviare il risultato.';
          continue;
        }
        queueMessage = messages[outcome.result_status] || 'Partita sincronizzata.';
        const current = outbox();
        if (!current || !write(outboxKey, current.filter(x => x.session_id !== next.session_id))) return;
      } catch {
        queueMessage = 'NAS irraggiungibile. Le partite in attesa restano sul telefono.';
      }
    }
  }
  async function drainOutbox() {
    if (globalThis.navigator?.onLine === false) {
      queueMessage = 'Invio automatico al ritorno della connessione.';
      refreshQueueStatus();
      return;
    }
    if (draining) return draining;
    draining = syncFinishedGames();
    try { await draining; }
    finally { draining = null; refreshQueueStatus(); }
  }
  const stamp = () => new Date().toISOString();
  const uuid = () => crypto.randomUUID ? crypto.randomUUID() : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => { const n = crypto.getRandomValues(new Uint8Array(1))[0] & 15; return (c === 'x' ? n : (n & 3) | 8).toString(16); });
  let matchRequest = 0, matchLoading = false, matchError = '', profileLoads = Promise.resolve();
  function useMatchCache(data, playerId = null) {
    const cachedPlayer = data?.player;
    sessionPlayer = cachedPlayer?.player_id && (!playerId || playerId === cachedPlayer.player_id)
      ? {player_id:cachedPlayer.player_id,name:cachedPlayer.name}
      : playerId ? {player_id:playerId} : null;
    cachedMatches = sessionPlayer && Array.isArray(data?.matches)
      ? data.matches.filter(match => match.p1 && match.p2 && [match.p1,match.p2].includes(sessionPlayer.player_id))
      : [];
  }
  function setup() {
    const generation = ++setupGeneration;
    const cached = read(rosterKey,[]);
    profiles = Array.isArray(cached) ? cached : [];
    useMatchCache(read('mana-life-matches-v1',null));
    drawSetup();
    refreshQueueStatus();
    loadTournamentMatches();
    if (navigator.onLine === false) return;
    fetch('/api/public/ranking').then(r=>r.ok?r.json():null).then(list=>{
      if(!Array.isArray(list))return;
      write(rosterKey,list);
      if(generation!==setupGeneration || game || !root.querySelector('#life-setup'))return;
      profiles=list;updateTournamentPlayers();updateMatchChoices();
      root.querySelectorAll('[data-profile]').forEach(select=>{const value=select.value;select.innerHTML='<option value="">Scegli profilo</option>'+profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');select.value=value;});
    }).catch(()=>{});
  }
  function updateTournamentPlayers() {
    const form = root.querySelector('#life-setup');
    if (!form) return;
    const select = form.elements.tournament_player;
    const roster = [...profiles];
    if (sessionPlayer && !roster.some(player => player.id === sessionPlayer.player_id)) {
      roster.push({id:sessionPlayer.player_id,name:sessionPlayer.name || 'Profilo del torneo'});
    }
    select.innerHTML = '<option value="">Seleziona profilo</option>' +
      roster.map(player => `<option value="${esc(player.id)}">${esc(player.name)}</option>`).join('');
    select.value = sessionPlayer?.player_id || '';
  }
  function loadTournamentMatches(playerId = null) {
    const request = ++matchRequest, generation = setupGeneration;
    const current = () => request === matchRequest && generation === setupGeneration && !game;
    matchError = '';
    matchLoading = navigator.onLine !== false;
    updateMatchChoices();
    if (!matchLoading) return;

    profileLoads = profileLoads.then(async () => {
      if (!current()) return;
      try {
        let response = await fetch('/api/player/session',{credentials:'same-origin'});
        let player = response.ok ? await response.json() : null;
        if (!current()) return;
        if (playerId && player?.player_id !== playerId) {
          response = await fetch('/api/player/login',{
            method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({player_id:playerId}),
          });
          if (!response.ok) throw Error('Accesso al profilo non riuscito.');
          player = await response.json();
        }
        if (!current()) return;
        if (!player?.player_id) {
          if (response.status !== 401) throw Error('Impossibile verificare il profilo.');
          useMatchCache(null);
          return;
        }
        if (sessionPlayer?.player_id !== player.player_id) {
          useMatchCache(read('mana-life-matches-'+player.player_id,null),player.player_id);
        }
        const responseMatches = await fetch('/api/player/matches',{credentials:'same-origin'});
        if (!responseMatches.ok) throw Error('Impossibile caricare gli abbinamenti.');
        const matches = await responseMatches.json();
        if (!Array.isArray(matches)) throw Error('Abbinamenti non validi.');
        if (!current()) return;
        const data = {player:{player_id:player.player_id,name:player.name},matches};
        write('mana-life-matches-'+player.player_id,data);
        write('mana-life-matches-v1',data);
        useMatchCache(data);
      } catch (error) {
        if (current()) matchError = error.message || 'Impossibile caricare gli abbinamenti.';
      } finally {
        if (current()) {
          matchLoading = false;
          updateTournamentPlayers();
          updateMatchChoices();
        }
      }
    });
    return profileLoads;
  }
  function updateMatchChoices() {
    const form = root.querySelector('#life-setup');
    if (!form || form.elements.kind.value !== 'tournament') return;
    const select = form.elements.match, previous = select.value;
    select.innerHTML = cachedMatches.length ? cachedMatches.map(match =>
      `<option value="${esc(match.id)}">🏆 ${esc(match.tournament?.config?.name || 'Torneo')} · ${esc(profiles.find(p => p.id === match.p1)?.name || 'Giocatore')} – ${esc(profiles.find(p => p.id === match.p2)?.name || 'Giocatore')}</option>`
    ).join('') : '<option value="">Nessun abbinamento disponibile</option>';
    select.value = cachedMatches.some(match => match.id === previous) ? previous : cachedMatches[0]?.id || '';
    select.disabled = matchLoading || !cachedMatches.length;
    root.querySelector('[data-match-hint]').textContent = matchLoading ? 'Caricamento incontro…' :
      matchError ? (cachedMatches.length ? 'Mostro gli ultimi abbinamenti salvati.' : matchError) :
      !sessionPlayer ? 'Seleziona il tuo profilo per trovare il tuo incontro.' :
      !cachedMatches.length ? 'Nessun incontro di torneo da giocare.' : '';
    form.querySelector('button[type="submit"]').disabled = matchLoading || !select.value;
    select.onchange();
  }
  function drawSetup() {
    root.innerHTML = `<div class="life-shell"><header><p class="life-kicker">MANA ARENA · AL TAVOLO</p><h1>Segnapunti vita</h1><p>Un telefono al centro del tavolo, un contatore per ogni lato.</p></header><form id="life-setup"><label>Tipo partita<select name="kind"><option value="friendly">Amichevole</option><option value="tournament">🏆 Partita di torneo</option></select></label><label data-tournament-player hidden>Il tuo profilo<select name="tournament_player"><option value="">Seleziona profilo</option></select></label><label data-match-label hidden>Incontro<select name="match"><option value="">Nessun abbinamento disponibile</option></select></label><p data-match-hint role="status" hidden></p><label data-friendly-format>Formato<select name="format"><option value="duel">Duello</option><option value="team">Squadre</option><option value="ffa">Tutti contro tutti</option></select></label><label>Vita iniziale<select name="life_mode"><option value="same">Uguale per tutti</option><option value="different">Diversa per ogni lato</option></select></label><label data-common>Vita iniziale comune<input type="number" name="common_life" min="1" max="999" value="20" required></label><div class="life-setup-sides"></div><div class="life-setup-actions"><button type="button" class="button secondary" data-add-side hidden>Aggiungi lato</button><button type="submit" class="button primary">Inizia partita</button></div><p data-queue-status role="status"></p><button type="button" class="button secondary" data-queue-retry hidden>Riprova sincronizzazione</button><button type="button" class="button secondary" data-queue-login hidden>Accedi come proponente e sincronizza</button><p class="life-error" role="alert"></p></form></div>`;
    const form = root.querySelector('form');
    function renderSides() {
      const editable = form.elements.kind.value !== 'tournament';
      const format = form.elements.format.value;
      const initial = [...root.querySelectorAll('[data-initial]')].map(input => input.value);
      root.querySelector('[data-add-side]').hidden = !editable || format !== 'ffa' || sides.length >= 4;
      root.querySelector('.life-setup-sides').innerHTML = sides.map((members, i) => {
        if (!editable && !members.length) return '';
        const controls = editable ? `<div class="life-add-member"><span data-profile-preview="${i}" class="life-profile-preview" aria-hidden="true"></span><select aria-label="Profilo lato ${i+1}" data-profile="${i}"><option value="">Scegli profilo</option>${profiles.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select><button type="button" data-add-profile="${i}">Aggiungi</button></div><div class="life-add-member"><input data-guest="${i}" maxlength="60" placeholder="Nome ospite" aria-label="Nome ospite lato ${i+1}"><button type="button" data-add-guest="${i}">Aggiungi</button></div>` : '';
        return `<fieldset><legend>${format === 'team' ? 'Squadra' : 'Lato'} ${i + 1}</legend><div class="life-members">${members.map((m, j) => `<div class="life-member"><span class="life-person">${avatar(m)}<span>${esc(m.name)}</span></span>${editable ? `<button type="button" data-remove-member="${i},${j}" aria-label="Rimuovi ${esc(m.name)}">×</button>` : ''}</div>`).join('')}</div>${controls}<label ${form.elements.life_mode.value === 'same' ? 'hidden' : ''}>Vita iniziale<input type="number" data-initial="${i}" min="1" max="999" value="${esc(initial[i] ?? 20)}" required></label>${editable && format === 'ffa' && sides.length > 2 ? `<button type="button" data-remove-side="${i}">Rimuovi lato</button>` : ''}</fieldset>`;
      }).join('');
    }
    updateTournamentPlayers();
    form.elements.tournament_player.onchange = () => {
      const id = form.elements.tournament_player.value;
      // Invalidate pending responses even when the profile is cleared.
      if (!id) {
        ++matchRequest;
        matchLoading = false;
        matchError = '';
        useMatchCache(null);
        updateMatchChoices();
        return;
      }
      useMatchCache(read('mana-life-matches-'+id,null),id);
      form.elements.match.value = '';
      loadTournamentMatches(id);
    };
    form.elements.kind.onchange = () => {
      const tournament = form.elements.kind.value === 'tournament';
      root.querySelector('[data-tournament-player]').hidden = !tournament;
      root.querySelector('[data-match-label]').hidden = !tournament;
      root.querySelector('[data-match-hint]').hidden = !tournament;
      root.querySelector('[data-friendly-format]').hidden = tournament;
      form.elements.format.value = 'duel';
      sides = [[],[]];
      if (tournament) updateMatchChoices();
      else {
        form.querySelector('button[type="submit"]').disabled = false;
        renderSides();
      }
    };
    form.elements.match.onchange = () => {
      const match = cachedMatches.find(item => item.id === form.elements.match.value);
      sides = match ? [match.p1,match.p2].map((id,index) => {
        const profile = profiles.find(p => p.id === id);
        return [{id,name:profile?.name || (id === sessionPlayer?.player_id ? sessionPlayer.name : '') || `Giocatore ${index + 1}`,photo_url:profile?.photo_url || null}];
      }) : [[],[]];
      renderSides();
    };
    form.addEventListener('change',event=>{const select=event.target;if(!select.matches('[data-profile]'))return;const person=profiles.find(p=>String(p.id)===select.value);root.querySelector(`[data-profile-preview="${select.dataset.profile}"]`).innerHTML=person?avatar(person):'';});
    form.elements.life_mode.onchange = () => { root.querySelector('[data-common]').hidden=form.elements.life_mode.value!=='same'; renderSides(); };
    form.elements.format.onchange = () => { sides = [[], []]; renderSides(); };
    root.onclick = e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.hasAttribute('data-queue-retry')) { drainOutbox(); return; }
      if (b.hasAttribute('data-queue-login')) { loginAndDrain(); return; }
      if (form.elements.kind.value === 'tournament') return;
      if (b.hasAttribute('data-add-side')) sides.push([]);
      else if (b.hasAttribute('data-remove-side')) sides.splice(Number(b.dataset.removeSide), 1);
      else if (b.hasAttribute('data-remove-member')) { const [i,j] = b.dataset.removeMember.split(',').map(Number); sides[i].splice(j,1); }
      else if (b.hasAttribute('data-add-profile')) { const i = Number(b.dataset.addProfile), id = root.querySelector(`[data-profile="${i}"]`).value, p = profiles.find(x => String(x.id) === id); if (!p) return; if (sides.flat().some(m => m.id === id)) { root.querySelector('.life-error').textContent = 'Questo profilo è già stato aggiunto.'; return; } sides[i].push({id,name:p.name,photo_url:p.photo_url||null}); }
      else if (b.hasAttribute('data-add-guest')) { const i = Number(b.dataset.addGuest), name = root.querySelector(`[data-guest="${i}"]`).value.trim(); if (!name) return; sides[i].push({name}); }
      else return;
      renderSides();
    };
    form.onsubmit = async e => {
      e.preventDefault(); if (busy || game) return; const format = form.elements.format.value, kind=form.elements.kind.value;
      const match = kind === 'tournament' ? cachedMatches.find(item => item.id === form.elements.match.value) : null;
      if (kind === 'tournament' && (matchLoading || !match || !sessionPlayer)) return;
      if (sides.some(s => !s.length || s.length > 6 || (format !== 'team' && s.length !== 1))) { root.querySelector('.life-error').textContent = format === 'team' ? 'Aggiungi da uno a sei giocatori per squadra.' : 'Aggiungi un solo giocatore per ogni lato.'; return; }
      const names = sides.map(s => s.map(m => m.name).join(' + '));
      if (names.some(n => n.length > 160)) { root.querySelector('.life-error').textContent = 'Il nome di un lato è troppo lungo (massimo 160 caratteri).'; return; }
      if (new Set(names.map(n => n.toLocaleLowerCase())).size !== names.length) { root.querySelector('.life-error').textContent = 'I lati devono avere nomi diversi.'; return; }
      game = {session_id:uuid(), started_at:stamp(), format, kind, match, proposer:sessionPlayer?.player_id, sides:sides.map((members,i) => ({name:names[i], members, initial:Number(form.elements.life_mode.value==='same'?form.elements.common_life.value:root.querySelector(`[data-initial="${i}"]`).value), life:Number(form.elements.life_mode.value==='same'?form.elements.common_life.value:root.querySelector(`[data-initial="${i}"]`).value)})), changes:[], rolls:{eligible:sides.map((_,i)=>i), values:{}, done:false}};
      if (!persist()) { game=null; return; }
      ++setupGeneration;busy=true;
      busy=false;drawGame();
    };
    renderSides();
  }
  let screenLock = null;
  let requestingLock = false;

  function updateLockStatus(message) {
    const status = root.querySelector('[data-screen-status]');
    if (status) status.textContent = message;
  }

  function tableVisible() {
    return (location.hash === '#life' || location.pathname === '/segnavita') && document.visibilityState === 'visible'
      && Boolean(root.querySelector('.life-table'));
  }

  async function syncScreenLock() {
    if (!tableVisible()) {
      const previous = screenLock;
      screenLock = null;
      if (previous) await previous.release().catch(() => {});
      return;
    }
    if (requestingLock || (screenLock && !screenLock.released)) return;
    if (!navigator.wakeLock) {
      updateLockStatus(window.isSecureContext ? 'Schermo sempre acceso non disponibile in questo browser.'
        : 'Per tenere acceso lo schermo, apri Mana Arena in HTTPS.');
      return;
    }
    requestingLock = true;
    try {
      const lock = await navigator.wakeLock.request('screen');
      if (!tableVisible()) {
        await lock.release();
        return;
      }
      screenLock = lock;
      updateLockStatus('Schermo sempre acceso');
      lock.addEventListener('release', () => {
        if (screenLock !== lock) return;
        screenLock = null;
        updateLockStatus('Schermo sempre acceso sospeso dal dispositivo.');
      });
    } catch {
      updateLockStatus('Il dispositivo può spegnere lo schermo per inattività.');
    } finally {
      requestingLock = false;
    }
  }

  document.addEventListener('visibilitychange', syncScreenLock);
  window.addEventListener('hashchange', syncScreenLock);
  window.addEventListener('pageshow', syncScreenLock);

  const tapFeedback = new Map();
  function drawGame() {
    root.classList.add('life-playing');
    root.innerHTML = `<div class="life-table-shell"><div class="life-table life-table-${game.sides.length}">${game.sides.map((side,i)=>`<article class="life-side life-side-${i}"><div class="life-side-inner"><div class="life-side-heading">${memberNames(side)}</div><div class="life-counter"><button type="button" data-delta="${i},-1" aria-label="Togli una vita a ${esc(side.name)}">−</button><div class="life-total"><strong data-life="${i}">${side.life}</strong><span class="life-change" data-feedback="${i}" aria-hidden="true"></span></div><button type="button" data-delta="${i},1" aria-label="Aggiungi una vita a ${esc(side.name)}">+</button></div><button class="life-undo" type="button" data-undo="${i}">Annulla</button></div></article>`).join('')}</div><div class="life-floating-controls"><button type="button" data-end>Termina</button><button type="button" data-dice-open aria-label="Apri dadi">⚄</button></div><p class="life-error" role="alert"></p><div class="life-dice-overlay" data-dice-overlay hidden><div class="life-dice-dialog" role="dialog" aria-modal="true" aria-labelledby="life-dice-title"><div class="life-dice-header"><h2 id="life-dice-title">Tira il D20</h2><button type="button" data-dice-close aria-label="Chiudi dadi">×</button></div><p class="life-roll-result" aria-live="polite"></p><div class="life-dice-grid">${game.sides.map((side,i)=>`<div class="life-dice-player"><div class="life-dice-name">${memberNames(side)}</div><div class="life-die-wrap"><canvas width="150" height="150" data-die="${i}" aria-label="Dado a venti facce"></canvas><span data-roll-value="${i}">—</span></div><button type="button" data-roll="${i}">Tira D20</button></div>`).join('')}</div></div></div></div>`;
    syncScreenLock();
    game.sides.forEach((_,i)=>{drawDie(root.querySelector(`[data-die="${i}"]`),0,0,0,game.rolls.values[i]);root.querySelector(`[data-roll-value="${i}"]`).textContent=game.rolls.values[i]||'—';}); updateRollStatus();
    root.onclick = e => { const b=e.target.closest('button'); if (!b || busy) return;
      if(b.hasAttribute('data-dice-open')){root.querySelector('[data-dice-overlay]').hidden=false;root.querySelector('[data-dice-close]').focus();}
      else if(b.hasAttribute('data-dice-close')){root.querySelector('[data-dice-overlay]').hidden=true;root.querySelector('[data-dice-open]').focus();}
      else if (b.dataset.delta) { const [i,delta]=b.dataset.delta.split(',').map(Number); change(i,delta); }
      else if (b.dataset.undo !== undefined) { const i=Number(b.dataset.undo), previous=[...game.changes].reverse().find(c=>c.player===game.sides[i].name && !c.undone && !c.undo); if(previous && game.changes.length<5000){previous.undone=true;change(i,-previous.delta,true);} }
      else if (b.dataset.roll !== undefined) roll(Number(b.dataset.roll));
      else if (b.hasAttribute('data-end')) end();
    };
    root.onkeydown=e=>{if(e.key==='Escape' && !root.querySelector('[data-dice-overlay]').hidden){root.querySelector('[data-dice-overlay]').hidden=true;root.querySelector('[data-dice-open]').focus();}};
  }
  function showTapFeedback(i,delta) {
    const prior=tapFeedback.get(i);if(prior)clearTimeout(prior.timer);const total=(prior?.total||0)+delta;
    const el=root.querySelector(`[data-feedback="${i}"]`);if(el){el.textContent=total>0?'+'+total:String(total);el.classList.add('is-visible');}
    const timer=setTimeout(()=>{tapFeedback.delete(i);const current=root.querySelector(`[data-feedback="${i}"]`);if(current)current.classList.remove('is-visible');},1100);tapFeedback.set(i,{total,timer});
  }
  function change(i,delta,undo=false){ if(game.changes.length>=5000){root.querySelector('.life-error').textContent='Limite di 5000 variazioni raggiunto.';return;} const side=game.sides[i];side.life+=delta;game.changes.push({at:stamp(),player:side.name,delta,life:side.life,...(undo?{undo:true}:{})});root.querySelector(`[data-life="${i}"]`).textContent=side.life;showTapFeedback(i,delta);persist(); }
  function roll(i) {
    const r=game.rolls; if(r.done || !r.eligible.includes(i) || r.values[i]!==undefined)return;
    const value=crypto.getRandomValues(new Uint32Array(1))[0]%20+1; r.values[i]=value;
    const canvas=root.querySelector(`[data-die="${i}"]`), label=root.querySelector(`[data-roll-value="${i}"]`), start=performance.now(); label.textContent='…';
    function frame(now){const t=Math.max(0,Math.min((now-start)/900,1)),bounce=t<.55?(1-t/.55)*130:Math.abs(Math.sin((t-.55)*Math.PI*5))*22*(1-t);drawDie(canvas,(1-t)*11,(1-t)*15,bounce,value);if(t<1)requestAnimationFrame(frame);else label.textContent=value;} requestAnimationFrame(frame);
    if(r.eligible.every(j=>r.values[j]!==undefined)){const high=Math.max(...r.eligible.map(j=>r.values[j])), winners=r.eligible.filter(j=>r.values[j]===high);if(winners.length===1){r.eligible=winners;r.done=true;}else{r.eligible=winners;r.values={};r.tie=true;}}
    persist();updateRollStatus();
  }
  function updateRollStatus(){const r=game.rolls;root.querySelectorAll('[data-roll]').forEach(b=>{const i=Number(b.dataset.roll);b.disabled=r.done||!r.eligible.includes(i)||r.values[i]!==undefined;});root.querySelector('.life-roll-result').textContent=r.done?`${game.sides[r.eligible[0]].name} inizia con ${r.values[r.eligible[0]]}.`:r.tie?'Parità: ritirano soltanto i lati a pari merito.':'Tira un D20 per ogni lato: il più alto inizia.';}
  function drawDie(canvas, ax, ay, bounce, result) {
    const c=canvas.getContext('2d'), phi=(1+Math.sqrt(5))/2, vertices=[[-1,phi,0],[1,phi,0],[-1,-phi,0],[1,-phi,0],[0,-1,phi],[0,1,phi],[0,-1,-phi],[0,1,-phi],[phi,0,-1],[phi,0,1],[-phi,0,-1],[-phi,0,1]], faces=[[0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],[1,5,9],[5,11,4],[11,10,2],[10,7,6],[7,1,8],[3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],[4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1]];
    if (result !== undefined) {
      // Rotate the selected triangular face towards the player as the spin settles.
      const normal = faces[result - 1].reduce(
        (sum, vertex) => sum.map((value, axis) => value + vertices[vertex][axis]), [0, 0, 0]);
      ax += Math.atan2(normal[1], normal[2]);
      ay -= Math.atan2(normal[0], Math.hypot(normal[1], normal[2]));
    }
    const p=vertices.map(([x,y,z])=>{const y1=y*Math.cos(ax)-z*Math.sin(ax),z1=y*Math.sin(ax)+z*Math.cos(ax);return [x*Math.cos(ay)+z1*Math.sin(ay),y1,-x*Math.sin(ay)+z1*Math.cos(ay)];});
    c.clearRect(0,0,150,150);c.fillStyle='rgba(0,0,0,.2)';c.beginPath();c.ellipse(75,132,Math.max(2,35-bounce/3),7,0,0,7);c.fill();
    faces.map((f,n)=>({f,n,z:f.reduce((sum,i)=>sum+p[i][2],0)})).sort((a,b)=>a.z-b.z).forEach(({f,n,z})=>{c.beginPath();f.forEach((i,j)=>{const [x,y]=p[i], X=75+x*26,Y=75+y*26-bounce;j?c.lineTo(X,Y):c.moveTo(X,Y)});c.closePath();c.fillStyle=`hsl(267 45% ${Math.max(28,Math.min(62,43+z*4))}%)`;c.fill();c.strokeStyle='#d9c8ff';c.lineWidth=1;c.stroke();if(z>1){const x=f.reduce((s,i)=>s+p[i][0],0)/3,y=f.reduce((s,i)=>s+p[i][1],0)/3;c.fillStyle='white';c.font='bold 11px sans-serif';c.textAlign='center';c.fillText(String(n+1),75+x*26,79+y*26-bounce);}});
  }
  function end(){
    root.classList.remove('life-playing');root.onkeydown=null;
    const max=Math.max(...game.sides.map(side=>side.life)), leaders=game.sides.filter(side=>side.life===max);
    let selected=leaders.length===1?game.sides.indexOf(leaders[0]):null;
    const matchType = game.kind === 'tournament'
      ? `<span aria-hidden="true">🏆</span> Partita di torneo · ${esc(game.match?.tournament?.config?.name || 'Torneo')}`
      : 'Amichevole';
    const winnerLabel = () => selected === null ? 'Seleziona chi ha vinto' : selected === -1
      ? 'Pareggio' : `Vincitore: ${game.sides[selected].name}`;
    root.innerHTML=`<div class="life-shell life-winner"><h1>Chi ha vinto?</h1><p class="life-result-type">${matchType}</p><div class="life-winner-options" role="group" aria-label="Vincitore">${game.sides.map((side,i)=>`<button type="button" class="life-winner-option" data-winner="${i}" aria-pressed="${i===selected}">${memberNames(side)}</button>`).join('')}</div><p data-result-winner role="status">${esc(winnerLabel())}</p><p class="life-error" role="alert"></p><button type="button" class="button primary life-confirm" data-confirm ${selected===null?'disabled':''}>Salva risultato</button><details class="life-other-results"><summary>Altre opzioni</summary><div><button type="button" class="button quiet" data-winner="-1" aria-pressed="false">Pareggio</button><button type="button" class="button quiet" data-back>Torna alla partita</button></div></details></div>`;
    syncScreenLock();
    root.onclick=e=>{const button=e.target.closest('button');if(!button)return;if(button.hasAttribute('data-back')){drawGame();return;}if(button.hasAttribute('data-winner')){selected=Number(button.dataset.winner);root.querySelectorAll('[data-winner]').forEach(option=>option.setAttribute('aria-pressed',String(Number(option.dataset.winner)===selected)));root.querySelector('[data-result-winner]').textContent=winnerLabel();root.querySelector('[data-confirm]').disabled=false;return;}if(!button.hasAttribute('data-confirm')||selected===null)return;
      game.payload={session_id:game.session_id,started_at:game.started_at,ended_at:stamp(),starting_life:game.sides[0].initial,format:game.format,players:game.sides.map(side=>({name:side.name,initial_life:side.initial,final_life:side.life,members:side.members.map(member=>({id:member.id||null,name:member.name}))})),changes:game.changes.map(({at,player,delta,life,undo})=>({at,player,delta,life,undo:!!undo})),winner:selected===-1?null:game.sides[selected].name};
      if(game.payload.winner && game.format!=='ffa' && game.sides.every(side=>side.members.length<=3 && side.members.every(member=>member.id))){const proposer=game.kind==='tournament'?game.proposer:game.sides[0].members[0].id;if(proposer)game.payload.result_target=game.kind==='tournament'?{kind:'tournament',tournament_id:game.match.tournament.id,match_id:game.match.id,proposer_id:proposer}:{kind:'friendly',proposer_id:proposer};}
      if(!enqueue(game.payload)){game.payload=null;return;}persist();savePending();
    };
  }
  function savePending(){
    const finished=game;
    root.innerHTML='<div class="life-shell"><h1>Archivio segnavita</h1><p data-status>Partita conservata su questo dispositivo.</p><p data-queue-status role="status"></p><p class="life-error" role="alert"></p><div class="life-setup-actions"><button type="button" class="button secondary" data-queue-retry hidden>Riprova sincronizzazione</button><button type="button" class="button secondary" data-queue-login hidden>Accedi come proponente e sincronizza</button><button type="button" class="button secondary" data-new>Nuova partita</button></div></div>';
    root.onclick=e=>{if(e.target.closest('[data-queue-retry]'))drainOutbox();if(e.target.closest('[data-queue-login]'))loginAndDrain();if(e.target.closest('[data-new]')){try{localStorage.removeItem(key);}catch(_){reportStorage('Impossibile aggiornare la memoria locale.');return;}sides=[[],[]];game=null;setup();}};
    if(finished.payload.result_target)root.querySelector('[data-status]').textContent+=' Il risultato verrà registrato alla sincronizzazione.';
    refreshQueueStatus();drainOutbox();
  }
  window.addEventListener('online',async () => {
    await drainOutbox();
    if (!game && root.querySelector('#life-setup')) loadTournamentMatches(sessionPlayer?.player_id);
  });
  window.openLifeCounter = () => { if (!root.firstElementChild) { game=restore(); if(game?.payload){savePending();} else if(game?.sides && game?.rolls)drawGame(); else setup(); } drainOutbox(); };
})();
