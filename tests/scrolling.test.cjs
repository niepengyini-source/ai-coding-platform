const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/scrolling.js'), 'utf8');
function setup({mobile=false,missing=false,top=0,height=300,total=900}={}) {
  let listener,options;
  const sidebar={parentElement:null,scrollTop:0,clientHeight:700,scrollHeight:700,overflowY:'visible',
    contains(node){return node===this||this.containsChild(node);},
    containsChild(node){return !!node?.parentElement&&(node.parentElement===this||this.containsChild(node.parentElement));},
    addEventListener(name,callback,opts){assert.equal(name,'wheel');listener=callback;options=opts;}};
  const nav={parentElement:sidebar,scrollTop:top,clientHeight:height,scrollHeight:total,overflowY:'auto'};
  const item={parentElement:nav,scrollTop:0,clientHeight:30,scrollHeight:30,overflowY:'visible'};
  const brand={...item,parentElement:sidebar};
  vm.runInNewContext(source,{document:{querySelector:()=>missing?null:sidebar},
    window:{matchMedia:()=>({matches:mobile})},getComputedStyle:node=>({overflowY:node.overflowY})});
  return {sidebar,nav,item,brand,options,wheel(deltaY,target=item,values={}){
    const event={target,deltaY,ctrlKey:false,defaultPrevented:false,preventDefault(){this.defaultPrevented=true;},...values};
    listener?.(event);return event;
  }};
}
test('navigation scrolls normally while it has room in the requested direction',()=>{
  const ui=setup({top:200}); assert.equal(ui.options.passive,false);
  assert.equal(ui.wheel(100).defaultPrevented,false); assert.equal(ui.wheel(-100).defaultPrevented,false);
});
test('navigation boundaries do not pass wheel scrolling into the right-hand page',()=>{
  const ui=setup(); assert.equal(ui.wheel(-100).defaultPrevented,true);
  ui.nav.scrollTop=600; assert.equal(ui.wheel(100).defaultPrevented,true);
  ui.nav.scrollTop=599.6; assert.equal(ui.wheel(100).defaultPrevented,true);
});
test('short navigation and non-scrolling sidebar headings do not move the page',()=>{
  const ui=setup({total:300}); assert.equal(ui.wheel(100).defaultPrevented,true);
  assert.equal(ui.wheel(100,ui.brand).defaultPrevented,true); assert.equal(ui.wheel(-100,ui.sidebar).defaultPrevented,true);
});
test('an independently scrollable nested menu is allowed before its parent',()=>{
  const ui=setup(); ui.item.overflowY='auto';ui.item.scrollHeight=180;
  assert.equal(ui.wheel(10).defaultPrevented,false); ui.item.scrollTop=150;ui.nav.scrollTop=600;
  assert.equal(ui.wheel(10).defaultPrevented,true);
});
test('mobile scrolling, zoom, horizontal wheel and already handled events keep native behavior',()=>{
  assert.equal(setup({mobile:true}).wheel(100).defaultPrevented,false);
  const ui=setup(); assert.equal(ui.wheel(-100,ui.item,{ctrlKey:true}).defaultPrevented,false);
  assert.equal(ui.wheel(0).defaultPrevented,false); assert.equal(ui.wheel(100,ui.item,{defaultPrevented:true}).defaultPrevented,true);
});
test('pages without a sidebar have no wheel interception',()=>{assert.equal(setup({missing:true}).wheel(100).defaultPrevented,false);});
