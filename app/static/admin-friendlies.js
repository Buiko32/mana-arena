(() => {
  'use strict';
  const root = document.querySelector('#admin-friendlies');
  if (!root) return;
  const esc = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pad = n => String(n).padStart(2,'0');
  const dayKey = d => `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
  let month = new Date(); month = new Date(month.getFullYear(),month.getMonth(),1);
  let day = '', selected = '', games = [], requestVersion = 0;
  const api = (path, options = {}) => window.manaAdminRequest(path, {...options, headers: {'Content-Type':'application/json'}});
  async function load() { const version=++requestVersion, end=new Date(month.getFullYear(),month.getMonth()+1,1); const result=await api('/api/admin/friendlies?start='+encodeURIComponent(month.toISOString())+'&end='+encodeURIComponent(end.toISOString())); if(version!==requestVersion)return; games=result; render(); }
  function render() {
    const start = (new Date(month.getFullYear(),month.getMonth(),1).getDay()+6)%7;
    const count = new Date(month.getFullYear(),month.getMonth()+1,0).getDate();
    const gamesOfDay = games.filter(g=>dayKey(new Date(g.at))===day);
    const game = gamesOfDay.find(g=>g.id===selected);
    const side = s => s.map(p=>p.name).join(' e ');
    root.innerHTML = `<div class="friendly-calendar"><div class="friendly-calendar-nav"><button type="button" data-month="-1" aria-label="Mese precedente">‹</button><strong>${esc(month.toLocaleDateString('it-IT',{month:'long',year:'numeric'}))}</strong><button type="button" data-month="1" aria-label="Mese successivo">›</button></div><div class="friendly-calendar-grid">${['L','M','M','G','V','S','D'].map(x=>`<span>${x}</span>`).join('')}${'<span></span>'.repeat(start)}${Array.from({length:count},(_,i)=>{const key=dayKey(new Date(month.getFullYear(),month.getMonth(),i+1)),countGames=games.filter(g=>dayKey(new Date(g.at))===key).length,has=countGames>0;return `<button type="button" data-day="${key}" aria-pressed="${key===day}" ${has?'class="has-games"':''}>${i+1}${has?`<span aria-label="${countGames} amichevoli">${'•'.repeat(Math.min(countGames,5))}</span>`:''}</button>`;}).join('')}</div></div><div class="friendly-day"><h4>${day?esc(new Date(day+'T12:00:00').toLocaleDateString('it-IT',{dateStyle:'full'})):'Seleziona un giorno'}</h4>${day?(gamesOfDay.map(g=>`<button type="button" class="button secondary" data-game="${esc(g.id)}">${esc(new Date(g.at).toLocaleTimeString('it-IT',{hour:'2-digit',minute:'2-digit',second:'2-digit'}))} · ${esc(side(g.sides[0]))} – ${esc(side(g.sides[1]))} · ${g.status==='pending'?'In attesa':'Vince '+esc(side(g.sides[g.winner==='a'?0:1]))}</button>`).join('')||'<p>Nessuna amichevole in questo giorno.</p>'):''}</div>${game?`<form id="friendly-edit"><h4>Modifica amichevole</h4><label>Data e ora<input type="datetime-local" name="at" step="1" required value="${dayKey(new Date(game.at))}T${pad(new Date(game.at).getHours())}:${pad(new Date(game.at).getMinutes())}:${pad(new Date(game.at).getSeconds())}"></label><label>Vincitore<select name="winner">${game.sides.map((s,i)=>`<option value="${i?'b':'a'}" ${game.winner===(i?'b':'a')?'selected':''}>${esc(side(s))}</option>`).join('')}</select></label><button class="button primary" type="submit" ${game.status==='pending'?'disabled':''}>Salva</button><button class="button danger" type="button" data-delete>Elimina definitivamente</button></form>`:''}<p role="status" class="friendly-message"></p>`;
    root.querySelectorAll('[data-month]').forEach(b=>b.onclick=()=>{month=new Date(month.getFullYear(),month.getMonth()+Number(b.dataset.month),1);day='';selected='';load().catch(e=>{root.textContent=e.message;});});
    root.querySelectorAll('[data-day]').forEach(b=>b.onclick=()=>{day=b.dataset.day;selected='';render();});
    root.querySelectorAll('[data-game]').forEach(b=>b.onclick=()=>{selected=b.dataset.game;render();});
    const form=root.querySelector('#friendly-edit');
    if(form){
      const path=`/api/admin/friendlies/${game.kind}/${encodeURIComponent(game.id)}`;
      form.onsubmit=async e=>{e.preventDefault();try{const winner=form.elements.winner.value;const body={at:new Date(form.elements.at.value).toISOString(),...(game.kind==='single'?{winner_id:game.sides[winner==='a'?0:1][0].id}:{winner})};await api(path,{method:'PATCH',body:JSON.stringify(body)});day=dayKey(new Date(body.at));month=new Date(new Date(body.at).getFullYear(),new Date(body.at).getMonth(),1);await load();root.querySelector('.friendly-message').textContent='Amichevole aggiornata.';}catch(err){root.querySelector('.friendly-message').textContent=err.message;}};
      form.querySelector('[data-delete]').onclick=async()=>{if(!confirm('Eliminare definitivamente questa amichevole? La classifica sarà ricalcolata.'))return;try{await api(path,{method:'DELETE'});selected='';await load();root.querySelector('.friendly-message').textContent='Amichevole eliminata.';}catch(err){root.querySelector('.friendly-message').textContent=err.message;}};
    }
  }
  window.openAdminFriendlies = () => load().catch(e=>{root.textContent=e.message;});
})();
