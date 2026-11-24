const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const html=fs.readFileSync(path.join(__dirname,'../../lisca/web/static/index.html'),'utf8');
const START=html.indexOf("  // 直播频道（ticket-19）");
const END=html.indexOf('  // 播放卡顿看门狗（ticket-19 复验）');

const abortError=()=>Object.assign(new Error('aborted'),{name:'AbortError'});

function element(){
  return {children:[],classList:{add(){},remove(){},toggle(){}},setAttribute(name,value){this[name]=value;},getAttribute(name){return this[name];},
    appendChild(child){this.children.push(child);},hidden:false,textContent:'',innerHTML:'',value:'',style:{},
    focus(){},close(){}};
}

function setup(){
  const els=new Map();
  const get=id=>{ if(!els.has(id)){els.set(id,element());} return els.get(id); };
  const fetches=[];
  let answerProbes=null;   // null => hold every probe until the test resolves it
  const context={
    document:{getElementById:get,createElement:()=>element()},
    setUiText:(el,value)=>{el.textContent=value;},
    setStatus:()=>{}, logEvent:()=>{}, openDialog:()=>{}, uiText:value=>String(value ?? ''),
    activeInput:'',urlEl:get('url'),startBtn:get('start'),stopBtn:{onclick:async()=>{}},
    chProbeBtn:get('chProbeBtn'),chInfo:get('chInfo'),chList:get('chList'),
    chPanel:get('chPanel'),chBtn:get('chBtn'),
    setTimeout:()=>0, clearTimeout:()=>{},
    AbortController, Map, Promise, Set,
    console,
    fetch:(url,init={})=>new Promise((resolve,reject)=>{
      const entry={url,signal:init.signal,resolve,reject};
      fetches.push(entry);
      if(init.signal){
        if(init.signal.aborted){reject(abortError());return;}
        init.signal.addEventListener('abort',()=>reject(abortError()),{once:true});
      }
      if(answerProbes && url.startsWith('/probe')) resolve(answerProbes());
    }),
  };
  vm.createContext(context);
  vm.runInContext(html.slice(START,END),context);
  const settle=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  const setChannels=list=>{context.__channels=list;vm.runInContext('channels = __channels',context);};
  const readChannels=()=>vm.runInContext('channels',context);
  return {context,fetches,get,settle,setChannels,readChannels,
    setProbeAnswer:(fn)=>{answerProbes=fn;},
    probeResponse:()=>({ok:true,json:async()=>({ok:true,latency_ms:5,mbps:2.5})}),
    resolveAll(){ for(const f of fetches) f.resolve(this.probeResponse()); },
  };
}

const channels=(count)=>Array.from({length:count},(_,index)=>({group:'g',name:`c${index}`,url:`https://media.test/${index}.m3u8`}));

test('cancel stops the batch, keeps measured rows and restores the control',async()=>{
  const env=setup();
  env.setChannels(channels(100));
  const {context,fetches,get,settle}=env;
  void context.startBatchScan();
  await settle(0);
  assert.equal(fetches.length,20,'starts with the documented 20 workers');
  assert.equal(get('chProbeLabel').textContent,'取消检测');
  assert.equal(get('chProbeBtn').getAttribute('aria-pressed'),'true');

  context.chProbeBtn.onclick();
  assert.equal(get('chProbeLabel').textContent,'批量检测');
  assert.equal(get('chProbeBtn').getAttribute('aria-pressed'),'false');
  assert.match(get('chInfo').textContent,/已测 0\/100/);

  env.resolveAll();
  await settle(10);
  assert.equal(fetches.length,20,'aborted workers must not start new probes');
  assert.equal(get('chProbeLabel').textContent,'批量检测');
});

test('a second batch starts immediately after cancel',async()=>{
  const env=setup();
  const {context,fetches,get,settle}=env;
  env.setChannels(channels(100));
  void context.startBatchScan();
  await settle(0);
  context.chProbeBtn.onclick();
  await settle(0);

  void context.startBatchScan();
  await settle(0);
  assert.equal(fetches.length,40,'the second batch is accepted, not ignored or stuck disabled');
  assert.equal(get('chProbeLabel').textContent,'取消检测');
  context.chProbeBtn.onclick();
  env.resolveAll();
  await settle(10);
});

test('replacing the list during a batch stops the old workers without throwing',async()=>{
  const env=setup();
  const {context,fetches,get,settle}=env;
  env.setChannels(channels(100));
  void context.startBatchScan();
  await settle(0);
  assert.equal(fetches.length,20);

  const load=context.loadChannels('https://media.test/list.m3u');
  await settle(0);
  const channelFetch=fetches.find(f=>f.url.startsWith('/channels'));
  assert.ok(channelFetch,'loading the replacement list reaches the server');
  channelFetch.resolve({ok:true,json:async()=>channels(3)});
  await load;
  await settle(0);

  assert.equal(env.readChannels().length,3,'the replacement list is installed');
  assert.equal(get('chProbeLabel').textContent,'批量检测');
  env.resolveAll();
  await settle(10);
  assert.equal(fetches.length,21,'the old batch fetches nothing after replacement');
});

test('completion restores the button label and reports the result',async()=>{
  const env=setup();
  const {context,get,settle}=env;
  env.setChannels(channels(4));
  env.setProbeAnswer(env.probeResponse);
  const done=context.startBatchScan();
  await done;
  assert.equal(get('chProbeLabel').textContent,'批量检测');
  assert.equal(get('chProbeBtn').getAttribute('aria-pressed'),'false');
  assert.match(get('chInfo').textContent,/批量检测完成 4\/4/);
});
