const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

const source = fs.readFileSync('app/static/life.js','utf8').replace(
  '  window.openLifeCounter =', '  globalThis.setupTest = {setup, enqueue, drainOutbox, refreshQueueStatus};\n  window.openLifeCounter =',
);
const profiles = [{id:'a',name:'Ada'},{id:'b',name:'Bruno'},{id:'c',name:'Carla'}];
const match = (id,p2) => ({id,p1:'a',p2,tournament:{id,config:{name:'Torneo ' + id}}});

function fixture({offline = false, matches = [match('uno','b'),match('due','c')], cached = false} = {}) {
  const values = new Map([['mana-life-roster-v1',JSON.stringify(profiles)]]);
  const player = {player_id:'a',name:'Ada'};
  if (cached) values.set('mana-life-matches-v1',JSON.stringify({player,matches}));
  const nodes = new Map();
  const field = (value = '') => ({value,innerHTML:'',hidden:false,disabled:false,textContent:''});
  const submit = field();
  const form = {
    elements:{kind:field('friendly'),format:field('duel'),tournament_player:field(),match:field(),life_mode:field('same')},
    querySelector:() => submit, addEventListener(){},
  };
  const root = {
    addEventListener(){}, querySelectorAll:() => [],
    querySelector(selector) {
      if (selector === '#life-setup' || selector === 'form') return form;
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
  vm.runInNewContext(source,ctx);
  return {api:ctx.setupTest,root,form,submit,calls};
}

test('loads and selects the first tournament match with fixed players',async () => {
  const {api,root,form,submit,calls} = fixture();
  api.setup();
  form.elements.kind.value = 'tournament';
  form.elements.kind.onchange();
  assert.equal(submit.disabled,true);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(calls.filter(path => path === '/api/player/matches').length,1);
  assert.equal(form.elements.match.value,'uno');
  assert.equal(submit.disabled,false);
  const sides = root.querySelector('.life-setup-sides');
  assert.match(sides.innerHTML,/Ada.*Bruno/);
  assert.doesNotMatch(sides.innerHTML,/Aggiungi|data-remove-member|Nome ospite/);
  assert.equal(root.querySelector('[data-add-side]').hidden,true);
  form.elements.match.value = 'due';
  form.elements.match.onchange();
  assert.match(sides.innerHTML,/Ada.*Carla/);
  form.elements.kind.value = 'friendly';
  form.elements.kind.onchange();
  assert.match(sides.innerHTML,/data-add-profile/);
  assert.doesNotMatch(root.innerHTML,/I risultati con un vincitore|Carica abbinamenti/);
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
