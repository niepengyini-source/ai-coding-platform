// Controller unit tests: fake DOM only, no browser or Windows automation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function setup(fetchImplementation, initial = {}, mode = 'annotation') {
  const listeners = {}, windowListeners = {}, timers = new Map();
  const fields = Object.fromEntries(['primary','note','no_code','uncertain','csrfmiddlewaretoken'].map(name => [name, {value:'',checked:false,disabled:false,addEventListener(){}}]));
  fields.csrfmiddlewaretoken.value = 'test-token';
  const secondary = [{value:'2',checked:false,disabled:false}];
  const buttons = [{disabled:false}, {disabled:false}];
  const status = {textContent:'', className:''};
  const form = {dataset:{endpoint:'/api/test/',mode,locked:'false'},
    addEventListener: (name, fn) => listeners[name] = fn,
    querySelector: selector => selector === '.save-status' ? status : fields[selector.match(/name=([^\]]+)/)[1]],
    querySelectorAll: selector => {
      if (selector === '[name=secondary]:checked') return secondary.filter(x => x.checked);
      if (selector === '[name=secondary]') return secondary;
      if (selector === 'button') return buttons;
      const inputs = Object.entries(fields).filter(([k]) => k !== 'csrfmiddlewaretoken').map(([,v]) => v).concat(secondary);
      if (selector.includes('button')) return inputs.concat(buttons);
      return inputs;
    }};
  const context = {document:{hidden:false,querySelector:()=>form,querySelectorAll:()=>[],
    getElementById: id => id === 'annotation-data' ? {textContent:JSON.stringify({revision:0,secondary:[],...initial})} : null},
    location:{pathname:'/fixture/',href:'http://localhost/fixture/'},
    window:{addEventListener:(name,fn)=>windowListeners[name]=fn},
    fetch:fetchImplementation, URL, Promise, Date, Math,
    setTimeout: fn => {const id=Math.random();timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),setInterval:()=>{}};
  vm.runInNewContext(source, context);
  return {fields,secondary,status,buttons,
    edit: text => {fields.note.value=text;listeners.input();},
    click: action => listeners.submit({preventDefault(){},submitter:{dataset:{action}}}),
    autoSave:()=>{const callbacks=[...timers.values()];timers.clear();callbacks.forEach(fn=>fn());},
    isDirty:()=>{let dirty=false;windowListeners.beforeunload({preventDefault(){dirty=true;}});return dirty;},
    canReturn:()=>{let blocked=false;windowListeners['platform:before-back']({preventDefault(){blocked=true;}});return !blocked;}};
}
function response(annotation, status=200, error) {
  return {ok:status===200,status,headers:{get:()=> 'application/json'},json:async()=>({annotation,error})};
}
async function flush() {for(let n=0;n<12;n++) await Promise.resolve();}

test('draft autosave sends Chinese notes and updates saved revision', async()=>{
  let sent;
  const ui=setup(async(url,options)=>{sent=JSON.parse(options.body);return response({revision:1,status:'draft'});});
  ui.fields.primary.value='1';ui.edit('依据前后对话判断');ui.autoSave();await flush();
  assert.equal(sent.note,'依据前后对话判断');assert.equal(sent.primary,1);
  assert.match(ui.status.textContent,/已保存/);assert.equal(ui.isDirty(),false);
});
test('new edits during an in-flight save remain dirty and are saved afterwards',async()=>{
  let resolveFirst;const sent=[];
  const ui=setup((url,options)=>{sent.push(JSON.parse(options.body));return sent.length===1?new Promise(resolve=>resolveFirst=resolve):Promise.resolve(response({revision:2,status:'draft'}));});
  ui.edit('第一版');ui.autoSave();ui.edit('最新输入');
  resolveFirst(response({revision:1,status:'draft'}));await flush();
  assert.equal(ui.fields.note.value,'最新输入');assert.equal(ui.isDirty(),true);
  ui.autoSave();await flush();assert.equal(sent[1].revision,1);assert.equal(sent[1].note,'最新输入');assert.equal(ui.isDirty(),false);
});
test('network retry preserves request token and does not lose later edits',async()=>{
  const sent=[];
  const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));if(sent.length===1) throw new Error('offline');return response({revision:sent.length-1,status:'draft'});});
  ui.edit('旧输入');ui.click('save');await flush();ui.edit('网络失败后的新输入');ui.click('save');await flush();
  assert.equal(sent[0].token,sent[1].token);assert.equal(ui.isDirty(),true);
  ui.autoSave();await flush();assert.equal(sent[2].note,'网络失败后的新输入');assert.equal(sent[2].revision,1);assert.equal(ui.isDirty(),false);
});
test('submit after retry commits latest input, not just the old draft',async()=>{
  const sent=[];
  const ui=setup(async(url,options)=>{const payload=JSON.parse(options.body);sent.push(payload);if(sent.length===1)throw new Error('offline');return response({revision:sent.length-1,status:payload.action==='submit'?'submitted':'draft'});});
  ui.edit('失败草稿');ui.click('save');await flush();ui.edit('最终提交输入');ui.click('submit');await flush();
  assert.equal(sent[2].action,'submit');assert.equal(sent[2].note,'最终提交输入');assert.equal(sent[2].revision,1);
  assert.match(ui.status.textContent,/已提交并锁定/);assert.equal(ui.buttons[0].disabled,true);
});
test('conflict preserves editable input and stops further overwrites',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response(null,409,'版本冲突，输入保留');});
  ui.edit('不能丢失的备注');ui.click('save');await flush();
  assert.equal(ui.fields.note.value,'不能丢失的备注');assert.equal(ui.fields.note.disabled,false);
  assert.equal(ui.isDirty(),true);assert.equal(ui.buttons[0].disabled,true);
  ui.autoSave();await flush();assert.equal(calls,1);
});
test('validation error does not claim success or discard input',async()=>{
  const ui=setup(async()=>response(null,400,'不确定时请说明原因。'));
  ui.edit('当前输入');ui.click('submit');await flush();
  assert.match(ui.status.textContent,/请说明原因/);assert.equal(ui.fields.note.value,'当前输入');assert.equal(ui.fields.note.disabled,false);assert.equal(ui.isDirty(),true);
});
test('decision edits are manual-save, not automatic',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1});},{},'decision');
  ui.edit('协商依据');ui.autoSave();await flush();assert.equal(calls,0);
  ui.click('save');await flush();assert.equal(calls,1);
});

test('return is blocked during unsaved and in-flight coding, then allowed after confirmed save',async()=>{
  let finish;
  const ui=setup(()=>new Promise(resolve=>finish=resolve));
  assert.equal(ui.canReturn(),true);
  ui.edit('未确认保存的编码');assert.equal(ui.canReturn(),false);
  ui.autoSave();assert.equal(ui.canReturn(),false);
  finish(response({revision:1,status:'draft'}));await flush();assert.equal(ui.canReturn(),true);
});

test('conflicts and failed network retries keep the return guard active',async()=>{
  const conflict=setup(async()=>response(null,409,'版本冲突'));
  conflict.edit('保留输入');conflict.click('save');await flush();assert.equal(conflict.canReturn(),false);
  const offline=setup(async()=>{throw new Error('offline');});
  offline.edit('等待重试');offline.click('save');await flush();assert.equal(offline.canReturn(),false);
});

test('manual decision must be saved before return, without implicit submission',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1});},{},'decision');
  ui.edit('协商判断');assert.equal(ui.canReturn(),false);assert.equal(calls,0);
  ui.click('save');await flush();assert.equal(ui.canReturn(),true);
});
