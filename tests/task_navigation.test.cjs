const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../static/task_navigation.js'),'utf8');
function setup({data={my_remaining:2,next_unfinished_url:'/r/7/?a=3'},fetcher,save=async()=>0,locked=false,modal=true,state={}}={}) {
  function node(extra={}) { return {disabled:false,hidden:false,open:false,handlers:{},dataset:{},textContent:'',className:'',
    addEventListener(type,fn){this.handlers[type]=fn;},click(){if(!this.disabled)return this.handlers.click?.();},focus(){this.focused=true;},...extra}; }
  let requests=0; const saved=[],navigations=[],events=[];
  const button=node({disabled:true}),status=node(),resume=node(),saveButton=node(),cancel=node(),retry=node();
  const nodes={'[data-task-completion-status]':status,'[data-task-continue]':resume,'[data-task-save]':saveButton,'[data-task-cancel]':cancel,'[data-task-retry]':retry};
  const dialog=node({dataset:{progressUrl:'/api/rounds/7/progress/?current=5',projectsUrl:'/projects/'},
    querySelector:s=>nodes[s],showModal:modal?function(){this.open=true;}:undefined,close(){this.open=false;}});
  const form=node({dataset:{locked:String(locked),shortcutScope:'7',...state},
    platformSaveBeforeNavigation(options){saved.push(options);return save(options);}});
  const document={querySelector:s=>({'[data-coding-last]':button,'[data-task-completion]':dialog,'.annotation-form':form}[s]||null)};
  const response=data=>({ok:true,headers:{get:()=> 'application/json'},json:async()=>data});
  vm.runInNewContext(source,{document,window:{dispatchEvent:e=>events.push(e.type)},Event:class{constructor(type){this.type=type;}},
    location:{assign:url=>navigations.push(url)},fetch:async(...args)=>{requests++;return fetcher?fetcher(...args):response(data);}});
  return {button,status,resume,saveButton,cancel,retry,dialog,form,saved,navigations,events,requests:()=>requests};
}
async function flush(){for(let i=0;i<18;i++)await Promise.resolve();}
test('last next checks fresh progress without saving or submitting, then resumes a private draft',async()=>{
  const ui=setup();ui.button.click();await flush();assert.equal(ui.dialog.open,true);
  assert.match(ui.status.textContent,/2 条未完成/);assert.equal(ui.saved.length,0);
  assert.deepEqual(ui.events,['platform:task-completion-open']);
  await ui.resume.click();assert.deepEqual(ui.navigations,['/r/7/?a=3']);assert.equal(ui.saved[0].force,true);
});
test('save task acknowledges current draft then goes to My Projects',async()=>{
  let finish;const ui=setup({save:()=>new Promise(resolve=>finish=resolve)});ui.button.click();await flush();
  ui.saveButton.click();ui.saveButton.click();ui.resume.click();assert.equal(ui.saved.length,1);assert.equal(ui.navigations.length,0);
  finish(0);await flush();assert.deepEqual(ui.navigations,['/projects/']);assert.equal(ui.form.dataset.locked,'false');
});
test('completed readonly task does not force a write and displays green completion',async()=>{
  const ui=setup({locked:true,data:{my_remaining:0,next_unfinished_url:''}});ui.button.click();await flush();
  assert.equal(ui.resume.disabled,true);assert.match(ui.status.textContent,/全部完成/);assert.equal(ui.status.className,'task-complete-success');
  await ui.saveButton.click();assert.equal(ui.saved[0].force,false);assert.deepEqual(ui.navigations,['/projects/']);
});
test('check errors can retry and never pretend the work is complete',async()=>{
  let fail=true;const ui=setup({fetcher:async()=>{if(fail)throw Error('离线');return {ok:true,headers:{get:()=> 'application/json'},json:async()=>({my_remaining:1,next_unfinished_url:'/r/7/?a=5'})};}});
  ui.button.click();await flush();assert.equal(ui.status.className,'error');assert.equal(ui.retry.hidden,false);assert.equal(ui.resume.disabled,true);
  fail=false;await ui.retry.click();assert.equal(ui.resume.disabled,false);assert.equal(ui.saved.length,0);
});
test('check deduplicates repeated clicks and cancellation does not save',async()=>{
  const ui=setup();ui.button.click();ui.button.click();ui.cancel.click();assert.equal(ui.dialog.open,true);
  await flush();ui.cancel.click();assert.equal(ui.dialog.open,false);assert.equal(ui.saved.length,0);assert.equal(ui.button.focused,true);
});
test('save failure keeps data and dialog for retry without navigation',async()=>{
  let fail=true;const ui=setup({save:async()=>{if(fail)throw Error('未保存');return 0;}});ui.button.click();await flush();
  await ui.saveButton.click();assert.equal(ui.dialog.open,true);assert.equal(ui.navigations.length,0);assert.match(ui.status.textContent,/未保存/);
  fail=false;await ui.saveButton.click();assert.deepEqual(ui.navigations,['/projects/']);
});
test('false save acknowledgment never navigates',async()=>{
  const ui=setup({save:async()=>false});ui.button.click();await flush();await ui.saveButton.click();assert.equal(ui.navigations.length,0);assert.equal(ui.status.className,'error');
});
test('invalid or foreign pending task URL is rejected before saving',async()=>{
  for(const url of ['/r/8/?a=1','https://evil.test/r/7/?a=2','/r/7/?a=2#fragment','/r/7/?a=2&extra=1']){
    const ui=setup({data:{my_remaining:1,next_unfinished_url:url}});ui.button.click();await flush();
    assert.equal(ui.resume.disabled,true,url);assert.equal(ui.status.className,'error');assert.equal(ui.saved.length,0);
  }
});
test('invalid counts and login HTML stay retryable rather than showing completion',async()=>{
  for(const data of [{my_remaining:-1},{my_remaining:'0'},{}]){const ui=setup({data});ui.button.click();await flush();assert.equal(ui.status.className,'error');}
  const ui=setup({fetcher:async()=>({ok:true,headers:{get:()=> 'text/html'}})});ui.button.click();await flush();assert.equal(ui.status.className,'error');
});
test('split preview, actual splitting and submit prevent last-task navigation',()=>{
  for(const state of [{navigationPending:'true',busy:'true',savingDraft:'true'},{splitPreview:'true'},{splitting:'true'},{busy:'true',savingDraft:'false'}]){
    const ui=setup({state});ui.button.click();assert.equal(ui.dialog.open,false);assert.equal(ui.requests(),0);
  }
});
test('draft in-flight saving permits prompt and waits before departure',async()=>{
  const ui=setup({state:{busy:'true',savingDraft:'true'}});ui.button.click();await flush();await ui.saveButton.click();assert.deepEqual(ui.navigations,['/projects/']);
});
test('unsupported dialog remains disabled instead of exposing an unsafe link',()=>{
  const ui=setup({modal:false});assert.equal(ui.button.disabled,true);assert.equal(ui.requests(),0);
});
