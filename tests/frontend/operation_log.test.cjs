const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const html=fs.readFileSync(path.join(__dirname,'../../lisca/web/static/index.html'),'utf8');
const START=html.indexOf("  // 会话日志：环形缓冲");
const END=html.indexOf('  // 字幕开关（ticket-13）');

function setup(){
  const calls=[];
  const timers=[];
  const elements={
    logPanel:{hidden:true},logText:{value:'',select(){},hidden:true},
    logBtn:{},copyLogBtn:{},exportLogBtn:{},styleBtn:{},stylePanel:{hidden:true},
    opsFilter:{value:'all'},
  };
  const context={
    URL,Headers,
    sessionStorage:{getItem:()=>null,setItem(){}},
    crypto:{randomUUID:()=>'session-abc'},
    document:{getElementById:id=>elements[id]||{value:'',hidden:true,setAttribute(){},style:{}},
      addEventListener(){},execCommand(){}},
    navigator:{clipboard:{writeText:async()=>{}}},
    fetch:async(url,init={})=>{calls.push({url,init});return {ok:true,json:async()=>({events:[]})};},
    setTimeout:(fn)=>{timers.push(fn);return timers.length;},clearTimeout(){},
    setStatus(){},openDialog(){},addEventListener(){},
    console,
  };
  vm.createContext(context);
  vm.runInContext(html.slice(START,END),context);
  return {context,elements,calls,timers};
}

test('logEvent records the action with the session and request headers',async()=>{
  const {context,calls}=setup();
  context.logEvent('scan_start','11473 个频道');
  context.flushOperations();
  const post=calls.find(call=>call.url==='/ops');
  assert.ok(post,'the queued action is posted to the operation record endpoint');
  const body=JSON.parse(post.init.body);
  assert.equal(body.events[0].action,'scan_start');
  assert.equal(body.events[0].detail,'11473 个频道');
  const headers=post.init.headers;
  assert.equal(headers.get('X-Lisca-Session-Id'),'session-abc');
  assert.match(headers.get('X-Lisca-Request-Id'),/^session-abc-\d+$/);
});

test('twenty actions flush without waiting for the timer',()=>{
  const {context,calls,timers}=setup();
  for(let index=0;index<20;index++) context.logEvent('style_panel',`step ${index}`);
  assert.equal(calls.filter(call=>call.url==='/ops').length,1);
  assert.equal(vm.runInContext('opsQueue.length',context),0,'the size threshold flushes without the timer firing');
});

test('the in-memory buffer keeps the redacted diagnostic line',()=>{
  const {context}=setup();
  context.logEvent('connect','https://media.test/video.mp4?token=private-token');
  const line=vm.runInContext('logBuffer[0]',context);
  assert.match(line,/media\.test\/video\.mp4/);
  assert.doesNotMatch(line,/private-token/);
});

test('the record view filters user actions from HTTP requests',()=>{
  const {context,elements}=setup();
  vm.runInContext(`opsTimeline = [
    {kind:'user',action:'scan_start',detail:'60 个频道',ts:'2026-11-24T14:47:57.126Z'},
    {kind:'http',method:'GET',path:'/channels',status:200,dur_ms:5,ts:'2026-11-24T14:47:56.649Z'}
  ]`,context);
  elements.opsFilter.value='http';
  context.renderOperations();
  assert.match(elements.logText.value,/http GET \/channels 200 5ms/);
  assert.doesNotMatch(elements.logText.value,/scan_start/);
  elements.opsFilter.value='user';
  context.renderOperations();
  assert.match(elements.logText.value,/scan_start: 60 个频道/);
  assert.doesNotMatch(elements.logText.value,/\/channels/);
});

test('system events are shown and can be filtered alone',()=>{
  const {context,elements}=setup();
  vm.runInContext(`opsTimeline = [
    {kind:'user',action:'scan_start',detail:'60 个频道',ts:'2026-11-24T14:47:57.126Z'},
    {kind:'system',action:'channels_loaded',count:60,dur_ms:12,ts:'2026-11-24T14:47:56.700Z'}
  ]`,context);
  elements.opsFilter.value='system';
  context.renderOperations();
  assert.match(elements.logText.value,/system channels_loaded: count=60/);
  assert.doesNotMatch(elements.logText.value,/scan_start/);
});
