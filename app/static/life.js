(() => {
  'use strict';
  const root = document.querySelector('#life');
  if (!root) return;
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
  function refreshQueueStatus(){const el=root.querySelector('[data-queue-status]');if(!el)return;const items=outbox();const login=root.querySelector('[data-queue-login]');if(login){login.hidden=!items?.[0]?.result_target;const proposer=items?.[0]?.players?.flatMap(p=>p.members).find(m=>m.id===items?.[0]?.result_target?.proposer_id);if(proposer)login.textContent='Accedi come '+proposer.name+' e sincronizza';}el.textContent=items===null?'Coda locale non disponibile.':items.length?`${items.length} partite in attesa di sincronizzazione. ${queueMessage}`:(queueMessage||'Tutte le partite sono sincronizzate.');}
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
      pending:'Risultato in attesa di conferma.',
      confirmed:'Risultato confermato.',
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
          queueMessage = 'Sincronizzazione non riuscita (HTTP ' + response.status + ').';
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
  function setup() {
    const generation=++setupGeneration;
    const cached=read(rosterKey,[]);profiles=Array.isArray(cached)?cached:[];
    const matchCache=read('mana-life-matches-v1',null);if(matchCache?.player?.csrf_token){matchCache.player={player_id:matchCache.player.player_id,name:matchCache.player.name};write('mana-life-matches-v1',matchCache);}sessionPlayer=matchCache?.player||null;cachedMatches=Array.isArray(matchCache?.matches)?matchCache.matches:[];
    drawSetup();refreshQueueStatus();
    if(navigator.onLine===false)return;
    fetch('/api/player/session',{credentials:'same-origin'}).then(async r=>{if(!r.ok || generation!==setupGeneration || game)return null;const rawPlayer=await r.json();const player={player_id:rawPlayer.player_id,name:rawPlayer.name};if(generation!==setupGeneration || game)return null;const mr=await fetch('/api/player/matches',{credentials:'same-origin'});if(!mr.ok)return null;return {player,matches:await mr.json()};}).then(data=>{if(!data || !Array.isArray(data.matches))return;write('mana-life-matches-v1',data);if(generation!==setupGeneration||game||!root.querySelector('#life-setup'))return;sessionPlayer=data.player;cachedMatches=data.matches;write('mana-life-matches-'+data.player.player_id,data);updateTournamentPlayers();updateMatchChoices();}).catch(()=>{});
    fetch('/api/public/ranking').then(r=>r.ok?r.json():null).then(list=>{
      if(!Array.isArray(list))return;
      write(rosterKey,list);
      if(generation!==setupGeneration || game || !root.querySelector('#life-setup'))return;
      profiles=list;updateTournamentPlayers();updateMatchChoices();
      root.querySelectorAll('[data-profile]').forEach(select=>{const value=select.value;select.innerHTML='<option value="">Scegli profilo</option>'+profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');select.value=value;});
    }).catch(()=>{});
  }
  function updateTournamentPlayers(){const form=root.querySelector('#life-setup');if(!form)return;const el=form.elements.tournament_player, previous=el.value||sessionPlayer?.player_id||'';el.innerHTML='<option value="">Seleziona profilo</option>'+profiles.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('');el.value=previous;}
  async function loadMatchesForPlayer(id){
    if(!id)return;const generation=setupGeneration, cached=read('mana-life-matches-'+id,null);if(cached?.player?.csrf_token){cached.player={player_id:cached.player.player_id,name:cached.player.name};write('mana-life-matches-'+id,cached);}sessionPlayer=cached?.player||{player_id:id};cachedMatches=Array.isArray(cached?.matches)?cached.matches:[];updateMatchChoices();
    if(navigator.onLine===false)return;
    try{const login=await fetch('/api/player/login',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify({player_id:id})});if(!login.ok)throw Error('Accesso al profilo non riuscito.');const rawPlayer=await login.json();const player={player_id:rawPlayer.player_id,name:rawPlayer.name};if(generation!==setupGeneration||game)return;const response=await fetch('/api/player/matches',{credentials:'same-origin'});if(!response.ok)throw Error('Impossibile caricare gli abbinamenti.');const matches=await response.json();if(!Array.isArray(matches))throw Error('Abbinamenti non validi.');write('mana-life-matches-'+id,{player,matches});write('mana-life-matches-v1',{player,matches});if(generation!==setupGeneration||game||root.querySelector('#life-setup')?.elements.tournament_player.value!==id)return;sessionPlayer=player;cachedMatches=matches;updateMatchChoices();}catch(error){if(generation===setupGeneration&&!game){root.querySelector('[data-match-hint]').textContent=error.message+' Uso gli abbinamenti eventualmente salvati.';}}
  }
  function updateMatchChoices(){
    const form=root.querySelector('#life-setup');if(!form)return;const select=form.elements.match, current=select.value;
    select.innerHTML='<option value="">Seleziona un abbinamento</option>'+cachedMatches.map((m,i)=>`<option value="${i}">${esc(m.tournament?.config?.name||'Torneo')} · ${esc(profiles.find(p=>p.id===m.p1)?.name||m.p1)} – ${esc(profiles.find(p=>p.id===m.p2)?.name||m.p2)}</option>`).join('');select.value=current;
    root.querySelector('[data-match-hint]').textContent=cachedMatches.length?'Scegli un incontro: i partecipanti sono precompilati.':'Nessun abbinamento salvato. Seleziona il tuo profilo e collegati per aggiornare il calendario.';
  }
  function drawSetup() {
    root.innerHTML = `<div class="life-shell"><header><p class="life-kicker">MANA ARENA · AL TAVOLO</p><h1>Segnapunti vita</h1><p>Un telefono al centro del tavolo, un contatore per ogni lato.</p></header><form id="life-setup"><label>Tipo partita<select name="kind"><option value="friendly">Amichevole</option><option value="tournament">Partita del torneo</option></select></label><label data-tournament-player hidden>Partecipante<select name="tournament_player"><option value="">Seleziona profilo</option></select></label><button type="button" class="button secondary" data-load-matches hidden>Carica abbinamenti del profilo</button><label data-match-label hidden>Incontro<select name="match"><option value="">Nessun abbinamento disponibile</option></select></label><p data-match-hint hidden>Seleziona il tuo profilo e carica i tuoi abbinamenti quando sei online. Offline sono disponibili gli ultimi abbinamenti salvati.</p><label>Formato<select name="format"><option value="duel">Duello</option><option value="team">Squadre</option><option value="ffa">Tutti contro tutti</option></select></label><label>Vita iniziale<select name="life_mode"><option value="same">Uguale per tutti</option><option value="different">Diversa per ogni lato</option></select></label><label data-common>Vita iniziale comune<input type="number" name="common_life" min="1" max="999" value="20" required></label><div class="life-setup-sides"></div><div class="life-setup-actions"><button type="button" class="button secondary" data-add-side hidden>Aggiungi lato</button><button type="submit" class="button primary">Inizia partita</button></div><p>Se tutti i partecipanti hanno un profilo e c’è un vincitore, il risultato segue la normale conferma. Avviando un’amichevole autorizzi l’accesso come primo giocatore del primo lato, anche alla riconnessione; per un torneo si usa il partecipante scelto. Ospiti, tutti contro tutti e pareggi restano nel solo archivio.</p><p data-queue-status role="status"></p><button type="button" class="button secondary" data-queue-retry>Riprova sincronizzazione</button><button type="button" class="button secondary" data-queue-login hidden>Accedi come proponente e sincronizza</button><p class="life-error" role="alert"></p></form></div>`;
    const form = root.querySelector('form');
    function renderSides() {
      const format = form.elements.format.value;
      root.querySelector('[data-add-side]').hidden = format !== 'ffa' || sides.length >= 4;
      root.querySelector('.life-setup-sides').innerHTML = sides.map((members, i) => `<fieldset><legend>${format === 'team' ? 'Squadra' : 'Lato'} ${i + 1}</legend><div class="life-members">${members.map((m, j) => `<div class="life-member"><span>${esc(m.name)}</span><button type="button" data-remove-member="${i},${j}" aria-label="Rimuovi ${esc(m.name)}">×</button></div>`).join('')}</div><div class="life-add-member"><select aria-label="Profilo lato ${i+1}" data-profile="${i}"><option value="">Scegli profilo</option>${profiles.map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select><button type="button" data-add-profile="${i}">Aggiungi</button></div><div class="life-add-member"><input data-guest="${i}" maxlength="60" placeholder="Nome ospite" aria-label="Nome ospite lato ${i+1}"><button type="button" data-add-guest="${i}">Aggiungi</button></div><label ${form.elements.life_mode.value === "same" ? "hidden" : ""}>Vita iniziale<input type="number" data-initial="${i}" min="1" max="999" value="20" required></label>${format === 'ffa' && sides.length > 2 ? `<button type="button" data-remove-side="${i}">Rimuovi lato</button>` : ''}</fieldset>`).join('');
      if(form.elements.kind.value==='tournament')root.querySelectorAll('.life-setup-sides button, .life-setup-sides select, .life-setup-sides input[data-guest]').forEach(el=>{el.disabled=true;});
    }
    updateTournamentPlayers();updateMatchChoices();
    form.elements.tournament_player.onchange=()=>{const id=form.elements.tournament_player.value;const cached=read('mana-life-matches-'+id,null);sessionPlayer=id?(cached?.player||{player_id:id}):null;cachedMatches=Array.isArray(cached?.matches)?cached.matches:[];sides=[[],[]];renderSides();updateMatchChoices();};
    form.elements.kind.onchange=()=>{const tournament=form.elements.kind.value==='tournament';root.querySelector('[data-tournament-player]').hidden=!tournament;root.querySelector('[data-load-matches]').hidden=!tournament;root.querySelector('[data-match-label]').hidden=!tournament;root.querySelector('[data-match-hint]').hidden=!tournament;form.elements.format.disabled=tournament;if(tournament){form.elements.format.value='duel';sides=[[],[]];}renderSides();};
    form.elements.match.onchange=()=>{const m=cachedMatches[Number(form.elements.match.value)];if(form.elements.match.value===''||!m)return;sides=[m.p1,m.p2].map(id=>[{id,name:profiles.find(p=>p.id===id)?.name||id}]);renderSides();};
    form.elements.life_mode.onchange = () => { root.querySelector('[data-common]').hidden=form.elements.life_mode.value!=='same'; renderSides(); };
    form.elements.format.onchange = () => { sides = [[], []]; renderSides(); };
    root.onclick = e => {
      const b = e.target.closest('button'); if (!b) return;
      if (b.hasAttribute('data-load-matches')) {
        loadMatchesForPlayer(form.elements.tournament_player.value);
        return;
      }
      if (b.hasAttribute('data-queue-retry')) { drainOutbox(); return; }
      if (b.hasAttribute('data-queue-login')) { loginAndDrain(); return; }
      const preserve = [...root.querySelectorAll('[data-initial]')].map(x => x.value);
      if (b.hasAttribute('data-add-side')) sides.push([]);
      else if (b.hasAttribute('data-remove-side')) sides.splice(Number(b.dataset.removeSide), 1);
      else if (b.hasAttribute('data-remove-member')) { const [i,j] = b.dataset.removeMember.split(',').map(Number); sides[i].splice(j,1); }
      else if (b.hasAttribute('data-add-profile')) { const i = Number(b.dataset.addProfile), id = root.querySelector(`[data-profile="${i}"]`).value, p = profiles.find(x => String(x.id) === id); if (!p) return; if (sides.flat().some(m => m.id === id)) { root.querySelector('.life-error').textContent = 'Questo profilo è già stato aggiunto.'; return; } sides[i].push({id,name:p.name}); }
      else if (b.hasAttribute('data-add-guest')) { const i = Number(b.dataset.addGuest), name = root.querySelector(`[data-guest="${i}"]`).value.trim(); if (!name) return; sides[i].push({name}); }
      else return;
      renderSides(); root.querySelectorAll('[data-initial]').forEach((x,i) => { if (preserve[i] !== undefined) x.value = preserve[i]; });
    };
    form.onsubmit = async e => {
      e.preventDefault(); if (busy || game) return; const format = form.elements.format.value, kind=form.elements.kind.value;
      if(kind==='tournament' && (form.elements.match.value==='' || !sessionPlayer)){root.querySelector('.life-error').textContent='Seleziona un abbinamento disponibile.';return;}
      if (sides.some(s => !s.length || s.length > 6 || (format !== 'team' && s.length !== 1))) { root.querySelector('.life-error').textContent = format === 'team' ? 'Aggiungi da uno a sei giocatori per squadra.' : 'Aggiungi un solo giocatore per ogni lato.'; return; }
      const names = sides.map(s => s.map(m => m.name).join(' + '));
      if (names.some(n => n.length > 160)) { root.querySelector('.life-error').textContent = 'Il nome di un lato è troppo lungo (massimo 160 caratteri).'; return; }
      if (new Set(names.map(n => n.toLocaleLowerCase())).size !== names.length) { root.querySelector('.life-error').textContent = 'I lati devono avere nomi diversi.'; return; }
      const match=kind==='tournament'?cachedMatches[Number(form.elements.match.value)]:null;
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

  function drawGame() {
    root.innerHTML = `<div class="life-table-shell"><header class="life-toolbar"><div><p class="life-kicker">PARTITA IN CORSO</p><h1>Segnapunti vita</h1></div><button type="button" class="button secondary" data-end>Termina</button></header><small data-screen-status role="status"></small><p class="life-roll-result" aria-live="polite">Tira un D20 per ogni lato: il più alto inizia.</p><div class="life-table life-table-${game.sides.length}">${game.sides.map((s,i)=>`<article class="life-side life-side-${i}"><div class="life-side-inner"><h2>${esc(s.name)}</h2><div class="life-counter"><button type="button" data-delta="${i},-1" aria-label="Togli una vita a ${esc(s.name)}">−</button><strong data-life="${i}">${s.life}</strong><button type="button" data-delta="${i},1" aria-label="Aggiungi una vita a ${esc(s.name)}">+</button></div><div class="life-side-actions"><button type="button" data-undo="${i}">Annulla</button><button type="button" data-roll="${i}">Tira D20</button></div><div class="life-die-wrap"><canvas width="150" height="150" data-die="${i}" aria-label="Dado a venti facce"></canvas><span data-roll-value="${i}">—</span></div></div></article>`).join('')}</div><p class="life-error" role="alert"></p></div>`;
    syncScreenLock();
    game.sides.forEach((_,i)=>{drawDie(root.querySelector(`[data-die="${i}"]`),0,0,0,game.rolls.values[i]);root.querySelector(`[data-roll-value="${i}"]`).textContent=game.rolls.values[i]||'—';}); updateRollStatus();
    root.onclick = e => { const b=e.target.closest('button'); if (!b || busy) return;
      if (b.dataset.delta) { const [i,delta]=b.dataset.delta.split(',').map(Number); change(i,delta); }
      else if (b.dataset.undo !== undefined) { const i=Number(b.dataset.undo), previous=[...game.changes].reverse().find(c=>c.player===game.sides[i].name && !c.undone && !c.undo); if(previous && game.changes.length<5000){previous.undone=true;change(i,-previous.delta,true);} }
      else if (b.dataset.roll !== undefined) roll(Number(b.dataset.roll));
      else if (b.hasAttribute('data-end')) end();
    };
  }
  function change(i,delta,undo=false){ if(game.changes.length>=5000){root.querySelector('.life-error').textContent='Limite di 5000 variazioni raggiunto.';return;} const s=game.sides[i];s.life+=delta;game.changes.push({at:stamp(),player:s.name,delta,life:s.life,...(undo?{undo:true}:{})});root.querySelector(`[data-life="${i}"]`).textContent=s.life; persist(); }
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
    const max=Math.max(...game.sides.map(s=>s.life)), tied=game.sides.filter(s=>s.life===max);
    root.innerHTML=`<div class="life-shell"><h1>Termina partita</h1><p>${tied.length===1?`Vincitore suggerito: ${esc(tied[0].name)} (${max} vite).`:'Parità: scegli manualmente il risultato.'}</p><form><label>Risultato<select name="winner" required>${tied.length>1?'<option value="" selected disabled>Scegli il risultato</option>':''}<option value="-1">Pareggio / nessun vincitore</option>${game.sides.map((s,i)=>`<option value="${i}" ${tied.length===1&&s===tied[0]?'selected':''}>${esc(s.name)}</option>`).join('')}</select></label><p>La partita sarà salvata nell’Archivio segnavita.</p><p class="life-error" role="alert"></p><div class="life-setup-actions"><button type="button" class="button secondary" data-back>Torna alla partita</button><button class="button primary">Salva partita</button></div></form></div>`;
    syncScreenLock();
    root.onclick=e=>{if(e.target.closest('[data-back]'))drawGame();};root.querySelector('form').onsubmit=e=>{e.preventDefault();const selected=e.currentTarget.elements.winner.value;game.payload={session_id:game.session_id,started_at:game.started_at,ended_at:stamp(),starting_life:game.sides[0].initial,format:game.format,players:game.sides.map(s=>({name:s.name,initial_life:s.initial,final_life:s.life,members:s.members.map(m=>({id:m.id||null,name:m.name}))})),changes:game.changes.map(({at,player,delta,life,undo})=>({at,player,delta,life,undo:!!undo})),winner:selected==='-1'?null:game.sides[Number(selected)].name};
      if(game.payload.winner && game.format!=='ffa' && game.sides.every(s=>s.members.length<=3 && s.members.every(m=>m.id))){const proposer=game.kind==='tournament'?game.proposer:game.sides[0].members[0].id;if(proposer)game.payload.result_target=game.kind==='tournament'?{kind:'tournament',tournament_id:game.match.tournament.id,match_id:game.match.id,proposer_id:proposer}:{kind:'friendly',proposer_id:proposer};}
      if(!enqueue(game.payload)){game.payload=null;return;}persist();savePending();};
  }
  function savePending(){
    const finished=game;
    root.innerHTML='<div class="life-shell"><h1>Archivio segnavita</h1><p data-status>Partita conservata su questo dispositivo.</p><p data-queue-status role="status"></p><p class="life-error" role="alert"></p><div class="life-setup-actions"><button type="button" class="button secondary" data-retry>Riprova sincronizzazione</button><button type="button" class="button secondary" data-queue-login hidden>Accedi come proponente e sincronizza</button><button type="button" class="button secondary" data-new>Nuova partita</button></div></div>';
    root.onclick=e=>{if(e.target.closest('[data-retry]'))drainOutbox();if(e.target.closest('[data-queue-login]'))loginAndDrain();if(e.target.closest('[data-new]')){try{localStorage.removeItem(key);}catch(_){reportStorage('Impossibile aggiornare la memoria locale.');return;}sides=[[],[]];game=null;setup();}};
    if(finished.payload.result_target)root.querySelector('[data-status]').textContent+=' Il risultato seguirà la normale conferma dopo la sincronizzazione.';
    refreshQueueStatus();drainOutbox();
  }
  window.addEventListener('online',()=>drainOutbox());
  window.openLifeCounter = () => { if (!root.firstElementChild) { game=restore(); if(game?.payload){savePending();} else if(game?.sides && game?.rolls)drawGame(); else setup(); } drainOutbox(); };
})();
