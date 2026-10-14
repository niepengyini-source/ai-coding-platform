// Controller unit tests: fake DOM only, no browser or Windows automation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function setup(fetchImplementation, initial = {}, mode = 'annotation', nextUrl = '/r/1/?a=2') {
  const listeners = {}, windowListeners = {}, timers = new Map();
  const fields = Object.fromEntries(['primary','note','no_code','uncertain','csrfmiddlewaretoken'].map(name => [name, {name,value:'',checked:false,disabled:false,handlers:{},addEventListener(type,fn){this.handlers[type]=fn;},dispatchEvent(event){this.handlers[event.type]?.({...event,target:this});listeners[event.type]?.({target:this});}}]));
  fields.csrfmiddlewaretoken.value = 'test-token';
  const secondary = ['2','3'].map(value => ({name:'secondary',value,checked:false,disabled:false,parentElement:{textContent:'代码 '+value},dispatchEvent(){listeners.change({target:this});}}));
  const buttons = [{disabled:false}, {disabled:false}];
  const codeButtons = [{disabled:false}];
  const legacyNotice = {hidden:true,textContent:''};
  const status = {textContent:'', className:''};
  const navigations = [];
  const form = {dataset:{endpoint:'/api/test/',mode,locked:'false',nextUrl},
    addEventListener: (name, fn) => listeners[name] = fn,
    querySelector: selector => selector === '.save-status' ? status : selector === '[data-legacy-secondary]' ? legacyNotice : fields[selector.match(/name=([^\]]+)/)?.[1]],
    querySelectorAll: selector => {
      if (selector === '[name=secondary]:checked') return secondary.filter(x => x.checked);
      if (selector === '[name=secondary]') return secondary;
      if (selector === 'button[data-action]') return buttons;
      if (selector === 'button') return buttons.concat(codeButtons);
      const inputs = Object.entries(fields).filter(([k]) => k !== 'csrfmiddlewaretoken').map(([,v]) => v).concat(secondary);
      if (selector.includes('button')) return inputs.concat(buttons,codeButtons);
      return inputs;
    }};
  const context = {document:{hidden:false,querySelector:()=>form,querySelectorAll:()=>[],
    getElementById: id => id === 'annotation-data' ? {textContent:JSON.stringify({revision:0,primary:1,secondary:[],...initial})} : null},
    location:{pathname:'/fixture/',origin:'http://localhost',href:'http://localhost/fixture/',assign: url=>navigations.push(url)},
    window:{addEventListener:(name,fn)=>windowListeners[name]=fn},
    fetch:fetchImplementation, URL, Promise, Date, Math,Event:class{constructor(type){this.type=type;}},
    setTimeout: fn => {const id=Math.random();timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),setInterval:()=>{}};
  vm.runInNewContext(source, context);
  return {fields,secondary,status,buttons,codeButtons,navigations,form,legacyNotice,
    choosePrimary: value => {fields.primary.value=value;fields.primary.dispatchEvent({type:'change'});},
    chooseNoCode: checked => {fields.no_code.checked=checked;fields.no_code.dispatchEvent({type:'change'});},
    chooseSecondary: index => {secondary[index].checked=true;listeners.change({target:secondary[index]});},
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

test('empty coding starts disabled, selecting enables and clearing disables both manual buttons',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},{primary:null});
  assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.click('save');ui.click('submit_next');await flush();assert.equal(calls,0);
  ui.choosePrimary('1');assert.equal(ui.buttons.every(button=>!button.disabled),true);
  ui.choosePrimary('');assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.edit('尚未选代码的备注');ui.autoSave();await flush();assert.equal(calls,1);
  assert.equal(ui.buttons.every(button=>button.disabled),true);
});
test('no-code and uncertainty remain valid judgments; uncertainty requires a reason',async()=>{
  const ui=setup(async()=>response({revision:1,status:'draft'}),{primary:null});
  ui.chooseNoCode(true);assert.equal(ui.buttons.every(button=>!button.disabled),true);
  ui.chooseNoCode(false);assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.fields.uncertain.checked=true;ui.fields.uncertain.dispatchEvent({type:'change'});
  assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.edit('这里的含义还不能确定');assert.equal(ui.buttons.every(button=>!button.disabled),true);
  ui.edit('  ');assert.equal(ui.buttons.every(button=>button.disabled),true);
});
test('valid manual buttons recover after network or validation failure but not a conflict',async()=>{
  for(const code of [400,503]) {
    const ui=setup(async()=>response(null,code,'请重试'));
    ui.click('save');await flush();assert.equal(ui.buttons.every(button=>!button.disabled),true);
  }
  const offline=setup(async()=>{throw new Error('offline');});offline.click('save');await flush();
  assert.equal(offline.buttons.every(button=>!button.disabled),true);
  const conflict=setup(async()=>response(null,409,'冲突'));conflict.click('save');await flush();
  assert.equal(conflict.buttons.every(button=>button.disabled),true);
});
test('preloaded coded record initializes enabled while a locked record stays read only',()=>{
  const ui=setup(async()=>response({revision:1,status:'draft'}),{primary:1});
  assert.equal(ui.buttons.every(button=>!button.disabled),true);
  ui.form.dataset.splitting='true';ui.form.platformRestoreControls();
  assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.form.dataset.splitting='false';ui.form.platformRestoreControls();
  assert.equal(ui.buttons.every(button=>!button.disabled),true);
});

test('draft saves do not disable code buttons or primary and secondary choices',async()=>{
  let finish;const ui=setup(()=>new Promise(resolve=>finish=resolve));
  ui.fields.primary.value='1';ui.chooseSecondary(0);ui.edit('先保存');ui.autoSave();
  assert.equal(ui.codeButtons[0].disabled,false);assert.equal(ui.fields.primary.disabled,false);
  assert.equal(ui.secondary[0].disabled,false);assert.equal(ui.buttons[0].disabled,true);
  ui.chooseSecondary(1);assert.equal(ui.secondary[0].checked,false);assert.equal(ui.secondary[1].checked,true);
  finish(response({revision:1,status:'draft'}));await flush();assert.equal(ui.isDirty(),true);
});
test('primary and one secondary are saved together and changing secondary never erases primary',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));return response({revision:sent.length,status:'draft'});});
  ui.fields.primary.value='1';ui.chooseSecondary(0);ui.autoSave();await flush();
  assert.equal(sent[0].primary,1);assert.deepEqual(sent[0].secondary,[2]);
  ui.chooseSecondary(1);ui.autoSave();await flush();assert.equal(sent[1].primary,1);assert.deepEqual(sent[1].secondary,[3]);
});

test('rapid primary and secondary choices preserve only the latest pair after an in-flight draft',async()=>{
  let finish;const sent=[];
  const ui=setup((url,options)=>{
    sent.push(JSON.parse(options.body));
    return sent.length===1?new Promise(resolve=>finish=resolve):Promise.resolve(response({revision:sent.length,status:'draft'}));
  });
  ui.choosePrimary('1');ui.chooseSecondary(0);ui.autoSave();
  for(let n=0;n<20;n++) {ui.choosePrimary(n%2?'4':'1');ui.chooseSecondary(n%2);}
  assert.equal(sent.length,1);assert.equal(ui.codeButtons[0].disabled,false);assert.equal(ui.fields.primary.disabled,false);
  finish(response({revision:1,status:'draft'}));await flush();ui.autoSave();await flush();
  assert.equal(sent.length,2);assert.equal(sent[1].revision,1);assert.equal(sent[1].primary,4);assert.deepEqual(sent[1].secondary,[3]);
  assert.equal(ui.isDirty(),false);assert.equal(ui.navigations.length,0);
});

test('choosing a new primary retains a different secondary but clears an identical secondary',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));return response({revision:sent.length,status:'draft'});});
  ui.choosePrimary('1');ui.chooseSecondary(0);ui.choosePrimary('4');ui.autoSave();await flush();
  assert.equal(sent[0].primary,4);assert.deepEqual(sent[0].secondary,[2]);
  ui.choosePrimary('2');ui.autoSave();await flush();assert.equal(sent[1].primary,2);assert.deepEqual(sent[1].secondary,[]);
});
test('splitting flushes a save already in flight and then the latest edits',async()=>{
  let finish;const sent=[];const ui=setup((url,options)=>{sent.push(JSON.parse(options.body));return sent.length===1?new Promise(resolve=>finish=resolve):Promise.resolve(response({revision:2,status:'draft'}));});
  ui.edit('第一版');ui.autoSave();ui.edit('拆分前的新判断');
  const saved=ui.form.platformSaveBeforeSplit();finish(response({revision:1,status:'draft'}));
  assert.equal(await saved,2);assert.equal(sent.length,2);assert.equal(sent[1].note,'拆分前的新判断');assert.equal(ui.canReturn(),true);
});
test('a failed save prevents splitting and preserves the pending coding input',async()=>{
  const ui=setup(async()=>{throw new Error('offline');});ui.edit('保留这个判断');
  await assert.rejects(ui.form.platformSaveBeforeSplit(),/尚未保存/);
  assert.equal(ui.fields.note.value,'保留这个判断');assert.equal(ui.canReturn(),false);
});
test('old multiple secondary labels are not silently truncated on autosave',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));return sent.length===1?response(null,400,'次代码只能选择一个'):response({revision:1,status:'draft'});},{primary:1,secondary:[2,3]});
  assert.equal(ui.legacyNotice.hidden,false);ui.edit('原备注');ui.autoSave();await flush();assert.deepEqual(sent[0].secondary,[2,3]);
  ui.chooseSecondary(0);ui.autoSave();await flush();assert.deepEqual(sent[1].secondary,[2]);
});

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
test('conflict preserves input values but disables edits and stops further overwrites',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response(null,409,'版本冲突，输入保留');});
  ui.edit('不能丢失的备注');ui.click('save');await flush();
  assert.equal(ui.fields.note.value,'不能丢失的备注');assert.equal(ui.fields.note.disabled,true);
  assert.equal(ui.fields.primary.disabled,true);assert.equal(ui.secondary.every(el=>el.disabled),true);
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

test('decision main and one secondary coexist and no-code explicitly clears both',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));return response({revision:sent.length});},{no_code:true,uncertain:true},'decision');
  ui.choosePrimary('1');assert.equal(ui.fields.no_code.checked,false);ui.chooseSecondary(0);ui.edit('人工协商依据');
  ui.click('save');await flush();assert.equal(sent[0].primary,1);assert.deepEqual(sent[0].secondary,[2]);
  ui.chooseNoCode(true);assert.equal(ui.fields.primary.value,'');assert.equal(ui.secondary.some(el=>el.checked),false);
  assert.equal(ui.fields.note.value,'人工协商依据');assert.equal(ui.fields.uncertain.checked,true);
  ui.autoSave();await flush();assert.equal(sent.length,1);
  ui.click('save');await flush();assert.equal(sent[1].primary,null);assert.deepEqual(sent[1].secondary,[]);assert.equal(sent[1].no_code,true);
});

test('a shared decision conflict retains its chosen pair and disables further mutations',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response(null,409,'协商结论已被另一成员保存');},{},'decision');
  ui.choosePrimary('1');ui.chooseSecondary(1);ui.edit('保留协商依据');ui.click('save');await flush();
  assert.equal(ui.fields.primary.value,'1');assert.equal(ui.secondary[1].checked,true);assert.equal(ui.fields.note.value,'保留协商依据');
  assert.equal(ui.fields.primary.disabled,true);assert.equal(ui.fields.note.disabled,true);assert.equal(ui.secondary.every(el=>el.disabled),true);
  ui.click('save');await flush();assert.equal(calls,1);assert.equal(ui.canReturn(),false);
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

test('confirm and next submits once and only navigates after acknowledged submission',async()=>{
  let finish;const sent=[];
  const ui=setup((url,options)=>{sent.push(JSON.parse(options.body));return new Promise(resolve=>finish=resolve);});
  ui.fields.primary.value='1';ui.edit('最终判断');ui.click('submit_next');
  assert.equal(ui.navigations.length,0);assert.equal(sent[0].action,'submit');
  finish(response({revision:1,status:'submitted'}));await flush();
  assert.deepEqual(ui.navigations,['/r/1/?a=2']);assert.equal(ui.canReturn(),true);
});
test('confirm and next after a failed autosave submits latest text before navigation',async()=>{
  const sent=[];
  const ui=setup(async(url,options)=>{const payload=JSON.parse(options.body);sent.push(payload);if(sent.length===1)throw new Error('offline');return response({revision:sent.length-1,status:payload.action==='submit'?'submitted':'draft'});});
  ui.edit('之前的草稿');ui.autoSave();await flush();ui.edit('最终输入');ui.click('submit_next');await flush();
  assert.equal(sent[2].action,'submit');assert.equal(sent[2].note,'最终输入');
  assert.deepEqual(ui.navigations,['/r/1/?a=2']);
});
test('confirm and next errors retain text without navigating; autosave never advances',async()=>{
  for(const status of [400,409,503]) {
    const ui=setup(async()=>response(null,status,'请核对输入'));
    ui.edit('不能丢');ui.click('submit_next');await flush();
    assert.equal(ui.fields.note.value,'不能丢');assert.equal(ui.navigations.length,0);
  }
  const ui=setup(async()=>response({revision:1,status:'draft'}));
  ui.edit('只保存');ui.autoSave();await flush();assert.equal(ui.navigations.length,0);
});
test('confirm and next refuses an external or unrelated destination',async()=>{
  for(const target of ['https://other.test/', '//other.test/r/1/', '/logout/', '/r/1/?a=2&evil=1']) {
    const ui=setup(async()=>response({revision:1,status:'submitted'}),{},'annotation',target);
    ui.click('submit_next');await flush();assert.equal(ui.navigations.length,0);
  }
});
