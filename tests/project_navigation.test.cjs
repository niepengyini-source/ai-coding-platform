const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/project_navigation.js'), 'utf8');

function setup({user='7', project='2', store=new Map(), unavailable=false, absent=false, incomplete=false}={}) {
  const attributes = {'aria-controls':'project-navigation','aria-expanded':'false'};
  const handlers = {}, windowHandlers = {};
  const panel = {hidden:true};
  const label = {textContent:'展开项目功能'};
  const toggle = {getAttribute:key=>attributes[key], setAttribute:(key,value)=>attributes[key]=value,
    querySelector:()=>label,
    addEventListener:(name,handler)=>handlers[name]=handler};
  const group = {dataset:{projectId:project},querySelector:()=>toggle};
  vm.runInNewContext(source, {
    document:{body:{dataset:{navigationUser:user}},querySelector:()=>absent?null:group,
      getElementById:id=>id==='project-navigation'&&!incomplete?panel:null},
    sessionStorage:{getItem:key=>{if(unavailable)throw Error('blocked');return store.get(key)??null;},
      setItem:(key,value)=>{if(unavailable)throw Error('blocked');store.set(key,value);}},
    window:{addEventListener:(name,handler)=>windowHandlers[name]=handler},
  });
  return {panel, label, attributes, store, click:()=>handlers.click(), restore:()=>windowHandlers.pageshow({persisted:true})};
}

test('project functions start collapsed without changing the overview link',()=>{
  const ui=setup();
  assert.equal(ui.panel.hidden,true);
  assert.equal(ui.attributes['aria-expanded'],'false');
  assert.equal(ui.attributes['aria-label'],'展开项目功能');
  assert.equal(ui.label.textContent,'展开项目功能');
});
test('one click opens and a second closes with matching accessible state',()=>{
  const ui=setup();ui.click();
  assert.equal(ui.panel.hidden,false);
  assert.equal(ui.attributes['aria-expanded'],'true');
  assert.equal(ui.attributes['aria-label'],'收起项目功能');
  assert.equal(ui.label.textContent,'收起项目功能');
  ui.click();assert.equal(ui.panel.hidden,true);
  assert.equal(ui.attributes['aria-label'],'展开项目功能');
  assert.equal(ui.label.textContent,'展开项目功能');
});
test('a chosen open state follows navigation within the same project',()=>{
  const store=new Map();setup({store}).click();
  assert.equal(setup({store}).panel.hidden,false);
});
test('closing remains closed after entering another project page',()=>{
  const store=new Map();const first=setup({store});first.click();first.click();
  assert.equal(setup({store}).panel.hidden,true);
});
test('different users and projects do not share expanded state',()=>{
  const store=new Map();setup({store}).click();
  assert.equal(setup({store,user:'8'}).panel.hidden,true);
  assert.equal(setup({store,project:'3'}).panel.hidden,true);
});
test('unavailable storage still permits toggling on this page',()=>{
  const ui=setup({unavailable:true});ui.click();assert.equal(ui.panel.hidden,false);
  ui.click();assert.equal(ui.panel.hidden,true);
});
test('untrusted stored values are treated as closed',()=>{
  for(const value of ['true','{"open":true}','https://example.invalid/', '<script>']) {
    const store=new Map([['ai-coding-platform:project-menu:v1:7:2',value]]);
    assert.equal(setup({store}).panel.hidden,true);
  }
});
test('browser Back restores the latest chosen state rather than stale expanded UI',()=>{
  const store=new Map();const page=setup({store});page.click();
  setup({store}).click();page.restore();assert.equal(page.panel.hidden,true);
});
test('pages outside a project and incomplete menus do not break other scripts',()=>{
  assert.doesNotThrow(()=>setup({absent:true}));
  assert.doesNotThrow(()=>setup({incomplete:true}));
});
