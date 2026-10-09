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
  vm.runInNewContext(source,{document:{getElementById:()=>hasForm?form:null}});
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
