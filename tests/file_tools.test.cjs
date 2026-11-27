const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/file_tools.js'), 'utf8');

function fixture(hasForm=true) {
  const inputs=[{checked:true},{checked:false}], handlers={};
  const buttons=['all','none'].map(mode=>({dataset:{exportSelect:mode},addEventListener:(event,fn)=>handlers[mode]=fn}));
  const form={querySelectorAll:selector=>selector==='[data-export-select]'?buttons:inputs};
  vm.runInNewContext(source,{document:{getElementById:id=>id==='export-options' && hasForm?form:null}});
  return {inputs,handlers};
}
test('all selects every export field without submitting a form',()=>{
  const ui=fixture();ui.handlers.all();assert.ok(ui.inputs.every(input=>input.checked));
});
test('none clears all export fields and does not affect data',()=>{
  const ui=fixture();ui.handlers.none();assert.ok(ui.inputs.every(input=>!input.checked));
});
test('ordinary pages without an export form do not gain click handlers',()=>{
  assert.doesNotThrow(()=>fixture(false));
});

test('export content switch only submits relevant options and retains previous selections',()=>{
  let change;
  const target={value:'codebook',addEventListener:(event,fn)=>{change=fn;}};
  const sections=Object.fromEntries(['book','round','history'].map(key=>{
    const input={disabled:false,value:key==='round'?'17':'yes'};
    return [`[data-export-${key}]`,{hidden:false,input,querySelectorAll:()=>[input]}];
  }));
  const form={querySelector:selector=>selector==='[name="target"]'?target:sections[selector]};
  vm.runInNewContext(source,{document:{getElementById:id=>id==='export-source'?form:null}});
  const visible=()=>Object.entries(sections).filter(([,section])=>!section.hidden).map(([key])=>key);
  assert.deepEqual(visible(),['[data-export-book]']);
  assert.equal(sections['[data-export-round]'].input.disabled,true);
  target.value='mine';change();
  assert.deepEqual(visible(),['[data-export-round]']);
  assert.equal(sections['[data-export-round]'].input.disabled,false);
  assert.equal(sections['[data-export-round]'].input.value,'17');
  target.value='units';change();
  assert.deepEqual(visible(),['[data-export-history]']);
  target.value='codebook';change();
  assert.equal(sections['[data-export-history]'].input.disabled,true);
});

test('changing export target marks existing preview stale and disables download until reloaded',()=>{
  let onTargetChange;
  const target={name:'target',type:'select-one',value:'mine',disabled:false,addEventListener:(_,fn)=>{onTargetChange=fn;}};
  const book={name:'book',type:'select-one',value:'4',disabled:false};
  const round={name:'round',type:'select-one',value:'9',disabled:false};
  const history={name:'include_inactive',type:'checkbox',checked:false,disabled:false};
  const sections={
    '[data-export-book]':{hidden:false,querySelectorAll:()=>[book]},
    '[data-export-round]':{hidden:false,querySelectorAll:()=>[round]},
    '[data-export-history]':{hidden:false,querySelectorAll:()=>[history]},
  };
  const reloadBtn={classList:{secondary:true,toggle(cls,val){this[cls]=val;}}};
  const staleHint={hidden:true};
  const downloadBtn={disabled:false};
  const sourceForm={
    querySelector:sel=>sel==='[name="target"]'?target:sel==='button[type="submit"]'?reloadBtn:sections[sel],
    querySelectorAll:()=>[target,book,round,history],
  };
  const optionsForm={
    querySelector:sel=>sel==='[data-export-stale-hint]'?staleHint:sel==='button[name="action"][value="download"]'?downloadBtn:null,
    querySelectorAll:()=>[],
  };
  vm.runInNewContext(source,{document:{getElementById:id=>id==='export-source'?sourceForm:id==='export-options'?optionsForm:null}});
  assert.equal(downloadBtn.disabled,false);
  assert.equal(staleHint.hidden,true);
  target.value='codebook';onTargetChange();
  assert.equal(downloadBtn.disabled,true);
  assert.equal(staleHint.hidden,false);
  assert.equal(reloadBtn.classList.secondary,false);
  target.value='mine';onTargetChange();
  assert.equal(downloadBtn.disabled,false);
  assert.equal(staleHint.hidden,true);
});
