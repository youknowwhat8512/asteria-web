const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.resolve(__dirname, '../site-analytics.js'), 'utf8');
function environment({pathname='/magazine/test-episode/', hidden=false, ready='complete'}={}) {
  const state={requests:[],removed:[],ui:[],listeners:{}};
  const document={readyState:ready,visibilityState:hidden?'hidden':'visible',
    querySelectorAll:()=>[],getElementById:id=>id==='articleRoot'?{dataset:{articleSlug:'test-episode'}}:null,
    createElement:()=>{state.ui.push('element');return {setAttribute(){},addEventListener(){},appendChild(){},remove(){}};},
    head:{appendChild:()=>state.ui.push('style')},body:{appendChild:()=>state.ui.push('body')},
    hasFocus:()=>true,addEventListener:(name,fn)=>state.listeners[name]=fn};
  const storage={getItem:()=>{throw Error('Must not read an identifier');},setItem:()=>{throw Error('Must not write an identifier');},removeItem:key=>state.removed.push(key)};
  const context={document,location:{pathname,search:'?email=never-send@example.com',origin:'https://asteria.club'},
    localStorage:storage,sessionStorage:storage,crypto:{randomUUID:()=>{throw Error('Must not create an identifier');}},
    fetch:async(url,options)=>{state.requests.push({url,options,payload:JSON.parse(options.body)});return {ok:true};},
    addEventListener:(name,fn)=>state.listeners[name]=fn,setInterval:()=>{throw Error('No engagement timers');},console};
  Object.defineProperty(document,'cookie',{get(){throw Error('No cookie read');},set(){throw Error('No cookie write');}});
  context.window=context;vm.createContext(context);vm.runInContext(source,context);
  return {state,context,document};
}
const a=environment();
assert.equal(a.state.ui.length,0,'No consent popup, settings button or style');
assert.equal(a.state.requests.length,1,'One anonymous page view per document');
assert.deepEqual(a.state.requests[0].payload,{kind:'page_view',path:'/magazine/test-episode/'});
assert.equal(a.state.requests[0].options.credentials,'omit');
assert.equal(a.state.requests[0].options.referrerPolicy,'no-referrer');
assert.deepEqual(a.state.removed.sort(),['asteria_analytics_browser_v1','asteria_analytics_consent_v1']);
vm.runInContext(source,a.context);assert.equal(a.state.requests.length,1,'Loading script twice must not double count');
const hidden=environment({hidden:true});assert.equal(hidden.state.requests.length,0);
hidden.document.visibilityState='visible';hidden.state.listeners.visibilitychange();assert.equal(hidden.state.requests.length,1);
hidden.state.listeners.visibilitychange();assert.equal(hidden.state.requests.length,1);
const pending=environment({ready:'loading'});assert.equal(pending.state.requests.length,0);pending.state.listeners.DOMContentLoaded();assert.equal(pending.state.requests.length,1);
assert.equal(environment({pathname:'/private/'}).state.requests.length,0);
assert.equal(environment({pathname:'/magazine/article.html'}).state.requests[0].payload.path,'/magazine/test-episode/');
const blocked=environment({ready:'loading'});blocked.context.localStorage.removeItem=()=>{throw Error('Storage blocked');};blocked.state.listeners.DOMContentLoaded();assert.equal(blocked.state.requests.length,1);
console.log('PASS: no consent UI/IDs/cookies/storage writes; one path-only page view; visibility, aliases, deduplication and blocked storage');
