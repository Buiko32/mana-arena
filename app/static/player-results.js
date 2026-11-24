(() => {
  'use strict';
  const $ = s => document.querySelector(s), esc = x => String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let session=null, players=[], matches=[];
  const dialog=$('#player-results-dialog'), content=$('#player-results-content'), select=$('#player-identity');
  async function api(path,options={}) {const headers=new Headers(options.headers||{});if(session?.csrf_token)headers.set('X-Player-CSRF',session.csrf_token);const r=await fetch(path,{...options,headers,credentials:'same-origin'});const data=await r.json();if(!r.ok){const error=Error(data.detail?.message||'Operazione non riuscita.');error.status=r.status;throw error;}return data;}
  const post=(path,data={})=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  function error(e){const el=$('#player-results-error');if(el)el.textContent=e.message;}
  async function getSession(){try{session=await api('/api/player/session');localStorage.setItem('mana-arena-player-id',session.player_id);if(![...select.options].some(o=>o.value===session.player_id))select.add(new Option(session.name,session.player_id));select.value=session.player_id;document.dispatchEvent(new Event('player-identity-changed'));}catch(error){if(error.status===401){session=null;try{localStorage.removeItem('mana-arena-player-id');}catch{}select.value='';}else throw error;}}
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
      await post(path, payload);
      if (dialog.open) {
        resultSaved = true;
        if (form.isConnected) content.innerHTML = '<p role="status">Risultato registrato.</p>';
      } else {
        document.dispatchEvent(new Event('player-result-saved'));
      }
    } catch (err) {
      form.querySelector(errorSelector).textContent = err.message;
      delete form.dataset.saving;
      enabledControls.forEach(control => { control.disabled = false; });
    }
  }
  async function render(){if(!session){content.innerHTML='<p>Seleziona il tuo nome in alto per accedere.</p>';return;}
    const [ranking,ownMatches]=await Promise.all([api('/api/public/ranking'),api('/api/player/matches')]);players=ranking;matches=ownMatches;
    const name=id=>players.find(p=>p.id===id)?.name||'Giocatore';
    $('#player-results-title').textContent=`Inserisci il risultato ${session.name}`;
    content.innerHTML=`<div class="results-panel"><button type="button" class="button secondary" id="friendly-result">Amichevole (1–3 per parte)</button><form id="new-player-result" class="results-panel"><label>Partita di torneo<select name="match" required><option value="">Seleziona una partita</option>${matches.map((m,i)=>`<option value="${i}">🏆 ${esc(m.tournament.config.name)} · ${esc(name(m.p1))} – ${esc(name(m.p2))}</option>`).join('')}</select></label><label>Chi ha vinto?<select name="winner" required><option value="">Seleziona chi ha vinto</option></select></label><div class="result-review" aria-live="polite"></div><p id="player-results-error" class="results-error" role="alert"></p><button class="button primary" type="submit" disabled>Salva risultato</button></form></div>`;
    $('#friendly-result').addEventListener('click',renderTeamForm);
    const form=$('#new-player-result');
    function update(){const value=form.elements.match.value,m=value===''?null:matches[Number(value)],winner=form.elements.winner.value;updateSummary(form,m?'🏆 Partita di torneo · '+m.tournament.config.name:'Partita di torneo',m?[[name(m.p1)],[name(m.p2)]]:[[],[]],winner?name(winner):'',Boolean(m&&winner));}
    form.elements.match.addEventListener('change',()=>{const value=form.elements.match.value,m=value===''?null:matches[Number(value)];form.elements.winner.replaceChildren(new Option('Seleziona chi ha vinto',''));if(m)for(const id of [m.p1,m.p2])form.elements.winner.add(new Option(name(id),id));update();});
    form.elements.winner.addEventListener('change',update);update();
    form.addEventListener('submit',event=>{event.preventDefault();const value=form.elements.match.value,m=value===''?null:matches[Number(value)],winner=form.elements.winner.value;if(m&&winner)saveResult(form,'/api/player/results',{tournament_id:m.tournament.id,match_id:m.id,winner_id:winner},'#player-results-error');});
  }
  function renderTeamForm(){
    const choice=(p,locked=false)=>`<label class="team-choice"><span class="team-choice-person">${window.manaPlayerAvatar(p.name,p.photo_url,p.avatar_hue,p.avatar_color)}<span>${esc(p.name)}${locked?' (tu)':''}</span></span><input type="checkbox" value="${esc(p.id)}" ${locked?'checked disabled':''}></label>`;
    const options=players.filter(p=>p.id!==session.player_id).map(p=>choice(p)).join('');
    const own=players.find(p=>p.id===session.player_id)||{id:session.player_id,name:session.name};
    content.innerHTML=`<form id="team-result-form" class="results-panel"><p>Puoi giocare da solo o scegliere fino a due compagni. Seleziona da uno a tre avversari.</p><fieldset><legend>La tua squadra</legend><div class="team-choices">${choice(own,true)}<div id="team-a" class="team-choices">${options}</div></div></fieldset><fieldset><legend>Avversari</legend><div id="team-b" class="team-choices">${options}</div></fieldset><label>Chi ha vinto?<select name="winner" required></select></label><div class="result-review" aria-live="polite"></div><p id="team-result-error" class="results-error" role="alert"></p><button class="button primary" type="submit" disabled>Salva risultato</button><button class="button quiet" type="button" id="team-result-back">Indietro</button></form>`;
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
    $('#team-result-back').addEventListener('click',()=>render().catch(error));
    teamForm.addEventListener('submit',event=>{event.preventDefault();const a=[session.player_id,...selected('a')],b=selected('b'),win=winner.value;if(a.length>3||b.length<1||b.length>3||!win||new Set([...a,...b]).size!==a.length+b.length){$('#team-result-error').textContent='Scegli da 1 a 3 giocatori distinti per parte e il vincitore.';return;}saveResult(teamForm,'/api/player/team-results',{team_a:a,team_b:b,winner:win},'#team-result-error');});
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
  $('#results-open').addEventListener('click',async()=>{dialog.showModal();try{await getSession();await render();}catch(e){content.textContent=e.message;}});
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
