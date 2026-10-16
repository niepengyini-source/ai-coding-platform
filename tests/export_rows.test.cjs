const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/export_rows.js'), 'utf8');
const searchNames = ['row_query', 'row_match', 'row_metadata_field', 'row_metadata_value', 'row_status'];

function input(value='') {
  return {value, checked:false, events:{}, addEventListener(name, callback) { this.events[name]=callback; }};
}
function setup({total=35, retained='', visible='1,2', checked=[], mode='all', missing=false}={}) {
  const fields = Object.fromEntries(searchNames.map(name=>[name,input()]));
  Object.assign(fields,{selection:input(mode),limit:input(),retained_selected:input(retained),visible_ids:input(visible)});
  const boxes = visible.split(',').filter(Boolean).map(id=>Object.assign(input(id),{checked:checked.includes(id)}));
  const buttons = ['all','none','clear'].map(mode=>Object.assign(input(),{dataset:{exportRowSelect:mode}}));
  const summary={},panel={},download={};
  const form={dataset:{rowCount:String(total)},querySelector(selector) {
    if(selector==='[data-export-row-summary]') return summary;
    if(selector==='[data-export-row-limit]') return panel;
    if(selector==='[value="download"]') return download;
    const name=selector.match(/name="([^"]+)"/)?.[1];
    return missing && name==='selection' ? null : fields[name];
  },querySelectorAll(selector) {return selector==='[name="selected"]'?boxes:buttons;}};
  vm.runInNewContext(source,{document:{querySelectorAll:()=>[form]}});
  return {fields,boxes,buttons,summary,panel,download,
    choose(value){fields.selection.value=value;fields.selection.events.change();},
    quantity(value){fields.limit.value=String(value);fields.limit.events.input();},
    check(index,checked){boxes[index].checked=checked;boxes[index].events.change();},
    click(mode){buttons.find(button=>button.dataset.exportRowSelect===mode).events.click();}};
}

test('default export includes all matches across pages and hides N',()=>{
  const ui=setup(); assert.equal(ui.download.disabled,false); assert.equal(ui.fields.limit.disabled,true);
  assert.equal(ui.panel.hidden,true);assert.match(ui.summary.textContent,/全部 35 条/);
});
test('checking rows chooses selected mode and empty selection cannot download',()=>{
  const ui=setup();ui.check(0,true);assert.equal(ui.fields.selection.value,'selected');
  assert.match(ui.summary.textContent,/1 条/);assert.equal(ui.download.disabled,false);
  ui.check(0,false);assert.equal(ui.download.disabled,true);
});
test('selected count combines other pages and current checkboxes without duplicates',()=>{
  const ui=setup({retained:'1,8,9',checked:['1'],mode:'selected'});
  assert.match(ui.summary.textContent,/3 条/);ui.check(1,true);assert.match(ui.summary.textContent,/4 条/);
  ui.check(0,false);assert.match(ui.summary.textContent,/3 条/);
  assert.equal(ui.fields.retained_selected.value,'1,8,9');
});
test('unchecking a retained visible row removes it from the effective count',()=>{
  const ui=setup({retained:'1,8',checked:[],mode:'selected'});assert.match(ui.summary.textContent,/1 条/);
  assert.equal(ui.download.disabled,false);
});
test('page-level select and clear leave selections on other pages alone',()=>{
  const ui=setup({retained:'8',mode:'selected'});ui.click('all');assert.ok(ui.boxes.every(x=>x.checked));
  assert.match(ui.summary.textContent,/3 条/);ui.click('none');assert.ok(ui.boxes.every(x=>!x.checked));
  assert.match(ui.summary.textContent,/1 条/);assert.equal(ui.fields.retained_selected.value,'8');
});
test('clear-all clears prior pages as well and never falls back to all',()=>{
  const ui=setup({retained:'1,8',checked:['1'],mode:'selected'});ui.click('clear');
  assert.equal(ui.fields.retained_selected.value,'');assert.equal(ui.download.disabled,true);
  assert.equal(ui.fields.selection.value,'selected');assert.match(ui.summary.textContent,/0 条/);
});
test('first N validates quantity and reports min of request and available rows',()=>{
  const ui=setup();ui.choose('first');assert.equal(ui.panel.hidden,false);assert.equal(ui.fields.limit.required,true);
  for(const n of ['',0,-1,1.5,'abc',50001]){ui.quantity(n);assert.equal(ui.download.disabled,true,String(n));}
  ui.quantity(7);assert.equal(ui.download.disabled,false);assert.match(ui.summary.textContent,/前 7 条/);
  ui.quantity(50);assert.match(ui.summary.textContent,/前 35 条/);
  ui.choose('all');assert.equal(ui.fields.limit.required,false);assert.equal(ui.fields.limit.disabled,true);
});
test('empty results allow a field template, not first N or unchecked selection',()=>{
  const ui=setup({total:0,visible:''});assert.equal(ui.download.disabled,false);assert.match(ui.summary.textContent,/空表/);
  ui.choose('first');ui.quantity(1);assert.equal(ui.download.disabled,true);
  ui.choose('selected');assert.equal(ui.download.disabled,true);
});
test('every search control invalidates download until a fresh preview',()=>{
  for(const name of searchNames){const ui=setup();ui.fields[name].events.input();assert.equal(ui.download.disabled,true);
    ui.choose('all');ui.click('all');ui.quantity(2);assert.equal(ui.download.disabled,true);
    assert.match(ui.summary.textContent,/筛选并预览/);}
});
test('unrelated pages and incomplete forms remain unaffected',()=>{
  assert.doesNotThrow(()=>vm.runInNewContext(source,{document:{querySelectorAll:()=>[]}}));
  assert.doesNotThrow(()=>setup({missing:true}));
});
test('filter and page buttons bypass unrelated export validation',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../templates/coding/exports.html'),'utf8');
  const tags=html.match(/<button\b[^>]*>/g).filter(tag=>/value="filter"|name="row_page"/.test(tag));
  assert.equal(tags.length,4);assert.ok(tags.every(tag=>/formnovalidate/.test(tag)));
  assert.match(html,/name="retained_selected"/);assert.match(html,/action="#export-options"/);
});
