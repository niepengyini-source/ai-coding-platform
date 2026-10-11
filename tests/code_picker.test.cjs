const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/code_picker.js'), 'utf8');

function element(values={}) {
  return {hidden:false,disabled:false,value:'',checked:false,textContent:'',dataset:{},handlers:{},attributes:{},
    addEventListener(name,fn){this.handlers[name]=fn;},
    dispatchEvent(event){this.handlers[event.type]?.(event);},
    setAttribute(name,value){this.attributes[name]=value;},...values};
}
function setup(locked=false) {
  const primary=element({value:'1',disabled:locked}), noCode=element(), search=element({disabled:locked});
  const summary=element(), empty=element(), clear=element({disabled:locked}), fallback=element();
  const choices=[element({dataset:{codeChoice:'1',label:'PLAN · 计划'},disabled:locked}),element({dataset:{codeChoice:'2',label:'CHECK · 检查'},disabled:locked})];
  const cards=[element({textContent:'PLAN 计划 确定目标'}),element({textContent:'CHECK 检查 核对进度'})];
  const secondary=[element({checked:true})], secondaryPanel=element();
  let changes=0;primary.addEventListener('change',()=>changes++);
  // Count bubbling changes independently from the picker's own synchronization listener.
  const dispatch=primary.dispatchEvent;primary.dispatchEvent=function(e){changes++;dispatch.call(this,e);};
  const form={querySelector:sel=>({'[name=primary]':primary,'[name=no_code]':noCode,'[data-primary-fallback]':fallback,'[data-secondary-options]':secondaryPanel}[sel]),querySelectorAll:()=>secondary};
  const picker={hidden:true,closest:()=>form,querySelector:sel=>({'[name=code_search]':search,'[data-code-summary]':summary,'[data-code-empty]':empty,'[data-code-clear]':clear}[sel]),querySelectorAll:sel=>sel==='[data-code-choice]'?choices:cards};
  vm.runInNewContext(source,{document:{querySelector:()=>picker},Event:class{constructor(type){this.type=type;}}});
  return {primary,noCode,search,summary,empty,clear,fallback,choices,cards,secondary,secondaryPanel,picker,changes:()=>changes};
}
test('initial chosen code stays visible with existing secondary options expanded',()=>{
  const ui=setup();assert.equal(ui.choices[0].attributes['aria-pressed'],'true');
  assert.equal(ui.summary.textContent,'已选：PLAN · 计划');assert.equal(ui.secondaryPanel.open,true);
  assert.equal(ui.fallback.hidden,true);assert.equal(ui.picker.hidden,false);
});
test('selecting a code changes the real value without submitting, and clears no-code',()=>{
  const ui=setup();ui.noCode.checked=true;ui.choices[1].handlers.click();
  assert.equal(ui.primary.value,'2');assert.equal(ui.noCode.checked,false);assert.equal(ui.changes(),1);
  assert.equal(ui.choices[1].attributes['aria-pressed'],'true');
  ui.clear.handlers.click();assert.equal(ui.primary.value,'');
});
test('search filters definitions without altering the annotation',()=>{
  const ui=setup();ui.search.value='核对';ui.search.handlers.input();
  assert.equal(ui.cards[0].hidden,true);assert.equal(ui.cards[1].hidden,false);assert.equal(ui.changes(),0);
  ui.search.value='不存在';ui.search.handlers.input();assert.equal(ui.empty.hidden,false);
});
test('read-only choices cannot change while rules remain searchable',()=>{
  const ui=setup(true);ui.choices[1].handlers.click();ui.clear.handlers.click();
  assert.equal(ui.primary.value,'1');assert.equal(ui.changes(),0);assert.equal(ui.search.disabled,false);
});
test('no-code clears primary and secondary, without silently changing uncertain',()=>{
  const ui=setup();ui.noCode.checked=true;ui.noCode.handlers.change();
  assert.equal(ui.primary.value,'');assert.equal(ui.secondary[0].checked,false);
});
