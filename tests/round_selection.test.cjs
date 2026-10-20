const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const {parsePositions, createSelection, requestSequence} = require('../static/round_selection.js');
const source = fs.readFileSync(path.join(__dirname, '../static/round_selection.js'), 'utf8');

test('batch positions combine separated items and ranges without duplicates', () => {
  assert.deepEqual(parsePositions('3、8，15-18; 17 47\n80-82', 100), [3,8,15,16,17,18,47,80,81,82]);
});
test('blank batch is harmless and first and last material positions are valid', () => {
  assert.deepEqual(parsePositions('  ', 100), []);
  assert.deepEqual(parsePositions('1,100',100),[1,100]);
});
test('spaces around range separators match the server parser', () => {
  assert.deepEqual(parsePositions('2 - 4、7\n-\n9',10),[2,3,4,7,8,9]);
});
test('invalid positions and huge or descending ranges are rejected before selection', () => {
  for (const value of ['0','-1','3-2','1.5','2e2','one','1-2-3','9999999999999999999999','1-10001'])
    assert.throws(() => parsePositions(value,20000,10000));
  assert.throws(() => parsePositions('99-101',100));
  assert.throws(() => parsePositions('101',100));
});
test('position numbers map to actual ids rather than being interpreted as system ids', () => {
  const index = [641,645,672,696,722];
  const selection = createSelection(index);
  selection.add(parsePositions('1,3-4',index.length).map(position => index[position-1]));
  assert.deepEqual(selection.values(), ['641','672','696']);
});
test('selection survives separate page additions and removal while deduplicating', () => {
  const selection = createSelection([641,645,672,696,722], [641]);
  selection.add([645,672]);
  selection.add([696,641]);
  selection.remove(645);
  assert.deepEqual(selection.values(),['641','672','696']);
  assert.equal(selection.has(641),true);
  assert.equal(selection.size,3);
});
test('failed bulk addition is atomic and preserves earlier selected ids', () => {
  const selection = createSelection([1,2,3,4],[1],2);
  assert.throws(() => selection.add([2,3]));
  assert.deepEqual(selection.values(),['1']);
  assert.throws(() => selection.add([2,99]));
  assert.deepEqual(selection.values(),['1']);
});
test('clear and subsequent additions do not retain selections from old pages', () => {
  const selection = createSelection([1,2,3],[1,2]);
  const version = selection.version;
  selection.clear(); selection.add([3]);
  assert.deepEqual(selection.values(),['3']);
  assert.ok(selection.version > version);
});
test('selection change can invalidate an in-flight select-all result', () => {
  const selection = createSelection([1,2,3],[1]);
  const requestVersion = selection.version;
  selection.remove(1);
  assert.notEqual(requestVersion,selection.version);
});
test('newer queries and mode changes invalidate older response tickets', () => {
  const requests = requestSequence();
  const old = requests.next();
  const latest = requests.next();
  assert.equal(requests.isCurrent(old),false);
  assert.equal(requests.isCurrent(latest),true);
  requests.next();
  assert.equal(requests.isCurrent(latest),false);
});

// Small DOM fixture exercises the actual controller, not a second implementation.
class Node {
  constructor(values={}) { Object.assign(this,{value:'',disabled:false,hidden:false,checked:false,dataset:{},children:[],handlers:{}},values); }
  addEventListener(type,fn) { (this.handlers[type] ||= []).push(fn); }
  emit(type,event={}) { return (this.handlers[type] || []).map(fn => fn(event)); }
  setAttribute(name,value) { this[name]=value; }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  focus() { this.focused=true; }
  querySelectorAll(selector) {
    if(this.controls) return this.controls;
    const descendants=this.children.flatMap(node=>[node,...node.querySelectorAll(selector)]);
    return selector === 'input[type="checkbox"]' ? descendants.filter(node=>node.type==='checkbox') : descendants;
  }
}
function result(payload={}) {
  const ids=Object.hasOwn(payload,'selected') ? payload.selected : [1,2,3];
  return {pool_hash:'pool',page:1,pages:1,count:ids.length,results:ids.map(id=>({id,position:id,text:'材料'+id,document:'文件',source_position:id})),
    ...(payload.all ? {ids} : {})};
}
function response(data) { return {ok:true,json:async()=>data}; }
async function flush() { for(let i=0;i<12;i++) await Promise.resolve(); }
function setup({initialIds='',initialMode='random',fetchImplementation=async payload=>response(result(payload))}={}) {
  const names=Object.fromEntries(['unit_ids','manual_positions','count','start_position','seed','stratify','distribution','double_percent','pool_hash','csrfmiddlewaretoken'].map(name=>[name,new Node()]));
  Object.assign(names.unit_ids,{value:initialIds}); names.pool_hash.value='pool'; names.distribution.value='all';
  names.count.value='2'; names.start_position.value='2'; names.seed.value='2026';
  const parts=Object.fromEntries(['preview','status','list','selected-count','show-selected','select-matching','filter','previous','next','page','add','picker','clear','count','count-hint','mixed'].map(name=>[name,new Node()]));
  parts.add.hidden=true; parts.picker.hidden=true;
  const fields=Object.fromEntries(['q','document','metadata_field','metadata_value'].map(name=>[name,new Node()]));
  const radios=['random','sequential','manual'].map(value=>new Node({value,checked:value===initialMode}));
  const panels=['random','sequential','manual'].map(value=>new Node({dataset:{roundPanel:value},controls:value==='random' ? [names.seed,names.stratify] : value==='sequential' ? [names.start_position] : [names.manual_positions,...Object.values(fields),parts.add,parts['show-selected'],parts['select-matching'],parts.filter,parts.clear,parts.previous,parts.next]}));
  const form=new Node({dataset:{unitChoicesUrl:'/choices/'}});
  form.querySelector=selector=>selector==='[name="selection_mode"]:checked' ? radios.find(node=>node.checked)
    : selector.startsWith('[name=') ? names[selector.slice(7,-2)] : parts[selector.slice(12,-1)];
  form.querySelectorAll=selector=>selector==='[data-round-panel]' ? panels : selector==='[name="selection_mode"]' ? radios : [];
  const ids={'round-arrangement':form,'round-unit-index':new Node({textContent:'[1,2,3]'})};
  for(const [name,node] of Object.entries(fields)) ids[{'q':'round-unit-query','document':'round-unit-document','metadata_field':'round-unit-field','metadata_value':'round-unit-value'}[name]]=node;
  const calls=[],navigations=[];
  vm.runInNewContext(source,{document:{getElementById:id=>ids[id],createElement:()=>new Node()},AbortController,
    fetch:async(url,options)=>{const payload=JSON.parse(options.body);calls.push(payload);return fetchImplementation(payload);},
    location:{pathname:'/arrange/',search:'?source=guide',assign:url=>navigations.push(url)}});
  return {form,names,parts,fields,radios,panels,calls,navigations,issue:form.children[0],
    mode(value){radios.forEach(node=>node.checked=node.value===value);radios.find(node=>node.checked).emit('change');},
    click(name){return parts[name].emit('click')[0];},
    submit(){const event={preventDefault(){this.prevented=true;}};form.emit('submit',event);return event;}};
}

test('stale restored ids keep the picker usable and original values until explicit clear',async()=>{
  const ui=setup({initialIds:'1,99',initialMode:'manual'}); await flush();
  assert.equal(ui.parts.picker.hidden,false);
  assert.equal(ui.form.handlers.submit.length,1);
  assert.equal(ui.issue.hidden,false);
  assert.equal(ui.parts.preview.disabled,true);
  assert.equal(ui.names.unit_ids.value,'1,99');
  assert.equal(ui.parts.list.querySelectorAll('input[type="checkbox"]')[0].checked,true);
  assert.equal(ui.submit().prevented,true);
  ui.click('clear');
  assert.equal(ui.names.unit_ids.value,'');
  assert.equal(ui.issue.hidden,true);
  assert.equal(ui.parts.preview.disabled,false);
});

test('recovery warning remains visible outside manual mode and refresh navigates with GET',async()=>{
  const ui=setup({initialIds:'99'});
  assert.equal(ui.issue.hidden,false);
  assert.equal(ui.parts.preview.disabled,true);
  assert.equal(ui.panels.find(node=>node.dataset.roundPanel==='manual').hidden,true);
  ui.issue.children[1].emit('click');
  assert.deepEqual(ui.navigations,['/arrange/?source=guide']);
  ui.mode('manual'); await flush(); ui.click('clear');
  assert.equal(ui.parts.preview.disabled,false);
});

test('modes disable irrelevant fields without discarding their values or repeated click handlers',async()=>{
  const ui=setup({initialIds:'1'});
  assert.equal(ui.names.unit_ids.disabled,true);
  assert.equal(ui.names.manual_positions.disabled,true);
  assert.equal(ui.names.seed.disabled,false);
  ui.mode('manual'); await flush();
  assert.equal(ui.names.count.disabled,true);
  assert.equal(ui.names.unit_ids.disabled,false);
  ui.mode('sequential');
  assert.equal(ui.names.start_position.disabled,false);
  assert.equal(ui.names.count.disabled,false);
  assert.equal(ui.names.seed.disabled,true);
  assert.equal(ui.names.unit_ids.disabled,true);
  ui.mode('manual'); await flush();
  assert.equal(ui.names.unit_ids.value,'1');
  assert.equal(ui.names.count.value,'2');
  assert.equal(ui.radios.every(node=>node.handlers.change.length===1 && !node.handlers.click),true);
});

test('preview is blocked until all matching selection has finished',async()=>{
  let finish;
  const ui=setup({initialIds:'1',initialMode:'manual',fetchImplementation:async payload=>payload.all
    ? new Promise(resolve=>finish=resolve) : response(result(payload))});
  await flush(); const pending=ui.click('select-matching');
  assert.equal(ui.parts.preview.disabled,true);
  assert.equal(ui.submit().prevented,true);
  finish(response(result({all:true}))); await pending;
  assert.equal(ui.names.unit_ids.value,'1,2,3');
  assert.equal(ui.parts.preview.disabled,false);
});

test('clearing the selected-list view requests an explicit empty set on page one',async()=>{
  const ui=setup({initialIds:'1,2',initialMode:'manual'}); await flush();
  ui.click('show-selected'); await flush();
  assert.deepEqual(ui.calls.at(-1).selected,[1,2]);
  ui.click('clear'); await flush();
  assert.deepEqual(ui.calls.at(-1).selected,[]);
  assert.equal(ui.calls.at(-1).page,1);
  assert.equal(ui.names.unit_ids.value,'');
  assert.match(ui.parts.page.textContent,/0条/);
});

test('a superseded response cannot replace a newer filter result',async()=>{
  let finish;
  const ui=setup({initialMode:'manual',fetchImplementation:async payload=>payload.q
    ? response({...result(),results:[{id:2,position:2,text:'新筛选',document:'文件',source_position:2}]})
    : new Promise(resolve=>finish=resolve)});
  ui.fields.q.value='新';ui.fields.q.emit('keydown',{key:'Enter',preventDefault(){}});await flush();
  finish(response({...result(),results:[{id:1,position:1,text:'旧筛选',document:'文件',source_position:1}]}));await flush();
  assert.equal(ui.parts.list.children[0].children[1].children[1].textContent,'新筛选');
});

test('all matching uses the displayed filter until new input is explicitly applied',async()=>{
  const ui=setup({initialMode:'manual'});await flush();
  ui.fields.q.value='尚未筛选';
  await ui.click('select-matching');
  assert.equal(ui.calls.at(-1).q,'');
  ui.click('filter');await flush();
  assert.equal(ui.calls.at(-1).q,'尚未筛选');
  await ui.click('select-matching');
  assert.equal(ui.calls.at(-1).q,'尚未筛选');
});

test('failed new filters do not change all-matching conditions from the displayed list',async()=>{
  const ui=setup({initialMode:'manual',fetchImplementation:async payload=>payload.q
    ? {ok:false,json:async()=>({error:'筛选失败'})} : response(result(payload))});await flush();
  ui.fields.q.value='失败条件';ui.click('filter');await flush();
  assert.match(ui.parts.status.textContent,/筛选失败/);
  assert.equal(ui.parts.preview.disabled,false);
  await ui.click('select-matching');
  assert.equal(ui.calls.at(-1).q,'');
});
