// Progress polling tests use a fake DOM, never real browser credentials.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/workflow.js'), 'utf8');

function counters(attribute) {
  const values = Object.fromEntries(['total', 'submitted', 'saved', 'unstarted', 'percent'].map(key =>
    [key, {textContent:'old-' + key, getAttribute:()=>key}]));
  const bar = {value:0};
  return {values, bar,
    querySelectorAll: selector => selector === `[${attribute}]` ? Object.values(values) : [],
    querySelector:()=>bar};
}
function setup(fetch, {present=true, hidden=false}={}) {
  const my = counters('data-metric'), team = counters('data-metric');
  const round = counters('data-row-metric'), member = counters('data-row-metric');
  const status = {textContent:'initial'};
  const targets = {'[data-summary="my"]':my, '[data-summary="team"]':team,
    '[data-round-progress="4"]':round, '[data-member-progress="2"]':member};
  const panel = {dataset:{progressEndpoint:'/api/projects/1/progress/?page=2'},
    querySelector:selector=>targets[selector] || null};
  let tick;
  const document = {hidden, querySelector:()=>present ? panel : null, getElementById:()=>status};
  vm.runInNewContext(source, {document, fetch, Date, Math, Number, String, Array,
    setInterval: fn=>{tick=fn;}});
  return {my, team, round, member, status, tick, document};
}
const response = (data, status=200, type='application/json') => ({ok:status===200,
  headers:{get:()=>type}, json:async()=>data});
async function flush() {for(let i=0;i<12;i++) await Promise.resolve();}

test('progress polling updates summaries and current rows without answers', async()=>{
  let called;
  const ui = setup(async (url, options)=>{called={url,options};return response({
    my:{total:10, submitted:3, saved:2, unstarted:5, percent:30},
    team:{total:20,submitted:6,percent:30}, rows:[{round_id:4,total:10,submitted:3,percent:30}],
    members:[{coder_id:2,total:10,submitted:3,percent:30}]});});
  ui.tick();await flush();
  assert.equal(called.url,'/api/projects/1/progress/?page=2');
  assert.equal(called.options.credentials,'same-origin');
  assert.equal(ui.my.values.submitted.textContent,'3');
  assert.equal(ui.my.bar.value,30);
  assert.equal(ui.round.values.total.textContent,'10');
  assert.equal(ui.member.values.submitted.textContent,'3');
  assert.match(ui.status.textContent,/进度已更新/);
});

test('pages without progress do not install polling',()=>{
  const ui=setup(()=>{throw new Error('unexpected');},{present:false});
  assert.equal(ui.tick,undefined);
});

test('hidden pages do not request progress',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({});},{hidden:true});
  ui.tick();await flush();assert.equal(calls,0);
});

test('failed login or network polling preserves the previous numbers',async()=>{
  const ui=setup(async()=>response({},403,'text/html'));
  ui.tick();await flush();
  assert.equal(ui.my.values.total.textContent,'old-total');
  assert.match(ui.status.textContent,/暂未更新/);
});

test('overlapping timers do not create simultaneous progress requests',async()=>{
  let finish, calls=0;
  const ui=setup(()=>{calls++;return new Promise(resolve=>finish=resolve);});
  ui.tick();ui.tick();assert.equal(calls,1);
  finish(response({my:{total:0,percent:0}}));await flush();
  ui.tick();assert.equal(calls,2);
});

test('non-numeric values and untrusted row identifiers are not rendered',async()=>{
  const ui=setup(async()=>response({my:{total:'<script>bad</script>',submitted:-1,percent:200},
    rows:[{round_id:'4',submitted:99}], members:[{coder_id:'2',submitted:99}]}));
  ui.tick();await flush();
  assert.equal(ui.my.values.total.textContent,'old-total');
  assert.equal(ui.my.values.submitted.textContent,'old-submitted');
  assert.equal(ui.my.bar.value,100);
  assert.equal(ui.round.values.submitted.textContent,'old-submitted');
});
