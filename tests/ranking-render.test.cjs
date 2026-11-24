const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const test = require('node:test');

test('ranking and avatar retain their DOM when the server returns the same data', () => {
  const writes = new Map();
  const elements = new Map();
  const source = fs.readFileSync('app/static/app.js','utf8').replace('  init();','  window.renderRankingForTest = renderRanking;');
  const window = {addEventListener(){}};
  const document = {
    addEventListener(){},
    documentElement:{classList:{toggle(){}}},
    querySelector(selector) {
      if (!elements.has(selector)) elements.set(selector,{
        setAttribute(){},addEventListener(){},
        set innerHTML(markup){writes.set(selector,(writes.get(selector) || 0) + 1);this.markup = markup;},
      });
      return elements.get(selector);
    },
  };
  vm.runInNewContext(source,{window,document,localStorage:{getItem:() => 'player'}});
  const player = {id:'player',name:'Ada',photo_url:'/photos/abc.jpg',rank:1,rating:1500,matches:0,wins:0,losses:0};
  window.renderRankingForTest([player]);
  window.renderRankingForTest([{...player}]);
  assert.equal(writes.get('#ranking-list'),1);
  assert.equal(writes.get('#profile-open'),1);
  assert.match(elements.get('#ranking-list').markup,/ranking-name[^>]*>Ada/);
  window.renderRankingForTest([{...player,rating:1515}]);
  assert.equal(writes.get('#ranking-list'),2);
  assert.equal(writes.get('#profile-open'),1);
  assert.match(elements.get('#ranking-list').markup,/>1515</);
});
