// Logical navigation tests use a fake DOM/storage, never automate a browser.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/navigation.js'), 'utf8');

function setup(current, fallback, {user='7', store=new Map(), storageError=false, blocked=false, traversal=false, noButton=false}={}) {
  const listeners={}, windowListeners={}, attributes={href:fallback}, moves=[];
  const button={textContent:'← 返回上一页',getAttribute:k=>attributes[k],setAttribute:(k,v)=>attributes[k]=v,
    addEventListener:(name,fn)=>listeners[name]=fn};
  const hint={textContent:''};
  const url=new URL(current,'http://localhost');
  const context={URL,Event,document:{body:{dataset:{navigationUser:user}},getElementById:id=>id==='page-back'?(noButton?null:button):hint},
    location:{origin:url.origin,pathname:url.pathname,search:url.search,assign:value=>moves.push(value)},
    performance:{getEntriesByType:()=>[{type:traversal?'back_forward':'navigate'}]},
    sessionStorage:{getItem:key=>{if(storageError)throw new Error('unavailable');return store.get(key)||null;},
      setItem:(key,value)=>{if(storageError)throw new Error('unavailable');store.set(key,value);}},
    window:{addEventListener:(name,fn)=>windowListeners[name]=fn,dispatchEvent:event=>{if(blocked)event.preventDefault();return !event.defaultPrevented;}}};
  vm.runInNewContext(source,context);
  return {button,hint,attributes,moves,store,click:()=>listeners.click({preventDefault(){}}),
    restore:()=>windowListeners.pageshow({persisted:true})};
}

test('direct project page safely returns to its project instead of an external referrer',()=>{
  const ui=setup('/p/2/units/','/p/2/');ui.click();assert.deepEqual(ui.moves,['/p/2/']);
});
test('start page has an explained disabled return, not a misleading self-refresh',()=>{
  const ui=setup('/','/');assert.equal(ui.attributes['aria-disabled'],'true');ui.click();assert.deepEqual(ui.moves,[]);
});
test('several return clicks walk the visited paths and preserve queries',()=>{
  const store=new Map();setup('/','/',{store});setup('/p/2/','/',{store});
  setup('/p/2/units/?page=2&q=访谈','/p/2/',{store});
  let ui=setup('/p/2/books/4/','/p/2/',{store});ui.click();assert.equal(decodeURI(ui.moves[0]),'/p/2/units/?page=2&q=访谈');
  ui=setup(ui.moves[0],'/p/2/',{store});ui.click();assert.equal(ui.moves[0],'/p/2/');
  ui=setup('/p/2/','/',{store});ui.click();assert.equal(ui.moves[0],'/');
});
test('post-redirect to the same page does not create a return loop or replay a form',()=>{
  const store=new Map();setup('/p/2/','/',{store});setup('/p/2/members/','/p/2/',{store});
  const ui=setup('/p/2/members/','/p/2/',{store});ui.click();assert.equal(ui.moves[0],'/p/2/');
});
test('an intentional normal A-B-A navigation can return to B',()=>{
  const store=new Map();setup('/p/2/','/',{store});setup('/p/2/units/','/p/2/',{store});
  const ui=setup('/p/2/','/',{store});ui.click();assert.equal(ui.moves[0],'/p/2/units/');
});
test('storage containing external URLs, API endpoints and downloads is ignored',()=>{
  const store=new Map([['ai-coding-platform:navigation:v1:7',JSON.stringify(['https://evil.invalid/','//evil.invalid/',
    '/\\evil.invalid/','/api/assignments/2/save/','/r/2/export/all/','/p/2/invitation/','javascript:alert(1)'])]]);
  const ui=setup('/p/2/units/','/p/2/',{store});ui.click();assert.equal(ui.moves[0],'/p/2/');
});
test('unavailable storage still gives a working safe return',()=>{
  const ui=setup('/r/4/review/','/r/4/',{storageError:true});ui.click();assert.equal(ui.moves[0],'/r/4/');
});
test('navigation histories are isolated by login identity',()=>{
  const store=new Map();setup('/p/2/','/',{store,user:'7'});setup('/p/2/units/','/p/2/',{store,user:'7'});
  const ui=setup('/accounts/register/','/login/',{store,user:'anonymous'});ui.click();assert.equal(ui.moves[0],'/login/');
});
test('coding save guard blocks navigation and retains the path stack',()=>{
  const store=new Map();setup('/p/2/','/',{store});const ui=setup('/r/4/','/p/2/',{store,blocked:true});
  const before=store.get('ai-coding-platform:navigation:v1:7');ui.click();
  assert.deepEqual(ui.moves,[]);assert.match(ui.hint.textContent,/未确认保存/);
  assert.equal(store.get('ai-coding-platform:navigation:v1:7'),before);
});
test('native Back and bfcache restoration align with the latest per-tab history',()=>{
  const store=new Map();setup('/p/2/','/',{store});const middle=setup('/p/2/units/','/p/2/',{store});
  setup('/p/2/books/4/','/p/2/',{store});middle.restore();middle.click();assert.equal(middle.moves[0],'/p/2/');
});
test('completed password change returns to the previous workspace, not password fields',()=>{
  const store=new Map();setup('/p/2/','/',{store});setup('/accounts/password/','/',{store});
  const ui=setup('/accounts/password/done/','/',{store});ui.click();assert.equal(ui.moves[0],'/p/2/');
});
test('new export and private import-preview pages keep logical history, not downloads',()=>{
  const store=new Map();setup('/p/2/books/4/','/p/2/',{store});
  setup('/p/2/books/4/import/?draft=demo&stage=preview','/p/2/',{store});
  const ui=setup('/p/2/exports/?target=codebook&book=4','/p/2/',{store});ui.click();
  assert.equal(ui.moves[0],'/p/2/books/4/import/?draft=demo&stage=preview');
});
test('an imported codebook returns to the earlier codebook instead of replaying a completed wizard',()=>{
  const store=new Map();setup('/p/2/','/',{store});setup('/p/2/books/4/','/p/2/',{store});
  setup('/p/2/books/4/import/?draft=demo','/p/2/',{store});
  setup('/p/2/books/4/import/?draft=demo&stage=preview','/p/2/',{store});
  const ui=setup('/p/2/books/5/','/p/2/',{store});ui.click();assert.equal(ui.moves[0],'/p/2/books/4/');
});

test('quick start progress and searched history keep their return paths',()=>{
  const store=new Map();setup('/p/2/','/',{store});
  setup('/p/2/quick-start/','/p/2/',{store});
  setup('/p/2/progress/?page=2','/p/2/',{store});
  setup('/p/2/history/?mode=exact&unit_id=31&period=week','/p/2/',{store});
  const ui=setup('/p/2/history/77/','/p/2/',{store});ui.click();
  assert.equal(ui.moves[0],'/p/2/history/?mode=exact&unit_id=31&period=week');
});

test('history reopen and personal finish posts never become return targets',()=>{
  const store=new Map([['ai-coding-platform:navigation:v1:7',JSON.stringify([
    '/p/2/history/77/reopen/','/p/2/quick/5/finish/','/api/projects/2/progress/'])]]);
  const ui=setup('/p/2/progress/','/p/2/',{store});ui.click();
  assert.equal(ui.moves[0],'/p/2/');
});

test('the module chooser is recorded even without a return button',()=>{
  const store=new Map();setup('/','/',{store,noButton:true});
  const ui=setup('/groups/join/','/projects/',{store});ui.click();
  assert.equal(ui.moves[0],'/');
});

test('the project list is recorded without adding a return button to its layout',()=>{
  const store=new Map();setup('/','/',{store,noButton:true});
  setup('/projects/','/',{store,noButton:true});
  const ui=setup('/groups/join/','/',{store});ui.click();
  assert.equal(ui.moves[0],'/projects/');
});

test('the project-list return button goes back to the module chooser',()=>{
  const store=new Map();setup('/','/',{store,noButton:true});
  const ui=setup('/projects/','/',{store});ui.click();
  assert.equal(ui.attributes.href,'/');assert.deepEqual(ui.moves,['/']);
});

test('the project-list return button preserves the actual previous project',()=>{
  const store=new Map();setup('/p/2/','/projects/',{store});
  const ui=setup('/projects/','/',{store});ui.click();
  assert.equal(ui.attributes.href,'/p/2/');assert.deepEqual(ui.moves,['/p/2/']);
});

test('a directly opened project list safely falls back to the module chooser',()=>{
  const ui=setup('/projects/','/');ui.click();
  assert.notEqual(ui.attributes['aria-disabled'],'true');assert.deepEqual(ui.moves,['/']);
});

test('the chooser resets old history when a new login reaches home',()=>{
  const store=new Map();setup('/p/2/','/projects/',{store});
  setup('/p/2/history/','/p/2/',{store});setup('/','/',{store,noButton:true});
  const ui=setup('/groups/join/','/',{store});ui.click();assert.equal(ui.moves[0],'/');
});

test('POST-only material changes are never a return destination',()=>{
  const store=new Map();setup('/p/2/units/?q=hello&page=3','/p/2/',{store});
  setup('/p/2/units/8/','/p/2/units/',{store});
  const ui=setup('/guide/','/',{store});ui.click();
  assert.equal(ui.moves[0],'/p/2/units/?q=hello&page=3');
});

test('native history can skip several documents rather than only one',()=>{
  const store=new Map();setup('/p/2/','/projects/',{store});
  const first=setup('/p/2/units/?page=2','/p/2/',{store});
  setup('/p/2/books/4/','/p/2/',{store});setup('/p/2/exports/','/p/2/',{store});
  first.restore();first.click();assert.equal(first.moves[0],'/p/2/');
});

test('unknown and missing pages retain the most recent valid return target',()=>{
  const store=new Map();setup('/p/2/','/projects/',{store});
  setup('/p/2/units/?page=2','/p/2/',{store});
  const ui=setup('/missing/','/',{store});ui.click();
  assert.equal(ui.moves[0],'/p/2/units/?page=2');
  const list=setup(ui.moves[0],'/p/2/',{store});list.click();assert.equal(list.moves[0],'/p/2/');
});

test('authentication pages are never stored as a previous working page',()=>{
  const store=new Map();setup('/login/','/login/',{store,noButton:true});
  setup('/accounts/register/','/login/',{store,noButton:true});
  const ui=setup('/groups/join/','/',{store});ui.click();assert.equal(ui.moves[0],'/');
});

test('refreshing join after a form error keeps the original chooser as its previous page',()=>{
  const store=new Map();setup('/','/',{store,noButton:true});setup('/groups/join/','/',{store});
  const ui=setup('/groups/join/','/',{store});ui.click();assert.equal(ui.moves[0],'/');
});

test('native Forward after Back rebuilds a safe working trail',()=>{
  const store=new Map();setup('/p/2/','/projects/',{store});
  const list=setup('/p/2/units/?page=3','/p/2/',{store});
  const book=setup('/p/2/books/4/','/p/2/',{store});
  list.restore();book.restore();book.click();
  assert.equal(book.moves[0],'/p/2/units/?page=3');
});

test('cached chooser restoration also starts a clean trail without a return button',()=>{
  const store=new Map();const home=setup('/','/',{store,noButton:true});
  setup('/projects/','/',{store,noButton:true});setup('/p/2/','/projects/',{store});
  home.restore();const ui=setup('/groups/join/','/',{store});ui.click();assert.equal(ui.moves[0],'/');
});

test('cached project-list restoration records the chooser-to-project path without a button',()=>{
  const store=new Map();setup('/','/',{store,noButton:true});
  const projects=setup('/projects/','/',{store,noButton:true});setup('/p/2/','/projects/',{store});
  projects.restore();const ui=setup('/groups/join/','/',{store});ui.click();assert.equal(ui.moves[0],'/projects/');
});

test('join still returns to the chooser when browser storage is unavailable',()=>{
  const ui=setup('/groups/join/','/',{storageError:true});ui.click();assert.equal(ui.moves[0],'/');
});

test('cancelled import to the same book never reopens the old import wizard',()=>{
  const store=new Map();setup('/p/2/','/projects/',{store});setup('/p/2/books/4/','/p/2/',{store});
  setup('/p/2/books/4/import/?draft=demo&stage=preview','/p/2/',{store});
  const ui=setup('/p/2/books/4/','/p/2/',{store});ui.click();assert.equal(ui.moves[0],'/p/2/');
});
