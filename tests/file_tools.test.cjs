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
