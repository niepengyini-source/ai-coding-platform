const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/unsplitting.js'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));
const reply = (status = 200, data = {ok:true, next_url:'/r/3/?a=7'}) => ({ok:status === 200, status,
  headers:{get:() => 'application/json'}, json:async () => data});

function setup(fetch, {confirm=true, saveFails=false, saveImplementation=null, locked=false}={}) {
  const control = () => ({disabled:locked});
  const controls = [control(), control(), control()];
  const button = {disabled:false, textContent:'', handlers:{}, addEventListener(type, handler){this.handlers[type]=handler;}};
  const status = {textContent:''}, destinations=[], events={}, requests=[], prompts=[], signals=[];
  let flushes=0;
  const form = {dataset:{revision:'4', bookId:'8', shortcutScope:'3', quickMode:'true', locked:String(locked)},
    querySelectorAll:() => controls,
    querySelector:() => ({value:'test-csrf'}),
    platformSaveBeforeSplit:async () => {flushes++;if(saveFails)throw new Error('当前编码保存失败');if(saveImplementation)await saveImplementation();if(!locked)form.dataset.revision='5';},
    platformRestoreControls:() => controls.forEach(field => field.disabled=locked || form.dataset.splitting==='true')};
  const option={parent_id:7,parent_revision:2,branch_token:'branch-fingerprint',part_count:3,coded_count:2,text:'甲乙丙丁'};
  const panel={dataset:{endpoint:'/api/assignments/9/unsplit/'},querySelector:selector=>selector==='[data-unsplit-confirm]'?button:status};
  class Event {constructor(type){this.type=type;this.defaultPrevented=false;}preventDefault(){this.defaultPrevented=true;}}
  const window={confirm:message=>{prompts.push(message);return confirm;},
    addEventListener(type,handler){(events[type] ||= []).push(handler);},
    dispatchEvent(event){signals.push({type:event.type,splitting:form.dataset.splitting});(events[event.type]||[]).forEach(handler=>handler(event));return !event.defaultPrevented;}};
  vm.runInContext(source,vm.createContext({document:{querySelector:selector=>selector==='[data-unsplit-panel]'?panel:form,
    getElementById:()=>({textContent:JSON.stringify(option)})},window,Event,Math,Promise,
    fetch:async (url,options)=>{requests.push({url,payload:JSON.parse(options.body),headers:options.headers});return fetch(url,options);},
    location:{assign:target=>{assert.equal(window.dispatchEvent(new Event('platform:before-back')),true);destinations.push(target);}}}));
  return {button,status,controls,form,option,destinations,window,Event,requests,prompts,signals,flushes:()=>flushes};
}

test('restore confirmation saves latest coding, uses its revision and restores the parent without leaving quick mode',async()=>{
  const ui=setup(async()=>reply());await ui.button.handlers.click();
  assert.equal(ui.flushes(),1);assert.equal(ui.prompts.length,1);assert.match(ui.prompts[0],/保留在历史/);
  assert.equal(ui.requests[0].url,'/api/assignments/9/unsplit/');
  const payload=ui.requests[0].payload;
  assert.equal(payload.parent_id,7);assert.equal(payload.parent_revision,2);assert.equal(payload.branch_token,'branch-fingerprint');
  assert.equal(payload.revision,5);assert.equal(payload.book_id,8);assert.equal(payload.confirm_coded,true);assert.match(payload.token,/^[a-f0-9-]{36}$/);
  assert.deepEqual(ui.destinations,['/r/3/?a=7']);assert.equal(ui.form.dataset.quickMode,'true');
  assert.equal(ui.controls.every(field=>field.disabled),true);assert.equal(ui.button.disabled,true);
});

test('cancelling restore confirmation does not save, mutate or navigate',async()=>{
  const ui=setup(async()=>{throw new Error('unexpected');},{confirm:false});await ui.button.handlers.click();
  assert.equal(ui.flushes(),0);assert.equal(ui.requests.length,0);assert.deepEqual(ui.destinations,[]);
  assert.equal(ui.form.dataset.splitting,undefined);assert.equal(ui.form.dataset.quickMode,'true');
});

test('restore waits for draft save completion and prevents double clicks and back navigation',async()=>{
  let finish;const ui=setup(async()=>reply(),{saveImplementation:()=>new Promise(resolve=>finish=resolve)});
  const request=ui.button.handlers.click();await settle();await ui.button.handlers.click();
  assert.equal(ui.flushes(),1);assert.equal(ui.requests.length,0);assert.equal(ui.controls.every(field=>field.disabled),true);
  assert.equal(ui.window.dispatchEvent(new ui.Event('platform:before-back')),false);
  const unload=new ui.Event('beforeunload');ui.window.dispatchEvent(unload);assert.equal(unload.defaultPrevented,true);
  finish();await request;assert.equal(ui.requests.length,1);assert.deepEqual(ui.destinations,['/r/3/?a=7']);
});

test('save failure never sends restore, unlocks editable controls and allows retry',async()=>{
  let attempts=0;const ui=setup(async()=>reply(),{saveImplementation:async()=>{if(++attempts===1)throw new Error('当前编码保存失败');}});await ui.button.handlers.click();
  assert.equal(ui.requests.length,0);assert.equal(ui.status.textContent,'当前编码保存失败');
  assert.equal(ui.button.disabled,false);assert.equal(ui.controls.every(field=>!field.disabled),true);
  assert.equal(ui.form.dataset.quickMode,'true');assert.equal(ui.form.dataset.splitting,'false');
  await ui.button.handlers.click();assert.equal(attempts,2);assert.equal(ui.requests.length,1);assert.deepEqual(ui.destinations,['/r/3/?a=7']);
});

test('a submitted child can restore even when annotation fields are read-only',async()=>{
  const ui=setup(async()=>reply(),{locked:true});assert.equal(ui.button.disabled,false);
  await ui.button.handlers.click();assert.equal(ui.requests[0].payload.revision,4);assert.deepEqual(ui.destinations,['/r/3/?a=7']);
});

test('network uncertainty keeps the exact request, UUID and navigation guard for safe retry',async()=>{
  let calls=0;const ui=setup(async()=>{if(++calls===1)throw new Error('offline');return reply();});
  await ui.button.handlers.click();assert.equal(ui.destinations.length,0);assert.equal(ui.button.textContent,'重试撤回拆分');
  assert.equal(ui.button.disabled,false);assert.equal(ui.controls.every(field=>field.disabled),true);
  assert.equal(ui.window.dispatchEvent(new ui.Event('platform:before-back')),false);
  await ui.button.handlers.click();assert.deepEqual(ui.requests[0].payload,ui.requests[1].payload);
  assert.equal(ui.flushes(),1);assert.equal(ui.prompts.length,1);assert.deepEqual(ui.destinations,['/r/3/?a=7']);
});

test('503 keeps the request for retry while 400 clears it and restores editing',async()=>{
  for(const code of [503,400]){
    const ui=setup(async()=>reply(code,{error:'暂未完成'}));await ui.button.handlers.click();
    assert.equal(ui.status.textContent,'暂未完成');assert.equal(ui.controls.every(field=>field.disabled),code===503);
    assert.equal(ui.form.dataset.splitting,String(code===503));assert.equal(ui.button.disabled,false);
    await ui.button.handlers.click();
    assert.equal(ui.flushes(),code===503?1:2);assert.equal(ui.prompts.length,code===503?1:2);
    if(code===503)assert.deepEqual(ui.requests[0].payload,ui.requests[1].payload);
  }
});

test('409 retains original coding read-only and never navigates or retries stale data',async()=>{
  const ui=setup(async()=>reply(409,{error:'其他页面更新了片段，请刷新核对'}));await ui.button.handlers.click();
  assert.equal(ui.status.textContent,'其他页面更新了片段，请刷新核对');assert.equal(ui.button.disabled,true);
  assert.equal(ui.controls.every(field=>field.disabled),true);assert.equal(ui.form.dataset.quickMode,'true');
  await ui.button.handlers.click();assert.equal(ui.requests.length,1);assert.deepEqual(ui.destinations,[]);
});

test('non-JSON responses retain the original request rather than assuming success',async()=>{
  const ui=setup(async()=>({ok:true,status:200,headers:{get:()=> 'text/html'}}));await ui.button.handlers.click();
  assert.match(ui.status.textContent,/核对登录状态/);assert.equal(ui.form.dataset.splitting,'true');assert.equal(ui.button.disabled,false);
  assert.equal(ui.destinations.length,0);await ui.button.handlers.click();assert.deepEqual(ui.requests[0].payload,ui.requests[1].payload);
});

test('a successful restore only navigates to the current round and locks obsolete controls',async()=>{
  for(const next_url of ['https://example.com/','/r/8/?a=7','/login/','/r/3/?a=7&next=bad','']){
    const ui=setup(async()=>reply(200,{ok:true,next_url}));await ui.button.handlers.click();
    assert.match(ui.status.textContent,/拆分已撤回/);assert.deepEqual(ui.destinations,[]);assert.equal(ui.button.disabled,true);
    assert.equal(ui.controls.every(field=>field.disabled),true);
  }
});

test('an active local split preview blocks restoring until it has been cancelled',async()=>{
  const ui=setup(async()=>reply());ui.form.dataset.splitPreview='true';ui.window.dispatchEvent(new ui.Event('platform:split-preview-change'));
  assert.equal(ui.button.disabled,true);await ui.button.handlers.click();assert.equal(ui.flushes(),0);
  ui.form.dataset.splitPreview='false';ui.window.dispatchEvent(new ui.Event('platform:split-preview-change'));
  assert.equal(ui.button.disabled,false);await ui.button.handlers.click();assert.equal(ui.requests.length,1);
});

test('an existing split request prevents restore and restore emits operation locking signals',async()=>{
  const ui=setup(async()=>reply());ui.form.dataset.splitting='true';await ui.button.handlers.click();assert.equal(ui.requests.length,0);
  ui.form.dataset.splitting='false';await ui.button.handlers.click();
  const signals=ui.signals.filter(signal=>signal.type==='platform:unit-operation-change');
  assert.equal(signals[0].splitting,'true');assert.equal(signals.at(-1).splitting,'true');
});
