const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/shortcuts.js'), 'utf8');
const actions=['primary','secondary','save','next','pickerUp','pickerDown','pickerConfirm','pickerClose','modePrimary','modeSecondary','skip'];
const defaults={primary:'Alt+Q',secondary:'Alt+E',save:'Alt+F',next:'Alt+G',pickerUp:'W',pickerDown:'S',pickerConfirm:'Enter',pickerClose:'Alt+X',modePrimary:'1',modeSecondary:'2',skip:'Alt+Enter'};

function setup({user='1', withForm=true, withSettings=false, count=3, initial='', stored=null, storageFails=false, modal=true, fallback=false,
  sessionValues=new Map(), storedMode=null, mode='annotation', scope='1', formState={}, primaryDisabled=false, sessionFails=false,
  nextHook=true, beforeNavigation=()=>Promise.resolve(0), skipUrl, projectId='1',withSkipPrompt=true,nextUrls=[],withCompletionDialog=true}={}) {
  const events = {}, windowEvents = {}, observers = [], writes = [], sessionWrites=[], navigations=[],prepOptions=[];
  let changes=0, clicks=0, nextClicks=0, nextCalls=0, prepCalls=0,modalCalls=0,showCalls=0;
  const document = {activeElement:null, body:{dataset:{navigationUser:user}},
    addEventListener(name, callback) { events[name] = callback; },
  };
  function element(properties={}) {
    return {disabled:false, hidden:false, checked:false, value:'', dataset:{}, children:[], attributes:{}, handlers:{}, isConnected:true,tagName:'DIV',type:'text',parentElement:null,
      get isContentEditable(){return this.parentElement?.isContentEditable||false;},
      classList:{values:new Set(),toggle(name,force){const enabled=force??!this.values.has(name);if(enabled)this.values.add(name);else this.values.delete(name);return enabled;},contains(name){return this.values.has(name);}},
      addEventListener(name, callback) { (this.handlers[name] ||= []).push(callback); },
      dispatchEvent(event) { if (!event.target) event.target = this; for (const callback of this.handlers[event.type] || []) callback(event); },
      setAttribute(name,value) { this.attributes[name]=value; },
      removeAttribute(name) { delete this.attributes[name]; },
      append(child) { const nodes=child.fragment?child.children:[child]; for(const node of nodes){node.parentElement=this;this.children.push(node);} },
      replaceChildren() { this.children=[]; },
      scrollIntoView(){},
      focus() { document.activeElement?.dispatchEvent({type:'blur'}); document.activeElement=this; this.dispatchEvent({type:'focus'}); },
      blur() { this.dispatchEvent({type:'blur'}); if(document.activeElement===this) document.activeElement=null; },
      click() { if (!this.disabled) this.dispatchEvent({type:'click'}); },
      matches(selector){return selector.split(',').some(part=>{
        const value=part.trim(),tag=value.match(/^[a-z]+/i)?.[0];
        if(tag&&tag.toUpperCase()!==this.tagName)return false;
        const attributes=[...value.matchAll(/\[([\w-]+)(?:=["']?([^\]"']+)["']?)?\]/g)];
        if(!tag&&!attributes.length)return false;
        return attributes.every(([,name,expected])=>{
          const actual=name.startsWith('data-')?this.dataset[name.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase())]
            :name==='contenteditable'?this.isContentEditable?'true':undefined:this.attributes[name]??this[name];
          return expected===undefined?actual!==undefined&&actual!==false:String(actual)===expected;
        });
      });},
      closest(selector){return this.matches(selector)?this:this.parentElement?.closest(selector)||null;},
      contains(target){return target===this||this.children.some(child=>child.contains?.(target));},
      ...properties,
    };
  }
  document.body=element({tagName:'BODY',dataset:{navigationUser:user}});
  const primary=element({value:initial,disabled:primaryDisabled,tagName:'SELECT'}), noCode=element({tagName:'INPUT',type:'checkbox'}), search=element({tagName:'INPUT',type:'search'}), list=element(), closeButton=element({tagName:'BUTTON'}), title=element(), total=element(), empty=element(), note=element({tagName:'TEXTAREA'});
  const secondary=Array.from({length:count},(_,index)=>element({value:String(index+1),tagName:'INPUT',type:'radio'}));
  const save=element({tagName:'BUTTON'}), next=element({tagName:'BUTTON'}); save.addEventListener('click',()=>clicks++); next.addEventListener('click',()=>nextClicks++);
  const skipConfirm=element({tagName:'BUTTON'}),skipCancel=element({tagName:'BUTTON'});
  const skipButtons=Array.from({length:2},()=>element({tagName:'BUTTON',type:'button',dataset:{shortcutSkip:''}}));
  const skipButtonHints=skipButtons.map(button=>{const hint=element({dataset:{shortcutHint:'skip'}});button.append(hint);return hint;});
  const nextLinks=nextUrls.map(href=>element({tagName:'A',href,dataset:{codingNext:''}}));
  const skipPrompt=element({hidden:true,querySelector(selector){return {'[data-shortcut-skip-confirm]':skipConfirm,'[data-shortcut-skip-cancel]':skipCancel}[selector]||null;}});
  const anchor={before(node){node.moved=true;}};
  const form=element({tagName:'FORM',dataset:{locked:'false',busy:'false',splitting:'false',mode,shortcutScope:scope,projectId,skipUrl:skipUrl??`/r/${scope}/?a=2`,...formState},querySelector(selector){
    return {'[name=primary]':primary,'[name=no_code]':noCode,'[name=note]':note,'[data-code-picker]':anchor,'[data-action="save"]':save,'[data-action="submit_next"]':next,
      '[data-shortcut-skip-prompt]':withSkipPrompt?skipPrompt:null,'[data-shortcut-skip-confirm]':skipConfirm,'[data-shortcut-skip-cancel]':skipCancel}[selector] || null;
  },querySelectorAll(selector){return selector==='[name=secondary]'?secondary:selector==='[data-code-choice]'&&!fallback?codeButtons:[];}});
  if(nextHook)form.platformSubmitNext=()=>{nextCalls++;nextClicks++;return Promise.resolve(true);};
  form.platformSaveBeforeNavigation=options=>{prepCalls++;prepOptions.push(options);return beforeNavigation(options);};
  const emitChange=event=>{changes++; form.dispatchEvent({type:'change',target:event.target});};
  primary.addEventListener('change',emitChange); secondary.forEach(input=>input.addEventListener('change',emitChange));
  const codeButtons=Array.from({length:count},(_,index)=>{
    const id=String(index+1), card={querySelector:()=>({textContent:`定义 ${index+1} details`})};
    const button=element({tagName:'BUTTON',dataset:{codeChoice:id,label:`C${id} · 代码 ${id}`},closest:selector=>selector==='[data-code-card]'?card:null});
    button.addEventListener('click',()=>{primary.value=id;noCode.checked=false;secondary.forEach(input=>{if(input.value===id)input.checked=false;});primary.dispatchEvent({type:'change'});});
    return button;
  });
  primary.options = [{value:'',textContent:'请选择',dataset:{}},...codeButtons.map((button,index)=>({value:button.dataset.codeChoice,textContent:button.dataset.label,dataset:{definition:`协商定义 ${index+1}`}}))];
  const openButtons=['primary','secondary'].map(action=>element({dataset:{shortcutOpen:action}}));
  const toolbar=element({hidden:true,querySelectorAll:()=>openButtons});
  const dialog=element({hidden:true,open:false,show:modal?function(){this.open=true;showCalls++;}:undefined,showModal:modal?function(){this.open=true;modalCalls++;}:undefined,close(){this.open=false;},
    querySelector(selector){return {'[data-shortcut-search]':search,'[data-shortcut-list]':list,'[data-shortcut-close]':closeButton,'#shortcut-dialog-title':title,'[data-shortcut-count]':total,'[data-shortcut-empty]':empty}[selector];}});
  const completionDialog=element({tagName:'DIALOG',open:false});
  const settingsFields=actions.map(action=>element({tagName:'INPUT',dataset:{shortcutRecord:action}}));
  const settingsForm=element(), settingsStatus=element(), reset=element();
  const settings=element({hidden:true,querySelector(selector){return {'[data-shortcut-settings-form]':settingsForm,'[data-shortcut-settings-status]':settingsStatus,'[data-shortcut-reset]':reset}[selector];},querySelectorAll:()=>settingsFields});
  const hints=[...actions.map(action=>element({dataset:{shortcutHint:action}})),...skipButtonHints];
  document.querySelector=selector=>({'.annotation-form':withForm?form:null,'[data-shortcut-toolbar]':withForm?toolbar:null,'[data-shortcut-dialog]':withForm?dialog:null,'[data-shortcut-settings]':withSettings?settings:null,'[data-task-completion]':withCompletionDialog?completionDialog:null}[selector] || null);
  document.querySelectorAll=selector=>selector==='[data-shortcut-hint]'?hints:selector==='[data-shortcut-skip]'?skipButtons:selector==='[data-coding-next]'?nextLinks:[];
  document.createElement=()=>element(); document.createDocumentFragment=()=>element({fragment:true});
  const storage={getItem(){if(storageFails)throw Error('denied');return stored;},setItem(key,value){if(storageFails)throw Error('denied');writes.push({key,value});}};
  const modeKey=`ai-coding-platform:quick-coding:v1:${user}:${scope}`;
  if(storedMode!==null)sessionValues.set(modeKey,storedMode);
  const sessionStorage={
    getItem(key){if(sessionFails)throw Error('denied');return sessionValues.get(key)??null;},
    setItem(key,value){if(sessionFails)throw Error('denied');sessionValues.set(key,value);sessionWrites.push({action:'set',key,value});},
    removeItem(key){if(sessionFails)throw Error('denied');sessionValues.delete(key);sessionWrites.push({action:'remove',key});},
  };
  const location={origin:'http://127.0.0.1:8001',href:`http://127.0.0.1:8001/r/${scope}/?a=1`,assign(url){navigations.push(url);}};
  const window={sessionStorage,location,addEventListener(name,callback){windowEvents[name]=callback;},dispatchEvent(event){windowEvents[event.type]?.(event);}};
  vm.runInNewContext(source,{document,window,location,URL,localStorage:storage,sessionStorage,MutationObserver:class{constructor(callback){observers.push(callback);}observe(){}},Event:class{constructor(type,options={}){this.type=type;Object.assign(this,options);}}});
  function key(key, properties={}) {
    const event={key,ctrlKey:false,altKey:false,shiftKey:false,metaKey:false,defaultPrevented:false,target:document.activeElement,
      preventDefault(){this.defaultPrevented=true;},...properties};
    events.keydown?.(event); return event;
  }
  function record(action,keyValue,properties) { settingsFields.find(field=>field.dataset.shortcutRecord===action).focus(); key(keyValue,properties); }
  return {document,window,element,api:window.PlatformShortcuts,form,primary,noCode,secondary,save,next,search,list,dialog,toolbar,openButtons,total,empty,title,closeButton,hints,codeButtons,
    settings,settingsForm,settingsStatus,settingsFields,reset,writes,key,record,windowEvents,sessionValues,sessionWrites,modeKey,note,navigations,skipPrompt,skipConfirm,skipCancel,skipButtons,skipButtonHints,nextLinks,completionDialog,prepOptions,
    openCompletion(){window.dispatchEvent({type:'platform:task-completion-open'});completionDialog.open=true;},
    clickNext(index=0,properties={}){const event={type:'click',button:0,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},...properties};nextLinks[index].dispatchEvent(event);return event;},
    searchFor(text){search.focus();search.value=text;search.dispatchEvent({type:'input'});},
    refresh(){observers.forEach(callback=>callback());}, changes:()=>changes, clicks:()=>clicks, nextClicks:()=>nextClicks,nextCalls:()=>nextCalls,prepCalls:()=>prepCalls,showCalls:()=>showCalls,modalCalls:()=>modalCalls,
    submitSettings(){settingsForm.dispatchEvent({type:'submit',preventDefault(){}});},
    updateStorage(value){stored=value;},
  };
}

test('default bindings do not depend on code count and validate atomically',()=>{
  const ui=setup({user:'anonymous'});
  assert.deepEqual({...ui.api.defaults},defaults);
  assert.equal(ui.api.validate(defaults).error,undefined);
  const invalid=ui.api.validate({...defaults,secondary:'Alt+Q'});
  assert.match(invalid.error,/重复/); assert.equal(invalid.values,undefined);
  for(const action of actions) {
    const incomplete={...defaults}; delete incomplete[action];
    assert.ok(ui.api.validate(incomplete).error,action);
    assert.ok(ui.api.validate({...defaults,[action]:''}).error,action);
  }
});

test('normalization accepts canonical modified letters, digits, arrows, enter and function keys',()=>{
  const ui=setup({user:'anonymous'});
  assert.equal(ui.api.normalize('Shift+Alt+j'),'Alt+Shift+J');
  assert.equal(ui.api.normalize('Ctrl+2'),'Ctrl+2');
  assert.equal(ui.api.normalize('Meta+arrowdown'),'Meta+ArrowDown');
  assert.equal(ui.api.normalize('Ctrl+f2'),'Ctrl+F2');
  assert.equal(ui.api.normalize('w'),'W'); assert.equal(ui.api.normalize('s'),'S');
  for(const value of [null,{},'Ctrl+Ctrl+J','Super+J','Ctrl++','Ctrl+Control']) assert.equal(ui.api.normalize(value),'');
});

test('bare letters, modifier-only, browser navigation and common system combinations cannot be saved',()=>{
  const ui=setup({user:'anonymous'});
  for(const chord of ['J','Shift+J','Alt','Ctrl+L','Meta+W','Ctrl+Shift+T','Alt+ArrowLeft','Alt+F4','Ctrl+Alt+K','Ctrl+Tab','Alt+Escape','Ctrl+Space']) {
    assert.ok(ui.api.validate({...ui.api.defaults,primary:chord}).error,chord);
  }
});

test('only chooser navigation accepts unmodified letters and only confirmation accepts plain Enter',()=>{
  const ui=setup({user:'anonymous'});
  assert.equal(ui.api.validate({...defaults,pickerUp:'a',pickerDown:'d'}).error,undefined);
  for(const action of ['primary','secondary','save','next','pickerConfirm','pickerClose']) {
    for(const key of ['W','S','A']) assert.ok(ui.api.validate({...defaults,[action]:key}).error,`${action}: ${key}`);
  }
  assert.equal(ui.api.validate({...defaults,pickerConfirm:'Enter'}).error,undefined);
  for(const action of actions.filter(action=>action!=='pickerConfirm')) assert.ok(ui.api.validate({...defaults,[action]:'Enter'}).error,action);
  assert.match(ui.api.validate({...defaults,pickerUp:'S'}).error,/重复/);
  for(const key of ['1','ArrowDown','Enter','Shift+W']) assert.ok(ui.api.validate({...defaults,pickerUp:key}).error,key);
});

test('event recognition uses physical letter keys and ignores composition or AltGraph',()=>{
  const ui=setup({user:'anonymous'});
  assert.equal(ui.api.eventChord({key:'∆',code:'KeyJ',altKey:true}),'Alt+J');
  assert.equal(ui.api.eventChord({key:'¡',code:'Digit1',altKey:true}),'Alt+1');
  assert.equal(ui.api.eventChord({key:'w',code:'KeyW'}),'W');
  assert.equal(ui.api.eventChord({key:'s',code:'KeyS'}),'S');
  assert.equal(ui.api.eventChord({key:'j',altKey:true,isComposing:true}),'');
  assert.equal(ui.api.eventChord({key:'j',altKey:true,keyCode:229}),'');
  assert.equal(ui.api.eventChord({key:'j',altKey:true,getModifierState:()=>true}),'');
});

test('opening and searching never change coding data; Escape restores prior focus',()=>{
  const ui=setup(), origin=ui.save; origin.focus();
  ui.key('q',{altKey:true}); assert.equal(ui.dialog.open,true); assert.equal(ui.document.activeElement,ui.list);
  ui.searchFor('定义 2'); assert.equal(ui.list.children.length,1); assert.equal(ui.changes(),0);
  ui.key('Escape'); assert.equal(ui.dialog.hidden,true); assert.equal(ui.dialog.open,false);
  assert.equal(ui.document.activeElement,origin); assert.equal(ui.changes(),0);
});

test('Arrow, Home, End and Enter select any code without fixed numeric bindings',()=>{
  const ui=setup({count:150}); ui.key('q',{altKey:true});
  assert.equal(ui.list.children.length,150); ui.key('End');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-149');
  ui.key('Home'); ui.key('ArrowDown'); ui.key('ArrowUp'); ui.key('ArrowDown'); ui.key('Enter');
  assert.equal(ui.primary.value,'2'); assert.equal(ui.changes(),1); assert.equal(ui.dialog.open,false);
  ui.key('q',{altKey:true}); ui.key('End'); ui.key('Enter'); assert.equal(ui.primary.value,'150');
});

test('mouse selection follows the existing primary button change path',()=>{
  const ui=setup(); ui.noCode.checked=true; ui.key('q',{altKey:true}); ui.list.children[2].click();
  assert.equal(ui.primary.value,'3'); assert.equal(ui.noCode.checked,false); assert.equal(ui.changes(),1);
});

test('decision fallback searches complete option definitions and confirms through the real select',()=>{
  const ui=setup({fallback:true}); ui.noCode.checked=true; ui.key('q',{altKey:true}); ui.searchFor('协商定义 2');
  assert.equal(ui.list.children.length,1); assert.equal(ui.changes(),0);
  ui.key('Enter'); assert.equal(ui.primary.value,'2'); assert.equal(ui.noCode.checked,false); assert.equal(ui.changes(),1);
});

test('secondary requires a primary, excludes that primary and preserves the primary on selection',()=>{
  const ui=setup(); assert.equal(ui.openButtons[1].disabled,true); ui.key('e',{altKey:true}); assert.equal(ui.dialog.open,false);
  ui.key('q',{altKey:true}); ui.key('Enter'); assert.equal(ui.primary.value,'1'); assert.equal(ui.openButtons[1].disabled,false);
  ui.secondary[1].checked=true; ui.key('e',{altKey:true}); assert.equal(ui.list.children.length,2);
  ui.key('End'); ui.key('Enter'); assert.equal(ui.primary.value,'1');
  assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,true]);
  assert.equal(ui.changes(),2);
});

test('empty search handles Enter without submitting or changing data',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.searchFor('unknown');
  assert.equal(ui.empty.hidden,false); const event=ui.key('Enter');
  assert.equal(event.defaultPrevented,true); assert.equal(ui.changes(),0); assert.equal(ui.dialog.open,true);
});

test('nonmodal chooser allows native Tab focus and never fires save or next shortcuts while choosing',()=>{
  const ui=setup(); ui.key('q',{altKey:true});
  assert.equal(ui.showCalls(),1); assert.equal(ui.modalCalls(),0);
  assert.equal(ui.key('Tab').defaultPrevented,false); assert.equal(ui.key('Tab',{shiftKey:true}).defaultPrevented,false);
  ui.search.focus();
  ui.key('f',{altKey:true}); ui.key('g',{altKey:true});
  assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0); assert.equal(ui.primary.value,'');
  ui.key('Enter',{ctrlKey:true}); assert.equal(ui.clicks(),0); assert.equal(ui.primary.value,'1');
});

test('plain text, IME events, repeats and pre-consumed shortcuts do not hijack editing',()=>{
  const ui=setup();
  for(const properties of [{},{altKey:true,isComposing:true},{altKey:true,keyCode:229},{altKey:true,repeat:true},{altKey:true,defaultPrevented:true}]) {
    ui.key('q',properties); assert.equal(ui.dialog.open,false);
  }
  assert.equal(ui.key('a').defaultPrevented,false); assert.equal(ui.changes(),0);
});

test('locked, submitting, splitting and disabled forms reject keyboard and mouse mutations',()=>{
  for(const state of ['locked','busy','splitting','disabled']) {
    const ui=setup(); if(state==='disabled')ui.primary.disabled=true;else ui.form.dataset[state]='true'; ui.refresh();
    ui.key('q',{altKey:true}); ui.openButtons[0].click(); ui.key('f',{altKey:true});
    assert.equal(ui.dialog.open,false,state); assert.equal(ui.changes(),0,state); assert.equal(ui.clicks(),0,state);
  }
});

test('a form becoming busy closes an open picker without committing its pending choice',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('End'); ui.form.dataset.busy='true'; ui.refresh();
  assert.equal(ui.dialog.open,false); assert.equal(ui.primary.value,''); assert.equal(ui.changes(),0);
});

test('save and next shortcuts click only the corresponding enabled existing buttons',()=>{
  const ui=setup(); ui.key('f',{altKey:true}); ui.key('g',{altKey:true});
  assert.equal(ui.clicks(),1); assert.equal(ui.nextClicks(),1);
  ui.save.disabled=true; ui.next.disabled=true; ui.key('f',{altKey:true}); ui.key('g',{altKey:true});
  assert.equal(ui.clicks(),1); assert.equal(ui.nextClicks(),1);
});

test('custom key recording is not applied until the whole valid settings form is saved',()=>{
  const ui=setup({withSettings:true}); ui.record('primary','r',{altKey:true});
  assert.equal(ui.writes.length,0); ui.save.focus(); ui.key('r',{altKey:true}); assert.equal(ui.dialog.open,false);
  ui.submitSettings(); assert.equal(ui.writes.length,1); assert.equal(ui.writes[0].key,'ai-coding-platform:shortcuts:v1:1');
  ui.key('r',{altKey:true}); assert.equal(ui.dialog.open,true); assert.equal(ui.hints[0].textContent,'Alt+R');
});

test('duplicate settings fail without partial persistence or changing active shortcuts',()=>{
  const ui=setup({withSettings:true}); ui.record('primary','e',{altKey:true}); ui.submitSettings();
  assert.equal(ui.writes.length,0); assert.match(ui.settingsStatus.textContent,/重复/);
  ui.save.focus(); ui.key('q',{altKey:true}); assert.equal(ui.dialog.open,true);
});

test('reserved recording and Escape preserve the previous setting and do not mark coding dirty',()=>{
  const ui=setup({withSettings:true}); ui.record('primary','l',{ctrlKey:true});
  assert.match(ui.settingsStatus.textContent,/浏览器/); assert.equal(ui.settingsFields[0].value,'Alt+Q');
  ui.key('Escape'); assert.equal(ui.document.activeElement,null); assert.equal(ui.changes(),0); assert.equal(ui.writes.length,0);
});

test('Escape cancels an otherwise valid key recording before it is saved',()=>{
  const ui=setup({withSettings:true}); ui.record('primary','r',{altKey:true}); assert.equal(ui.settingsFields[0].value,'Alt+R');
  ui.key('Escape'); assert.equal(ui.settingsFields[0].value,'Alt+Q');
  ui.submitSettings(); assert.equal(JSON.parse(ui.writes[0].value).primary,'Alt+Q');
});

test('a valid account preference is restored on reload without re-saving or dirtying data',()=>{
  const saved={primary:'Alt+R',secondary:'Alt+K',save:'Ctrl+Enter',next:'Ctrl+Shift+Enter'};
  const ui=setup({withSettings:true,stored:JSON.stringify(saved)});
  assert.equal(ui.hints[0].textContent,'Alt+R'); assert.equal(ui.settingsFields[0].value,'Alt+R');
  ui.key('q',{altKey:true}); assert.equal(ui.dialog.open,false);
  ui.key('r',{altKey:true}); assert.equal(ui.dialog.open,true); assert.equal(ui.writes.length,0); assert.equal(ui.changes(),0);
  assert.deepEqual(ui.settingsFields.slice(4,8).map(field=>field.value),['W','S','Enter','Alt+X']);
});

test('storage failures show a clear message and never replace active bindings',()=>{
  const ui=setup({withSettings:true,storageFails:true}); assert.match(ui.settingsStatus.textContent,/默认/);
  ui.record('primary','r',{altKey:true}); ui.submitSettings(); assert.match(ui.settingsStatus.textContent,/无法保存/);
  ui.save.focus(); ui.key('q',{altKey:true}); assert.equal(ui.dialog.open,true); assert.equal(ui.hints[0].textContent,'Alt+Q');
});

test('invalid saved settings fall back to defaults, reset persists all eleven defaults',()=>{
  const ui=setup({withSettings:true,stored:'{"primary":"Ctrl+L"}'}); assert.match(ui.settingsStatus.textContent,/无效/);
  ui.reset.click(); assert.equal(ui.writes.length,1); assert.deepEqual(JSON.parse(ui.writes[0].value),defaults);
});

test('account-scoped cross-tab updates refresh hints; another account cannot change them',()=>{
  const ui=setup(), updated={...ui.api.defaults,primary:'Alt+R'};
  ui.windowEvents.storage({key:'ai-coding-platform:shortcuts:v1:2',newValue:JSON.stringify(updated)}); assert.equal(ui.hints[0].textContent,'Alt+Q');
  ui.windowEvents.storage({key:'ai-coding-platform:shortcuts:v1:1',newValue:JSON.stringify(updated)}); assert.equal(ui.hints[0].textContent,'Alt+R');
  ui.key('r',{altKey:true}); assert.equal(ui.dialog.open,true);
});

test('back-forward pageshow reloads same-tab settings without network, persistence or coding changes',()=>{
  const ui=setup({withSettings:true}), updated={...ui.api.defaults,primary:'Alt+R'};
  ui.updateStorage(JSON.stringify(updated)); ui.windowEvents.pageshow({persisted:true});
  assert.equal(ui.hints[0].textContent,'Alt+R'); assert.equal(ui.settingsFields[0].value,'Alt+R');
  assert.equal(ui.writes.length,0); assert.equal(ui.changes(),0); assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0);
  ui.key('q',{altKey:true}); assert.equal(ui.dialog.open,false); ui.key('r',{altKey:true}); assert.equal(ui.dialog.open,true);
});

test('pageshow after preference removal restores defaults and invalid storage leaves active bindings intact',()=>{
  const saved={primary:'Alt+R',secondary:'Alt+K',save:'Ctrl+Enter',next:'Ctrl+Shift+Enter'};
  const ui=setup({stored:JSON.stringify(saved)}); ui.updateStorage('{broken'); ui.windowEvents.pageshow({persisted:true});
  assert.equal(ui.hints[0].textContent,'Alt+R'); ui.updateStorage(null); ui.windowEvents.pageshow({persisted:true});
  assert.equal(ui.hints[0].textContent,'Alt+Q'); assert.equal(ui.writes.length,0); assert.equal(ui.changes(),0);
});

test('pages without a coding form do not consume coding shortcuts',()=>{
  const ui=setup({withForm:false,withSettings:true}); assert.equal(ui.key('q',{altKey:true}).defaultPrevented,false);
  assert.equal(ui.settings.hidden,false);
});

test('old browsers without dialog support keep toolbar hidden and avoid shortcut errors',()=>{
  const ui=setup({modal:false}); assert.equal(ui.toolbar.hidden,true);
  assert.doesNotThrow(()=>ui.key('q',{altKey:true})); assert.equal(ui.changes(),0);
});

test('default left-hand combinations complete primary, secondary, save, next and cancel without distant keys',()=>{
  const ui=setup();
  ui.key('q',{altKey:true}); ui.key('s'); ui.key('s'); ui.key('w');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1');
  ui.key('Enter'); assert.equal(ui.primary.value,'2'); assert.equal(ui.dialog.open,false);
  ui.key('e',{altKey:true}); ui.key('s'); ui.key('Enter');
  assert.equal(ui.primary.value,'2'); assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,true]);
  ui.key('f',{altKey:true}); ui.key('g',{altKey:true});
  assert.equal(ui.clicks(),1); assert.equal(ui.nextClicks(),1);
  ui.key('q',{altKey:true}); ui.key('w'); ui.key('x',{altKey:true});
  assert.equal(ui.dialog.open,false); assert.equal(ui.primary.value,'2'); assert.equal(ui.changes(),2);
});

test('picker-only combinations are not consumed when the chooser is closed',()=>{
  const ui=setup();
  for(const key of ['w','s']) assert.equal(ui.key(key).defaultPrevented,false,key);
  assert.equal(ui.key('Enter').defaultPrevented,false);
  assert.equal(ui.key('x',{altKey:true}).defaultPrevented,false);
  assert.equal(ui.dialog.open,false); assert.equal(ui.changes(),0); assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0);
});

test('plain W and S never hijack other pages or ordinary coding fields',()=>{
  for(const withForm of [false,true]) {
    const ui=setup({withForm,withSettings:true}); ui.save.focus();
    for(const key of ['w','s','W','S']) assert.equal(ui.key(key).defaultPrevented,false,`${withForm}: ${key}`);
    assert.equal(ui.dialog.open,false); assert.equal(ui.changes(),0); assert.equal(ui.clicks(),0);
  }
});

test('W and S type normally in picker search and resume navigation only after list regains focus',()=>{
  const ui=setup(); ui.key('q',{altKey:true});
  assert.equal(ui.document.activeElement,ui.list); ui.key('s');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1');
  ui.searchFor('代码'); assert.equal(ui.document.activeElement,ui.search);
  for(const key of ['w','s','W','S']) assert.equal(ui.key(key).defaultPrevented,false,key);
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-0'); assert.equal(ui.changes(),0);
  ui.key('ArrowDown'); assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1');
  assert.equal(ui.key('Tab').defaultPrevented,true); assert.equal(ui.document.activeElement,ui.list);
  assert.equal(ui.key('s').defaultPrevented,true); assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-2');
  ui.key('w'); ui.key('Enter'); assert.equal(ui.primary.value,'2');
});

test('picker combinations handle empty search results and close without coding or submitting',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.searchFor('没有这样的代码'); ui.list.focus();
  for(const [key,properties] of [['s',{}],['w',{}],['Enter',{}]]) {
    assert.equal(ui.key(key,properties).defaultPrevented,true,key);
    assert.equal(ui.dialog.open,true); assert.equal(ui.changes(),0);
    assert.equal(ui.list.attributes['aria-activedescendant'],undefined);
  }
  ui.key('f',{altKey:true}); ui.key('g',{altKey:true});
  assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0);
  ui.key('x',{altKey:true}); assert.equal(ui.dialog.open,false); assert.equal(ui.changes(),0);
});

test('zero available codes cannot open either picker or mutate coding data',()=>{
  const ui=setup({count:0});
  ui.key('q',{altKey:true}); ui.key('e',{altKey:true});
  assert.equal(ui.dialog.open,false); assert.equal(ui.openButtons[0].disabled,true);
  assert.equal(ui.openButtons[1].disabled,true); assert.equal(ui.changes(),0);
});

test('picker navigation, confirmation and close ignore composition, repeats and AltGraph',()=>{
  const ui=setup(); ui.key('q',{altKey:true});
  for(const properties of [{isComposing:true},{keyCode:229},{repeat:true},{getModifierState:()=>true}]) {
    for(const key of ['s','w']) ui.key(key,properties);
    ui.key('Enter',properties); ui.key('x',{altKey:true,...properties});
    assert.equal(ui.dialog.open,true); assert.equal(ui.changes(),0);
    assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-0');
  }
  ui.key('s'); ui.key('Enter'); assert.equal(ui.primary.value,'2');
});

test('all four chooser actions are customizable and apply only after the full settings form is saved',()=>{
  const ui=setup({withSettings:true});
  const changes={pickerUp:'t',pickerDown:'d',pickerConfirm:'a',pickerClose:'v'};
  for(const [action,key] of Object.entries(changes)) ui.record(action,key,{altKey:true});
  assert.equal(ui.writes.length,0); ui.submitSettings(); assert.equal(ui.writes.length,1);
  const saved=JSON.parse(ui.writes[0].value);
  assert.equal(Object.keys(saved).length,11);
  for(const [action,key] of Object.entries(changes)) assert.equal(saved[action],`Alt+${key.toUpperCase()}`);
  ui.save.focus(); ui.key('q',{altKey:true});
  assert.equal(ui.key('s').defaultPrevented,false);
  ui.key('d',{altKey:true}); ui.key('d',{altKey:true}); ui.key('t',{altKey:true}); ui.key('a',{altKey:true});
  assert.equal(ui.primary.value,'2'); assert.equal(ui.dialog.open,false);
  ui.key('q',{altKey:true}); ui.key('v',{altKey:true}); assert.equal(ui.dialog.open,false);
});

test('custom bare-letter navigation retains search isolation and modified navigation can work in search',()=>{
  const ui=setup({withSettings:true}); ui.record('pickerUp','a'); ui.record('pickerDown','d'); ui.submitSettings();
  assert.equal(ui.writes.length,1); const saved=JSON.parse(ui.writes[0].value);
  assert.equal(saved.pickerUp,'A'); assert.equal(saved.pickerDown,'D');
  ui.save.focus(); ui.key('q',{altKey:true}); ui.key('d'); ui.key('d'); ui.key('a');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1');
  ui.searchFor('代码'); assert.equal(ui.key('d').defaultPrevented,false); assert.equal(ui.key('a').defaultPrevented,false);
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-0');
  ui.key('x',{altKey:true}); ui.record('pickerDown','s',{altKey:true}); ui.submitSettings();
  ui.save.focus(); ui.key('q',{altKey:true}); ui.searchFor('代码');
  assert.equal(ui.key('s',{altKey:true}).defaultPrevented,true);
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1'); ui.key('Enter');
  assert.equal(ui.primary.value,'2');
});

test('old four-action storage upgrades all chooser bindings without changing or re-saving old preferences',()=>{
  const saved={primary:'Alt+J',secondary:'Alt+K',save:'Ctrl+Enter',next:'Ctrl+Shift+Enter'};
  const ui=setup({withSettings:true,stored:JSON.stringify(saved)});
  assert.deepEqual(ui.settingsFields.slice(0,4).map(field=>field.value),Object.values(saved));
  assert.deepEqual(ui.settingsFields.slice(4,8).map(field=>field.value),['W','S','Enter','Alt+X']);
  ui.key('q',{altKey:true}); assert.equal(ui.dialog.open,false);
  ui.key('j',{altKey:true}); ui.key('s'); ui.key('Enter'); assert.equal(ui.primary.value,'2');
  ui.key('Enter',{ctrlKey:true}); ui.key('Enter',{ctrlKey:true,shiftKey:true});
  assert.equal(ui.clicks(),1); assert.equal(ui.nextClicks(),1); assert.equal(ui.writes.length,0);
});

test('legacy conflicts keep every user binding, disable only conflicting chooser additions and explain recovery',()=>{
  const saved={primary:'Alt+Z',secondary:'Alt+X',save:'Alt+S',next:'Alt+W'};
  const ui=setup({withSettings:true,stored:JSON.stringify(saved)});
  assert.deepEqual(ui.settingsFields.slice(0,4).map(field=>field.value),Object.values(saved));
  assert.deepEqual(ui.settingsFields.slice(4,8).map(field=>field.value),['W','S','Enter','未设置']);
  assert.deepEqual(ui.hints.slice(4,8).map(hint=>hint.textContent),['W','S','Enter','未设置']);
  assert.match(ui.settingsStatus.textContent,/原快捷键已保留/); assert.match(ui.settingsStatus.textContent,/重新设置/);
  assert.match(ui.settingsStatus.textContent,/方向键/);
  for(const key of ['Alt','Control','Shift','Meta','a']) assert.equal(ui.key(key).defaultPrevented,false,key);
  assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0);
  ui.key('z',{altKey:true}); assert.equal(ui.dialog.open,true);
  ui.key('s',{altKey:true}); ui.key('w',{altKey:true});
  assert.equal(ui.dialog.open,true); assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0);
  ui.key('ArrowDown'); ui.key('Enter'); assert.equal(ui.primary.value,'2');
  ui.key('s',{altKey:true}); ui.key('w',{altKey:true}); assert.equal(ui.clicks(),1); assert.equal(ui.nextClicks(),1);
  ui.submitSettings(); assert.equal(ui.writes.length,0); assert.match(ui.settingsStatus.textContent,/组合/);
  ui.reset.click(); assert.deepEqual(JSON.parse(ui.writes[0].value),defaults);
});

test('only colliding legacy additions are disabled while other chooser defaults remain usable',()=>{
  const saved={primary:'Alt+X',secondary:'Alt+K',save:'Ctrl+Enter',next:'Ctrl+Shift+Enter'};
  const ui=setup({withSettings:true,stored:JSON.stringify(saved)});
  assert.deepEqual(ui.settingsFields.slice(4,8).map(field=>field.value),['W','S','Enter','未设置']);
  ui.key('x',{altKey:true}); ui.key('s'); ui.key('w'); ui.key('Escape');
  assert.equal(ui.dialog.open,false); assert.equal(ui.changes(),0); assert.equal(ui.writes.length,0);
});

test('legacy upgrades and conflicts behave identically for pageshow and same-account storage events',()=>{
  for(const trigger of ['pageshow','storage']) {
    const ui=setup({withSettings:true});
    const saved={primary:'Alt+X',secondary:'Alt+K',save:'Ctrl+Enter',next:'Ctrl+Shift+Enter'};
    if(trigger==='pageshow') { ui.updateStorage(JSON.stringify(saved)); ui.windowEvents.pageshow({persisted:true}); }
    else ui.windowEvents.storage({key:'ai-coding-platform:shortcuts:v1:1',newValue:JSON.stringify(saved)});
    assert.equal(ui.hints[0].textContent,'Alt+X',trigger); assert.equal(ui.hints[7].textContent,'未设置',trigger);
    assert.equal(ui.settingsFields[7].value,'未设置',trigger); assert.match(ui.settingsStatus.textContent,/原快捷键已保留/);
    ui.key('x',{altKey:true}); ui.key('s'); ui.key('Enter'); assert.equal(ui.primary.value,'2',trigger);
    assert.equal(ui.writes.length,0); assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0);
  }
});

test('partially populated new-format settings cannot masquerade as legacy preferences',()=>{
  const partial={primary:'Alt+R',secondary:'Alt+K',save:'Ctrl+Enter',next:'Ctrl+Shift+Enter',pickerUp:'Alt+W'};
  const ui=setup({withSettings:true,stored:JSON.stringify(partial)});
  assert.match(ui.settingsStatus.textContent,/无效/); assert.equal(ui.hints[0].textContent,'Alt+Q');
  ui.key('r',{altKey:true}); assert.equal(ui.dialog.open,false);
  ui.key('q',{altKey:true}); assert.equal(ui.dialog.open,true); assert.equal(ui.writes.length,0);
});

test('Alt+Q activates continuous coding and Enter keeps it active for the next unit',()=>{
  const ui=setup();
  assert.equal(ui.sessionValues.has(ui.modeKey),false);
  ui.key('q',{altKey:true}); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  assert.equal(ui.document.activeElement,ui.list); ui.key('s'); ui.key('Enter');
  assert.equal(ui.primary.value,'2'); assert.equal(ui.dialog.open,false); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.key('g',{altKey:true}); assert.equal(ui.nextClicks(),1);
  const next=setup({sessionValues:ui.sessionValues});
  assert.equal(next.dialog.open,true); assert.equal(next.document.activeElement,next.list);
  assert.equal(next.title.textContent,'选择主代码'); assert.equal(next.changes(),0);
  next.key('Enter'); assert.equal(next.primary.value,'1'); assert.equal(next.sessionValues.get(next.modeKey),'on');
});

test('the primary toolbar activates continuous coding but secondary alone does not',()=>{
  const ui=setup({initial:'1'});
  ui.key('e',{altKey:true}); assert.equal(ui.dialog.open,true); assert.equal(ui.sessionValues.has(ui.modeKey),false);
  ui.key('Enter'); assert.equal(ui.sessionValues.has(ui.modeKey),false);
  ui.openButtons[0].click(); assert.equal(ui.dialog.open,true); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('primary and secondary shortcuts switch the open chooser without saving or advancing',()=>{
  const ui=setup({initial:'1'}); ui.save.focus(); ui.key('q',{altKey:true});
  assert.equal(ui.title.textContent,'选择主代码');
  assert.equal(ui.key('e',{altKey:true}).defaultPrevented,true); assert.equal(ui.title.textContent,'选择次代码');
  assert.equal(ui.list.children.length,2); assert.equal(ui.document.activeElement,ui.list);
  ui.key('q',{altKey:true}); assert.equal(ui.title.textContent,'选择主代码'); assert.equal(ui.list.children.length,3);
  ui.key('e',{altKey:true}); ui.key('s'); ui.key('Enter');
  assert.equal(ui.primary.value,'1'); assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,true]);
  assert.equal(ui.document.activeElement,ui.form);
  assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  const next=setup({sessionValues:ui.sessionValues}); assert.equal(next.title.textContent,'选择主代码');
});

test('continuous mode is isolated by account, round and current-tab session storage',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('Enter');
  assert.equal(setup({sessionValues:ui.sessionValues,scope:'2'}).dialog.open,false);
  assert.equal(setup({sessionValues:ui.sessionValues,user:'2'}).dialog.open,false);
  assert.equal(setup().dialog.open,false);
  assert.equal(setup({sessionValues:ui.sessionValues}).dialog.open,true);
  assert.equal(ui.sessionValues.size,1);
});

test('Alt+X exits continuous mode after confirmation even while the chooser is already closed',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('Enter');
  assert.equal(ui.dialog.open,false); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  assert.equal(ui.key('x',{altKey:true}).defaultPrevented,true);
  assert.equal(ui.sessionValues.has(ui.modeKey),false); assert.equal(ui.primary.value,'1'); assert.equal(ui.changes(),1);
  assert.equal(setup({sessionValues:ui.sessionValues}).dialog.open,false);
});

test('Escape, native cancel and close button only collapse the chooser while Alt+X exits continuous mode',()=>{
  for(const action of ['escape','cancel','button','shortcut']) {
    const ui=setup(); ui.key('q',{altKey:true}); ui.key('s');
    if(action==='escape')ui.key('Escape');
    if(action==='cancel')ui.dialog.dispatchEvent({type:'cancel',preventDefault(){}});
    if(action==='button')ui.closeButton.click();
    if(action==='shortcut')ui.key('x',{altKey:true});
    const continues=action!=='shortcut';
    assert.equal(ui.dialog.open,false,action); assert.equal(ui.sessionValues.has(ui.modeKey),continues,action);
    assert.equal(ui.changes(),0,action); assert.equal(setup({sessionValues:ui.sessionValues}).dialog.open,continues,action);
  }
});

test('busy, splitting and locked changes close the chooser without clearing continuous mode',()=>{
  for(const state of ['busy','splitting','locked']) {
    const ui=setup(); ui.key('q',{altKey:true}); ui.key('s'); ui.form.dataset[state]='true'; ui.refresh();
    assert.equal(ui.dialog.open,false,state); assert.equal(ui.changes(),0,state);
    assert.equal(ui.sessionValues.get(ui.modeKey),'on',state);
    assert.equal(setup({sessionValues:ui.sessionValues}).dialog.open,true,state);
  }
});

test('automatic continuation is restricted to editable annotation pages and does not clear the saved mode',()=>{
  const ui=setup({storedMode:'on'}); assert.equal(ui.dialog.open,true);
  for(const options of [{mode:'decision'},{formState:{locked:'true'}},{formState:{busy:'true'}},
    {formState:{splitting:'true'}},{primaryDisabled:true},{count:0},{modal:false}]) {
    const blocked=setup({sessionValues:ui.sessionValues,...options});
    assert.equal(blocked.dialog.open,false,JSON.stringify(options));
    assert.equal(blocked.sessionValues.get(blocked.modeKey),'on',JSON.stringify(options));
    assert.equal(blocked.changes(),0,JSON.stringify(options));
  }
  const help=setup({sessionValues:ui.sessionValues,withForm:false,withSettings:true});
  assert.equal(help.dialog.open,false); assert.equal(help.sessionValues.get(help.modeKey),'on');
  const resumed=setup({sessionValues:ui.sessionValues}); assert.equal(resumed.dialog.open,true);
});

test('session storage failure cannot prevent manual coding, confirmation, saving or exit',()=>{
  const ui=setup({sessionFails:true});
  assert.doesNotThrow(()=>ui.key('q',{altKey:true})); assert.equal(ui.dialog.open,true);
  ui.key('s'); ui.key('Enter'); assert.equal(ui.primary.value,'2');
  ui.key('f',{altKey:true}); assert.equal(ui.clicks(),1);
  assert.doesNotThrow(()=>ui.key('x',{altKey:true}));
  ui.key('q',{altKey:true}); assert.doesNotThrow(()=>ui.closeButton.click()); assert.equal(ui.dialog.open,false);
});

test('continuous coding follows a customized primary shortcut and still restores as primary on the next unit',()=>{
  const stored=JSON.stringify({...defaults,primary:'Alt+R'}), ui=setup({stored});
  ui.key('q',{altKey:true}); assert.equal(ui.dialog.open,false); assert.equal(ui.sessionValues.has(ui.modeKey),false);
  ui.key('r',{altKey:true}); assert.equal(ui.dialog.open,true); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.key('Enter'); const next=setup({stored,sessionValues:ui.sessionValues});
  assert.equal(next.dialog.open,true); assert.equal(next.title.textContent,'选择主代码'); assert.equal(next.changes(),0);
});

test('BFCache restoration keeps continuous mode off after another unit exits the same round',()=>{
  const previous=setup(); previous.key('q',{altKey:true}); previous.key('Enter');
  const next=setup({sessionValues:previous.sessionValues}); assert.equal(next.dialog.open,true);
  next.key('x',{altKey:true}); assert.equal(previous.sessionValues.has(previous.modeKey),false);
  previous.windowEvents.pageshow({persisted:true});
  assert.equal(previous.dialog.open,false); assert.equal(previous.sessionValues.has(previous.modeKey),false);
  assert.equal(previous.primary.value,'1'); assert.equal(previous.changes(),1);
  assert.equal(previous.key('x',{altKey:true}).defaultPrevented,false);
});

test('returning from help restores primary choice only while continuous mode remains on',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('Enter');
  const help=setup({sessionValues:ui.sessionValues,withForm:false,withSettings:true});
  assert.equal(help.sessionValues.get(help.modeKey),'on');
  ui.windowEvents.pageshow({persisted:true}); assert.equal(ui.dialog.open,true);
  assert.equal(ui.title.textContent,'选择主代码'); assert.equal(ui.document.activeElement,ui.list);
  assert.equal(ui.changes(),1); assert.equal(ui.clicks(),0); assert.equal(ui.nextClicks(),0);
});

test('BFCache restoration closes an obsolete cached picker when another page turned mode off',()=>{
  const previous=setup(); previous.key('q',{altKey:true}); previous.key('s');
  const next=setup({sessionValues:previous.sessionValues}); next.key('x',{altKey:true});
  assert.equal(previous.dialog.open,true); previous.windowEvents.pageshow({persisted:true});
  assert.equal(previous.dialog.open,false); assert.equal(previous.primary.value,''); assert.equal(previous.changes(),0);
  assert.equal(previous.sessionValues.has(previous.modeKey),false);
});

test('pageshow never resets search or pending choice in an already open continuous picker',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.searchFor('定义 2');
  assert.equal(ui.list.children.length,1); assert.equal(ui.document.activeElement,ui.search);
  ui.windowEvents.pageshow({persisted:true});
  assert.equal(ui.dialog.open,true); assert.equal(ui.search.value,'定义 2'); assert.equal(ui.list.children.length,1);
  assert.equal(ui.document.activeElement,ui.search); assert.equal(ui.changes(),0);
  ui.key('Enter'); assert.equal(ui.primary.value,'2');
});

test('disabled close binding cannot match bare modifier keys or exit continuous mode',()=>{
  const saved={primary:'Alt+X',secondary:'Alt+K',save:'Ctrl+Enter',next:'Ctrl+Shift+Enter'};
  const ui=setup({stored:JSON.stringify(saved)}); ui.key('x',{altKey:true});
  for(const key of ['Shift','Control','Alt','Meta']) {
    assert.equal(ui.key(key).defaultPrevented,false,key);
    assert.equal(ui.dialog.open,true,key); assert.equal(ui.sessionValues.get(ui.modeKey),'on',key);
  }
  assert.equal(ui.primary.value,''); assert.equal(ui.changes(),0);
  ui.key('Escape'); assert.equal(ui.dialog.open,false); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('Enter on the close button never selects a code before native button activation',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('s'); ui.closeButton.focus();
  ui.key('Enter'); assert.equal(ui.primary.value,''); assert.equal(ui.changes(),0);
  ui.closeButton.click(); assert.equal(ui.dialog.open,false); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('plain numeric mode selectors are allowed only for the two mode-specific actions',()=>{
  const ui=setup({user:'anonymous'});
  assert.equal(ui.api.validate({...defaults,modePrimary:'3',modeSecondary:'4'}).error,undefined);
  for(const action of actions.filter(action=>!['modePrimary','modeSecondary'].includes(action))) {
    assert.ok(ui.api.validate({...defaults,[action]:'5'}).error,action);
  }
  assert.match(ui.api.validate({...defaults,modeSecondary:'1'}).error,/重复/);
});

test('mode selectors 1 and 2 require active quick mode and select primary or secondary without changing labels',()=>{
  const ui=setup({initial:'1'});
  assert.equal(ui.key('1').defaultPrevented,false); assert.equal(ui.key('2').defaultPrevented,false);
  assert.equal(ui.dialog.open,false); ui.key('q',{altKey:true});
  assert.equal(ui.key('2').defaultPrevented,true); assert.equal(ui.title.textContent,'选择次代码');
  assert.equal(ui.list.children.length,2); assert.equal(ui.primary.value,'1');
  assert.equal(ui.key('1').defaultPrevented,true); assert.equal(ui.title.textContent,'选择主代码');
  ui.key('2'); ui.key('s'); ui.key('Enter');
  assert.equal(ui.primary.value,'1'); assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,true]);
  assert.equal(ui.document.activeElement,ui.form); assert.equal(ui.nextCalls(),0);
});

test('mode selector digits remain normal text in search, notes, selects and editable content',()=>{
  const ui=setup({initial:'1'}); ui.key('q',{altKey:true}); ui.searchFor('代码');
  for(const key of ['1','2']) assert.equal(ui.key(key).defaultPrevented,false,key);
  assert.equal(ui.title.textContent,'选择主代码'); ui.key('Enter');
  for(const target of [ui.note,ui.primary,ui.search]) {
    target.focus(); for(const key of ['1','2']) assert.equal(ui.key(key).defaultPrevented,false,`${target.tagName}: ${key}`);
    assert.equal(ui.dialog.open,false);
  }
  const editable={tagName:'DIV',isContentEditable:true,focus(){ui.document.activeElement=this;},closest(){return this;}};
  editable.focus(); assert.equal(ui.key('1').defaultPrevented,false); assert.equal(ui.key('2').defaultPrevented,false);
  assert.equal(ui.dialog.open,false); assert.equal(ui.nextCalls(),0);
});

test('first Enter selects code and only the second Enter submits next with primary present and optional secondary',async()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('s');
  ui.key('Enter'); assert.equal(ui.primary.value,'2'); assert.equal(ui.dialog.open,false);
  assert.equal(ui.document.activeElement,ui.form); assert.equal(ui.nextCalls(),0);
  assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,false]);
  assert.equal(ui.key('Enter').defaultPrevented,true); await new Promise(setImmediate);
  assert.equal(ui.nextCalls(),1); assert.equal(ui.nextClicks(),1); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('second Enter never advances without primary or from a typing field, inactive mode or locked state',async()=>{
  const noPrimary=setup(); noPrimary.key('q',{altKey:true}); noPrimary.form.dataset.busy='true'; noPrimary.refresh();
  noPrimary.form.dataset.busy='false'; noPrimary.form.focus(); noPrimary.key('Enter');
  assert.equal(noPrimary.nextCalls(),0);
  const inactive=setup({initial:'1'}); inactive.form.focus(); assert.equal(inactive.key('Enter').defaultPrevented,false);
  for(const options of [{formState:{locked:'true'}},{formState:{splitting:'true'}},{primaryDisabled:true}]) {
    const ui=setup({initial:'1',storedMode:'on',...options}); ui.form.focus(); ui.key('Enter'); assert.equal(ui.nextCalls(),0,JSON.stringify(options));
  }
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('Enter');
  for(const target of [ui.note,ui.primary,ui.search]) { target.focus(); assert.equal(ui.key('Enter').defaultPrevented,false,target.tagName); }
  await new Promise(setImmediate); assert.equal(ui.nextCalls(),0); assert.equal(inactive.nextCalls(),0);
});

test('second Enter can fall back to the existing next button and never clicks its disabled state',async()=>{
  const ui=setup({nextHook:false}); ui.key('q',{altKey:true}); ui.key('Enter'); ui.key('Enter');
  await new Promise(setImmediate); assert.equal(ui.nextClicks(),1); assert.equal(ui.nextCalls(),0);
  ui.next.disabled=true; ui.key('Enter'); await new Promise(setImmediate); assert.equal(ui.nextClicks(),1);
});

test('mode selectors and skip action can be customized and are saved together with all eleven settings',()=>{
  const ui=setup({withSettings:true}); ui.record('modePrimary','3'); ui.record('modeSecondary','4'); ui.record('skip','v',{altKey:true});
  ui.submitSettings(); assert.equal(ui.writes.length,1); const saved=JSON.parse(ui.writes[0].value);
  assert.equal(Object.keys(saved).length,11); assert.equal(saved.modePrimary,'3'); assert.equal(saved.modeSecondary,'4'); assert.equal(saved.skip,'Alt+V');
  ui.save.focus(); ui.key('q',{altKey:true}); ui.key('Enter');
  assert.equal(ui.key('1').defaultPrevented,false); assert.equal(ui.key('2').defaultPrevented,false);
  ui.key('4'); assert.equal(ui.title.textContent,'选择次代码'); ui.key('3'); assert.equal(ui.title.textContent,'选择主代码');
});

test('old eight-action preferences preserve every binding while upgrading the new three actions',()=>{
  const saved={primary:'Alt+R',secondary:'Alt+T',save:'Ctrl+Enter',next:'Ctrl+Shift+Enter',pickerUp:'A',pickerDown:'D',pickerConfirm:'Alt+Z',pickerClose:'Alt+X'};
  const ui=setup({withSettings:true,stored:JSON.stringify(saved)});
  assert.deepEqual(ui.settingsFields.slice(0,8).map(field=>field.value),Object.values(saved));
  assert.deepEqual(ui.settingsFields.slice(8).map(field=>field.value),['1','2','Alt+Enter']);
  ui.key('r',{altKey:true}); ui.key('d'); ui.key('z',{altKey:true}); assert.equal(ui.primary.value,'2');
  assert.equal(ui.writes.length,0); assert.equal(ui.nextCalls(),0);
});

test('old preference using Alt+Enter keeps that binding and disables only the new skip binding',()=>{
  const saved={primary:'Alt+J',secondary:'Alt+K',save:'Alt+Enter',next:'Ctrl+Shift+Enter'};
  const ui=setup({withSettings:true,stored:JSON.stringify(saved)});
  assert.equal(ui.settingsFields[2].value,'Alt+Enter'); assert.equal(ui.settingsFields[10].value,'未设置');
  assert.equal(ui.hints[10].textContent,'未设置'); assert.match(ui.settingsStatus.textContent,/原快捷键已保留/);
  ui.key('j',{altKey:true}); ui.key('Enter'); ui.key('Enter',{altKey:true});
  assert.equal(ui.clicks(),1); assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
});

test('first Alt+Enter highlights optional skip reason, then second waits for draft saving without submitting',async()=>{
  let complete;
  const pending=new Promise(resolve=>{complete=resolve;}), ui=setup({beforeNavigation:()=>pending});
  ui.key('q',{altKey:true}); ui.key('s'); ui.key('Enter');
  assert.equal(ui.key('Enter',{altKey:true}).defaultPrevented,true);
  assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.note.classList.contains('shortcut-skip-attention'),true);
  assert.equal(ui.document.activeElement,ui.note); assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
  assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.key('Enter',{altKey:true});
  assert.equal(ui.prepCalls(),1); assert.equal(ui.navigations.length,0); assert.equal(ui.nextCalls(),0);
  assert.deepEqual(JSON.parse(JSON.stringify(ui.prepOptions)),[{force:true}]);
  complete(0); await new Promise(setImmediate);
  assert.equal(ui.navigations.length,1); assert.match(ui.navigations[0],/\/r\/1\/\?a=2$/);
  assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.note.classList.contains('shortcut-skip-attention'),false);
  assert.equal(ui.primary.value,'2'); assert.equal(ui.sessionValues.get(ui.modeKey),'on'); assert.equal(ui.nextCalls(),0);
});

test('skip works from an unselected open picker and deduplicates pending navigation',async()=>{
  let complete; const pending=new Promise(resolve=>{complete=resolve;});
  const ui=setup({beforeNavigation:()=>pending}); ui.key('q',{altKey:true});
  ui.key('Enter',{altKey:true}); ui.key('Enter',{altKey:true});
  assert.equal(ui.prepCalls(),1); assert.equal(ui.primary.value,''); assert.equal(ui.nextCalls(),0); assert.equal(ui.navigations.length,0);
  complete(3); await new Promise(setImmediate); assert.equal(ui.navigations.length,1); assert.equal(ui.changes(),0);
});

test('failed draft flush never skips, loses the current label or turns off continuous mode',async()=>{
  for(const beforeNavigation of [()=>Promise.resolve(false),()=>Promise.reject(Error('save failed'))]) {
    const ui=setup({beforeNavigation}); ui.key('q',{altKey:true}); ui.key('s'); ui.key('Enter');
    ui.key('Enter',{altKey:true}); ui.key('Enter',{altKey:true}); await new Promise(setImmediate);
    assert.equal(ui.navigations.length,0); assert.equal(ui.primary.value,'2'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
    assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.note.classList.contains('shortcut-skip-attention'),true);
    assert.equal(ui.nextCalls(),0); ui.key('Enter',{altKey:true}); await new Promise(setImmediate); assert.equal(ui.prepCalls(),2);
  }
});

test('skip is mode-specific, annotation-only and blocked by locked, disabled or splitting state',async()=>{
  const inactive=setup(); inactive.key('Enter',{altKey:true}); assert.equal(inactive.prepCalls(),0);
  for(const options of [{mode:'decision'},{formState:{locked:'true'}},{formState:{splitting:'true'}},{primaryDisabled:true}]) {
    const ui=setup({storedMode:'on',...options}); ui.key('Enter',{altKey:true}); await new Promise(setImmediate);
    assert.equal(ui.prepCalls(),0,JSON.stringify(options)); assert.equal(ui.navigations.length,0,JSON.stringify(options));
  }
  const busy=setup({storedMode:'on',formState:{busy:'true'}}); busy.key('Enter',{altKey:true}); busy.key('Enter',{altKey:true}); await new Promise(setImmediate);
  assert.equal(busy.prepCalls(),1); assert.equal(busy.navigations.length,1);
});

test('skip only accepts same-round unit links or the current project progress link',async()=>{
  for(const skipUrl of ['/r/1/?a=42','/p/1/progress/']) {
    const ui=setup({skipUrl,storedMode:'on'}); ui.key('Enter',{altKey:true}); ui.key('Enter',{altKey:true}); await new Promise(setImmediate);
    assert.equal(ui.navigations.length,1,skipUrl); assert.ok(ui.navigations[0].endsWith(skipUrl));
  }
  for(const skipUrl of ['https://evil.example/r/1/?a=2','//evil.example/r/1/?a=2','javascript:alert(1)',
    '/r/2/?a=2','/p/2/progress/','/r/1/?a=text','/r/1/?a=2&next=https://evil.example','/r/1/delete/','']) {
    const ui=setup({skipUrl,storedMode:'on'}); ui.key('Enter',{altKey:true}); ui.key('Enter',{altKey:true}); await new Promise(setImmediate);
    assert.equal(ui.navigations.length,0,skipUrl); assert.equal(ui.nextCalls(),0,skipUrl); assert.equal(ui.prepCalls(),0,skipUrl);
  }
});

test('a pending second-Enter submission is deduplicated and blocks competing skip navigation',async()=>{
  let complete, calls=0; const pending=new Promise(resolve=>{complete=resolve;});
  const ui=setup(); ui.form.platformSubmitNext=()=>{calls++;return pending;};
  ui.key('q',{altKey:true}); ui.key('Enter'); ui.key('Enter'); ui.key('Enter'); ui.key('Enter',{altKey:true});
  assert.equal(calls,1); assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
  assert.equal(ui.form.dataset.navigationPending,'true');
  complete(false); await new Promise(setImmediate);
  assert.equal(ui.primary.value,'1'); assert.equal(ui.sessionValues.get(ui.modeKey),'on'); assert.equal(ui.form.dataset.navigationPending,'false');
});

test('failed submission preserves the label and mode and permits a deliberate retry',async()=>{
  let calls=0; const ui=setup(); ui.form.platformSubmitNext=()=>{calls++;return Promise.reject(Error('submit failed'));};
  ui.key('q',{altKey:true}); ui.key('Enter'); ui.key('Enter'); await new Promise(setImmediate);
  assert.equal(ui.navigations.length,0); assert.equal(ui.primary.value,'1'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.key('Enter'); await new Promise(setImmediate); assert.equal(calls,2);
});

test('skip without a draft-flush hook fails safely instead of navigating with unsaved work',async()=>{
  const ui=setup(); ui.form.platformSaveBeforeNavigation=undefined;
  ui.key('q',{altKey:true}); ui.key('Enter'); ui.key('Enter',{altKey:true}); ui.key('Enter',{altKey:true}); await new Promise(setImmediate);
  assert.equal(ui.navigations.length,0); assert.equal(ui.primary.value,'1'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  assert.equal(ui.nextCalls(),0);
});

test('second Enter delegates to the asynchronous next hook while a draft save is busy and deduplicates retries',async()=>{
  let complete,calls=0;
  const pending=new Promise(resolve=>{complete=resolve;});
  const ui=setup({initial:'1',storedMode:'on',formState:{busy:'true'}});
  ui.form.platformSubmitNext=()=>{calls++;return pending;}; ui.form.focus();
  assert.equal(ui.key('Enter').defaultPrevented,true); ui.key('Enter');
  assert.equal(calls,1); assert.equal(ui.clicks(),0); assert.equal(ui.navigations.length,0);
  complete(false); await new Promise(setImmediate);
  assert.equal(ui.primary.value,'1'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('mouse can edit notes while the nonmodal picker stays open without selecting or exiting quick mode',async()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('s'); ui.note.focus();
  assert.equal(ui.showCalls(),1); assert.equal(ui.modalCalls(),0);
  for(const key of ['w','s','1','2','Enter']) assert.equal(ui.key(key).defaultPrevented,false,key);
  ui.note.value='先记下疑问\n稍后再判断'; ui.note.dispatchEvent({type:'input'});
  assert.equal(ui.dialog.open,true); assert.equal(ui.primary.value,''); assert.equal(ui.changes(),0);
  assert.equal(ui.sessionValues.get(ui.modeKey),'on'); assert.equal(ui.nextCalls(),0);
  ui.list.focus(); ui.key('s'); ui.key('Enter');
  assert.equal(ui.primary.value,'3'); assert.equal(ui.note.value,'先记下疑问\n稍后再判断');
  assert.equal(ui.sessionValues.get(ui.modeKey),'on'); await new Promise(setImmediate); assert.equal(ui.nextCalls(),0);
});

test('mouse selection outside the picker preserves quick mode and form Enter advances instead of choosing a pending code',async()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('s');
  ui.codeButtons[2].focus(); ui.codeButtons[2].click();
  assert.equal(ui.primary.value,'3'); assert.equal(ui.changes(),1); assert.equal(ui.dialog.open,true);
  assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.form.focus(); assert.equal(ui.key('Enter').defaultPrevented,true); await new Promise(setImmediate);
  assert.equal(ui.primary.value,'3'); assert.equal(ui.changes(),1); assert.equal(ui.nextCalls(),1);
  assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('skip confirmation button saves an optional reason without requiring a primary or submitting a result',async()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('Enter',{altKey:true});
  assert.equal(ui.dialog.open,false); assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.document.activeElement,ui.note);
  ui.note.value='需要更多上下文'; ui.note.dispatchEvent({type:'input'});
  assert.equal(ui.key('Enter').defaultPrevented,false); assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
  ui.skipConfirm.click(); await new Promise(setImmediate);
  assert.equal(ui.prepCalls(),1); assert.deepEqual(JSON.parse(JSON.stringify(ui.prepOptions)),[{force:true}]);
  assert.equal(ui.navigations.length,1); assert.equal(ui.note.value,'需要更多上下文'); assert.equal(ui.primary.value,'');
  assert.equal(ui.nextCalls(),0); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('skip reason is optional and the confirm button and repeated keys share the same pending guard',async()=>{
  let complete;const pending=new Promise(resolve=>{complete=resolve;}),ui=setup({beforeNavigation:()=>pending});
  ui.key('q',{altKey:true}); ui.key('Enter',{altKey:true}); ui.skipConfirm.click();
  ui.skipConfirm.click(); ui.key('Enter',{altKey:true});
  assert.equal(ui.note.value,''); assert.equal(ui.prepCalls(),1); assert.equal(ui.navigations.length,0);
  complete(0); await new Promise(setImmediate); assert.equal(ui.navigations.length,1); assert.equal(ui.nextCalls(),0);
});

test('canceling skip removes the note highlight and retains labels, notes and quick mode without navigation',async()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('s'); ui.key('Enter'); ui.note.value='留待讨论';
  ui.key('Enter',{altKey:true}); ui.skipCancel.click();
  assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.note.classList.contains('shortcut-skip-attention'),false);
  assert.equal(ui.document.activeElement,ui.form); assert.equal(ui.primary.value,'2'); assert.equal(ui.note.value,'留待讨论');
  assert.equal(ui.sessionValues.get(ui.modeKey),'on'); await new Promise(setImmediate);
  assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0); assert.equal(ui.nextCalls(),0);
});

test('only Alt+X exits quick mode from a skip reason and clears the prompt without saving or navigating',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('Enter',{altKey:true});
  assert.equal(ui.key('Escape').defaultPrevented,false); assert.equal(ui.skipPrompt.hidden,false);
  assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  assert.equal(ui.key('x',{altKey:true}).defaultPrevented,true);
  assert.equal(ui.sessionValues.has(ui.modeKey),false); assert.equal(ui.skipPrompt.hidden,true);
  assert.equal(ui.note.classList.contains('shortcut-skip-attention'),false);
  assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
});

test('legacy pages without a skip prompt safely leave the current work in place',async()=>{
  const ui=setup({withSkipPrompt:false}); ui.key('q',{altKey:true});
  ui.key('Enter',{altKey:true}); ui.key('Enter',{altKey:true}); await new Promise(setImmediate);
  assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0); assert.equal(ui.primary.value,'');
  assert.equal(ui.dialog.open,true); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('both visible skip buttons open the optional-reason prompt outside quick mode without selecting a code',()=>{
  for(const index of [0,1]) {
    const ui=setup();
    assert.equal(ui.skipButtons[index].disabled,false);
    ui.skipButtons[index].click();
    assert.equal(ui.skipPrompt.hidden,false,index); assert.equal(ui.document.activeElement,ui.note,index);
    assert.equal(ui.note.classList.contains('shortcut-skip-attention'),true,index);
    assert.equal(ui.primary.value,'',index); assert.equal(ui.prepCalls(),0,index); assert.equal(ui.nextCalls(),0,index);
    assert.equal(ui.sessionValues.has(ui.modeKey),false,index); assert.equal(ui.form.dataset.quickMode,'false',index);
    assert.equal(ui.dialog.open,false,index);
  }
});

test('ordinary-mode skip confirmation saves an optional note as draft, never submits and does not enable quick mode',async()=>{
  const ui=setup(); ui.skipButtons[0].click(); ui.note.value='暂不确定，稍后处理';
  ui.skipConfirm.click(); await new Promise(setImmediate);
  assert.equal(ui.prepCalls(),1); assert.deepEqual(JSON.parse(JSON.stringify(ui.prepOptions)),[{force:true}]);
  assert.equal(ui.navigations.length,1); assert.match(ui.navigations[0],/\/r\/1\/\?a=2$/);
  assert.equal(ui.note.value,'暂不确定，稍后处理'); assert.equal(ui.primary.value,''); assert.equal(ui.nextCalls(),0);
  assert.equal(ui.sessionValues.has(ui.modeKey),false); assert.equal(ui.form.dataset.quickMode,'false');
});

test('a second visible skip click confirms an empty-code draft and deduplicates all skip entrances while saving',async()=>{
  let complete;
  const ui=setup({beforeNavigation:()=>new Promise(resolve=>{complete=resolve;})});
  ui.skipButtons[0].click(); ui.skipButtons[1].click();
  ui.skipButtons[0].click(); ui.skipButtons[1].click(); ui.skipConfirm.click();
  assert.equal(ui.prepCalls(),1); assert.equal(ui.navigations.length,0); assert.equal(ui.nextCalls(),0);
  assert.equal(ui.primary.value,''); assert.equal(ui.note.value,'');
  complete(0); await new Promise(setImmediate);
  assert.equal(ui.navigations.length,1); assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.changes(),0);
});

test('the picker skip button closes the chooser but retains quick mode and existing labels for draft saving',async()=>{
  const ui=setup({initial:'1'}); ui.key('q',{altKey:true}); ui.key('s');
  assert.equal(ui.dialog.open,true); ui.skipButtons[1].click();
  assert.equal(ui.dialog.open,false); assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.primary.value,'1');
  assert.equal(ui.sessionValues.get(ui.modeKey),'on'); assert.equal(ui.nextCalls(),0);
  ui.skipConfirm.click(); await new Promise(setImmediate);
  assert.equal(ui.prepCalls(),1); assert.equal(ui.navigations.length,1); assert.equal(ui.primary.value,'1');
  assert.equal(ui.sessionValues.get(ui.modeKey),'on'); assert.equal(ui.changes(),0); assert.equal(ui.nextCalls(),0);
});

test('Enter on the picker skip button keeps native activation rather than selecting a pending code or submitting',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('s'); ui.skipButtons[1].focus();
  assert.equal(ui.key('Enter').defaultPrevented,false); assert.equal(ui.primary.value,''); assert.equal(ui.changes(),0);
  assert.equal(ui.nextCalls(),0); assert.equal(ui.prepCalls(),0); assert.equal(ui.dialog.open,true);
  ui.skipButtons[1].click(); assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.dialog.open,false);
  assert.equal(ui.sessionValues.get(ui.modeKey),'on'); assert.equal(ui.primary.value,'');
});

test('both visible skip buttons disable during pending navigation and recover together after a saving failure',async()=>{
  let complete;
  const ui=setup({beforeNavigation:()=>new Promise(resolve=>{complete=resolve;})});
  ui.skipButtons[0].click(); ui.skipConfirm.click();
  assert.equal(ui.skipButtons.every(button=>button.disabled),true);
  assert.equal(ui.form.dataset.navigationPending,'true');
  complete(false); await new Promise(setImmediate);
  assert.equal(ui.skipButtons.some(button=>button.disabled),false); assert.equal(ui.skipPrompt.hidden,false);
  assert.equal(ui.form.dataset.navigationPending,'false');
  assert.equal(ui.navigations.length,0); assert.equal(ui.nextCalls(),0);
});

test('visible skip buttons retain input and the prompt after failed saving and allow a deliberate retry',async()=>{
  for(const failure of [()=>Promise.resolve(false),()=>Promise.reject(Error('保存失败'))]) {
    let fail=true;
    const ui=setup({initial:'2',beforeNavigation:()=>fail?failure():Promise.resolve(0)});
    ui.note.value='保留原有备注'; ui.secondary[0].checked=true;
    ui.skipButtons[0].click(); ui.skipConfirm.click(); await new Promise(setImmediate);
    assert.equal(ui.navigations.length,0); assert.equal(ui.prepCalls(),1); assert.equal(ui.primary.value,'2');
    assert.equal(ui.note.value,'保留原有备注'); assert.equal(ui.secondary[0].checked,true);
    assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.note.classList.contains('shortcut-skip-attention'),true);
    assert.equal(ui.sessionValues.has(ui.modeKey),false); assert.equal(ui.nextCalls(),0);
    fail=false; ui.skipButtons[1].click(); await new Promise(setImmediate);
    assert.equal(ui.prepCalls(),2); assert.equal(ui.navigations.length,1); assert.equal(ui.nextCalls(),0);
  }
});

test('visible skip buttons reject non-annotation, locked, split-preview, splitting and disabled submission states',async()=>{
  for(const options of [{mode:'decision'},{formState:{locked:'true'}},{formState:{splitting:'true'}},
    {formState:{splitPreview:'true'}},{primaryDisabled:true},{primaryDisabled:true,formState:{busy:'true'}}]) {
    const ui=setup(options); ui.skipButtons[0].click(); ui.skipButtons[1].click(); ui.skipConfirm.click();
    await new Promise(setImmediate);
    assert.equal(ui.skipPrompt.hidden,true,JSON.stringify(options)); assert.equal(ui.prepCalls(),0,JSON.stringify(options));
    assert.equal(ui.navigations.length,0,JSON.stringify(options)); assert.equal(ui.nextCalls(),0,JSON.stringify(options));
    assert.equal(ui.sessionValues.has(ui.modeKey),false,JSON.stringify(options));
  }
});

test('visible skip buttons become usable again after a split preview is cancelled',()=>{
  const ui=setup(); ui.form.dataset.splitPreview='true'; ui.refresh();
  assert.equal(ui.skipButtons.every(button=>button.disabled),true);
  ui.skipButtons[0].click(); assert.equal(ui.skipPrompt.hidden,true);
  ui.form.dataset.splitPreview='false'; ui.windowEvents['platform:split-preview-change']();
  assert.equal(ui.skipButtons.some(button=>button.disabled),false);
  ui.skipButtons[0].click(); assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.prepCalls(),0);
});

test('draft autosaving does not block visible skip buttons and navigation still waits for the draft-flush hook',async()=>{
  let complete;
  const ui=setup({formState:{busy:'true',savingDraft:'true'},beforeNavigation:()=>new Promise(resolve=>{complete=resolve;})});
  ui.skipButtons[0].click(); ui.skipButtons[1].click();
  assert.equal(ui.prepCalls(),1); assert.equal(ui.navigations.length,0); assert.equal(ui.nextCalls(),0);
  complete(2); await new Promise(setImmediate); assert.equal(ui.navigations.length,1);
});

test('visible skip buttons use the same safe destination whitelist as keyboard skipping',async()=>{
  for(const skipUrl of ['https://evil.example/r/1/?a=2','//evil.example/r/1/?a=2','/r/2/?a=2','/p/2/progress/','']) {
    const ui=setup({skipUrl}); ui.skipButtons[0].click(); ui.skipConfirm.click(); await new Promise(setImmediate);
    assert.equal(ui.prepCalls(),0,skipUrl); assert.equal(ui.navigations.length,0,skipUrl); assert.equal(ui.nextCalls(),0,skipUrl);
    assert.equal(ui.skipPrompt.hidden,false,skipUrl); assert.equal(ui.sessionValues.has(ui.modeKey),false,skipUrl);
  }
});

test('both visible skip buttons show updated custom skip bindings while the inactive keyboard shortcut stays inactive',()=>{
  const ui=setup({withSettings:true}); ui.record('skip','v',{altKey:true}); ui.submitSettings(); ui.form.focus();
  assert.deepEqual(ui.skipButtonHints.map(hint=>hint.textContent),['Alt+V','Alt+V']);
  assert.equal(ui.key('v',{altKey:true}).defaultPrevented,false); assert.equal(ui.skipPrompt.hidden,true);
  ui.skipButtons[0].click(); assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.sessionValues.has(ui.modeKey),false);
  ui.skipCancel.click(); ui.key('q',{altKey:true}); ui.key('v',{altKey:true});
  assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('a legacy unassigned skip shortcut leaves mouse skip available without changing preserved bindings',()=>{
  const stored=JSON.stringify({primary:'Alt+J',secondary:'Alt+K',save:'Alt+Enter',next:'Ctrl+Shift+Enter'});
  const ui=setup({stored}); assert.deepEqual(ui.skipButtonHints.map(hint=>hint.textContent),['未设置','未设置']);
  ui.skipButtons[0].click(); assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.prepCalls(),0);
  assert.equal(ui.sessionValues.has(ui.modeKey),false); assert.equal(ui.hints[2].textContent,'Alt+Enter');
});

test('canceling a mouse-triggered skip in ordinary mode preserves edits without saving or enabling quick mode',()=>{
  const ui=setup({initial:'2'}); ui.note.value='待复查'; ui.skipButtons[0].click(); ui.skipCancel.click();
  assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.note.classList.contains('shortcut-skip-attention'),false);
  assert.equal(ui.note.value,'待复查'); assert.equal(ui.primary.value,'2'); assert.equal(ui.prepCalls(),0);
  assert.equal(ui.navigations.length,0); assert.equal(ui.nextCalls(),0); assert.equal(ui.sessionValues.has(ui.modeKey),false);
});

test('visible skip buttons fail safely when the draft-save hook or skip-prompt markup is unavailable',async()=>{
  const missingSave=setup(); missingSave.form.platformSaveBeforeNavigation=undefined;
  missingSave.skipButtons[0].click(); missingSave.skipConfirm.click(); await new Promise(setImmediate);
  assert.equal(missingSave.navigations.length,0); assert.equal(missingSave.prepCalls(),0);
  assert.equal(missingSave.skipPrompt.hidden,false); assert.equal(missingSave.nextCalls(),0);
  const missingPrompt=setup({withSkipPrompt:false}); missingPrompt.skipButtons[0].click(); missingPrompt.skipButtons[1].click();
  await new Promise(setImmediate);
  assert.equal(missingPrompt.prepCalls(),0); assert.equal(missingPrompt.navigations.length,0); assert.equal(missingPrompt.nextCalls(),0);
});

test('the task Next link first requests an optional reason and then saves a draft to its own target rather than the skip target',async()=>{
  const ui=setup({nextUrls:['/r/1/?a=8']});
  assert.equal(ui.form.dataset.nextReminderReady,'true'); assert.equal(ui.clickNext().defaultPrevented,true);
  assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.document.activeElement,ui.note);
  assert.equal(ui.note.classList.contains('shortcut-skip-attention'),true); assert.equal(ui.prepCalls(),0);
  assert.equal(ui.navigations.length,0); assert.equal(ui.sessionValues.has(ui.modeKey),false);
  ui.clickNext(); await new Promise(setImmediate);
  assert.equal(ui.prepCalls(),1); assert.deepEqual(JSON.parse(JSON.stringify(ui.prepOptions)),[{force:true}]);
  assert.deepEqual(ui.navigations,['/r/1/?a=8']); assert.equal(ui.primary.value,''); assert.equal(ui.note.value,'');
  assert.equal(ui.nextCalls(),0); assert.equal(ui.sessionValues.has(ui.modeKey),false);
});

test('the shared save-and-skip confirmation button retains a task Next link destination and current edits',async()=>{
  const ui=setup({initial:'2',nextUrls:['http://127.0.0.1:8001/r/1/?a=8']});
  ui.note.value='稍后补充判断'; ui.secondary[0].checked=true;
  ui.clickNext(); ui.skipConfirm.click(); await new Promise(setImmediate);
  assert.deepEqual(ui.navigations,['/r/1/?a=8']); assert.equal(ui.primary.value,'2'); assert.equal(ui.secondary[0].checked,true);
  assert.equal(ui.note.value,'稍后补充判断'); assert.equal(ui.prepCalls(),1); assert.equal(ui.nextCalls(),0);
});

test('switching the task Next target requires a fresh reminder before saving or navigating',async()=>{
  const ui=setup({nextUrls:['/r/1/?a=8','/r/1/?a=9']});
  ui.clickNext(0); ui.clickNext(1); await new Promise(setImmediate);
  assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0); assert.equal(ui.skipPrompt.hidden,false);
  ui.clickNext(1); await new Promise(setImmediate);
  assert.equal(ui.prepCalls(),1); assert.deepEqual(ui.navigations,['/r/1/?a=9']); assert.equal(ui.nextCalls(),0);
});

test('the visible skip buttons and quick-mode skip key confirm the destination already requested by task Next',async()=>{
  for(const confirmation of ['button','keyboard']) {
    const ui=setup({nextUrls:['/r/1/?a=8'],storedMode:confirmation==='keyboard'?'on':null});
    ui.clickNext();
    if(confirmation==='keyboard')ui.key('Enter',{altKey:true});else ui.skipButtons[0].click();
    await new Promise(setImmediate);
    assert.equal(ui.prepCalls(),1,confirmation); assert.deepEqual(ui.navigations,['/r/1/?a=8'],confirmation);
    assert.equal(ui.nextCalls(),0,confirmation); assert.equal(ui.form.dataset.quickMode,String(confirmation==='keyboard'),confirmation);
  }
});

test('canceling a task Next reminder clears its destination so a later generic skip uses the default skip URL',async()=>{
  const ui=setup({nextUrls:['/r/1/?a=8']}); ui.clickNext(); ui.skipCancel.click();
  assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
  ui.skipButtons[0].click(); assert.equal(ui.prepCalls(),0); ui.skipConfirm.click(); await new Promise(setImmediate);
  assert.deepEqual(ui.navigations,['/r/1/?a=2']); assert.equal(ui.nextCalls(),0);
});

test('opening a code chooser after a task Next reminder clears the old pending target without losing quick mode',async()=>{
  const ui=setup({nextUrls:['/r/1/?a=8']}); ui.clickNext(); ui.key('q',{altKey:true});
  assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.dialog.open,true); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.key('Enter',{altKey:true}); assert.equal(ui.prepCalls(),0); ui.key('Enter',{altKey:true}); await new Promise(setImmediate);
  assert.deepEqual(ui.navigations,['/r/1/?a=2']); assert.equal(ui.nextCalls(),0); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('a task Next saving failure preserves its destination, prompt and edits for retry',async()=>{
  for(const failure of [()=>Promise.resolve(false),()=>Promise.reject(Error('保存失败'))]) {
    let fail=true;
    const ui=setup({initial:'2',nextUrls:['/r/1/?a=8'],beforeNavigation:()=>fail?failure():Promise.resolve(0)});
    ui.note.value='不要丢失的备注'; ui.clickNext(); ui.clickNext(); await new Promise(setImmediate);
    assert.equal(ui.prepCalls(),1); assert.equal(ui.navigations.length,0); assert.equal(ui.skipPrompt.hidden,false);
    assert.equal(ui.note.value,'不要丢失的备注'); assert.equal(ui.primary.value,'2'); assert.equal(ui.nextCalls(),0);
    fail=false; ui.clickNext(); await new Promise(setImmediate);
    assert.equal(ui.prepCalls(),2); assert.deepEqual(ui.navigations,['/r/1/?a=8']);
  }
});

test('task Next shares the pending guard with mouse skip, keyboard skip and the confirmation button',async()=>{
  let complete;
  const ui=setup({nextUrls:['/r/1/?a=8'],storedMode:'on',beforeNavigation:()=>new Promise(resolve=>{complete=resolve;})});
  ui.clickNext(); ui.clickNext(); ui.clickNext(); ui.skipButtons[0].click(); ui.skipConfirm.click(); ui.key('Enter',{altKey:true});
  assert.equal(ui.prepCalls(),1); assert.equal(ui.navigations.length,0); assert.equal(ui.nextCalls(),0);
  complete(0); await new Promise(setImmediate);
  assert.deepEqual(ui.navigations,['/r/1/?a=8']); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('task Next refuses cross-origin and fragment destinations before opening a prompt or saving',async()=>{
  for(const href of ['https://evil.example/r/1/?a=8','//evil.example/r/1/?a=8','javascript:alert(1)',
    'http://127.0.0.1:8000/r/1/?a=8','/r/1/?a=8#fragment']) {
    const ui=setup({nextUrls:[href]}); assert.equal(ui.clickNext().defaultPrevented,true,href); ui.skipConfirm.click();
    await new Promise(setImmediate);
    assert.equal(ui.skipPrompt.hidden,true,href); assert.equal(ui.prepCalls(),0,href); assert.equal(ui.navigations.length,0,href);
    assert.equal(ui.nextCalls(),0,href); assert.equal(ui.sessionValues.has(ui.modeKey),false,href);
  }
});

test('task Next confirmation permits only the current round unit or project progress destination',async()=>{
  for(const href of ['/r/2/?a=8','/p/2/progress/','/r/1/delete/','/r/1/?a=8&next=https://evil.example','/r/1/?a=text','/r/1/?a=8&a=9']) {
    const ui=setup({nextUrls:[href]}); ui.clickNext(); ui.clickNext(); await new Promise(setImmediate);
    assert.equal(ui.prepCalls(),0,href); assert.equal(ui.navigations.length,0,href); assert.equal(ui.nextCalls(),0,href);
    assert.equal(ui.skipPrompt.hidden,false,href);
  }
  const progress=setup({nextUrls:['/p/1/progress/']}); progress.clickNext(); progress.clickNext(); await new Promise(setImmediate);
  assert.deepEqual(progress.navigations,['/p/1/progress/']); assert.equal(progress.prepCalls(),1); assert.equal(progress.nextCalls(),0);
});

test('modified, middle-button and pre-consumed task Next clicks retain native browser behavior without prompting',()=>{
  for(const properties of [{ctrlKey:true},{metaKey:true},{shiftKey:true},{altKey:true},{button:1},{button:2},{defaultPrevented:true}]) {
    const ui=setup({nextUrls:['/r/1/?a=8']}); const event=ui.clickNext(0,properties);
    assert.equal(event.defaultPrevented,!!properties.defaultPrevented,JSON.stringify(properties));
    assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
  }
});

test('task Next cannot save, prompt or navigate from decision, locked, splitting, preview or disabled forms',async()=>{
  for(const options of [{mode:'decision'},{formState:{locked:'true'}},{formState:{splitting:'true'}},
    {formState:{splitPreview:'true'}},{primaryDisabled:true}]) {
    const ui=setup({nextUrls:['/r/1/?a=8'],...options}); ui.clickNext(); ui.clickNext(); ui.skipConfirm.click();
    await new Promise(setImmediate);
    assert.equal(ui.skipPrompt.hidden,true,JSON.stringify(options)); assert.equal(ui.prepCalls(),0,JSON.stringify(options));
    assert.equal(ui.navigations.length,0,JSON.stringify(options)); assert.equal(ui.nextCalls(),0,JSON.stringify(options));
  }
});

test('pages without a coding form never mark task Next as reminder-ready or consume its clicks',()=>{
  const ui=setup({withForm:false,nextUrls:['/r/1/?a=8']}); assert.equal(ui.form.dataset.nextReminderReady,undefined);
  assert.equal(ui.clickNext().defaultPrevented,false); assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
});

test('opening the last-task completion prompt closes a pending picker without selecting a code or exiting quick mode',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('s');
  assert.equal(ui.dialog.open,true); ui.openCompletion();
  assert.equal(ui.completionDialog.open,true); assert.equal(ui.dialog.open,false); assert.equal(ui.dialog.hidden,true);
  assert.equal(ui.primary.value,''); assert.equal(ui.changes(),0); assert.equal(ui.nextCalls(),0); assert.equal(ui.prepCalls(),0);
  assert.equal(ui.form.dataset.quickMode,'true'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('completion modal leaves W/S, mode selectors, Enter and modified shortcuts to the dialog without coding side effects',async()=>{
  const ui=setup({initial:'1',storedMode:'on'}); ui.openCompletion(); ui.form.focus();
  for(const [key,properties] of [['w',{}],['s',{}],['1',{}],['2',{}],['Enter',{}],['Enter',{altKey:true}],
    ['q',{altKey:true}],['e',{altKey:true}],['f',{altKey:true}],['g',{altKey:true}],['x',{altKey:true}]]) {
    assert.equal(ui.key(key,properties).defaultPrevented,false,`${key} ${JSON.stringify(properties)}`);
  }
  await new Promise(setImmediate);
  assert.equal(ui.dialog.open,false); assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.primary.value,'1');
  assert.equal(ui.changes(),0); assert.equal(ui.clicks(),0); assert.equal(ui.nextCalls(),0); assert.equal(ui.prepCalls(),0);
  assert.equal(ui.navigations.length,0); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.completionDialog.open=false;
  assert.equal(ui.key('s').defaultPrevented,true); assert.equal(ui.dialog.open,true);
  assert.equal(ui.title.textContent,'选择主代码'); ui.key('Enter');
  assert.equal(ui.primary.value,'2'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('opening completion clears the skip reminder and old task Next destination while preserving notes and quick mode',async()=>{
  const ui=setup({initial:'1',storedMode:'on',nextUrls:['/r/1/?a=8']}); ui.note.value='保留备注'; ui.clickNext();
  assert.equal(ui.skipPrompt.hidden,false); assert.equal(ui.note.classList.contains('shortcut-skip-attention'),true);
  ui.openCompletion();
  assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.note.classList.contains('shortcut-skip-attention'),false);
  assert.equal(ui.note.value,'保留备注'); assert.equal(ui.primary.value,'1'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  assert.equal(ui.prepCalls(),0); assert.equal(ui.navigations.length,0);
  ui.completionDialog.open=false; ui.key('Enter',{altKey:true}); assert.equal(ui.prepCalls(),0);
  ui.key('Enter',{altKey:true}); await new Promise(setImmediate);
  assert.deepEqual(ui.navigations,['/r/1/?a=2']); assert.equal(ui.nextCalls(),0);
});

test('a completion modal never implicitly enters quick mode and normal activation resumes only after it closes',()=>{
  const ui=setup(); ui.openCompletion(); assert.equal(ui.key('q',{altKey:true}).defaultPrevented,false);
  assert.equal(ui.form.dataset.quickMode,'false'); assert.equal(ui.sessionValues.has(ui.modeKey),false); assert.equal(ui.dialog.open,false);
  ui.completionDialog.open=false; assert.equal(ui.key('q',{altKey:true}).defaultPrevented,true);
  assert.equal(ui.dialog.open,true); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  const legacy=setup({withCompletionDialog:false}); legacy.key('q',{altKey:true});
  assert.equal(legacy.dialog.open,true); assert.equal(legacy.sessionValues.get(legacy.modeKey),'on');
});

test('quick-mode W/S still navigate the active chooser after mouse focus moves to material, body or a code card',()=>{
  for(const targetName of ['material','body','code-card']) {
    const ui=setup({count:4}); ui.key('q',{altKey:true}); ui.key('s');
    const target=targetName==='body'?ui.document.body:targetName==='code-card'?ui.codeButtons[3]:ui.element({tagName:'P'});
    target.focus();
    assert.equal(ui.key('s').defaultPrevented,true,targetName);
    assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-2',targetName);
    assert.equal(ui.key('w').defaultPrevented,true,targetName);
    assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1',targetName);
    assert.equal(ui.dialog.open,true,targetName); assert.equal(ui.primary.value,'',targetName);
    assert.equal(ui.sessionValues.get(ui.modeKey),'on',targetName);
  }
});

test('quick-mode W/S reopen the last secondary chooser after mouse collapse without switching back to primary',()=>{
  const ui=setup({initial:'1',count:4}); ui.key('q',{altKey:true}); ui.key('2');
  ui.closeButton.focus(); ui.closeButton.click(); assert.equal(ui.dialog.open,false);
  ui.document.body.focus(); assert.equal(ui.key('s').defaultPrevented,true);
  assert.equal(ui.dialog.open,true); assert.equal(ui.title.textContent,'选择次代码');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1');
  ui.key('Enter'); assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,true,false]);
  assert.equal(ui.primary.value,'1'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('quick-mode W/S resume primary navigation after selecting a chooser option with the mouse',()=>{
  const ui=setup({count:4}); ui.key('q',{altKey:true}); ui.list.children[1].click();
  assert.equal(ui.primary.value,'2'); assert.equal(ui.dialog.open,false);
  ui.element({tagName:'P'}).focus(); assert.equal(ui.key('s').defaultPrevented,true);
  assert.equal(ui.dialog.open,true); assert.equal(ui.title.textContent,'选择主代码');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-2');
  ui.key('Enter'); assert.equal(ui.primary.value,'3'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('quick-mode 1/2 switch chooser after mouse focus on radio, checkbox, button or body',()=>{
  for(const targetName of ['radio','checkbox','button','code-card','body']) {
    const ui=setup({initial:'1'}); ui.key('q',{altKey:true});
    const target={radio:ui.secondary[1],checkbox:ui.noCode,button:ui.save,'code-card':ui.codeButtons[1],body:ui.document.body}[targetName];
    target.focus(); assert.equal(ui.key('2').defaultPrevented,true,targetName);
    assert.equal(ui.title.textContent,'选择次代码',targetName);
    target.focus(); assert.equal(ui.key('1').defaultPrevented,true,targetName);
    assert.equal(ui.title.textContent,'选择主代码',targetName);
    assert.equal(ui.changes(),0,targetName); assert.equal(ui.sessionValues.get(ui.modeKey),'on',targetName);
  }
});

test('quick-mode navigation never steals W/S or 1/2 from text inputs, notes, selects or editable descendants',()=>{
  const ui=setup({initial:'1'}); ui.key('q',{altKey:true});
  const editable=ui.element({isContentEditable:true}), editableChild=ui.element({tagName:'SPAN'}); editable.append(editableChild);
  for(const target of [ui.search,ui.note,ui.primary,ui.element({tagName:'INPUT',type:'text'}),editable,editableChild]) {
    target.focus();
    for(const key of ['w','s','1','2']) assert.equal(ui.key(key).defaultPrevented,false,`${target.tagName}: ${key}`);
    assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-0');
    assert.equal(ui.title.textContent,'选择主代码'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  }
  assert.equal(ui.primary.value,'1'); assert.equal(ui.changes(),0);
});

test('quick-mode Enter confirms a pending code after mouse focus on material and then advances from body',async()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.key('s'); ui.element({tagName:'P'}).focus();
  assert.equal(ui.key('Enter').defaultPrevented,true); assert.equal(ui.primary.value,'2');
  assert.equal(ui.dialog.open,false); assert.equal(ui.nextCalls(),0);
  ui.document.body.focus(); assert.equal(ui.key('Enter').defaultPrevented,true);
  await new Promise(setImmediate); assert.equal(ui.nextCalls(),1);
  assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('quick-mode Enter preserves native links and unrelated buttons while chooser is open or collapsed',async()=>{
  const ui=setup({initial:'1'}); ui.key('q',{altKey:true}); ui.key('s');
  const link=ui.element({tagName:'A'}),button=ui.element({tagName:'BUTTON'});
  for(const target of [link,button,ui.closeButton]) {
    target.focus(); assert.equal(ui.key('Enter').defaultPrevented,false,target.tagName);
    assert.equal(ui.primary.value,'1'); assert.equal(ui.dialog.open,true);
  }
  ui.closeButton.click(); assert.equal(ui.dialog.open,false);
  for(const target of [link,button]) {
    target.focus(); assert.equal(ui.key('Enter').defaultPrevented,false,target.tagName);
  }
  await new Promise(setImmediate); assert.equal(ui.nextCalls(),0); assert.equal(ui.changes(),0);
  assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('mouse selection of a secondary radio makes subsequent quick navigation resume secondary choices',()=>{
  const ui=setup({initial:'1',count:4}); ui.key('q',{altKey:true});
  ui.secondary[2].focus(); ui.secondary[2].checked=true; ui.secondary[2].dispatchEvent({type:'change'});
  ui.document.body.focus(); assert.equal(ui.key('s').defaultPrevented,true);
  assert.equal(ui.title.textContent,'选择次代码'); assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-2');
  ui.key('Enter'); assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,false,true]);
  assert.equal(ui.primary.value,'1'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('mouse selection of a primary code card makes subsequent quick navigation resume primary choices',()=>{
  const ui=setup({initial:'1',count:4}); ui.key('q',{altKey:true}); ui.key('2');
  ui.codeButtons[2].focus(); ui.codeButtons[2].click(); assert.equal(ui.primary.value,'3');
  ui.document.body.focus(); assert.equal(ui.key('s').defaultPrevented,true);
  assert.equal(ui.title.textContent,'选择主代码'); assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-3');
  ui.key('Enter'); assert.equal(ui.primary.value,'4'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('draft-only autosave keeps quick chooser open and responsive without permitting other busy mutations',()=>{
  const ui=setup(); ui.key('q',{altKey:true}); ui.form.dataset.busy='true'; ui.form.dataset.savingDraft='true'; ui.refresh();
  assert.equal(ui.dialog.open,true); ui.document.body.focus(); assert.equal(ui.key('s').defaultPrevented,true);
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1');
  ui.key('Enter'); assert.equal(ui.primary.value,'2'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.document.body.focus(); ui.key('s'); assert.equal(ui.dialog.open,true);
  ui.form.dataset.savingDraft='false'; ui.refresh(); assert.equal(ui.dialog.open,false);
  const before=ui.changes(); ui.key('s'); assert.equal(ui.dialog.open,false); assert.equal(ui.changes(),before);
});

test('quick-mode Enter resumes from form-owned radio and checkbox after mouse focus without requiring a secondary',async()=>{
  for(const targetName of ['radio','checkbox']) {
    const ui=setup({initial:'1'}); ui.key('q',{altKey:true}); ui.closeButton.click();
    const target=targetName==='radio'?ui.secondary[1]:ui.noCode;
    ui.form.append(target); target.focus();
    assert.equal(ui.key('Enter').defaultPrevented,true,targetName);
    await new Promise(setImmediate); assert.equal(ui.nextCalls(),1,targetName);
    assert.equal(ui.primary.value,'1',targetName); assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,false],targetName);
    assert.equal(ui.sessionValues.get(ui.modeKey),'on',targetName);
  }
});

test('quick-mode Enter preserves native summary, role controls and interactive descendants with an open or collapsed chooser',async()=>{
  const ui=setup({initial:'1'}); ui.key('q',{altKey:true}); ui.key('s');
  const summary=ui.element({tagName:'SUMMARY'}),roleButton=ui.element({attributes:{role:'button'}}),roleLink=ui.element({attributes:{role:'link'}});
  const button=ui.element({tagName:'BUTTON'}),buttonChild=ui.element({tagName:'SPAN'}); button.append(buttonChild);
  const link=ui.element({tagName:'A'}),linkChild=ui.element({tagName:'SPAN'}); link.append(linkChild);
  for(const open of [true,false]) {
    if(!open)ui.closeButton.click();
    for(const target of [summary,roleButton,roleLink,buttonChild,linkChild]) {
      target.focus(); assert.equal(ui.key('Enter').defaultPrevented,false,`${open}: ${target.tagName}`);
      assert.equal(ui.dialog.open,open); assert.equal(ui.primary.value,'1');
    }
  }
  await new Promise(setImmediate); assert.equal(ui.nextCalls(),0); assert.equal(ui.changes(),0);
});

test('quick-mode Enter preserves native input buttons instead of selecting a code or advancing',async()=>{
  for(const type of ['button','submit','reset','image']) {
    const ui=setup({initial:'1'}),target=ui.element({tagName:'INPUT',type}); ui.form.append(target);
    ui.key('q',{altKey:true}); ui.key('s'); target.focus();
    assert.equal(ui.key('Enter').defaultPrevented,false,`${type}: open`);
    assert.equal(ui.dialog.open,true,type); assert.equal(ui.primary.value,'1',type);
    ui.closeButton.click(); target.focus(); assert.equal(ui.key('Enter').defaultPrevented,false,`${type}: collapsed`);
    await new Promise(setImmediate); assert.equal(ui.nextCalls(),0,type); assert.equal(ui.changes(),0,type);
  }
});

test('mouse editing notes does not reset a pending quick choice, picker mode or search text',()=>{
  const ui=setup({initial:'1',count:5}); ui.key('q',{altKey:true}); ui.key('2'); ui.searchFor('代码'); ui.list.focus(); ui.key('s'); ui.key('s');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-2');
  ui.note.focus(); ui.note.value='备注里还有2个w和s'; ui.note.dispatchEvent({type:'input'}); ui.form.dispatchEvent({type:'input',target:ui.note});
  ui.note.dispatchEvent({type:'change'}); ui.form.dispatchEvent({type:'change',target:ui.note});
  assert.equal(ui.dialog.open,true); assert.equal(ui.title.textContent,'选择次代码'); assert.equal(ui.search.value,'代码');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-2'); assert.equal(ui.primary.value,'1');
  ui.document.body.focus(); ui.key('Enter'); assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,false,true,false]);
  assert.equal(ui.note.value,'备注里还有2个w和s'); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('custom quick navigation resumes after mouse focus, collapse and selection without taking over text editing',()=>{
  const ui=setup({initial:'1',count:5,stored:JSON.stringify({...defaults,pickerUp:'A',pickerDown:'D'})});
  ui.key('q',{altKey:true}); ui.document.body.focus();
  assert.equal(ui.key('s').defaultPrevented,false); assert.equal(ui.key('d').defaultPrevented,true);
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1');
  ui.key('2'); ui.closeButton.focus(); ui.closeButton.click(); ui.document.body.focus();
  assert.equal(ui.key('d').defaultPrevented,true); assert.equal(ui.title.textContent,'选择次代码');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1'); ui.key('Enter');
  assert.deepEqual(ui.secondary.map(input=>input.checked),[false,false,true,false,false]);
  ui.note.focus(); assert.equal(ui.key('a').defaultPrevented,false); assert.equal(ui.key('d').defaultPrevented,false); assert.equal(ui.dialog.open,false);
  ui.document.body.focus(); assert.equal(ui.key('a').defaultPrevented,true); assert.equal(ui.title.textContent,'选择次代码');
  assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-0'); ui.key('Enter');
  assert.deepEqual(ui.secondary.map(input=>input.checked),[false,true,false,false,false]); assert.equal(ui.primary.value,'1');
});

test('custom confirmation is used both to choose a code and to confirm next without a secondary',async()=>{
  const ui=setup({stored:JSON.stringify({...defaults,pickerConfirm:'Alt+Z'})});
  ui.key('q',{altKey:true});ui.key('s');ui.key('z',{altKey:true});
  assert.equal(ui.primary.value,'2');assert.equal(ui.dialog.open,false);assert.equal(ui.nextCalls(),0);
  assert.equal(ui.key('Enter').defaultPrevented,false);assert.equal(ui.nextCalls(),0);
  ui.key('z',{altKey:true});await new Promise(setImmediate);assert.equal(ui.nextCalls(),1);
  assert.equal(ui.secondary.some(input=>input.checked),false);assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('split preview collapses choice and pauses shortcuts without exiting quick mode, then cancel restores it',async()=>{
  const ui=setup({initial:'1'});ui.key('q',{altKey:true});ui.key('2');
  ui.form.dataset.splitPreview='true';ui.windowEvents['platform:split-preview-change']();
  assert.equal(ui.dialog.open,false);assert.equal(ui.sessionValues.get(ui.modeKey),'on');
  ui.document.body.focus();
  for(const [key,extra] of [['s',{}],['1',{}],['Enter',{}],['Enter',{altKey:true}],['g',{altKey:true}]])ui.key(key,extra);
  await new Promise(setImmediate);assert.equal(ui.changes(),0);assert.equal(ui.nextCalls(),0);assert.equal(ui.skipPrompt.hidden,true);
  ui.form.dataset.splitPreview='false';ui.windowEvents['platform:split-preview-change']();
  assert.equal(ui.dialog.open,true);assert.equal(ui.title.textContent,'选择次代码');assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('exiting during split preview stays exited after cancellation and locked splitting cannot reopen',()=>{
  const ui=setup();ui.key('q',{altKey:true});ui.form.dataset.splitPreview='true';ui.windowEvents['platform:split-preview-change']();
  ui.key('x',{altKey:true});ui.form.dataset.splitPreview='false';ui.windowEvents['platform:split-preview-change']();
  assert.equal(ui.dialog.open,false);assert.equal(ui.sessionValues.has(ui.modeKey),false);
  ui.key('q',{altKey:true});ui.form.dataset.splitting='true';ui.refresh();ui.windowEvents['platform:split-preview-change']();
  assert.equal(ui.dialog.open,false);assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});

test('custom second confirmation still requires a primary and protects typing fields',async()=>{
  const ui=setup({stored:JSON.stringify({...defaults,pickerConfirm:'Alt+Z'})});
  ui.key('q',{altKey:true});ui.closeButton.click();ui.form.focus();ui.key('z',{altKey:true});
  await new Promise(setImmediate);assert.equal(ui.nextCalls(),0);
  ui.primary.value='1';ui.note.focus();assert.equal(ui.key('z',{altKey:true}).defaultPrevented,false);
  await new Promise(setImmediate);assert.equal(ui.nextCalls(),0);
});

test('active quick mode respects pre-consumed navigation, mode, confirmation and skip keys',async()=>{
  const ui=setup({initial:'1'}); ui.key('q',{altKey:true}); ui.key('s'); ui.document.body.focus();
  for(const [key,properties] of [['w',{}],['s',{}],['1',{}],['2',{}],['Enter',{}],['Enter',{altKey:true}]]) {
    ui.key(key,{defaultPrevented:true,...properties});
    assert.equal(ui.title.textContent,'选择主代码',key); assert.equal(ui.dialog.open,true,key);
    assert.equal(ui.list.attributes['aria-activedescendant'],'shortcut-code-1',key);
    assert.equal(ui.primary.value,'1',key); assert.equal(ui.skipPrompt.hidden,true,key);
  }
  ui.closeButton.click(); ui.document.body.focus(); ui.key('Enter',{defaultPrevented:true});
  ui.key('Enter',{altKey:true,defaultPrevented:true});
  await new Promise(setImmediate); assert.equal(ui.changes(),0); assert.equal(ui.nextCalls(),0); assert.equal(ui.prepCalls(),0);
  assert.equal(ui.navigations.length,0); assert.equal(ui.skipPrompt.hidden,true); assert.equal(ui.sessionValues.get(ui.modeKey),'on');
});
