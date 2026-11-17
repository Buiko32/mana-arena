(() => {
  'use strict';
  const $ = s => document.querySelector(s), esc = x => String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let session=null, players=[], matches=[];
  const dialog=$('#player-results-dialog'), content=$('#player-results-content'), select=$('#player-identity');
  async function api(path,options={}) {const headers=new Headers(options.headers||{});if(session?.csrf_token)headers.set('X-Player-CSRF',session.csrf_token);const r=await fetch(path,{...options,headers,credentials:'same-origin'});const data=await r.json();if(!r.ok){const error=Error(data.detail?.message||'Operazione non riuscita.');error.status=r.status;throw error;}return data;}
  const post=(path,data={})=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  function error(e){const el=$('#player-results-error');if(el)el.textContent=e.message;}
  async function getSession(){try{session=await api('/api/player/session');localStorage.setItem('mana-arena-player-id',session.player_id);if(![...select.options].some(o=>o.value===session.player_id))select.add(new Option(session.name,session.player_id));select.value=session.player_id;document.dispatchEvent(new Event('player-identity-changed'));}catch(error){if(error.status===401){session=null;try{localStorage.removeItem('mana-arena-player-id');}catch{}select.value='';}else throw error;}}
  async function render(){if(!session){content.innerHTML='<p>Seleziona il tuo nome in alto per accedere.</p>';return;}
    const [ranking,ownMatches]=await Promise.all([api('/api/public/ranking'),api('/api/player/matches')]);players=ranking;matches=ownMatches;
    const name=id=>players.find(p=>p.id===id)?.name||'Giocatore';
    $('#player-results-title').textContent=`Inserisci il risultato ${session.name}`;
    content.innerHTML=`<div class="results-panel"><form id="new-player-result" class="results-panel"><label>Partita<select name="match">${matches.map((m,i)=>`<option value="${i}">${esc(m.tournament.config.name)} · ${esc(name(m.p1))} – ${esc(name(m.p2))}</option>`).join('')}<option value="friendly">Amichevole</option></select></label><label id="opponent-label">Avversario<select name="opponent">${players.filter(p=>p.id!==session.player_id).map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select></label><label>Vincitore<select name="winner"></select></label><p id="player-results-error" class="results-error" role="alert"></p><button class="button primary" type="submit">Invia all’avversario</button></form></div>`;
    content.querySelector('[data-result-reveal]')?.addEventListener('click',event=>{const id=event.currentTarget.dataset.resultReveal;dialog.close();document.dispatchEvent(new CustomEvent('reveal-tournament',{detail:id}));});
    const teamButton=document.createElement('button');teamButton.type='button';teamButton.className='button secondary';teamButton.textContent='Amichevole a squadre';content.prepend(teamButton);teamButton.addEventListener('click',renderTeamForm);
    const form=$('#new-player-result');function update(){const m=matches[Number(form.elements.match.value)],friendly=form.elements.match.value==='friendly';$('#opponent-label').hidden=!friendly;form.elements.opponent.required=friendly;const opponent=friendly?form.elements.opponent.value:(m.p1===session.player_id?m.p2:m.p1);form.elements.winner.innerHTML=[session.player_id,opponent].filter(Boolean).map(id=>`<option value="${esc(id)}">${esc(name(id))}</option>`).join('');}form.elements.match.addEventListener('change',update);form.elements.opponent.addEventListener('change',update);update();form.addEventListener('submit',async e=>{e.preventDefault();const m=matches[Number(form.elements.match.value)],friendly=form.elements.match.value==='friendly';try{await post('/api/player/results',friendly?{opponent_id:form.elements.opponent.value,winner_id:form.elements.winner.value}:{tournament_id:m.tournament.id,match_id:m.id,winner_id:form.elements.winner.value});await render();const notice=document.createElement('p');notice.setAttribute('role','status');notice.textContent='Risultato inviato: in attesa della conferma dell’avversario.';content.prepend(notice);}catch(err){error(err)}});
  }
  function renderTeamForm(){
    const choice=(p,locked=false)=>`<label class="team-choice"><span class="team-choice-person">${window.manaPlayerAvatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)}<span>${esc(p.name)}${locked?' (tu)':''}</span></span><input type="checkbox" value="${esc(p.id)}" ${locked?'checked disabled':''}></label>`;
    const options=players.filter(p=>p.id!==session.player_id).map(p=>choice(p)).join('');
    const own=players.find(p=>p.id===session.player_id)||{id:session.player_id,name:session.name};
    content.innerHTML=`<form id="team-result-form" class="results-panel"><p>Seleziona da 2 a 3 giocatori per squadra. Tu fai parte della squadra A.</p><fieldset><legend>Squadra A · ${esc(session.name)}</legend><div class="team-choices">${choice(own,true)}<div id="team-a" class="team-choices">${options}</div></div></fieldset><fieldset><legend>Squadra B · avversari</legend><div id="team-b" class="team-choices">${options}</div></fieldset><label>Squadra vincitrice<select name="winner"><option value="a">Squadra A</option><option value="b">Squadra B</option></select></label><p id="team-result-error" class="results-error" role="alert"></p><button class="button primary" type="submit">Invia agli avversari</button><button class="button quiet" type="button" id="team-result-back">Indietro</button></form>`;
    const teamForm=$('#team-result-form');
    teamForm.addEventListener('change',event=>{
      const input=event.target;
      if(!input.matches('#team-a input[type="checkbox"], #team-b input[type="checkbox"]'))return;
      const other=input.closest('#team-a')?$('#team-b'):$('#team-a');
      const counterpart=[...other.querySelectorAll('input[type="checkbox"]')].find(item=>item.value===input.value);
      if(counterpart){counterpart.disabled=input.checked;counterpart.closest('.team-choice').classList.toggle('is-unavailable',input.checked);}
    });
    $('#team-result-back').addEventListener('click',()=>render().catch(error));
    $('#team-result-form').addEventListener('submit',async event=>{event.preventDefault();const selected=id=>[...document.querySelectorAll(`#${id} input:checked`)].map(input=>input.value);const a=[session.player_id,...selected('team-a')],b=selected('team-b');if(a.length<2||a.length>3||b.length<2||b.length>3||new Set([...a,...b]).size!==a.length+b.length){$('#team-result-error').textContent='Scegli da 2 a 3 giocatori distinti per ciascuna squadra.';return;}try{await post('/api/player/team-results',{team_a:a,team_b:b,winner:event.currentTarget.elements.winner.value});content.innerHTML='<p role="status">Risultato inviato: tutti gli avversari devono confermare.</p>';await refreshConfirmationCount();}catch(err){$('#team-result-error').textContent=err.message;}});
  }
  const profileDialog=$('#profile-dialog'), profileContent=$('#profile-content');
  function hueHex(h){const hue=((Number(h)||0)%360)/360, sat=.64, light=.48;const f=n=>{const k=(n+hue*12)%12;const a=sat*Math.min(light,1-light);return Math.round(255*(light-a*Math.max(-1,Math.min(k-3,9-k,1)))).toString(16).padStart(2,'0');};return `#${f(0)}${f(8)}${f(4)}`;}
  async function renderProfile(notice=''){
    if(!session){profileContent.innerHTML='<p>Seleziona il tuo nome in alto per modificare il profilo.</p>';return;}
    const p=await api('/api/player/profile'), color=/^#[0-9a-f]{6}$/i.test(p.avatar_color||'')?p.avatar_color:hueHex(p.avatar_hue);
    profileContent.innerHTML=`<div class="profile-panel"><p class="profile-name">${esc(p.name)}</p><div class="profile-preview">${p.photo_url?`<div><span>Foto attuale</span><img src="${esc(p.photo_url)}" alt="Immagine del profilo"></div>`:''}<div><span>Segnaposto</span><span id="profile-avatar-preview">${window.manaPlayerAvatar(p.name,null,p.avatar_hue,p.avatar_color)}</span></div></div><form id="profile-color-form" class="profile-form"><label>Colore del segnaposto <input type="color" name="picker" value="${esc(color)}"></label><label>Codice colore <input name="color" value="${esc(color)}" pattern="#[0-9a-fA-F]{6}" maxlength="7" required spellcheck="false"></label><button class="button secondary" type="submit">Salva colore</button></form><form id="profile-photo-form" class="profile-form"><label>Immagine del profilo <input type="file" name="file" accept="image/jpeg,image/png,image/webp" required></label><button class="button primary" type="submit">Carica immagine</button></form>${p.photo_url?'<button type="button" id="profile-remove-photo" class="button quiet">Usa il segnaposto</button>':''}<p id="profile-status" role="status">${esc(notice)}</p></div>`;
    const colorForm=$('#profile-color-form'), picker=colorForm.elements.picker, input=colorForm.elements.color;
    const preview=color=>$('#profile-avatar-preview .avatar')?.style.setProperty('--avatar-color',color);picker.addEventListener('input',()=>{input.value=picker.value;preview(picker.value);});input.addEventListener('input',()=>{if(/^#[0-9a-f]{6}$/i.test(input.value)){picker.value=input.value;preview(input.value);}});
    colorForm.addEventListener('submit',async event=>{event.preventDefault();try{await api('/api/player/profile',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({avatar_color:input.value})});document.dispatchEvent(new Event('player-profile-changed'));await renderProfile('Colore aggiornato.');}catch(err){$('#profile-status').textContent=err.message;}});
    $('#profile-photo-form').addEventListener('submit',async event=>{event.preventDefault();try{const data=new FormData();data.append('file',event.currentTarget.elements.file.files[0]);await api('/api/player/profile/photo',{method:'POST',body:data});document.dispatchEvent(new Event('player-profile-changed'));await renderProfile('Immagine aggiornata.');}catch(err){$('#profile-status').textContent=err.message;}});
    $('#profile-remove-photo')?.addEventListener('click',async()=>{try{await api('/api/player/profile/photo',{method:'DELETE'});document.dispatchEvent(new Event('player-profile-changed'));await renderProfile('Segnaposto ripristinato.');}catch(err){$('#profile-status').textContent=err.message;}});
  }
  $('#profile-open').addEventListener('click',async()=>{profileDialog.showModal();try{await getSession();await renderProfile();}catch(err){profileContent.textContent=err.message;}});
  $('#profile-close').addEventListener('click',()=>profileDialog.close());
  const confirmations=$('#confirmations-dialog'), confirmationsContent=$('#confirmations-content');
  const confirmationButton=$('#confirmations-open'), confirmationCount=$('#confirmations-count');
  let confirmationRequest=0;
  function showConfirmationCount(count){confirmationCount.hidden=!count;confirmationCount.textContent=count>99?'99+':String(count||'');confirmationButton.setAttribute('aria-label',count?`Risultati da confermare: ${count}`:'Risultati da confermare');}
  async function refreshConfirmationCount(){const request=++confirmationRequest, playerId=session?.player_id;if(!playerId){showConfirmationCount(0);return;}try{const [results,teams]=await Promise.all([api('/api/player/results'),api('/api/player/team-results')]);if(request===confirmationRequest&&session?.player_id===playerId)showConfirmationCount(results.filter(p=>p.status==='pending'&&p.opponent_id===playerId).length+teams.filter(p=>p.players?.some(m=>m.player_id===playerId&&m.team==='b'&&!m.confirmed)).length);}catch{if(request===confirmationRequest)showConfirmationCount(0);}}

  async function renderConfirmations(){if(!session){confirmationsContent.textContent='Seleziona il tuo nome in alto.';return;}const [pending,profiles,teamResults]=await Promise.all([api('/api/player/results'),api('/api/public/ranking').catch(()=>[]),api('/api/player/team-results')]);const player=(id,name)=>{const p=profiles.find(item=>item.id===id)||{};return `<span class="confirmation-player">${window.manaPlayerAvatar(name,p.photo_url,p.avatar_hue,p.avatar_color)}<span>${esc(name)}</span></span>`;};const awaiting=pending.filter(p=>p.status==='pending');showConfirmationCount(awaiting.filter(p=>p.opponent_id===session.player_id).length+teamResults.filter(p=>p.players?.some(m=>m.player_id===session.player_id&&m.team==='b'&&!m.confirmed)).length);confirmationsContent.innerHTML=awaiting.map(p=>`<div class="result-pending"><div class="confirmation-match">${player(p.proposer_id,p.proposer_name)}<span class="confirmation-versus">vs</span>${player(p.opponent_id,p.opponent_name)}</div><p class="confirmation-winner">Vince ${esc(p.winner_name)}</p><div class="results-actions">${p.opponent_id===session.player_id?`<button type="button" class="button primary" data-confirm="${esc(p.id)}">Conferma</button>`:'<span>In attesa dell’avversario</span>'}<button type="button" class="button danger" data-reject="${esc(p.id)}">${p.opponent_id===session.player_id?'Rifiuta':'Annulla'}</button></div></div>`).join('')||'<p>Nessuna richiesta in attesa.</p>';if(teamResults.length){if(!awaiting.length)confirmationsContent.innerHTML='';confirmationsContent.innerHTML+=teamResults.map(p=>{const team=key=>`<section class="confirmation-team ${p.winning_team===key?'is-winning':''}"><div class="confirmation-team-heading"><strong>Squadra ${key.toUpperCase()}</strong>${p.winning_team===key?'<span class="confirmation-team-winner">Vincitrice</span>':''}</div><div class="confirmation-team-players">${p.players.filter(m=>m.team===key).map(m=>player(m.player_id,m.name)).join('')}</div></section>`;const me=p.players.find(m=>m.player_id===session.player_id);const canConfirm=me?.team==='b'&&!me.confirmed;const canReject=me?.team==='b'||p.proposer_id===session.player_id;return `<div class="result-pending"><strong>Amichevole a squadre</strong><div class="confirmation-teams">${team('a')}${team('b')}</div><div class="results-actions">${canConfirm?`<button type="button" class="button primary" data-team-confirm="${esc(p.id)}">Conferma</button>`:'<span>In attesa delle conferme</span>'}${canReject?`<button type="button" class="button danger" data-team-reject="${esc(p.id)}">${p.proposer_id===session.player_id?'Annulla':'Rifiuta'}</button>`:''}</div></div>`;}).join('');confirmationsContent.querySelectorAll('[data-team-confirm],[data-team-reject]').forEach(button=>button.onclick=async()=>{try{const id=button.dataset.teamConfirm||button.dataset.teamReject;await post(`/api/player/team-results/${encodeURIComponent(id)}/${button.dataset.teamConfirm?'confirm':'reject'}`);await renderConfirmations();}catch(err){confirmationsContent.textContent=err.message;}});}confirmationsContent.querySelectorAll('[data-confirm],[data-reject]').forEach(button=>button.onclick=async()=>{try{const id=button.dataset.confirm||button.dataset.reject;await post(`/api/player/results/${encodeURIComponent(id)}/${button.dataset.confirm?'confirm':'reject'}`);await renderConfirmations();}catch(err){confirmationsContent.textContent=err.message;}});}
  $('#confirmations-open').addEventListener('click',async()=>{confirmations.showModal();try{await getSession();await renderConfirmations();}catch(err){confirmationsContent.textContent=err.message;}});
  $('#confirmations-close').addEventListener('click',()=>confirmations.close());
  $('#results-open').addEventListener('click',async()=>{dialog.showModal();try{await getSession();await render();}catch(e){content.textContent=e.message;}});
  $('#player-results-close').addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{const id=session?.player_id||'';select.value=id;try{if(id)localStorage.setItem('mana-arena-player-id',id);else localStorage.removeItem('mana-arena-player-id');}catch{}});
  select.addEventListener('change',async()=>{++confirmationRequest;showConfirmationCount(0);const id=select.value;document.dispatchEvent(new Event('player-identity-changing'));try{if(!id){if(session)await post('/api/player/logout');session=null;localStorage.removeItem('mana-arena-player-id');}else{session=await post('/api/player/login',{player_id:id});localStorage.setItem('mana-arena-player-id',id);}document.dispatchEvent(new Event('player-identity-changed'));await refreshConfirmationCount();}catch(err){select.value=session?.player_id||'';document.dispatchEvent(new Event('player-identity-changed'));await refreshConfirmationCount();window.alert(err.message);}});
  const welcome=$('#identity-welcome'), welcomeForm=$('#identity-welcome-form'), welcomePlayers=$('#identity-welcome-players'), welcomeMessage=$('#identity-welcome-message'), welcomeSubmit=$('#identity-welcome-submit');
  async function showIdentityWelcome(){
    if(!welcome)return;
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
        welcome.close();await refreshConfirmationCount();
      }catch(err){document.dispatchEvent(new Event('player-identity-changed'));welcomeMessage.textContent=err.message||'Impossibile selezionare il giocatore.';}
      finally{welcomeSubmit.disabled=false;}
    });
  }
  let rememberedPlayer='';try{rememberedPlayer=localStorage.getItem('mana-arena-player-id')||'';}catch{}
  getSession().then(async()=>{
    if(!session&&rememberedPlayer){
      try{session=await post('/api/player/login',{player_id:rememberedPlayer});try{localStorage.setItem('mana-arena-player-id',session.player_id);}catch{}if(![...select.options].some(o=>o.value===session.player_id))select.add(new Option(session.name,session.player_id));select.value=session.player_id;document.dispatchEvent(new Event('player-identity-changed'));}catch{}
    }
    if(session)await refreshConfirmationCount();else await showIdentityWelcome();
  }).catch(()=>showIdentityWelcome());
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')refreshConfirmationCount();});
  setInterval(()=>{if(document.visibilityState==='visible')refreshConfirmationCount();},15000);
})();
