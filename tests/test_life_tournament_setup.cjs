const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync('app/static/life.js','utf8').replace(
  '  window.openLifeCounter =', '  globalThis.setupTest = {setup, enqueue, drainOutbox, refreshQueueStatus};\n  window.openLifeCounter =',
);
const profiles = [{id:'a',name:'Ada'},{id:'b',name:'Bruno',photo_url:'/photos/abcd.jpg'},{id:'c',name:'Carla'}];
const match = (id,p2) => ({id,p1:'a',p2,tournament:{id,config:{name:'Torneo ' + id}}});

function fixture({offline = false, matches = [match('uno','b'),match('due','c')], cached = false} = {}) {
  const values = new Map([['mana-life-roster-v1',JSON.stringify(profiles)]]);
  const player = {player_id:'a',name:'Ada'};
  if (cached) values.set('mana-life-matches-v1',JSON.stringify({player,matches}));
  const nodes = new Map();
  const field = (value = '') => ({value,innerHTML:'',hidden:false,disabled:false,textContent:'',focus(){}});
  const submit = field();
  const sides = {...field(), classList: {add() {}, remove() {}}};
  const form = {
    elements:{kind:field('friendly'),format:field('duel'),tournament_player:field(),match:field(),life_mode:field('same')},
    querySelector(selector) {
      if (selector === '.life-setup-sides') return sides;
      if (selector === '.life-error') return root.querySelector(selector);
      return submit;
    },
    addEventListener(event, callback){this[event] = callback;},
  };
  const root = {
    addEventListener(){}, querySelectorAll:() => [],
    querySelector(selector) {
      if (selector === '#life-setup' || selector === 'form') return form;
      if (selector === '.life-setup-sides') return sides;
      if (!nodes.has(selector)) nodes.set(selector,field());
      return nodes.get(selector);
    },
  };
  const calls = [];
  const ctx = {
    document:{querySelector:() => root,addEventListener(){}},window:{addEventListener(){}},
    navigator:{onLine:!offline},
    localStorage:{getItem:key => values.get(key) ?? null,setItem:(key,value) => values.set(key,value)},
    async fetch(path) {
      calls.push(path);
      const data = {'/api/player/session':player,'/api/player/matches':matches,'/api/public/ranking':profiles,
        '/api/life/games':{saved:true,result_status:'stats_only'}};
      return {ok:true,json:async () => data[path]};
    },
  };
  vm.runInNewContext(fs.readFileSync("app/static/player-avatar.js","utf8")+"\n"+source,ctx);
  return {api:ctx.setupTest,root,form,submit,calls};
}

test('loads and selects the first tournament match with fixed players',async () => {
  const {api,root,form,submit,calls} = fixture();
  api.setup();
  assert.equal(root.querySelector('[data-kind="tournament"]').hidden,true);
  form.elements.kind.value = 'tournament';
  form.elements.kind.onchange();
  assert.equal(submit.disabled,true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.filter(path => path === '/api/player/matches').length,1);
  assert.equal(form.elements.match.value,'uno');
  assert.equal(submit.disabled,false);
  assert.equal(root.querySelector('[data-kind="tournament"]').hidden,false);
  const sides = root.querySelector('.life-setup-sides');
  assert.match(sides.innerHTML,/Ada.*Bruno/);
  assert.doesNotMatch(sides.innerHTML,/Aggiungi|data-remove-member|Nome ospite/);
  assert.doesNotMatch(root.innerHTML,/data-add-side/);
  form.elements.match.value = 'due';
  form.elements.match.onchange();
  assert.match(sides.innerHTML,/Ada.*Carla/);
  form.elements.kind.value = 'friendly';
  form.elements.kind.onchange();
  assert.match(sides.innerHTML,/data-profile="0"/);
  assert.doesNotMatch(sides.innerHTML,/data-add-profile/);
  form.change({target:{value:'b',dataset:{profile:'0'},matches:selector => selector === '[data-profile]'}});
  assert.match(sides.innerHTML,/data-remove-member="0,0"/);
  assert.match(sides.innerHTML, /class="life-player-choice">\s*<span class="life-avatar"[^>]*>.*?<img src="\/photos\/abcd\.jpg"[^>]*><\/span>\s*<select[^>]*data-profile="0"[^>]*><option value="">Bruno<\/option>/s);
  assert.equal((sides.innerHTML.match(/value="b" disabled/g) || []).length, 2);
  assert.doesNotMatch(root.innerHTML,/I risultati con un vincitore|Carica abbinamenti/);
});

test('offers only friendly when the active player has no tournament match', async () => {
  const {api,root,form} = fixture({matches:[]});
  api.setup();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(root.querySelector('[data-kind="tournament"]').hidden,true);
  assert.equal(form.elements.kind.value,'friendly');
});

test('disables every selected team member and enables them again on removal', () => {
  const {api,root,form} = fixture({offline:true});
  api.setup();
  form.elements.format.value = 'team';
  form.elements.format.onchange();
  for (const value of ['a','b']) {
    form.change({target:{value,dataset:{profile:'0'},matches:selector => selector === '[data-profile]'}});
  }
  const sides = root.querySelector('.life-setup-sides');
  for (const value of ['a','b']) {
    assert.equal(sides.innerHTML.split(`value="${value}" disabled`).length - 1, 2);
  }
  root.onclick({target:{closest:() => ({
    dataset:{removeMember:'0,1'}, hasAttribute:name => name === 'data-remove-member',
  })}});
  assert.doesNotMatch(sides.innerHTML,/value="b" disabled/);
});

test('selects the first cached match when offline',() => {
  const {api,form,submit,calls} = fixture({offline:true,cached:true});
  api.setup();
  form.elements.kind.value = 'tournament';
  form.elements.kind.onchange();
  assert.equal(form.elements.match.value,'uno');
  assert.equal(submit.disabled,false);
  assert.deepEqual(calls,[]);
});

test('offers another sync attempt only while the queue has games',async () => {
  const {api,root} = fixture();
  const retry = root.querySelector('[data-queue-retry]');
  api.refreshQueueStatus();
  assert.equal(retry.hidden,true);
  assert.equal(root.querySelector('[data-queue-status]').textContent,'Tutte le partite sono sincronizzate.');
  api.enqueue({session_id:'uno',players:[]});
  api.refreshQueueStatus();
  assert.equal(retry.hidden,false);
  await api.drainOutbox();
  assert.equal(retry.hidden,true);
});
