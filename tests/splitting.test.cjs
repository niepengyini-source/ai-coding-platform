const {test} = require('node:test');
const assert = require('node:assert/strict');
const {splitPreview} = require('../static/splitting.js');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../static/splitting.js'),'utf8');

test('preview uses Unicode character positions rather than UTF-16 offsets',()=>{
  assert.deepEqual(splitPreview('先😊讨论，后检查',2),['先😊','讨论，后检查']);
});
test('preview preserves every original character including whitespace and newlines',()=>{
  const text=' 第一句。\n 第二句。 ';
  const parts=splitPreview(text,6);assert.equal(parts.join(''),text);
});
test('preview rejects endpoints, blank fragments and non-integer positions',()=>{
  for(const offset of [0,4,-1,'2',1.5,NaN]) assert.throws(()=>splitPreview('先后检查',offset));
  assert.throws(()=>splitPreview('  检查',1));
});

function setup(fetch, {revision=0,unsaved=false,personal=true}={}) {
  const el=values=>({disabled:false,hidden:false,value:'',textContent:'',handlers:{},addEventListener(type,fn){this.handlers[type]=fn;},...values});
  const previewButton=el(), confirmButton=el({disabled:true}), preview=el({hidden:true}), status=el();
  const reason=personal?null:el({value:'按两个行为分开'}), first=el(), second=el();
  const controls=[el(),el({value:'test-csrf'})];
  const form={dataset:{revision:String(revision)},querySelectorAll:()=>controls,querySelector:()=>controls[1]};
  const panel={dataset:{mode:personal?'personal':'suggestion',endpoint:'/api/assignments/1/split/'},
    querySelector:sel=>({'[data-split-preview]':previewButton,'[data-split-confirm]':confirmButton,'[data-split-parts]':preview,'[name=split_reason]':reason,'[data-split-status]':status,'[data-split-first]':first,'[data-split-second]':second}[sel])};
  const events={}, destinations=[];
  class Event {constructor(type){this.type=type;this.defaultPrevented=false;}preventDefault(){this.defaultPrevented=true;}}
  const window={addEventListener(type,fn){(events[type] ||= []).push(fn);},dispatchEvent(event){if(unsaved && event.type==='platform:before-back')event.preventDefault();(events[event.type]||[]).forEach(fn=>fn(event));return !event.defaultPrevented;},getSelection:()=>({rangeCount:1,isCollapsed:false,getRangeAt:()=>({startContainer:{},endContainer:{},endOffset:3})})};
  const text={textContent:'先😊讨论，再检查',contains:()=>true};
  vm.runInNewContext(source,{document:{querySelector:sel=>sel==='[data-split-panel]'?panel:form,getElementById:()=>text,
    createRange:()=>({selectNodeContents(){},setEnd(){},toString:()=> '先😊'})},window,Event,fetch,Math,
    location:{assign:url=>{assert.equal(window.dispatchEvent(new Event('platform:before-back')),true);destinations.push(url);}}});
  return {previewButton,confirmButton,preview,status,reason,first,second,controls,destinations,window,Event};
}
const reply=(status=200,data={ok:true,next_url:'/r/1/?a=2'})=>({ok:status===200,status,headers:{get:()=> 'application/json'},json:async()=>data});
test('selection preview and confirmation preserve text, disable edits in flight and advance after success',async()=>{
  let finish;const ui=setup(()=>new Promise(resolve=>finish=resolve));
  ui.previewButton.handlers.click();assert.equal(ui.first.textContent,'先😊');assert.equal(ui.second.textContent,'讨论，再检查');
  const request=ui.confirmButton.handlers.click();assert.equal(ui.controls[0].disabled,true);assert.equal(ui.destinations.length,0);
  finish(reply());await request;assert.deepEqual(ui.destinations,['/r/1/?a=2']);
});
test('failed split retries identical payload without leaving or permitting new edits',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));if(sent.length===1)throw new Error('offline');return reply();});
  ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();
  assert.equal(ui.destinations.length,0);assert.equal(ui.controls[0].disabled,true);assert.equal(ui.confirmButton.disabled,false);
  await ui.confirmButton.handlers.click();assert.deepEqual(sent[0],sent[1]);assert.match(sent[0].token,/^[0-9a-f-]{36}$/);
  assert.equal(ui.destinations.length,1);
});
test('saved or unsaved personal edits are not split, while a suggestion never navigates',async()=>{
  for(const options of [{revision:1},{unsaved:true}]) {
    const ui=setup(()=>{throw new Error('must not fetch');},options);ui.previewButton.handlers.click();
    assert.equal(ui.confirmButton.disabled,true);assert.equal(ui.preview.hidden,true);
  }
  const ui=setup(async()=>reply(200,{ok:true,message:'建议已发送'}),{personal:false,revision:3});
  ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();
  assert.equal(ui.status.textContent,'建议已发送');assert.equal(ui.destinations.length,0);assert.equal(ui.controls[0].disabled,false);
});
test('split conflict retains original text and never overwrites or advances',async()=>{
  const ui=setup(async()=>reply(409,{error:'其他页面已保存'}));ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();
  assert.equal(ui.status.textContent,'其他页面已保存');assert.equal(ui.first.textContent,'先😊');
  assert.equal(ui.confirmButton.disabled,true);assert.equal(ui.destinations.length,0);
});
