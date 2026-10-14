const {test} = require('node:test');
const assert = require('node:assert/strict');
const {splitPreview,selectionPreview,cutsPreview} = require('../static/splitting.js');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../static/splitting.js'),'utf8');
const appSource = fs.readFileSync(path.join(__dirname,'../static/app.js'),'utf8');

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
test('a middle selection becomes three exact pieces, including emoji and punctuation',()=>{
  const text='先😊讨论，再检查';const result=selectionPreview(text,1,4);
  assert.deepEqual(result.parts,['先','😊讨论','，再检查']);assert.equal(result.parts.join(''),text);
});
test('selection keeps blank margins with the text and rejects all-text or blank selections',()=>{
  assert.deepEqual(selectionPreview('  先讨论。后检查  ',2,6).parts,['  先讨论。','后检查  ']);
  for(const span of [[0,4],[2,2],[-1,2],[false,2],[1,8]]) assert.throws(()=>selectionPreview('先后检查',...span));
  assert.throws(()=>selectionPreview('先  后',1,3));
});

function setup(fetch, {revision=0,unsaved=false,personal=true,saveFails=false,saveImplementation=null,start=0,end=3,withApp=false,sourceText=null,displayText='先😊讨论，再检查',hasSelection=true}={}) {
  const el=values=>({disabled:false,hidden:false,value:'',textContent:'',dataset:{},children:[],handlers:{},classList:{toggle(){}},
    addEventListener(type,fn){this.handlers[type]=fn;},append(...children){this.children.push(...children);},
    querySelector(tag){return this.children.find(child=>child.tagName===tag);},...values});
  const previewButton=el(), confirmButton=el({disabled:true}), preview=el({hidden:true}), status=el();
  const undoButton=el(),cancelButton=el(),modeControls=el({hidden:true});
  const reason=personal?null:el({value:'按两个行为分开'}), pieces=[];
  const list=el({replaceChildren(){pieces.length=0;},append(row){pieces.push(row);}});
  const fields=Object.fromEntries(['primary','note','no_code','uncertain','csrfmiddlewaretoken'].map(name=>[name,el({name,checked:false})]));
  fields.csrfmiddlewaretoken.value='test-csrf';
  const secondary=['2','3'].map(value=>el({name:'secondary',value,checked:false}));
  const buttons=[el(),el()], codeButton=el(), saveStatus=el(), formEvents={}, timers=new Map();
  const editable=[fields.primary,fields.note,fields.no_code,fields.uncertain,...secondary];
  const controls=[...editable,fields.csrfmiddlewaretoken,...buttons,codeButton];
  let flushes=0;
  const form={dataset:{revision:String(revision),endpoint:'/api/test-save/',mode:'annotation',locked:'false'},
    addEventListener:(type,handler)=>formEvents[type]=handler,
    querySelector:selector=>selector==='.save-status'?saveStatus:fields[selector.match(/name=([^\]]+)/)?.[1]] || null,
    querySelectorAll:selector=>{
      if(selector==='[name=secondary]:checked')return secondary.filter(field=>field.checked);
      if(selector==='[name=secondary]')return secondary;
      if(selector==='button[data-action]')return buttons;
      if(selector==='button')return [...buttons,codeButton];
      return selector.includes('button')?controls:editable;
    },
    platformRestoreControls:()=>controls.forEach(field=>field.disabled=form.dataset.splitting==='true'),
    platformSaveBeforeSplit:async()=>{flushes++;if(saveFails)throw new Error('保存失败');if(saveImplementation)await saveImplementation();if(unsaved)form.dataset.revision=String(Number(form.dataset.revision)+1);unsaved=false;}};
  const panel={dataset:{mode:personal?'personal':'suggestion',endpoint:'/api/assignments/1/split/'},
    querySelector:sel=>({'[data-split-preview]':previewButton,'[data-split-confirm]':confirmButton,'[data-split-parts]':preview,'[data-split-list]':list,'[data-split-undo]':undoButton,'[data-split-cancel]':cancelButton,'[data-split-controls]':modeControls,'[name=split_reason]':reason,'[data-split-status]':status}[sel]),querySelectorAll:()=>pieces};
  const events={}, documentEvents={}, destinations=[];
  let selection={start,end,collapsed:!hasSelection,inside:true};
  class Event {constructor(type){this.type=type;this.defaultPrevented=false;}preventDefault(){this.defaultPrevented=true;}}
  const window={addEventListener(type,fn){(events[type] ||= []).push(fn);},dispatchEvent(event){if(unsaved && event.type==='platform:before-back')event.preventDefault();(events[event.type]||[]).forEach(fn=>fn(event));return !event.defaultPrevented;},getSelection:()=>({rangeCount:1,isCollapsed:selection.collapsed,removeAllRanges:()=>selection.collapsed=true,getRangeAt:()=>({startContainer:{},endContainer:{},startOffset:selection.start,endOffset:selection.end})})};
  const text=el({textContent:displayText,contains:()=>selection.inside});
  const context=vm.createContext({document:{querySelector:sel=>sel==='[data-split-panel]'?panel:form,querySelectorAll:()=>[],addEventListener:(type,handler)=>documentEvents[type]=handler,createElement:tagName=>el({tagName}),
    getElementById:id=>id==='current-unit-text'?text:id==='unit-source-data'&&sourceText!==null?{textContent:JSON.stringify(sourceText)}:id==='annotation-data'?{textContent:JSON.stringify({revision,secondary:[]})}:null,
    createRange:()=>({position:0,selectNodeContents(){},setEnd(node,offset){this.position=offset;},toString(){return text.textContent.slice(0,this.position);}})},window,Event,fetch,Math,Promise,Date,URL,
    setTimeout:fn=>{const id=Math.random();timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),setInterval:()=>{},
    location:{pathname:'/fixture/',origin:'http://localhost',href:'http://localhost/fixture/',assign:url=>{assert.equal(window.dispatchEvent(new Event('platform:before-back')),true);destinations.push(url);}}});
  if(withApp)vm.runInContext(appSource,context);
  vm.runInContext(source,context);
  return {previewButton,confirmButton,undoButton,cancelButton,modeControls,preview,status,reason,
    get first(){return pieces[0]?.querySelector('pre')||el();},get second(){return pieces[1]?.querySelector('pre')||el();},get third(){return pieces[2]?.querySelector('pre')||el();},
    pieces,controls,destinations,window,Event,form,fields,buttons,text,saveStatus,flushes:()=>flushes,
    select:(start,end,{collapsed=false,inside=true,event='mouseup'}={})=>{selection={start,end,collapsed,inside};if(event==='selectionchange')documentEvents[event]();else if(event)text.handlers[event]();},
    edit:note=>{fields.note.value=note;formEvents.input({target:fields.note});},
    autoSave:()=>{const callbacks=[...timers.values()];timers.clear();callbacks.forEach(fn=>fn());},
    save:()=>formEvents.submit({preventDefault(){},submitter:{dataset:{action:'save'}}})};
}
const reply=(status=200,data={ok:true,next_url:'/r/1/?a=2'})=>({ok:status===200,status,headers:{get:()=> 'application/json'},json:async()=>data});
test('selection preview and confirmation preserve text, disable edits in flight and advance after success',async()=>{
  let finish;const ui=setup(()=>new Promise(resolve=>finish=resolve));
  ui.previewButton.handlers.click();assert.equal(ui.first.textContent,'先😊');assert.equal(ui.second.textContent,'讨论，再检查');
  const request=ui.confirmButton.handlers.click();assert.equal(ui.controls[0].disabled,true);assert.equal(ui.destinations.length,0);
  await new Promise(resolve=>setImmediate(resolve));
  finish(reply());await request;assert.deepEqual(ui.destinations,['/r/1/?a=2']);
});
test('failed split retries identical payload without leaving or permitting new edits',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));if(sent.length===1)throw new Error('offline');return reply();});
  ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();
  assert.equal(ui.destinations.length,0);assert.equal(ui.controls[0].disabled,true);assert.equal(ui.confirmButton.disabled,false);
  await ui.confirmButton.handlers.click();assert.deepEqual(sent[0],sent[1]);assert.match(sent[0].token,/^[0-9a-f-]{36}$/);
  assert.equal(ui.destinations.length,1);
});
test('saved and unsaved personal edits are flushed before splitting, while a suggestion never navigates',async()=>{
  for(const options of [{revision:1},{unsaved:true}]) {
    const ui=setup(async()=>reply(),options);ui.previewButton.handlers.click();
    await ui.confirmButton.handlers.click();assert.equal(ui.flushes(),1);assert.equal(ui.destinations.length,1);
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
test('an unsuccessful coding save never sends a split request or loses the selection',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return reply();},{unsaved:true,saveFails:true});
  ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();
  assert.equal(calls,0);assert.equal(ui.first.textContent,'先😊');assert.equal(ui.controls[0].disabled,false);assert.equal(ui.destinations.length,0);
});
test('the actual middle-selection bounds, not a prefix only, are sent to the server',async()=>{
  let sent;const ui=setup(async(url,options)=>{sent=JSON.parse(options.body);return reply();},{start:1,end:5,revision:3});
  ui.previewButton.handlers.click();assert.equal(ui.pieces[2].hidden,false);await ui.confirmButton.handlers.click();
  assert.equal(sent.start,1);assert.deepEqual(sent.cuts,[1,4]);assert.equal(sent.revision,3);assert.equal(ui.flushes(),1);
});

test('suffix selections and every resulting piece retain exact Unicode boundaries',()=>{
  const text='甲😊乙\n丙🚀丁';
  assert.deepEqual(selectionPreview(text,4,7),{parts:['甲😊乙\n','丙🚀丁'],start:4,end:7});
  for (const [start,end] of [[0,2],[1,3],[4,7]]) {
    const result=selectionPreview(text,start,end);
    assert.equal(result.parts.join(''),text);
    assert.equal(result.parts.filter(part=>!part.trim()).length,0);
  }
});

test('an uncertain 503 split response keeps its UUID, selection and navigation guard for retry',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{
    sent.push(JSON.parse(options.body));
    return sent.length===1?reply(503,{error:'数据库繁忙，请重试'}):reply();
  },{start:1,end:5,unsaved:true});
  ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();
  assert.equal(ui.flushes(),1);assert.equal(ui.destinations.length,0);assert.equal(ui.controls[0].disabled,true);
  assert.equal(ui.window.dispatchEvent(new ui.Event('platform:before-back')),false);
  assert.equal(ui.first.textContent,'先');assert.equal(ui.second.textContent,'😊讨论');assert.equal(ui.third.textContent,'，再检查');
  await ui.confirmButton.handlers.click();
  assert.deepEqual(sent[0],sent[1]);assert.equal(ui.flushes(),1);assert.equal(ui.destinations.length,1);
});

test('a split save failure can be retried without changing the selected fragment',async()=>{
  let attempts=0;const sent=[];
  const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));return reply();},{unsaved:true,start:1,end:5,saveImplementation:async()=>{if(++attempts===1)throw new Error('保存失败');}});
  ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();
  assert.equal(ui.preview.hidden,false);assert.equal(ui.confirmButton.disabled,false);assert.equal(ui.previewButton.disabled,false);
  assert.equal(ui.first.textContent,'先');assert.equal(ui.second.textContent,'😊讨论');assert.equal(ui.third.textContent,'，再检查');
  assert.equal(ui.window.dispatchEvent(new ui.Event('platform:before-back')),false);
  await ui.confirmButton.handlers.click();assert.equal(attempts,2);assert.equal(sent.length,1);assert.equal(sent[0].revision,1);
  assert.equal(ui.destinations.length,1);
});

test('the split POST waits for coding save confirmation and uses its updated revision',async()=>{
  let saveFinished;const sent=[];
  const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));return reply();},
    {revision:4,unsaved:true,start:1,end:5,saveImplementation:()=>new Promise(resolve=>saveFinished=resolve)});
  ui.previewButton.handlers.click();const request=ui.confirmButton.handlers.click();
  assert.equal(sent.length,0);assert.equal(ui.controls[0].disabled,true);assert.equal(ui.destinations.length,0);
  assert.equal(ui.window.dispatchEvent(new ui.Event('platform:before-back')),false);
  saveFinished();await request;
  assert.equal(sent.length,1);assert.equal(sent[0].revision,5);assert.equal(sent[0].start,1);assert.deepEqual(sent[0].cuts,[1,4]);
  assert.equal(ui.destinations.length,1);
});

const annotationReply=(status=200,revision=1)=>reply(status,{annotation:{revision,status:'draft'},error:status===409?'版本冲突':status===400?'请核对判断':undefined});
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('combined controllers never reopen conflicted coding controls after a pre-split flush',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{sent.push({url,payload:JSON.parse(options.body)});return annotationReply(409);},{withApp:true,start:1,end:5});
  ui.edit('必须保留的判断');ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();
  assert.equal(sent.length,1);assert.equal(sent[0].url,'/api/test-save/');assert.equal(ui.destinations.length,0);
  assert.equal(ui.fields.note.value,'必须保留的判断');assert.equal(ui.fields.note.disabled,true);
  assert.equal(ui.buttons.every(button=>button.disabled),true);assert.equal(ui.fields.primary.disabled,true);
  ui.save();await settle();assert.equal(sent.length,1);
  assert.equal(ui.window.dispatchEvent(new ui.Event('platform:before-back')),false);
});

test('combined controllers restore Save after in-flight validation failure and preserve the selection',async()=>{
  let finish;const sent=[];const ui=setup((url,options)=>{
    sent.push({url,payload:JSON.parse(options.body)});
    if(sent.length===1)return new Promise(resolve=>finish=resolve);
    return Promise.resolve(annotationReply(sent.length===2?400:200));
  },{withApp:true,start:1,end:5});
  ui.fields.primary.value='1';ui.edit('需要再次保存');ui.autoSave();assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.previewButton.handlers.click();const splitRequest=ui.confirmButton.handlers.click();finish(annotationReply(400));await splitRequest;
  assert.equal(sent.length,2);assert.equal(sent.every(request=>request.url==='/api/test-save/'),true);
  assert.equal(ui.buttons.some(button=>button.disabled),false);assert.equal(ui.fields.note.disabled,false);
  assert.equal(ui.fields.note.value,'需要再次保存');assert.equal(ui.second.textContent,'😊讨论');assert.equal(ui.destinations.length,0);
  ui.save();await settle();assert.equal(sent.length,3);assert.equal(ui.saveStatus.className,'save-status success');
});

test('combined controllers restore Save after an uncertain in-flight network failure and retry the same UUID',async()=>{
  let fail;const sent=[];const ui=setup((url,options)=>{
    sent.push({url,payload:JSON.parse(options.body)});
    if(sent.length===1)return new Promise((resolve,reject)=>fail=reject);
    if(sent.length===2)return Promise.reject(new Error('offline'));
    return Promise.resolve(annotationReply());
  },{withApp:true,start:1,end:5});
  ui.fields.primary.value='1';ui.edit('网络中断也不能丢失');ui.autoSave();ui.previewButton.handlers.click();const splitRequest=ui.confirmButton.handlers.click();
  fail(new Error('offline'));await splitRequest;
  assert.equal(sent.length,2);assert.equal(ui.buttons.some(button=>button.disabled),false);assert.equal(ui.fields.note.disabled,false);
  assert.equal(ui.fields.note.value,'网络中断也不能丢失');assert.equal(ui.destinations.length,0);
  assert.equal(ui.window.dispatchEvent(new ui.Event('platform:before-back')),false);
  ui.save();await settle();assert.equal(sent.length,3);
  assert.deepEqual(sent[0].payload,sent[1].payload);assert.deepEqual(sent[0].payload,sent[2].payload);
  assert.equal(ui.window.dispatchEvent(new ui.Event('platform:before-back')),true);
});

test('raw CRLF source restoration keeps DOM selection and server Unicode offsets aligned',async()=>{
  const raw='前句。\r\n😊中段。\r\n后句。';let sent;
  const ui=setup(async(url,options)=>{sent=JSON.parse(options.body);return reply();},
    {withApp:true,sourceText:raw,displayText:raw.replaceAll('\r\n','\n'),start:5,end:10});
  assert.equal(ui.text.textContent,raw);ui.previewButton.handlers.click();
  assert.equal(ui.first.textContent,'前句。\r\n');assert.equal(ui.second.textContent,'😊中段。');assert.equal(ui.third.textContent,'\r\n后句。');
  assert.equal(ui.first.textContent+ui.second.textContent+ui.third.textContent,raw);
  await ui.confirmButton.handlers.click();assert.equal(sent.start,5);assert.deepEqual(sent.cuts,[5,9]);assert.equal(ui.destinations.length,1);
});

test('clicking the entry first opens a clear local split mode without requiring a prior selection',()=>{
  const ui=setup(async()=>reply(),{hasSelection:false});
  ui.previewButton.handlers.click();
  assert.equal(ui.modeControls.hidden,false);assert.equal(ui.preview.hidden,true);assert.equal(ui.status.textContent,'');
  assert.equal(ui.confirmButton.disabled,true);assert.equal(ui.cancelButton.disabled,false);
  ui.select(0,3);assert.equal(ui.pieces.length,2);assert.equal(ui.first.textContent,'先😊');
  assert.equal(ui.confirmButton.textContent,'确认拆分并编码（2条）');
});

test('successive mouse and keyboard selections accumulate sorted boundaries and preserve the whole text',async()=>{
  let sent;const original='甲乙丙丁戊己';
  const ui=setup(async(url,options)=>{sent=JSON.parse(options.body);return reply();},{hasSelection:false,displayText:original});
  ui.previewButton.handlers.click();ui.select(0,2);ui.select(3,5,{event:'keyup'});
  assert.deepEqual(ui.pieces.map(row=>row.querySelector('pre').textContent),['甲乙','丙','丁戊','己']);
  assert.equal(ui.pieces.map(row=>row.querySelector('pre').textContent).join(''),original);
  assert.equal(ui.confirmButton.textContent,'确认拆分并编码（4条）');
  await ui.confirmButton.handlers.click();assert.deepEqual(sent.cuts,[2,3,5]);assert.equal(sent.start,3);
});

test('repeated selections do not duplicate cuts and the latest selected piece remains the coding target',async()=>{
  let sent;const ui=setup(async(url,options)=>{sent=JSON.parse(options.body);return reply();},{hasSelection:false,displayText:'甲乙丙丁'});
  ui.previewButton.handlers.click();ui.select(0,2);ui.select(0,2);ui.select(2,4);
  assert.equal(ui.pieces.length,2);assert.equal(ui.status.textContent,'此片段已划分');
  await ui.confirmButton.handlers.click();assert.deepEqual(sent.cuts,[2]);assert.equal(sent.start,2);
});

test('Undo and Cancel only change the local preview and never send a request or edit the source',()=>{
  let calls=0;const original='甲乙丙丁戊己';
  const ui=setup(async()=>{calls++;return reply();},{hasSelection:false,displayText:original});
  ui.previewButton.handlers.click();ui.select(0,2);ui.select(3,5);
  ui.undoButton.handlers.click();assert.deepEqual(ui.pieces.map(row=>row.querySelector('pre').textContent),['甲乙','丙丁戊己']);
  ui.undoButton.handlers.click();assert.equal(ui.preview.hidden,true);assert.equal(ui.confirmButton.disabled,true);assert.equal(ui.undoButton.disabled,true);
  ui.select(1,3);ui.cancelButton.handlers.click();
  assert.equal(ui.modeControls.hidden,true);assert.equal(ui.preview.hidden,true);assert.equal(ui.previewButton.textContent,'拆分单元');
  assert.equal(ui.text.textContent,original);assert.equal(calls,0);assert.equal(ui.form.dataset.splitting,undefined);
});

test('a one-character or whitespace-only unit has a disabled entry and concise hint',()=>{
  for(const displayText of ['在','  在\r\n','\t\r\n']) {
    const ui=setup(async()=>reply(),{displayText,hasSelection:false});
    assert.equal(ui.previewButton.disabled,true);assert.equal(ui.status.textContent,'此单元无需拆分');
    ui.previewButton.handlers.click();assert.equal(ui.modeControls.hidden,true);assert.equal(ui.pieces.length,0);
  }
});

test('Cancel clears the native selection so opening the mode again starts with an empty preview',()=>{
  const ui=setup(async()=>reply(),{hasSelection:false,displayText:'甲乙丙丁'});
  ui.previewButton.handlers.click();ui.select(0,2);ui.cancelButton.handlers.click();ui.previewButton.handlers.click();
  assert.equal(ui.modeControls.hidden,false);assert.equal(ui.preview.hidden,true);assert.equal(ui.pieces.length,0);
  assert.equal(ui.confirmButton.disabled,true);assert.equal(ui.undoButton.disabled,true);assert.equal(ui.status.textContent,'');
});

test('Undo clears the native selection so Continue cannot silently restore the undone boundaries',()=>{
  const ui=setup(async()=>reply(),{hasSelection:false,displayText:'甲乙丙丁戊己'});
  ui.previewButton.handlers.click();ui.select(0,2);ui.select(3,5);ui.undoButton.handlers.click();
  ui.previewButton.handlers.click();assert.deepEqual(ui.pieces.map(row=>row.querySelector('pre').textContent),['甲乙','丙丁戊己']);
  ui.undoButton.handlers.click();ui.previewButton.handlers.click();
  assert.equal(ui.preview.hidden,true);assert.equal(ui.pieces.length,0);assert.equal(ui.confirmButton.disabled,true);
  assert.equal(ui.undoButton.disabled,true);assert.equal(ui.status.textContent,'');
});

test('a fresh drag in split mode clears native highlights and its mouseup adds a new boundary',()=>{
  const ui=setup(async()=>reply(),{hasSelection:false,displayText:'甲乙丙丁戊己'});
  ui.previewButton.handlers.click();ui.select(0,2);ui.text.handlers.mousedown({button:0,detail:1});
  assert.equal(ui.window.getSelection().isCollapsed,true);assert.equal(ui.pieces.length,2);
  ui.select(3,5);assert.deepEqual(ui.pieces.map(row=>row.querySelector('pre').textContent),['甲乙','丙','丁戊','己']);
});

test('ordinary reading and Shift or double-click selections retain native selection behavior',()=>{
  const ui=setup(async()=>reply(),{displayText:'甲乙丙丁',start:0,end:2});
  ui.text.handlers.mousedown({button:0,detail:1});assert.equal(ui.window.getSelection().isCollapsed,false);
  ui.previewButton.handlers.click();
  for(const event of [{button:0,detail:1,shiftKey:true},{button:0,detail:2},{button:2,detail:1}]) {
    ui.text.handlers.mousedown(event);assert.equal(ui.window.getSelection().isCollapsed,false);assert.equal(ui.pieces.length,2);
  }
});

test('a pending split retry never resets its native selection or preview on mousedown',async()=>{
  const ui=setup(async()=>{throw new Error('offline');},{displayText:'甲乙丙丁',start:0,end:2});
  ui.previewButton.handlers.click();await ui.confirmButton.handlers.click();ui.text.handlers.mousedown({button:0,detail:1});
  assert.equal(ui.window.getSelection().isCollapsed,false);assert.equal(ui.first.textContent,'甲乙');assert.equal(ui.pieces.length,2);
});

test('a valid selection survives focus loss before the entry is clicked',()=>{
  const ui=setup(async()=>reply(),{hasSelection:false});
  ui.select(1,5,{event:'selectionchange'});ui.select(0,0,{collapsed:true,event:null});
  ui.previewButton.handlers.click();assert.equal(ui.pieces.length,3);
  assert.equal(ui.second.textContent,'😊讨论');assert.equal(ui.confirmButton.disabled,false);
});

test('selecting all text, blank text or another element preserves an existing valid preview',()=>{
  const ui=setup(async()=>reply(),{hasSelection:false,displayText:'甲乙  丙丁'});
  ui.previewButton.handlers.click();ui.select(0,2);
  const before=ui.pieces.map(row=>row.querySelector('pre').textContent);
  ui.select(0,6);assert.match(ui.status.textContent,/不要选中整条/);assert.deepEqual(ui.pieces.map(row=>row.querySelector('pre').textContent),before);
  ui.select(2,4);assert.match(ui.status.textContent,/需要包含文字/);assert.deepEqual(ui.pieces.map(row=>row.querySelector('pre').textContent),before);
  ui.select(0,1,{inside:false});assert.deepEqual(ui.pieces.map(row=>row.querySelector('pre').textContent),before);assert.equal(ui.confirmButton.disabled,false);
});

test('the local preview permits 50 nonblank units and refuses a 51st without losing its cuts',async()=>{
  let sent;const ui=setup(async(url,options)=>{sent=JSON.parse(options.body);return reply();},{displayText:'甲'.repeat(51),hasSelection:false});
  ui.previewButton.handlers.click();for(let end=1;end<=49;end++)ui.select(0,end);
  assert.equal(ui.pieces.length,50);assert.equal(ui.confirmButton.textContent,'确认拆分并编码（50条）');
  ui.select(0,50);assert.equal(ui.pieces.length,50);assert.match(ui.status.textContent,/50 条/);
  await ui.confirmButton.handlers.click();assert.equal(sent.cuts.length,49);
});

test('cuts preview strictly validates boundaries, preserves Unicode and forbids blank units',()=>{
  assert.deepEqual(cutsPreview('甲😊乙\r\n丙丁',[1,3,6]),['甲','😊乙','\r\n丙','丁']);
  for(const cuts of [[],[0],[7],[true],[1.5],['1'],[2,1],[1,1],null])assert.throws(()=>cutsPreview('甲😊乙\r\n丙丁',cuts));
  assert.throws(()=>cutsPreview('甲  乙',[1,3]));
});

test('uncertain requests disable local Undo and Cancel so retries retain the original boundaries',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;throw new Error('offline');},{hasSelection:false,displayText:'甲乙丙丁'});
  ui.previewButton.handlers.click();ui.select(0,2);await ui.confirmButton.handlers.click();
  assert.equal(ui.undoButton.disabled,true);assert.equal(ui.cancelButton.disabled,true);
  ui.undoButton.handlers.click();ui.cancelButton.handlers.click();ui.select(0,1);
  assert.equal(ui.pieces.length,2);assert.equal(ui.first.textContent,'甲乙');assert.equal(calls,1);
});
