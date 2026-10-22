// Controller unit tests: fake DOM only, no browser or Windows automation.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');

function setup(fetchImplementation, initial = {}, mode = 'annotation', nextUrl = '/r/1/?a=2', formState = {}) {
  const listeners = {}, windowListeners = {}, documentListeners = {}, timers = new Map();
  const fields = Object.fromEntries(['primary','note','no_code','uncertain','csrfmiddlewaretoken'].map(name => [name, {name,value:'',checked:false,disabled:false,handlers:{},addEventListener(type,fn){this.handlers[type]=fn;},dispatchEvent(event){this.handlers[event.type]?.({...event,target:this});listeners[event.type]?.({target:this});}}]));
  fields.csrfmiddlewaretoken.value = 'test-token';
  const secondary = ['2','3'].map(value => ({name:'secondary',value,checked:false,disabled:false,parentElement:{textContent:'代码 '+value},dispatchEvent(){listeners.change({target:this});}}));
  const buttons = [{disabled:false,dataset:{action:'submit_next'}}, {disabled:false,dataset:{action:'save'}}];
  const codeButtons = [{disabled:false}];
  const legacyNotice = {hidden:true,textContent:''};
  const reviewMarker = {textContent:'',dataset:{}};
  const status = {textContent:'', className:''};
  const navigations = [];
  const confirmations = [];
  let confirmAnswer = true;
  const form = {dataset:{endpoint:'/api/test/',mode,locked:'false',nextUrl,...formState},
    addEventListener: (name, fn) => listeners[name] = fn,
    querySelector: selector => selector === '.save-status' ? status : selector === '[data-legacy-secondary]' ? legacyNotice : selector === '[data-review-marker]' ? reviewMarker : fields[selector.match(/name=([^\]]+)/)?.[1]],
    querySelectorAll: selector => {
      if (selector === '[name=secondary]:checked') return secondary.filter(x => x.checked);
      if (selector === '[name=secondary]') return secondary;
      if (selector === 'button[data-action]') return buttons;
      if (selector === 'button') return buttons.concat(codeButtons);
      const inputs = Object.entries(fields).filter(([k]) => k !== 'csrfmiddlewaretoken').map(([,v]) => v).concat(secondary);
      if (selector.includes('button')) return inputs.concat(buttons,codeButtons);
      return inputs;
    }};
  const context = {document:{hidden:false,querySelector:()=>form,querySelectorAll:()=>[],addEventListener:(name,fn)=>documentListeners[name]=fn,
    getElementById: id => id === 'annotation-data' ? {textContent:JSON.stringify({revision:0,primary:1,secondary:[],...initial})} : null},
    location:{pathname:'/fixture/',search:'',origin:'http://localhost',href:'http://localhost/fixture/',assign: url=>navigations.push(url)},
    window:{addEventListener:(name,fn)=>windowListeners[name]=fn,confirm:message=>{confirmations.push(message);return confirmAnswer;}},
    fetch:fetchImplementation, URL, Promise, Date, Math,Event:class{constructor(type){this.type=type;}},
    setTimeout: fn => {const id=Math.random();timers.set(id,fn);return id;},clearTimeout:id=>timers.delete(id),setInterval:()=>{}};
  vm.runInNewContext(source, context);
  const link = (href, attributes = {}, onDefault = () => {}) => {
    const anchor = {id:attributes.id || '',href:new URL(href,context.location.href).href,
      getAttribute:name=>name==='href'?href:attributes[name] ?? null,
      hasAttribute:name=>Object.hasOwn(attributes,name),
      click(overrides={}) {
        const event = {target:{closest:()=>anchor},button:0,defaultPrevented:false,
          preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){this.stopped=true;},...overrides};
        documentListeners.click(event);
        if(!event.defaultPrevented)onDefault(event);
        return event;
      }};
    return anchor;
  };
  const submitEvent = (target, submitter, overrides={}) => {
    const event = {target,submitter,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},
      stopImmediatePropagation(){this.stopped=true;},...overrides};
    documentListeners.submit(event);
    return event;
  };
  const leavingForm = (action='/logout/', target='', onSubmit=()=>{}) => {
    const requests=[];
    const other = {action:new URL(action,context.location.href).href,target,requests,
      requestSubmit(submitter) {
        requests.push(submitter);
        const event=submitEvent(other,submitter);
        if(!event.defaultPrevented)onSubmit(event);
      },
      submit(){throw new Error('Raw submit bypasses validation');},
      button(attributes={}) {
        return {name:attributes.name || '',value:attributes.value || '',
          hasAttribute:name=>Object.hasOwn(attributes,name),
          formAction:new URL(attributes.formaction || action,context.location.href).href,
          formTarget:attributes.formtarget || ''};
      },
      send(submitter,overrides={}) {const event=submitEvent(other,submitter,overrides);if(!event.defaultPrevented)onSubmit(event);return event;}};
    return other;
  };
  return {fields,secondary,status,buttons,codeButtons,navigations,form,legacyNotice,reviewMarker,confirmations,link,leavingForm,submitEvent,
    preview: enabled => {form.dataset.splitPreview=String(enabled);windowListeners['platform:split-preview-change']();},
    confirm:answer=>{confirmAnswer=answer;},
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

test('split preview pauses manual and keyboard next while draft saving remains available',async()=>{
  let calls=0; const ui=setup(async()=>{calls++;return response({revision:calls,status:'draft'});});
  ui.preview(true); assert.equal(ui.buttons[0].disabled,true); assert.equal(ui.buttons[1].disabled,false);
  ui.click('submit_next'); await flush(); assert.equal(calls,0);
  await assert.rejects(ui.form.platformSubmitNext(),/拆分/); assert.equal(calls,0);
  ui.click('save'); await flush(); assert.equal(calls,1); assert.equal(ui.navigations.length,0);
  ui.preview(false); assert.equal(ui.buttons[0].disabled,false); assert.equal(ui.buttons[1].disabled,false);
});

test('starting split preview during a queued confirm prevents a later stale submission',async()=>{
  const requests=[]; let complete;
  const ui=setup(body=>{requests.push(body);return new Promise(resolve=>complete=resolve);});
  ui.edit('正在保存'); const next=ui.form.platformSubmitNext(); await flush();
  assert.equal(requests.length,1); ui.preview(true);
  complete(response({revision:1,status:'draft'}));
  await assert.rejects(next,/不可提交/); assert.equal(requests.length,1); assert.equal(ui.navigations.length,0);
  assert.equal(ui.fields.note.value,'正在保存');
});

test('ready next-task reminder owns navigation before the quick-mode unsaved link guard',async()=>{
  let calls=0,forwarded=0;
  const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},{},'annotation','/r/1/?a=2',
    {quickMode:'true',nextReminderReady:'true'});
  ui.edit('先保留编辑');
  const event=ui.link('/r/1/?a=2',{'data-coding-next':''},()=>forwarded++).click();
  assert.equal(event.defaultPrevented,false);assert.equal(forwarded,1);assert.equal(ui.confirmations.length,0);
  await flush();assert.equal(calls,0);assert.equal(ui.navigations.length,0);assert.equal(ui.fields.note.value,'先保留编辑');
});

test('next-task marker without a ready controller retains existing save-before-leave protection',async()=>{
  let calls=0;
  const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},{},'annotation','/r/1/?a=2',
    {quickMode:'true',nextReminderReady:'false'});
  ui.edit('尚未保存');
  assert.equal(ui.link('/r/1/?a=2',{'data-coding-next':''}).click().defaultPrevented,true);
  await flush();assert.equal(ui.confirmations.length,1);assert.equal(calls,1);assert.deepEqual(ui.navigations,['/r/1/?a=2']);
});

test('empty coding starts disabled, selecting enables and clearing disables both manual buttons',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},{primary:null});
  assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.click('save');ui.click('submit_next');await flush();assert.equal(calls,0);
  ui.choosePrimary('1');assert.equal(ui.buttons.every(button=>!button.disabled),true);
  ui.choosePrimary('');assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.edit('尚未选代码的备注');ui.autoSave();await flush();assert.equal(calls,1);
  assert.equal(ui.buttons.every(button=>button.disabled),true);
});

test('clearing a saved primary also clears its secondary and autosaves an empty draft without erasing notes',async()=>{
  const sent=[];
  const ui=setup(async(_url,options)=>{const data=JSON.parse(options.body);sent.push(data);return response({revision:2,status:'draft'});},
    {primary:1,secondary:[2],note:'保留判断依据',revision:1});
  ui.choosePrimary('');ui.autoSave();await flush();
  assert.equal(sent.length,1);assert.equal(sent[0].action,'save');
  assert.equal(sent[0].primary,null);assert.deepEqual(sent[0].secondary,[]);
  assert.equal(sent[0].note,'保留判断依据');assert.equal(ui.secondary.some(input=>input.checked),false);
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
test('review label distinguishes untouched and note-only drafts from actual judgments',()=>{
  const ui=setup(async()=>response({revision:1,status:'draft'}),{primary:null,note:''});
  assert.equal(ui.reviewMarker.textContent,'未判断');
  ui.edit('先记下上下文');assert.equal(ui.reviewMarker.textContent,'未判断');
  ui.choosePrimary('1');assert.equal(ui.reviewMarker.textContent,'已确认');
  ui.choosePrimary('');assert.equal(ui.reviewMarker.textContent,'未判断');
  ui.chooseNoCode(true);assert.equal(ui.reviewMarker.textContent,'已确认');
  ui.chooseNoCode(false);assert.equal(ui.reviewMarker.textContent,'未判断');
});
test('review label preserves uncertain boolean meaning and still requires a reason',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},{primary:null});
  ui.fields.uncertain.checked=true;ui.fields.uncertain.dispatchEvent({type:'change'});
  assert.equal(ui.reviewMarker.textContent,'需复核');assert.equal(ui.reviewMarker.dataset.reviewState,'review');
  assert.equal(ui.buttons.every(button=>button.disabled),true);
  ui.edit('待核对这句的指代');assert.equal(ui.buttons.every(button=>!button.disabled),true);
  ui.click('save');await flush();assert.equal(calls,1);assert.equal(ui.fields.uncertain.checked,true);
  assert.equal(ui.reviewMarker.textContent,'需复核');
  ui.fields.uncertain.checked=false;ui.fields.uncertain.dispatchEvent({type:'change'});
  assert.equal(ui.reviewMarker.textContent,'未判断');
  ui.choosePrimary('1');assert.equal(ui.reviewMarker.textContent,'已确认');
});
test('readonly and submitted records keep their own review marker independently of save status',async()=>{
  const readonly=setup(async()=>response({revision:1,status:'draft'}),{primary:1,uncertain:true,revision:1},'annotation','',{locked:'true'});
  assert.equal(readonly.reviewMarker.textContent,'需复核');assert.equal(readonly.fields.primary.disabled,true);
  const submitted=setup(async()=>response({revision:2,status:'submitted'}),{primary:1,uncertain:false,revision:1});
  submitted.click('submit');await flush();assert.match(submitted.status.textContent,/已提交/);
  assert.equal(submitted.reviewMarker.textContent,'已确认');assert.equal(submitted.fields.uncertain.checked,false);
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
test('saving-draft marks only an in-flight editable draft and clears after success',async()=>{
  let finish;const ui=setup(()=>new Promise(resolve=>finish=resolve));
  assert.equal(ui.form.dataset.savingDraft,'false');
  ui.edit('鼠标修改后的自动保存');ui.autoSave();
  assert.equal(ui.form.dataset.busy,'true');assert.equal(ui.form.dataset.savingDraft,'true');
  assert.equal(ui.fields.primary.disabled,false);assert.equal(ui.fields.note.disabled,false);
  assert.equal(ui.secondary.every(input=>!input.disabled),true);assert.equal(ui.buttons.every(button=>button.disabled),true);
  finish(response({revision:1,status:'draft'}));await flush();
  assert.equal(ui.form.dataset.busy,'false');assert.equal(ui.form.dataset.savingDraft,'false');
  assert.equal(ui.fields.primary.disabled,false);assert.equal(ui.isDirty(),false);
});
test('submitting and advancing never advertise an editable draft even while flushing one',async()=>{
  let finish;const direct=setup(()=>new Promise(resolve=>finish=resolve));
  direct.click('submit');
  assert.equal(direct.form.dataset.busy,'true');assert.equal(direct.form.dataset.savingDraft,'false');
  assert.equal(direct.fields.primary.disabled,true);assert.equal(direct.fields.note.disabled,true);
  finish(response({revision:1,status:'submitted'}));await flush();
  assert.equal(direct.form.dataset.busy,'false');assert.equal(direct.form.dataset.savingDraft,'false');
  const sent=[],releases=[];const ui=setup((url,options)=>{
    sent.push(JSON.parse(options.body));return new Promise(resolve=>releases.push(resolve));
  });
  ui.edit('确认之前先保存');const submitted=ui.form.platformSubmitNext();
  assert.equal(sent[0].action,'save');assert.equal(ui.form.dataset.busy,'true');
  assert.equal(ui.form.dataset.savingDraft,'false');assert.equal(ui.fields.primary.disabled,true);
  releases.shift()(response({revision:1,status:'draft'}));await flush();
  assert.equal(sent[1].action,'submit');assert.equal(ui.form.dataset.savingDraft,'false');
  assert.equal(ui.fields.primary.disabled,true);assert.equal(ui.fields.note.disabled,true);
  releases.shift()(response({revision:2,status:'submitted'}));assert.equal(await submitted,true);
  assert.equal(ui.form.dataset.busy,'false');assert.equal(ui.form.dataset.savingDraft,'false');
});
test('failed draft saves clear the editable-draft flag without unlocking conflicts',async()=>{
  for(const failure of [400,409,503,'offline']) {
    let finish,fail;const ui=setup(()=>new Promise((resolve,reject)=>{finish=resolve;fail=reject;}));
    ui.edit('失败时保留输入');ui.autoSave();assert.equal(ui.form.dataset.savingDraft,'true',String(failure));
    if(failure==='offline')fail(new Error('offline'));else finish(response(null,failure,'保存失败'));
    await flush();
    assert.equal(ui.form.dataset.busy,'false',String(failure));assert.equal(ui.form.dataset.savingDraft,'false',String(failure));
    assert.equal(ui.fields.primary.disabled,failure===409,String(failure));
    assert.equal(ui.fields.note.value,'失败时保留输入');assert.equal(ui.isDirty(),true);
  }
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

test('navigation flush waits for an in-flight draft and saves the latest edits without submitting',async()=>{
  let finish;const sent=[];
  const ui=setup((url,options)=>{
    sent.push(JSON.parse(options.body));
    return sent.length===1?new Promise(resolve=>finish=resolve):Promise.resolve(response({revision:sent.length,status:'draft'}));
  });
  ui.edit('自动保存的草稿');ui.autoSave();ui.edit('跳过前的新备注');
  const saved=ui.form.platformSaveBeforeNavigation();
  assert.equal(sent.length,1);assert.equal(ui.canReturn(),false);
  finish(response({revision:1,status:'draft'}));
  assert.equal(await saved,2);assert.equal(sent.length,2);
  assert.equal(sent[1].note,'跳过前的新备注');assert.equal(sent[1].revision,1);
  assert.equal(sent.every(item=>item.action==='save'),true);assert.equal(ui.canReturn(),true);
  assert.equal(ui.navigations.length,0);
});

test('navigation flush completes an idempotent old draft before saving all newer edits',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{
    sent.push(JSON.parse(options.body));
    if(sent.length===1)throw new Error('offline');
    return response({revision:sent.length-1,status:'draft'});
  });
  ui.edit('上一次的草稿');ui.autoSave();await flush();ui.edit('当前新判断');
  assert.equal(await ui.form.platformSaveBeforeNavigation(),2);
  assert.equal(sent.length,3);assert.equal(sent[0].token,sent[1].token);
  assert.equal(sent[2].note,'当前新判断');assert.equal(sent[2].revision,1);
  assert.equal(ui.isDirty(),false);assert.equal(ui.navigations.length,0);
});

test('a failed in-flight navigation flush stops without retrying or discarding input',async()=>{
  for(const code of [400,409,503]) {
    let finish;let calls=0;
    const ui=setup(()=>{calls++;return new Promise(resolve=>finish=resolve);});
    ui.edit('必须保留的输入');ui.autoSave();
    const saved=ui.form.platformSaveBeforeNavigation();
    finish(response(null,code,'未保存成功'));
    await assert.rejects(saved,/尚未保存/);await flush();ui.autoSave();await flush();
    assert.equal(calls,1);assert.equal(ui.fields.note.value,'必须保留的输入');
    assert.equal(ui.navigations.length,0);assert.equal(ui.canReturn(),false);
  }
});

test('keyboard confirm requires a primary code, not only a no-code or uncertain judgment',async()=>{
  for(const judgment of [{primary:null},{primary:null,no_code:true},{primary:null,uncertain:true,note:'待确认'}]) {
    let calls=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'submitted'});},judgment);
    await assert.rejects(ui.form.platformSubmitNext(),/主代码/);
    assert.equal(calls,0);assert.equal(ui.navigations.length,0);
  }
});

test('keyboard confirm allows an empty secondary and navigates only after successful submission',async()=>{
  let finish;const sent=[];
  const ui=setup((url,options)=>{sent.push(JSON.parse(options.body));return new Promise(resolve=>finish=resolve);});
  const submitted=ui.form.platformSubmitNext();await flush();
  assert.equal(sent.length,1);assert.equal(sent[0].primary,1);assert.deepEqual(sent[0].secondary,[]);
  assert.equal(sent[0].action,'submit');assert.equal(ui.navigations.length,0);
  finish(response({revision:1,status:'submitted'}));
  assert.equal(await submitted,true);assert.deepEqual(ui.navigations,['/r/1/?a=2']);
});

test('keyboard confirm queues behind autosave, flushes newer choices, and submits the current revision once',async()=>{
  const releases=[];const sent=[];
  const ui=setup((url,options)=>{sent.push(JSON.parse(options.body));return new Promise(resolve=>releases.push(resolve));});
  ui.edit('自动保存中的原判断');ui.autoSave();
  ui.choosePrimary('4');ui.edit('按两次 Enter 时的新判断');
  const submitted=ui.form.platformSubmitNext();
  const duplicate=ui.form.platformSubmitNext();
  assert.equal(submitted,duplicate);assert.equal(sent.length,1);
  assert.equal(ui.fields.primary.disabled,true);assert.equal(ui.form.dataset.busy,'true');
  releases.shift()(response({revision:1,status:'draft'}));await flush();
  assert.equal(sent.length,2);assert.equal(sent[1].action,'save');
  assert.equal(sent[1].primary,4);assert.equal(sent[1].note,'按两次 Enter 时的新判断');
  releases.shift()(response({revision:2,status:'draft'}));await flush();
  assert.equal(sent.length,3);assert.equal(sent[2].action,'submit');assert.equal(sent[2].revision,2);
  assert.equal(ui.navigations.length,0);
  releases.shift()(response({revision:3,status:'submitted'}));assert.equal(await submitted,true);
  assert.deepEqual(ui.navigations,['/r/1/?a=2']);ui.autoSave();await flush();assert.equal(sent.length,3);
});

test('keyboard confirm fails safely when draft flushing fails and does not submit stale content',async()=>{
  for(const code of [400,409,503]) {
    const sent=[];const ui=setup(async(url,options)=>{sent.push(JSON.parse(options.body));return response(null,code,'保存被拒绝');});
    ui.edit('不能跳过的新备注');
    await assert.rejects(ui.form.platformSubmitNext(),/尚未保存/);ui.autoSave();await flush();
    assert.equal(sent.length,1);assert.equal(sent[0].action,'save');assert.match(ui.status.textContent,/保存被拒绝/);
    assert.equal(ui.fields.note.value,'不能跳过的新备注');assert.equal(ui.navigations.length,0);
  }
});

test('keyboard confirm waits for a failed autosave without silently retrying or submitting',async()=>{
  let fail;let calls=0;const ui=setup(()=>{calls++;return new Promise((resolve,reject)=>fail=reject);});
  ui.edit('保留网络失败后的判断');ui.autoSave();
  const submitted=ui.form.platformSubmitNext();fail(new Error('offline'));
  await assert.rejects(submitted,/尚未保存/);await flush();ui.autoSave();await flush();
  assert.equal(calls,1);assert.match(ui.status.textContent,/offline/);assert.equal(ui.navigations.length,0);
});

test('keyboard confirm keeps input and stays put on submission errors without an automatic retry',async()=>{
  for(const code of [400,409,503]) {
    let calls=0;const ui=setup(async()=>{calls++;return response(null,code,'提交未确认');});
    await assert.rejects(ui.form.platformSubmitNext(),/提交未确认/);await flush();ui.autoSave();await flush();
    assert.equal(calls,1);assert.equal(ui.fields.primary.value,1);assert.equal(ui.navigations.length,0);
    assert.match(ui.status.textContent,/提交未确认/);
  }
});

test('keyboard confirm refuses locked, splitting, conflicting, and already submitted records',async()=>{
  let calls=0;const send=async()=>{calls++;return response({revision:1,status:'submitted'});};
  const locked=setup(send,{},'annotation','/r/1/?a=2',{locked:'true'});
  await assert.rejects(locked.form.platformSubmitNext(),/不可提交/);
  const splitting=setup(send);splitting.form.dataset.splitting='true';
  await assert.rejects(splitting.form.platformSubmitNext(),/不可提交/);
  assert.equal(calls,0);
  const conflict=setup(async()=>response(null,409,'发生冲突'));conflict.edit('冲突');conflict.click('save');await flush();
  await assert.rejects(conflict.form.platformSubmitNext(),/不可提交/);
  const committed=setup(send);await committed.form.platformSubmitNext();
  await assert.rejects(committed.form.platformSubmitNext(),/不可提交/);assert.equal(calls,1);
});

test('navigation flush refuses an unresolved submission and keyboard retry reuses its token',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{
    sent.push(JSON.parse(options.body));
    return sent.length===1?response(null,503,'稍后重试'):response({revision:1,status:'submitted'});
  });
  await assert.rejects(ui.form.platformSubmitNext(),/稍后重试/);
  await assert.rejects(ui.form.platformSaveBeforeNavigation(),/正在提交/);
  assert.equal(await ui.form.platformSubmitNext(),true);assert.equal(sent.length,2);
  assert.equal(sent[0].token,sent[1].token);assert.equal(sent[1].action,'submit');
  assert.deepEqual(ui.navigations,['/r/1/?a=2']);
});

test('keyboard confirm still refuses an external or unrelated successful destination',async()=>{
  for(const target of ['https://other.test/','//other.test/r/1/','/logout/','/r/1/?a=2&evil=1']) {
    const ui=setup(async()=>response({revision:1,status:'submitted'}),{},'annotation',target);
    assert.equal(await ui.form.platformSubmitNext(),true);assert.equal(ui.navigations.length,0);
  }
});

test('a forced navigation save records an untouched empty skip as a draft without submitting',async()=>{
  const sent=[];const ui=setup(async(url,options)=>{
    sent.push(JSON.parse(options.body));return response({revision:1,status:'draft'});
  },{primary:null});
  assert.equal(ui.isDirty(),false);
  assert.equal(await ui.form.platformSaveBeforeNavigation({force:true}),1);
  assert.equal(sent.length,1);assert.equal(sent[0].action,'save');assert.equal(sent[0].primary,null);
  assert.deepEqual(sent[0].secondary,[]);assert.equal(sent[0].note,'');
  assert.equal(ui.isDirty(),false);assert.equal(ui.navigations.length,0);
});

test('quick-mode links ask first and leave only after the draft is acknowledged',async()=>{
  let finish;const sent=[];const ui=setup((url,options)=>{
    sent.push(JSON.parse(options.body));return new Promise(resolve=>finish=resolve);
  },{},'annotation','/r/1/?a=2',{quickMode:'true'});
  ui.edit('离开前的备注');const event=ui.link('/p/1/history/?q=test#record').click();
  assert.equal(event.defaultPrevented,true);assert.equal(event.stopped,true);assert.equal(ui.confirmations.length,1);
  assert.match(ui.confirmations[0],/保存当前编码后离开/);assert.equal(sent.length,1);
  assert.equal(sent[0].action,'save');assert.equal(sent[0].note,'离开前的备注');assert.equal(ui.navigations.length,0);
  finish(response({revision:1,status:'draft'}));await flush();
  assert.deepEqual(ui.navigations,['/p/1/history/?q=test#record']);assert.equal(ui.isDirty(),false);
  assert.equal(ui.form.dataset.quickMode,'true');
});

test('cancelling a quick-mode page change neither saves nor leaves and keeps the input',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  ui.edit('用户取消后仍保留');ui.confirm(false);const event=ui.link('/projects/').click();await flush();
  assert.equal(event.defaultPrevented,true);assert.equal(ui.confirmations.length,1);assert.equal(calls,0);
  assert.equal(ui.fields.note.value,'用户取消后仍保留');assert.equal(ui.isDirty(),true);assert.equal(ui.navigations.length,0);
});

test('quick-mode leave waits for in-flight autosave and all newer mouse edits before navigating',async()=>{
  const releases=[],sent=[];const ui=setup((url,options)=>{
    sent.push(JSON.parse(options.body));return new Promise(resolve=>releases.push(resolve));
  },{},'annotation','/r/1/?a=2',{quickMode:'true'});
  ui.edit('原判断');ui.autoSave();ui.choosePrimary('4');ui.edit('新判断');
  const target=ui.link('/p/1/exports/');target.click();target.click();
  assert.equal(sent.length,1);assert.equal(ui.confirmations.length,1);
  releases.shift()(response({revision:1,status:'draft'}));await flush();
  assert.equal(sent.length,2);assert.equal(sent[1].primary,4);assert.equal(sent[1].note,'新判断');
  assert.equal(sent[1].revision,1);assert.equal(sent.every(item=>item.action==='save'),true);
  assert.equal(ui.navigations.length,0);
  releases.shift()(response({revision:2,status:'draft'}));await flush();
  assert.deepEqual(ui.navigations,['/p/1/exports/']);assert.equal(ui.isDirty(),false);
});

test('quick-mode failed navigation save stays on the page with its current input and error',async()=>{
  for(const code of [400,409,503,'offline']) {
    let calls=0;const ui=setup(async()=>{
      calls++;if(code==='offline')throw new Error('offline');return response(null,code,'未保存成功');
    },{},'annotation','/r/1/?a=2',{quickMode:'true'});
    ui.edit('不能丢失的当前编码');ui.link('/projects/').click();await flush();
    assert.equal(calls,1);assert.equal(ui.navigations.length,0);assert.equal(ui.isDirty(),true);
    assert.equal(ui.fields.note.value,'不能丢失的当前编码');assert.match(ui.status.textContent,/未保存成功|offline/);
    assert.equal(ui.form.dataset.quickMode,'true');
  }
});

test('quick-mode page-back replays its own history handler once after saving, not before',async()=>{
  let finish;let backCalls=0;const ui=setup(()=>new Promise(resolve=>finish=resolve),
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  const back=ui.link('/p/1/',{id:'page-back'},()=>{assert.equal(ui.canReturn(),true);backCalls++;});
  ui.edit('返回前先保存');back.click();assert.equal(backCalls,0);assert.equal(ui.confirmations.length,1);
  finish(response({revision:1,status:'draft'}));await flush();
  assert.equal(backCalls,1);assert.equal(ui.confirmations.length,1);assert.equal(ui.navigations.length,0);
});

test('quick-mode clean links and non-navigation anchors do not open a save confirmation',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  assert.equal(ui.link('/projects/').click().defaultPrevented,false);
  ui.edit('仍保留在本页');
  for(const [href,attributes,eventOptions] of [
    ['#code-editor',{},{}],['/fixture/#code-editor',{},{}],['/p/1/exports/',{download:''},{}],
    ['/projects/',{target:'_blank'},{}],['/projects/',{'aria-disabled':'true'},{}],
    ['https://other.test/',{},{}],['mailto:test@example.test',{},{}],
    ['/projects/',{},{ctrlKey:true}],['/projects/',{},{button:1}],['/projects/',{},{defaultPrevented:true}]
  ])ui.link(href,attributes).click(eventOptions);
  await flush();assert.equal(ui.confirmations.length,0);assert.equal(calls,0);assert.equal(ui.navigations.length,0);
  assert.equal(ui.isDirty(),true);
});

test('outside quick mode normal links retain the existing unsaved-unload protection',async()=>{
  const ui=setup(async()=>response({revision:1,status:'draft'}));ui.edit('普通模式的草稿');
  assert.equal(ui.link('/projects/').click().defaultPrevented,false);
  assert.equal(ui.confirmations.length,0);assert.equal(ui.isDirty(),true);
});

test('quick-mode form navigation saves first and replays only the original form and submitter once',async()=>{
  let finish;const sent=[];let submissions=0;const ui=setup((url,options)=>{
    sent.push(JSON.parse(options.body));return new Promise(resolve=>finish=resolve);
  },{},'annotation','/r/1/?a=2',{quickMode:'true'});
  const other=ui.leavingForm('/r/1/manage/','',event=>{
    assert.equal(ui.isDirty(),false);assert.equal(event.submitter,button);submissions++;
  });
  const button=other.button({name:'action',value:'lock'});
  ui.edit('先保存，不能直接锁定');const event=other.send(button);
  assert.equal(event.defaultPrevented,true);assert.equal(event.stopped,true);assert.equal(submissions,0);
  assert.equal(other.requests.length,0);assert.equal(ui.confirmations.length,1);
  assert.equal(sent.length,1);assert.equal(sent[0].action,'save');
  finish(response({revision:1,status:'draft'}));await flush();
  assert.equal(submissions,1);assert.deepEqual(other.requests,[button]);assert.equal(ui.confirmations.length,1);
  assert.equal(button.name,'action');assert.equal(button.value,'lock');assert.equal(ui.navigations.length,0);
});

test('cancelling quick-mode form navigation keeps the page and never replays the form',async()=>{
  let calls=0;let submissions=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  const other=ui.leavingForm('/logout/','',()=>submissions++);
  ui.edit('取消退出后还在');ui.confirm(false);const event=other.send(other.button());await flush();
  assert.equal(event.defaultPrevented,true);assert.equal(ui.confirmations.length,1);
  assert.equal(calls,0);assert.equal(submissions,0);assert.equal(other.requests.length,0);
  assert.equal(ui.fields.note.value,'取消退出后还在');assert.equal(ui.isDirty(),true);
});

test('quick-mode form navigation stays put without replay after a failed save',async()=>{
  for(const code of [400,409,503,'offline']) {
    let calls=0;let submissions=0;const ui=setup(async()=>{
      calls++;if(code==='offline')throw new Error('offline');return response(null,code,'未保存成功');
    },{},'annotation','/r/1/?a=2',{quickMode:'true'});
    const other=ui.leavingForm('/logout/','',()=>submissions++);
    ui.edit('退出失败也不能丢');other.send(other.button());await flush();
    assert.equal(calls,1);assert.equal(submissions,0);assert.equal(other.requests.length,0);
    assert.equal(ui.fields.note.value,'退出失败也不能丢');assert.equal(ui.isDirty(),true);
    assert.match(ui.status.textContent,/未保存成功|offline/);
  }
});

test('form and link navigation share one pending save and never perform a second requested action',async()=>{
  let finish;let submissions=0;let secondSubmissions=0;const sent=[];const ui=setup((url,options)=>{
    sent.push(JSON.parse(options.body));return new Promise(resolve=>finish=resolve);
  },{},'annotation','/r/1/?a=2',{quickMode:'true'});
  const chosen=ui.leavingForm('/logout/','',()=>submissions++);
  const second=ui.leavingForm('/r/1/manage/','',()=>secondSubmissions++);
  const button=chosen.button();ui.edit('防止重复离开');chosen.send(button);chosen.send(button);
  second.send(second.button({name:'action',value:'lock'}));ui.link('/projects/').click();
  assert.equal(sent.length,1);assert.equal(ui.confirmations.length,1);assert.equal(submissions,0);
  finish(response({revision:1,status:'draft'}));await flush();
  assert.equal(submissions,1);assert.equal(secondSubmissions,0);assert.equal(second.requests.length,0);
  assert.equal(chosen.requests.length,1);assert.equal(ui.navigations.length,0);
});

test('a link-first save never replays a later competing form submission',async()=>{
  let finish;let submissions=0;const ui=setup(()=>new Promise(resolve=>finish=resolve),
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  const other=ui.leavingForm('/logout/','',()=>submissions++);
  ui.edit('只执行最先选择的跳转');ui.link('/projects/').click();other.send(other.button());
  assert.equal(ui.confirmations.length,1);finish(response({revision:1,status:'draft'}));await flush();
  assert.deepEqual(ui.navigations,['/projects/']);assert.equal(submissions,0);assert.equal(other.requests.length,0);
});

test('quick-mode form navigation without a submitter uses requestSubmit without fabricating an action',async()=>{
  let submissions=0;const ui=setup(async()=>response({revision:1,status:'draft'}),
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  const other=ui.leavingForm('/logout/','',event=>{assert.equal(event.submitter,undefined);submissions++;});
  ui.edit('键盘提交退出表单');other.send();await flush();
  assert.equal(submissions,1);assert.deepEqual(other.requests,[undefined]);
});

test('annotation submits, already prevented events and clean or non-quick forms are not intercepted',()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  const other=ui.leavingForm();assert.equal(other.send(other.button()).defaultPrevented,false);
  ui.edit('自己的编码表单');assert.equal(ui.submitEvent(ui.form).defaultPrevented,false);
  const alreadyPrevented=other.send(other.button(),{defaultPrevented:true});assert.equal(alreadyPrevented.stopped,undefined);
  ui.form.dataset.quickMode='false';assert.equal(other.send(other.button()).defaultPrevented,false);
  assert.equal(calls,0);assert.equal(ui.confirmations.length,0);assert.equal(ui.isDirty(),true);
});

test('quick-mode form interception respects external actions, new windows and submitter overrides',async()=>{
  let calls=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  ui.edit('仅拦截本窗口的站内表单');
  const external=ui.leavingForm('https://other.test/');assert.equal(external.send(external.button()).defaultPrevented,false);
  const newWindow=ui.leavingForm('/logout/','_blank');assert.equal(newWindow.send(newWindow.button()).defaultPrevented,false);
  const other=ui.leavingForm('/logout/');
  assert.equal(other.send(other.button({formaction:'https://other.test/'})).defaultPrevented,false);
  assert.equal(other.send(other.button({formtarget:'_blank'})).defaultPrevented,false);
  assert.equal(ui.confirmations.length,0);assert.equal(calls,0);
  const overridden=external.button({formaction:'/logout/',formtarget:'_self'});
  assert.equal(external.send(overridden).defaultPrevented,true);await flush();
  assert.equal(calls,1);assert.deepEqual(external.requests,[overridden]);assert.equal(ui.confirmations.length,1);
});

test('a browser without safe form replay retains unsaved edits instead of using raw submit',async()=>{
  let calls=0;let submissions=0;const ui=setup(async()=>{calls++;return response({revision:1,status:'draft'});},
    {},'annotation','/r/1/?a=2',{quickMode:'true'});
  const other=ui.leavingForm('/logout/','',()=>submissions++);other.requestSubmit=undefined;
  ui.edit('先保留输入');const event=other.send(other.button());await flush();
  assert.equal(event.defaultPrevented,true);assert.equal(submissions,0);assert.equal(calls,0);
  assert.match(ui.status.textContent,/无法安全继续/);assert.equal(ui.isDirty(),true);
});
