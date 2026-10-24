const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {RecoveryController,disableHlsAutomaticRetries}=require('../tools/webui_static/recovery_controller.js');

function clock(){
  let now=0,nextId=1;
  const jobs=new Map();
  return {
    setTimeout(callback,delay){const id=nextId++;jobs.set(id,{callback,at:now+delay});return id;},
    clearTimeout(id){jobs.delete(id);},
    advance(ms){
      now+=ms;
      for(const [id,job] of [...jobs])if(job.at<=now){jobs.delete(id);job.callback();}
    },
    get size(){return jobs.size;},
  };
}

test('media and recognition recovery consume one shared two-attempt budget',()=>{
  const timers=clock(),actions=[];
  const controller=new RecoveryController({setTimeout:timers.setTimeout,clearTimeout:timers.clearTimeout});
  controller.begin();
  assert.deepEqual(controller.schedule('media',true,info=>actions.push(['media',info.attempt])),
    {status:'scheduled',attempt:1,delayMs:1000,attempts:1,maxAttempts:2});
  timers.advance(1000);
  assert.deepEqual(actions,[['media',1]]);
  assert.deepEqual(controller.schedule('recognition',true,info=>actions.push(['recognition',info.attempt])),
    {status:'scheduled',attempt:2,delayMs:2000,attempts:2,maxAttempts:2});
  timers.advance(2000);
  assert.deepEqual(actions,[['media',1],['recognition',2]]);
  assert.equal(controller.schedule('media',true,()=>{}).status,'exhausted');
});

test('nonretryable failures do not wait and cancellation interrupts a pending retry',()=>{
  const timers=clock(),actions=[];
  const controller=new RecoveryController({setTimeout:timers.setTimeout,clearTimeout:timers.clearTimeout});
  controller.begin();
  assert.equal(controller.schedule('recognition',false,()=>actions.push('auth')).status,'manual');
  controller.schedule('media',true,()=>actions.push('retry'));
  assert.equal(timers.size,1);
  controller.cancel();
  assert.equal(timers.size,0);
  timers.advance(5000);
  assert.deepEqual(actions,[]);
});

test('default retry timers keep the browser global receiver',()=>{
  const source=fs.readFileSync(require.resolve('../tools/webui_static/recovery_controller.js'),'utf8');
  const browserGlobal={receiverTag:'browser-window',scheduled:[],cancelled:[]};
  browserGlobal.setTimeout=function(callback,delay){
    assert.equal(this.receiverTag,'browser-window','setTimeout lost its Window receiver');
    this.scheduled.push({callback,delay});return 73;
  };
  browserGlobal.clearTimeout=function(timerId){
    assert.equal(this.receiverTag,'browser-window','clearTimeout lost its Window receiver');
    this.cancelled.push(timerId);
  };
  vm.createContext(browserGlobal);
  vm.runInContext(source,browserGlobal);
  const controller=new browserGlobal.RecoveryController();
  controller.begin();
  assert.equal(controller.schedule('recognition',true,()=>{}).delayMs,1000);
  assert.deepEqual(browserGlobal.scheduled.map(job=>job.delay),[1000]);
  controller.cancel();
  assert.deepEqual(browserGlobal.cancelled,[73]);
});

test('coincident media and recognition failures resolve to one full-session recovery',()=>{
  const timers=clock(),actions=[];
  const controller=new RecoveryController({setTimeout:timers.setTimeout,clearTimeout:timers.clearTimeout});
  controller.begin();
  controller.schedule('media',true,info=>actions.push(['media',info.kinds]));
  controller.schedule('recognition',true,info=>actions.push(['recognition',info.kinds]));
  timers.advance(1000);
  assert.deepEqual(actions,[['recognition',['media','recognition']]]);
});

test('automatic reconnect preserves attempts until ten seconds of real progress',()=>{
  const timers=clock();
  const controller=new RecoveryController({setTimeout:timers.setTimeout,clearTimeout:timers.clearTimeout});
  controller.begin();
  controller.schedule('recognition',true,()=>{});
  timers.advance(1000);
  controller.begin({preserveAttempts:true});
  assert.equal(controller.attempts,1);
  for(let now=0;now<=10000;now+=500){
    const mediaTime=now/1000;
    controller.markProgress(now,{mediaTime,coverageTime:mediaTime+0.2});
  }
  assert.equal(controller.attempts,0);
});

test('late captions and stationary playback cannot reset the shared recovery budget',()=>{
  const timers=clock();
  const controller=new RecoveryController({setTimeout:timers.setTimeout,clearTimeout:timers.clearTimeout});
  controller.begin();controller.schedule('recognition',true,()=>{});timers.advance(1000);
  for(let now=0;now<=10000;now+=500)
    controller.markProgress(now,{mediaTime:5,coverageTime:3});
  for(let now=10000;now<=20000;now+=500)
    controller.markProgress(now,{mediaTime:5,coverageTime:6});
  assert.equal(controller.attempts,1);
  for(let now=20000;now<=30000;now+=500){
    const mediaTime=5.1+(now-20000)/1000;
    controller.markProgress(now,{mediaTime,coverageTime:mediaTime+0.2});
  }
  assert.equal(controller.attempts,0);
});

test('all vendored HLS loader retries are disabled so recovery budget remains authoritative',()=>{
  const Hls=require('../tools/webui_static/hls.min.js');
  const policies=disableHlsAutomaticRetries(Hls.DefaultConfig);
  const policyNames=Object.keys(Hls.DefaultConfig).filter(name=>name.endsWith('LoadPolicy'));
  assert.ok(policyNames.includes('fragLoadPolicy'));
  for(const name of policyNames){
    const policy=policies[name]?.default;
    assert.ok(policy,`${name} policy missing`);
    for(const retry of ['timeoutRetry','errorRetry'])
      if(policy[retry])assert.equal(policy[retry].maxNumRetry,0,`${name}.${retry}`);
  }
  assert.equal(Hls.DefaultConfig.fragLoadPolicy.default.errorRetry.maxNumRetry,6);
});
