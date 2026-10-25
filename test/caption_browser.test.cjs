const {test}=require('node:test'); const assert=require('node:assert/strict'); const fs=require('node:fs'); const vm=require('node:vm'); const path=require('node:path');
test('diagnostic log keeps source identity but hides signed URL query values',()=>{const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');const elements=new Map();const c={URL,document:{getElementById(id){if(!elements.has(id))elements.set(id,{hidden:true,value:'',onclick:null});return elements.get(id);}}};vm.createContext(c);const start=html.indexOf("const logBtn = document.getElementById('logBtn');"),end=html.indexOf('// 字幕开关',start);vm.runInContext(html.slice(start,end),c);c.logEvent('connect','https://media.test/video.mp4?token=private-token&sig=private-signature rtsp://media.test/live?auth=private-rtsp-auth');const line=vm.runInContext('logBuffer[0]',c);assert.match(line,/media\.test\/video\.mp4/);assert.match(line,/media\.test\/live/);assert.doesNotMatch(line,/private-token|private-signature|private-rtsp-auth/);});
function browser({playerWidth=260,fontSize=30,cssMaxWidth='90%'}={}){
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const element=()=>({textContent:'',hidden:true,dataset:{},classList:{contains:()=>false},style:{},remove(){}});
 const current=element(), late=element();
 current.style.fontSize=`${fontSize}px`;const playerWrap={clientWidth:playerWidth};
 const context={player:{currentTime:10,paused:false,pause(){this.paused=true;}},activeInput:'test.mp4',sharedViewerActive:false,cloudPlayback:true,originalCaptionsComplete:false,cloudResumeRequired:false,pauseStartedAt:null,preserveLiveMedia:false,ws:{readyState:1},holdingForSync:false,setStatus(){},connect(...args){context.reconnected=args;},currentEl:current,subEnabled:true,playStarted:true,translatedSession:false,captionRevisions:new Map(),MAX_RETAINED_CAPTIONS:20000,subs:[],previewSubs:[],seekPending:false,bilingualEl:{checked:false},historyItems:[],document:{getElementById:id=>id==='lateCaption'?late:id==='playerWrap'?playerWrap:null},getComputedStyle:element=>({fontStyle:'normal',fontWeight:'600',fontSize:element.style.fontSize||'30px',fontFamily:'Arial, sans-serif',letterSpacing:'normal',maxWidth:element.style.maxWidth||cssMaxWidth,paddingLeft:'10px',paddingRight:'10px',borderLeftWidth:'0px',borderRightWidth:'0px'}),fitCaptionWidth(){},logEvent(){},appendHistory:element,updateHistory(){},captionSegments:s=>[s],performance:{now:()=>context.now},now:0};
 const moduleFile=path.join(__dirname,'../tools/webui_static/caption_presenter.js');
 if(fs.existsSync(moduleFile)){const module=require(moduleFile);context.CaptionPresenter=module.CaptionPresenter;context.CaptionLayout=module.CaptionLayout;context.lines=()=>[];context.captionPresenter=new context.CaptionPresenter();}
 vm.createContext(context);
 const layoutStart=html.indexOf('const captionSegmentCache = new WeakMap();');
 const previewStart=html.indexOf('function onPreview(',layoutStart);
 vm.runInContext(html.slice(layoutStart,previewStart),context);
 const previewEnd=html.indexOf('\n\n  // Decoder PTS',previewStart);
 vm.runInContext(html.slice(previewStart,previewEnd),context);
 return {context,current,late,push:cue=>context.onSubtitle({type:'subtitle',translated:true,sentence_id:1,beg:0,end:2,is_final:true,text:'这句译文必须出现在视频内。',...cue}),preview:msg=>context.onPreview(msg),tick(ms){context.now=ms;context.syncSubtitle();}};
}
function assertOriginalPages(b,segments,text){
 assert.ok(segments.length>1);
 const layout=b.context.captionLayoutOptions();
 for(const segment of segments){
  const rows=segment.text.split('\n');
  assert.ok(rows.length<=2,segment.text);
  for(const row of rows)assert.ok(layout.measureText(row)<=layout.maxWidth,row);
 }
 assert.equal(segments.map(segment=>segment.text).join('').replace(/\s+/gu,''),text.replace(/\s+/gu,''));
}
function loadShareHistoryWarning(html,context){
 const start=html.indexOf('  function showShareHistoryNotice() {');
 const end=html.indexOf('  function appendHistory(',start);
 vm.runInContext(html.slice(start,end),context);
}
function shareHistoryViewer(){
 const b=browser(),c=b.context,html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const classes=new Set(),elements=new Map([['captionActions',{hidden:true}],['retryCaptions',{hidden:true}],
   ['shareHistoryNotice',{hidden:true,textContent:''}]]);
 c.generation=4;c.connectionGeneration=4;c.terminalReceived=false;c.recognitionOnlyStopped=false;
 c.audioClockPts=null;c.timelineMapped=false;c.queuedMediaMessages=[];c.mediaOffset=0;
 c.sharedViewerActive=true;c.preserveLiveMedia=false;c.originalCaptionMode=false;c.uiPaused=false;
 c.holdingForBuffer=false;c.holdingForSync=false;c.playStarted=false;c.captionHasSpeech=false;
 c.activeInput='';c.currentEl.classList={contains:name=>classes.has(name),add:name=>classes.add(name),remove:name=>classes.delete(name)};
 c.player={currentTime:1,paused:false,ended:false,playCalls:0,play(){this.playCalls++;return Promise.resolve();}};
 c.document={getElementById:id=>elements.get(id)||null};
 c.playbackGuard={done:false,degraded:false,captionProgress(){},removeCaption(){}};
 c.performance={now:()=>100};c.setStatus=(message,isError=false)=>{c.status=message;if(isError)classes.add('status-error');};
 c.logEvent=()=>{};c.markSessionRecoveryProgress=()=>{};c.stallWatchdog=()=>{};
 const syncStart=html.indexOf('  function syncSubtitle() {');
 const syncEnd=html.indexOf('\n\n  // 历史原地修订 partial',syncStart);
 vm.runInContext(html.slice(syncStart,syncEnd),c);
 loadShareHistoryWarning(html,c);
 const receiveStart=html.indexOf('    function receiveMessage(input) {');
 const receiveEnd=html.indexOf('    ws.onmessage = ',receiveStart);
 vm.runInContext(html.slice(receiveStart,receiveEnd),c);
 return {context:c,current:b.current};
}
function lateHlsClockViewer(){
 const b=shareHistoryViewer(),c=b.context,html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const {RecoveryController}=require('../tools/webui_static/recovery_controller.js');
 Object.assign(c,{generation:9,connectionGeneration:9,terminalReceived:false,recognitionOnlyStopped:false,
   preserveLiveMedia:true,originalCaptionMode:true,sharedViewerActive:false,isHls:true,
   audioClockPts:null,videoClockPts:null,hlsVideoClockPts:null,timelineMapped:false,mediaOffset:0,
   queuedMediaMessages:[],captionRevisions:new Map(),MAX_RETAINED_CAPTIONS:1000,
   captionHasSpeech:false,originalCaptionsComplete:false,playbackLimit:null,
   playbackGuard:{live:true,degraded:false,done:false,captionProgress(){},removeCaption(){}},
   recoveryController:new RecoveryController(),stallWatchdog(){},markSessionRecoveryProgress(){},
   syncPlaybackToBuffer(){},useCaptionBuffer:true,aligningInitialPlayback:false,
   currentEl:{textContent:'',classList:{contains:()=>false,add(){},remove(){}}},
   appendHistory(){return {remove(){}};},updateHistory(){},fitCaptionWidth(){},
   performance:{now:()=>100},setTimeout(){return 1;},logEvent(){}});
 c.player.currentTime=3;c.player.paused=true;
 const flushStart=html.indexOf('    function flushMediaClock() {');
 const flushEnd=html.indexOf('\n    delayEl.disabled',flushStart);
 vm.runInContext(html.slice(flushStart,flushEnd),c);
 c.flushCurrentMediaClock=c.flushMediaClock;
 c.activeMediaClockFlush=c.flushCurrentMediaClock;
 const offsetStart=html.indexOf('  function offsetMediaMessage(msg, offset) {');
 const offsetEnd=html.indexOf('\n\n  function shouldPreserveLiveMedia',offsetStart);
 vm.runInContext(html.slice(offsetStart,offsetEnd),c);
 const identityStart=html.indexOf('  function scopeSessionCaptionIdentity(');
 const identityEnd=html.indexOf('\n\n  async function connect(',identityStart);
 vm.runInContext(html.slice(identityStart,identityEnd),c);
 const receiveStart=html.indexOf('    function receiveMessage(input) {');
 const receiveEnd=html.indexOf('    ws.onmessage = ',receiveStart);
 vm.runInContext(html.slice(receiveStart,receiveEnd),c);
 return {context:c,current:b.current};
}
function socketRecoveryBrowser(eventName,status='scheduled'){
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const calls=[],failures=[],statuses=[];
 const c={terminalReceived:false,originalCaptionMode:true,sharedViewerActive:false,recognitionOnlyStopped:false,
   generation:7,recoveryController:{cancelled:false,pending:null},playbackGuard:{live:true},
   activeInput:'local.mp4',player:{currentTime:5},calls,failures,statuses,
   scheduleSessionRecovery(...args){calls.push(args);return {status};},
   connect(...args){c.connected=args;},setStatus(message){statuses.push(message);},
   showRecognitionFailure(message){failures.push(message);},logEvent(){},
   startBtn:{style:{}},stopBtn:{style:{}},shareCopyBtn:{style:{}},shareAddressEl:{hidden:false},
   currentEl:{classList:{contains:()=>false}},ws:{}};
 const start=html.indexOf('    function scheduleRecognitionSocketRecovery(');
 const closeStart=html.indexOf('    ws.onclose = (event) => {',start);
 const end=html.indexOf('\n    };',closeStart)+'\n    };'.length;
 vm.runInNewContext(html.slice(start,end),c);
 if(eventName==='error')c.ws.onerror();else c.ws.onclose({code:1006});
 return c;
}
function seekBrowser({position=0,playbackLimit=0,paused=true,confirmResult=true}={}){
 const b=browser(),c=b.context,listeners={},confirmMessages=[],timers=new Map();let nextTimer=1;
 Object.assign(c,{aligningInitialPlayback:false,recognitionOnlyStopped:false,originalCaptionsComplete:false,
   originalCaptionMode:true,cloudPlayback:true,sharedViewerActive:false,activeInput:'local.mp4',playbackLimit,
   lastStableMediaPosition:position,pendingSeekOrigin:position,pendingLocalSeek:null,
   pendingLocalSeekTimer:null,restoringSeek:null,confirmMessages,
   setTimeout(callback){const id=nextTimer++;timers.set(id,callback);return id;},
   clearTimeout(id){timers.delete(id);},
   window:{confirm(message){confirmMessages.push(message);return confirmResult;}},
   ws:{readyState:1,messages:[],send(message){this.messages.push(message);}},
   connect(...args){c.reconnected=args;},
   syncSubtitle(){},audioEl:{currentTime:position,pause(){},play(){return Promise.resolve();}}});
 c.player={currentTime:position,paused,ended:false,addEventListener(name,callback){listeners[name]=callback;},
   pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}};
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const helpersStart=html.indexOf('  function cancelPendingLocalSeek()');
 const helpersEnd=html.indexOf('  function holdCaptionForCloudReconnect()',helpersStart);
 const start=html.indexOf("  player.addEventListener('seeked', () => {");
 const end=html.indexOf('\n\n  async function cancelCaptionProcessing',start);
 vm.runInContext(html.slice(helpersStart,helpersEnd)+html.slice(start,end),c);
 return {context:c,seek(target){c.player.currentTime=target;listeners.seeked();},
   flushTimers(){for(const callback of timers.values())callback();timers.clear();},finishRestore(){listeners.seeked();}};
}
function endedSession(reason){
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const elements=new Map([['captionActions',{hidden:false}],['retryCaptions',{hidden:true}]]);
 const {RecoveryController}=require('../tools/webui_static/recovery_controller.js');
 const c={generation:1,connectionGeneration:1,preserveLiveMedia:false,RecoveryController,
   recoveryController:new RecoveryController(),recognitionOnlyStopped:false,audioClockPts:null,
   timelineMapped:false,queuedMediaMessages:[],mediaOffset:0,offsetMediaMessage:msg=>msg,
   playbackGuard:{done:false},document:{getElementById:id=>elements.get(id)},
   originalCaptionMode:true,isHls:false,currentEl:{classList:{contains:()=>false}},
   cloudResumeRequired:false,status:'',setStatus(message){c.status=message;},playStarted:false,
   playbackLimit:5,holdingForBuffer:false,uiPaused:false,player:{play(){return Promise.resolve();}},
   ws:{closed:false,close(){this.closed=true;}}};
 const start=html.indexOf('    function receiveMessage(input) {');
 const end=html.indexOf('    ws.onmessage = ',start);
 vm.runInNewContext(html.slice(start,end),c);
 c.receiveMessage(reason === undefined ? {type:'end'} : {type:'end',reason});
 return c;
}
function terminalCloseSession(reason,{awaitingTimeline=false}={}){
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const elements=new Map([['captionActions',{hidden:false}],['retryCaptions',{hidden:false}]]);
 const recoveries=[];
 const c={generation:1,connectionGeneration:1,preserveLiveMedia:false,terminalReceived:false,recognitionOnlyStopped:false,
   audioClockPts:awaitingTimeline?0:null,timelineMapped:false,queuedMediaMessages:[],mediaOffset:0,offsetMediaMessage:msg=>msg,
   playbackGuard:{done:false},document:{getElementById:id=>elements.get(id)},
   originalCaptionMode:true,isHls:false,currentEl:{classList:{contains:()=>false}},
   cloudResumeRequired:false,setStatus(){},playStarted:false,playbackLimit:5,holdingForBuffer:false,
   uiPaused:false,player:{currentTime:8,play(){return Promise.resolve();}},
   recoveryController:{pending:null,cancelled:false},activeInput:'local.mp4',sharedViewerActive:false,
   logEvent(){},scheduleSessionRecovery(...args){recoveries.push(args);return {status:'scheduled'};},
   connect(){},showCaptionRecovery(){},startBtn:{style:{}},stopBtn:{style:{}},shareCopyBtn:{style:{}},
   shareAddressEl:{hidden:false},ws:null};
 c.ws={close(code,reason){this.closeCode=code;this.closeReason=reason;this.onclose({code:code??1005});}};
 const closeStart=html.indexOf('    ws.onclose = (event) => {');
 const closeEnd=html.indexOf('\n    };',closeStart)+'\n    };'.length;
 vm.runInNewContext(html.slice(closeStart,closeEnd),c);
 const receiveStart=html.indexOf('    function receiveMessage(input) {');
 const receiveEnd=html.indexOf('    ws.onmessage = ',receiveStart);
 vm.runInNewContext(html.slice(receiveStart,receiveEnd),c);
 c.receiveMessage({type:'end',reason});
 if(awaitingTimeline)c.ws.onclose({code:1005});
 return {context:c,recoveries};
}
function closeDuringPendingRecovery(){
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const {RecoveryController}=require('../tools/webui_static/recovery_controller.js');
 let timerCallback;const recoveryController=new RecoveryController({setTimeout(fn){timerCallback=fn;return 1;},clearTimeout(){}});
 recoveryController.begin();recoveryController.schedule('recognition',true,()=>{});
 const elements=new Map([['captionActions',{hidden:false}],['retryCaptions',{hidden:true}]]);
 const c={terminalReceived:true,originalCaptionMode:true,sharedViewerActive:false,
   recognitionOnlyStopped:false,recoveryController,originalCaptionsComplete:false,processingComplete:false,
   currentEl:{classList:{contains:()=>false}},logEvent(){},setStatus(){},
   startBtn:{style:{display:'none'}},stopBtn:{style:{display:'inline-block'}},
   shareCopyBtn:{style:{display:'inline-block'}},shareAddressEl:{hidden:false},activeShareToken:'share',
   document:{getElementById:id=>elements.get(id)},ws:{}};
 const closeStart=html.indexOf('    ws.onclose = (event) => {');
 const closeEnd=html.indexOf('\n    };',closeStart)+'\n    };'.length;
 vm.runInNewContext(html.slice(closeStart,closeEnd),c);
 c.ws.onclose({code:1000});
 return {context:c,elements,timerCallback};
}
function playBrowser({position=2,pauseStartedAt=0,now=3000,readyState=1}={}){
 const b=browser(),c=b.context,listeners={};
 Object.assign(c,{pauseStartedAt,uiPaused:true,cloudResumeRequired:false,connectionGeneration:1,recognitionOnlyStopped:false,
   originalCaptionsComplete:false,cloudPlayback:true,sharedViewerActive:false,activeInput:'local.mp4',
   playStarted:true,holdingForSync:false,holdingForBuffer:false,skipReconnectOnNextPlay:false,
   ws:{readyState,messages:[],send(message){this.messages.push(message);}},
   audioEl:{pause(){},play(){return Promise.resolve();}},
   setStatus(){},syncSubtitle(){},logEvent(){},performance:{now:()=>c.now}});
 c.now=now;
 c.player={currentTime:position,paused:false,ended:false,addEventListener(name,callback){listeners[name]=callback;},
   pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}};
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const helpersStart=html.indexOf('  function holdCaptionForCloudReconnect()');
 const helpersEnd=html.indexOf('  function sharedWordRatio(',helpersStart);
 const playStart=html.indexOf("  player.addEventListener('play', () => {");
 const playEnd=html.indexOf("  player.addEventListener('pause', () => {",playStart);
 vm.runInContext(html.slice(helpersStart,helpersEnd)+html.slice(playStart,playEnd),c);
 return {context:c,play(){listeners.play();}};
}
test('late complete translations appear on the video rather than disappearing',()=>{const b=browser();b.push({});b.tick(0);assert.match(b.current.textContent,/这句译文/);assert.equal(b.late.hidden,true);});
test('a truncated share history notice stays nonterminal and later captions still render',()=>{
 const {context:c,current}=shareHistoryViewer();
 c.receiveMessage({type:'status',failure_category:'share_history_truncated',retryable:false,
   message:'较早字幕记录未保留，当前直播继续'});
 c.receiveMessage({type:'ready',original:true,buffered:false,delay:0});
 c.receiveMessage({type:'subtitle',sentence_id:301,revision:1,beg:0,end:2,
   is_final:true,translated:false,text:'历史截断后仍继续的字幕'});
 assert.equal(c.currentEl.classList.contains('err'),false);
 assert.equal(c.player.playCalls,1);
 assert.equal(c.document.getElementById('shareHistoryNotice').hidden,false);
 assert.equal(c.document.getElementById('shareHistoryNotice').textContent,'较早字幕记录未保留，当前直播继续');
 assert.match(current.textContent,/历史截断后仍继续的字幕/);
 assert.match(c.status,/原文字幕已就绪/);
});
test('legacy share-history error messages use the same nonterminal notice',()=>{
 const {context:c,current}=shareHistoryViewer();
 c.receiveMessage({type:'error',failure_category:'share_history_truncated',retryable:false,
   message:'历史字幕记录不完整'});
 c.receiveMessage({type:'ready',original:true,buffered:false,delay:0});
 c.receiveMessage({type:'subtitle',sentence_id:302,revision:1,beg:0,end:2,
   is_final:true,translated:false,text:'兼容旧提示后的字幕'});
 assert.equal(c.currentEl.classList.contains('err'),false);
 assert.equal(c.document.getElementById('shareHistoryNotice').hidden,false);
 assert.equal(c.document.getElementById('shareHistoryNotice').textContent,'较早字幕记录未保留，当前直播继续');
 assert.match(current.textContent,/兼容旧提示后的字幕/);
});
test('websocket error and abnormal close use the same recovery action and preserve their messages',()=>{
 for(const eventName of ['error','close']){
  const c=socketRecoveryBrowser(eventName);
  assert.equal(c.calls.length,1,eventName);
  assert.equal(c.calls[0][0],'recognition');
  assert.equal(c.calls[0][1],true);
  assert.match(c.statuses.at(-1),/直播画面继续播放/);
  c.calls[0][2]({});
  assert.deepEqual(Array.from(c.connected).slice(0,3),['local.mp4',null,5]);
  assert.equal(c.connected[3].automaticRecovery,true);
 }
 const error=socketRecoveryBrowser('error','exhausted');
 const close=socketRecoveryBrowser('close','exhausted');
 assert.match(error.failures[0],/连接失败/);
 assert.match(close.failures[0],/异常关闭/);
});
test('caption identity capacity stops recognition while preserving existing transcript rows',()=>{
 const b=browser(),c=b.context,calls=[];
 c.MAX_RETAINED_CAPTIONS=1;
 c.captionRevisions=new Map([['existing',0]]);
 c.subs=[{sentenceId:'existing',text:'已保留终稿。'}];
 c.cancelCaptionProcessing=(...args)=>{calls.push(args);return Promise.resolve();};
 const accepted=c.onSubtitle({sentence_id:'new',revision:0,beg:12,end:13,text:'新终稿。',is_final:true});
 assert.equal(accepted,false);
 assert.equal(c.captionRevisions.size,1);
 assert.equal(c.subs[0].text,'已保留终稿。');
 assert.deepEqual(calls,[['字幕记录达到安全容量上限，识别已停止；现有字幕已保留',true,true]]);
});
test('server caption capacity errors retain existing captions and continue in only-play mode',async()=>{
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const row={sentenceId:'old',text:'已有终稿。'},preview={sentenceId:'draft',text:'已有预览。'};
 const actions={hidden:false};let resets=0;
 const c={generation:1,connectionGeneration:1,preserveLiveMedia:false,recognitionOnlyStopped:false,sharedViewerActive:false,
   originalCaptionMode:true,audioClockPts:null,timelineMapped:false,queuedMediaMessages:[],mediaOffset:0,
   recoveredRevisionOffsets:null,
   recoveryController:{cancel(){}},cancelPendingLocalSeek(){},pendingSeekOrigin:null,restoringSeek:null,
   skipReconnectOnNextPlay:false,playbackGuard:{done:false},cloudResumeRequired:false,pauseStartedAt:1,
   cloudPlayback:true,originalCaptionsComplete:false,holdingForSync:true,holdingForBuffer:true,
   playStarted:false,playbackLimit:5,activeCloudSession:null,activeShareToken:null,
   subs:[row],previewSubs:[preview],captionPresenter:{reset(){resets++;}},currentEl:{textContent:'保留当前字幕',classList:{remove(){}}},
   ws:{onmessage(){},onclose(){},close(){this.closed=true;}},player:{paused:true,pause(){this.paused=true;},
     play(){this.paused=false;return Promise.resolve();}},audioEl:{pause(){}},
   startBtn:{style:{}},stopBtn:{style:{}},shareCopyBtn:{style:{}},shareAddressEl:{hidden:false},
   document:{getElementById:id=>id==='captionActions'?actions:null},
   scheduleSessionRecovery(){return {status:'manual'};},showCaptionRecovery(message){c.status=message;},
   holdCaptionForCloudReconnect(){return false;},setStatus(message){c.status=message;}};
 vm.createContext(c);
 loadShareHistoryWarning(html,c);
 const cancelStart=html.indexOf('  async function cancelCaptionProcessing(');
 const cancelEnd=html.indexOf('  function showCaptionRecovery(',cancelStart);
 vm.runInContext(html.slice(cancelStart,cancelEnd),c);
 const receiveStart=html.indexOf('    function receiveMessage(input) {');
 const receiveEnd=html.indexOf('    ws.onmessage = ',receiveStart);
 vm.runInContext(html.slice(receiveStart,receiveEnd),c);
 c.receiveMessage({type:'error',failure_category:'capacity',retryable:false,message:'容量已满'});
 await Promise.resolve();
 assert.equal(c.subs[0],row);assert.equal(c.previewSubs[0],preview);
 assert.equal(resets,0);assert.equal(c.player.paused,false);
 assert.equal(c.recognitionOnlyStopped,true);assert.equal(c.cloudPlayback,false);
 assert.match(c.status,/容量已满/);assert.equal(actions.hidden,true);
});
test('provider token drafts never become visible subtitles or a second reading frame',()=>{const b=browser();b.push({is_final:false,end:null,text:'这'});b.tick(0);assert.equal(b.current.textContent,'');assert.equal(b.late.hidden,true);});
test('a completed cue remains readable after its source interval has expired',()=>{const b=browser();b.context.player.currentTime=1.9;b.push({});b.tick(0);b.context.player.currentTime=2.1;b.tick(100);assert.match(b.current.textContent,/这句译文/);});

const {CaptionPresenter,pages,readingMs}=require('../tools/webui_static/caption_presenter.js');
test('fast paragraphs paginate to two target lines with complete words',()=>{const text='This is a very long sentence with several complete phrases that should remain readable. '.repeat(8).trim();const result=pages(text);assert.ok(result.length>3);assert.ok(result.every(p=>p.split('\n').length<=2));assert.equal(result.join(' ').replace(/\s+/g,' '),text);});
test('incoming fast speech never replaces the currently readable page',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:'first',beg:0,text:'第一句完整的译文。'});assert.equal(p.tick(2,0).text,'第一句完整的译文。');for(let i=0;i<20;i++)p.enqueue({sentence_id:i,beg:0,text:'随后到来的完整短句。'});assert.equal(p.tick(3,2399).text,'第一句完整的译文。');assert.ok(p.skipped>0);assert.notEqual(p.tick(4,2401).text,'第一句完整的译文。');});
test('pause freezes reading time and resume starts from the remaining duration',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:1,beg:0,text:'Hello there.'});p.tick(0,0);p.tick(1,500);assert.ok(p.tick(1,10000,true));assert.ok(p.tick(1,10100));assert.ok(p.tick(2,11000));assert.equal(p.tick(3,13000),null);});
test('source timestamps prevent early display and reset clears every old page',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:1,beg:10,text:'Future sentence.'});assert.equal(p.tick(1,0),null);assert.ok(p.tick(10,1000));p.reset();assert.equal(p.tick(10,1100),null);});
test('duplicate final revisions do not requeue a caption',()=>{const p=new CaptionPresenter();const cue={sentence_id:1,beg:0,text:'Hello there.'};p.enqueue(cue);p.enqueue(cue);assert.equal(p.queue.length,1);assert.equal(readingMs('Hi'),2400);});

test('narrow screens reflow complete captions without losing text',()=>{const p=new CaptionPresenter();p.setColumns(20);const text='这是一个用于验证小屏幕下长字幕不会超过两行并且不会丢失任何文字的完整句子。';p.enqueue({sentence_id:1,beg:0,text});const all=[p.tick(0,0),...p.queue];assert.ok(all.every(c=>c.text.split('\n').length<=2));assert.equal(all.map(c=>c.text.replace(/\n/g,'')).join(''),text);});
test('original captions split to two-line phrases at a narrow player width',()=>{const b=browser({playerWidth:260,fontSize:30});const text='窄屏字幕需要根据播放器实际宽度切分完整原文并确保每一页都不超过两行。'.repeat(2);const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assertOriginalPages(b,segments,text);});
test('large desktop caption settings reduce original phrase width to two lines',()=>{const b=browser({playerWidth:900,fontSize:64});const text='字幕字号调大之后每个原文短句也要按较窄可读行宽拆分以免遮挡画面内容'.repeat(3);const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assertOriginalPages(b,segments,text);});
test('maximum font size still fits two lines on the narrowest player',()=>{const b=browser({playerWidth:260,fontSize:64});const text='窄屏大字号字幕仍需要遵守播放器的真实可用宽度并切成能完整阅读的短句'.repeat(2);const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assertOriginalPages(b,segments,text);});
test('320px captions at maximum font size split at the actual two-line Han capacity',()=>{const b=browser({playerWidth:288,fontSize:64});const text='这是一段用于验证字幕在窄屏和放大字号后仍保持完整短句的原文内容';const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assertOriginalPages(b,segments,text);});
test('preview messages use the same timed two-line pagination as final captions',()=>{
 const b=browser({playerWidth:288,fontSize:30}),c=b.context;
 const text='Rescuers are searching for people near the station; filters work well, 每天客流量大概在100多组的样子，然后成交率达到25%左右。';
 c.player.currentTime=1;
 b.preview({sentence_id:'preview-layout',beg:0,end:20,text});
 const segments=c.captionSegments(c.previewSubs[0]);
 assertOriginalPages(b,segments,text);
 for(const segment of segments){
  c.player.currentTime=(segment.beg+segment.end)/2;
  b.tick(0);
  assert.equal(b.current.textContent,segment.text);
 }
 assert.deepEqual(Array.from(segments.flatMap(segment=>segment.text.match(/[\p{Script=Latin}\p{N}]+(?:[-’'][\p{Script=Latin}\p{N}]+)*/gu)||[])),
  Array.from(text.match(/[\p{Script=Latin}\p{N}]+(?:[-’'][\p{Script=Latin}\p{N}]+)*/gu)||[]));
});
test('word-timed original pages retain quotes, punctuation, and symbols outside ASR tokens',()=>{
 const b=browser({playerWidth:260,fontSize:30});
 const text='“One very long sentence reached 100% on Friday, then every durable filter passed.”';
 const words=Array.from(text.matchAll(/[\p{L}\p{N}]+/gu),(match,index)=>[index*.2,index*.2+.15,match[0]]);
 const segments=b.context.captionSegments({beg:0,end:10,text,words});
 assertOriginalPages(b,segments,text);
});
test('stable caption pages are cached and resize changes the layout key',()=>{
 const b=browser({playerWidth:260,fontSize:30});
 const text='Repeated caption layout should reuse measured pages until the player or font size changes.';
 const cue={beg:0,end:10,text,words:[]};
 const first=b.context.captionSegments(cue);
 assert.strictEqual(b.context.captionSegments(cue),first);
 b.current.style.fontSize='52px';
 const resized=b.context.captionSegments(cue);
 assert.notStrictEqual(resized,first);
 assertOriginalPages(b,resized,text);
});
test('caption width follows computed CSS max-width across the media breakpoint',()=>{
 for(const [playerWidth,cssMaxWidth,ratio] of [[694,'90%',.90],[341,'94%',.94]]){
  const b=browser({playerWidth,cssMaxWidth});
  const expected=(playerWidth*ratio-20)*.97;
  assert.ok(Math.abs(b.context.captionLayoutOptions().maxWidth-expected)<.01,{playerWidth,cssMaxWidth});
 }
});
test('long large-font captions do not leave sub-600ms orphan pages',()=>{
 const b=browser({playerWidth:288,fontSize:64});
 const text='这是一段用于验证字幕在窄屏和放大字号后仍保持完整短句的原文内容，所有文字都应该在对应语音时间内显示，而不是被裁切或挤成很多行。';
 const wordDuration=10.8/text.length;
 const words=Array.from(text,(char,index)=>[index*wordDuration,(index+1)*wordDuration,char]);
 for(const cue of [
  {beg:0,end:10.8,text,words},
  {beg:0,end:10.8,text,words:[]},
 ]){
  const segments=b.context.captionSegments(cue);
  assertOriginalPages(b,segments,text);
  assert.ok(segments.every(segment=>segment.end-segment.beg>=.6),
   JSON.stringify(segments.map(segment=>({seconds:+(segment.end-segment.beg).toFixed(3),chars:segment.text.replace(/\s+/gu,'').length}))));
  assert.ok(segments.every(segment=>segment.beg>=0&&segment.end<=10.8));
 }
});
test('an unfinished local seek inside the prepared range reuses subtitles without reconnecting',()=>{const b=seekBrowser({position:2,playbackLimit:5});b.seek(4);assert.equal(b.context.reconnected,undefined);assert.equal(b.context.confirmMessages.length,0);});
test('an unfinished local seek outside the prepared range asks before a billed restart',()=>{const b=seekBrowser({position:2,playbackLimit:5});b.seek(8);assert.deepEqual(b.context.ws.messages,['pause']);assert.equal(b.context.reconnected,undefined);b.flushTimers();assert.equal(b.context.confirmMessages.length,1);assert.match(b.context.confirmMessages[0],/可能.*API|API.*计费/);assert.deepEqual(Array.from(b.context.reconnected),['local.mp4',null,8]);});
test('declining an unprepared seek restores the old position and playback without reconnecting',()=>{const b=seekBrowser({position:2,playbackLimit:5,paused:false,confirmResult:false});b.seek(8);b.flushTimers();assert.equal(b.context.player.currentTime,2);b.finishRestore();assert.equal(b.context.player.paused,false);assert.equal(b.context.skipReconnectOnNextPlay,true);assert.equal(b.context.reconnected,undefined);});
test('continuous unprepared seeks ask once and restart only at the final position',()=>{const b=seekBrowser({position:2,playbackLimit:5});b.seek(7);b.seek(8);b.seek(9);b.flushTimers();assert.equal(b.context.confirmMessages.length,1);assert.deepEqual(Array.from(b.context.reconnected),['local.mp4',null,9]);});
test('a short local resume clears its pause clock and uses the existing socket',()=>{const b=playBrowser();b.play();assert.equal(b.context.pauseStartedAt,null);assert.deepEqual(b.context.ws.messages,['resume']);assert.equal(b.context.reconnected,undefined);});
test('a completed worker end marks local original captions reusable',()=>{const c=endedSession('completed');assert.equal(c.originalCaptionsComplete,true);assert.equal(c.playbackLimit,Infinity);assert.equal(c.playbackGuard.done,true);});
test('cancelled, failed, and legacy worker ends do not claim completion or unbounded media',()=>{for(const reason of ['cancelled','error',undefined]){const c=endedSession(reason);assert.equal(c.originalCaptionsComplete,false);assert.equal(c.playbackLimit,5);assert.equal(c.playbackGuard.done,false);}});
test('a terminal worker end never schedules another recovery from its socket close',()=>{
 for(const reason of ['completed','cancelled','error']){
  const {context,recoveries}=terminalCloseSession(reason);
  assert.equal(recoveries.length,0,`${reason} end must not schedule recovery`);
  assert.equal(context.ws.closeCode,1000);
  assert.equal(context.terminalReceived,true);
 }
});
test('a terminal end waiting for its media timeline still suppresses close recovery',()=>{
 const {context,recoveries}=terminalCloseSession('completed',{awaitingTimeline:true});
 assert.equal(recoveries.length,0);
 assert.deepEqual(context.queuedMediaMessages.map(message=>message.type),['end']);
 assert.equal(context.terminalReceived,true);
});
test('a terminal close keeps stop and only-play visible while recovery backoff is pending',()=>{
 const {context,elements}=closeDuringPendingRecovery();
 assert.equal(context.recoveryController.pending.attempt,1);
 assert.equal(context.stopBtn.style.display,'inline-block');
 assert.equal(context.startBtn.style.display,'none');
 assert.equal(elements.get('captionActions').hidden,false);
});
test('long English captions wrap to two lines on a narrow player',()=>{const b=browser({playerWidth:260,fontSize:30});const sentence='This complete English caption should keep its words and remain easy to read on a narrow player.';const text=[sentence,sentence].join(' ');const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assert.ok(segments.every(s=>s.text.length<=25),segments);assert.equal(segments.map(s=>s.text).join(' ').replace(/\s+/g,' '),text.replace(/\s+/g,' '));});

test('resizing a pending queue reapplies its budget without replacing the current page',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:1,beg:0,text:'第一句完整的字幕。'});const current=p.tick(0,0);p.enqueue({sentence_id:2,beg:0,text:'这是一段较长的字幕用于检查放大字号之后待显示分页的总阅读时间仍然受到限制不会无限积压。'.repeat(3)});p.setColumns(12);assert.ok(p.current.text.startsWith('第一句'));assert.ok(p.queue.reduce((n,c)=>n+c.hold,0)<=14000);assert.equal(current.text,'第一句完整的字幕。');});
test('a whole-clause correction updates pending pages but never token-rewrites the active page',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:1,revision:1,beg:0,text:'I love cats.'});assert.equal(p.tick(0,0).text,'I love cats.');p.enqueue({sentence_id:1,revision:2,beg:0,text:'I really love cats.'});assert.equal(p.tick(1,100).text,'I love cats.');assert.equal(p.tick(3,2500).text,'I really love cats.');});

test('local video keeps every complete page instead of using the live catch-up policy',()=>{const p=new CaptionPresenter();p.live=false;for(let i=0;i<20;i++)p.enqueue({sentence_id:i,beg:i,text:'本地视频应保留每一句完整字幕。'});assert.equal(p.skipped,0);assert.equal(p.queue.length,20);p.setColumns(12);assert.equal(p.skipped,0);assert.ok(p.queue.length>=20);});

test('corrections retain source order when newer clauses are already waiting',()=>{const p=new CaptionPresenter();p.live=false;const cue=(id,text,revision=1)=>({sentence_id:id,beg:0,text,revision});p.enqueue(cue('a','Old A.'));p.tick(0,0);p.enqueue(cue('b','B.'));p.enqueue(cue('c','C.'));p.enqueue(cue('a','Corrected A.',2));assert.deepEqual(p.queue.map(c=>c.sentence_id),['a','b','c']);p.enqueue(cue('b','Corrected B.',2));assert.deepEqual(p.queue.map(c=>c.sentence_id),['a','b','c']);});
test('removed clauses cannot survive in the current caption or its pending pages',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:'a',revision:1,beg:0,text:'Deleted source.'});p.tick(0,0);p.remove('a',2);assert.equal(p.current,null);assert.equal(p.queue.length,0);p.enqueue({sentence_id:'a',revision:1,beg:0,text:'Stale network event.'});assert.equal(p.queue.length,0);});
test('a pre-seek translation cannot refill the cleared queue at the new position',()=>{const b=browser();b.context.seekPending=true;b.context.player.currentTime=100;b.push({beg:1,end:2});assert.equal(b.current.textContent,'');assert.equal(b.context.seekPending,true);b.push({beg:99,end:101,sentence_id:2});assert.match(b.current.textContent,/这句译文/);});

test('an old final after a removal cannot recreate a transcript row',()=>{const b=browser();b.context.captionPresenter.remove(1,2);b.push({revision:1});assert.equal(b.context.subs.length,0);assert.equal(b.current.textContent,'');});
test('a stale original caption after a removal cannot recreate its transcript row',()=>{const b=browser(),id='asr-7';const old={sentence_id:id,revision:0,beg:1,end:2,is_final:true,translated:false,text:'已撤回的原文。'};b.context.onSubtitle(old);assert.equal(b.context.subs.length,1);b.context.captionPresenter.remove(id,1);b.context.captionRevisions.set(id,1);b.context.subs=b.context.subs.filter(s=>s.sentenceId!==id);b.context.onSubtitle(old);assert.equal(b.context.subs.length,0);});
test('a newer original revision after a removal can restore the corrected sentence',()=>{const b=browser(),id='asr-7';b.context.captionRevisions.set(id,1);b.context.onSubtitle({sentence_id:id,revision:2,beg:1,end:2,is_final:true,translated:false,text:'已修正的原文。'});assert.equal(b.context.subs.length,1);assert.equal(b.context.subs[0].text,'已修正的原文。');b.context.onSubtitle({sentence_id:id,revision:0,beg:1,end:2,is_final:true,translated:false,text:'更旧的原文。'});assert.equal(b.context.subs[0].text,'已修正的原文。');});
test('an anonymous caption is not blocked by another sentence retraction',()=>{const b=browser();b.context.captionRevisions.set('asr-7',1);b.context.onSubtitle({beg:3,end:4,is_final:true,translated:false,text:'匿名字幕仍可显示。'});assert.equal(b.context.subs.length,1);});
test('captions without a revision retain the legacy client behavior',()=>{const b=browser(),id='legacy-id';b.context.onSubtitle({sentence_id:id,beg:1,end:2,is_final:true,translated:false,text:'旧服务字幕。'});b.context.subs=[];b.context.captionPresenter.remove(id,0);b.context.onSubtitle({sentence_id:id,beg:1,end:2,is_final:true,translated:false,text:'旧服务后续字幕。'});assert.equal(b.context.subs.length,1);assert.equal(b.context.subs[0].text,'旧服务后续字幕。');});

test('a cloud disconnect during local pause preserves the caption and reconnects at the same position on play',()=>{const b=browser();b.push({});vm.runInContext('uiPaused=true',b.context);assert.equal(b.context.holdCaptionForCloudReconnect(),true);assert.match(b.current.textContent,/这句译文/);assert.equal(b.context.resumeClosedCloudAfterPause(),true);assert.deepEqual(Array.from(b.context.reconnected).slice(0,3),['test.mp4',null,10]);assert.equal(b.context.reconnected[3].automaticRecovery,true);assert.equal(b.context.cloudResumeRequired,false);assert.equal(b.context.resumeClosedCloudAfterPause(),false);});
test('cloud failures during normal playback remain visible errors rather than silent reconnect loops',()=>{const b=browser();assert.equal(b.context.holdCaptionForCloudReconnect(),false);assert.equal(b.context.cloudResumeRequired,false);});

test('a recovered ASR session reusing a sentence ID preserves both generations of transcript history',()=>{
 const b=browser(),c=b.context,html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 Object.assign(c,{generation:2,connectionGeneration:2,preserveLiveMedia:true,recognitionOnlyStopped:false,
   audioClockPts:null,videoClockPts:null,hlsVideoClockPts:null,timelineMapped:false,queuedMediaMessages:[],mediaOffset:0,
   originalCaptionMode:true,captionHasSpeech:true,playStarted:true,holdingForBuffer:false,
   syncSubtitle(){},markSessionRecoveryProgress(){},
   playbackGuard:{captionProgress(){},removeCaption(){}}});
 const old={sentence_id:'sentence-1',revision:4,beg:1,end:2,is_final:true,translated:false,text:'上一会话的句子。'};
 c.onSubtitle(old);
 const previewStart=html.indexOf('  function onPreview(msg) {');
 const previewEnd=html.indexOf('\n\n  // Decoder PTS',previewStart);
 vm.runInContext(html.slice(previewStart,previewEnd),c);
 c.onPreview({sentence_id:'sentence-1',beg:2,end:3,text:'上一会话草稿'});
 const helperStart=html.indexOf('  function scopeSessionCaptionIdentity(');
 const helperEnd=html.indexOf('\n\n  async function connect(',helperStart);
 vm.runInContext(html.slice(helperStart,helperEnd),c);
 const start=html.indexOf('    function receiveMessage(input) {');
 const end=html.indexOf('    ws.onmessage = ',start);
 vm.runInContext(html.slice(start,end),c);
 c.receiveMessage({type:'subtitle',sentence_id:'sentence-1',revision:0,beg:8,end:9,
   is_final:true,translated:false,text:'新会话的不同句子。'});
 assert.equal(c.subs.length,2,'new ASR generation must append a distinct transcript row');
 assert.deepEqual(Array.from(c.subs,s=>s.text),['上一会话的句子。','新会话的不同句子。']);
 assert.notEqual(c.subs[0].sentenceId,c.subs[1].sentenceId);
 c.receiveMessage({type:'preview',sentence_id:'sentence-1',beg:9,end:10,text:'新会话草稿'});
 c.receiveMessage({type:'caption_remove',sentence_id:'sentence-1',revision:1});
 assert.deepEqual(Array.from(c.subs,s=>s.text),['上一会话的句子。']);
 assert.deepEqual(Array.from(c.previewSubs,s=>s.text),['上一会话草稿']);
});

test('a recovered live session drains queued subtitles when the preserved HLS clock arrives late',()=>{
 const {context:c}=lateHlsClockViewer();
 c.receiveMessage({type:'timeline',audio_pts:10});
 c.receiveMessage({type:'ready',original:true,buffered:true});
 c.receiveMessage({type:'subtitle',sentence_id:'live-sentence',revision:1,beg:1,end:2,
   is_final:true,translated:false,text:'Live sentence'});
 assert.deepEqual(Array.from(c.queuedMediaMessages,message=>message.type),['ready','subtitle']);
 c.hlsVideoClockPts=8; // the existing HLS instance delivers INIT_PTS after the new ASR timeline
 c.receiveMessage({type:'status',message:'still connected'});
 assert.equal(c.timelineMapped,true);
 assert.deepEqual(Array.from(c.queuedMediaMessages),[]);
 assert.equal(c.subs.length,1);
 assert.equal(c.subs[0].text,'Live sentence');
 assert.equal(c.subs[0].beg,3);
 assert.equal(c.subs[0].end,4);
 assert.equal(c.player.playCalls,1);
 assert.equal(c.terminalReceived,false);
});

test('the preserved HLS INIT_PTS callback drains a queued terminal end without another WS message',()=>{
 const {context:c}=lateHlsClockViewer(),html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 c.ws={close(){c.closed=true;}};
 c.receiveMessage({type:'timeline',audio_pts:10});
 c.receiveMessage({type:'ready',original:true,buffered:true});
 c.receiveMessage({type:'subtitle',sentence_id:'live-sentence',revision:1,beg:1,end:2,
   is_final:true,translated:false,text:'Live sentence'});
 c.receiveMessage({type:'end',reason:'completed'});
 assert.deepEqual(Array.from(c.queuedMediaMessages,message=>message.type),['ready','subtitle','end']);
 const ptsStart=html.indexOf('      hlsPlayer.on(Hls.Events.INIT_PTS_FOUND, (_evt, data) => {');
 const ptsEnd=html.indexOf('\n      hlsPlayer.on(Hls.Events.ERROR',ptsStart);
 c.hlsPlayer={on(_event,callback){c.onInitPts=callback;}};
 c.Hls={Events:{INIT_PTS_FOUND:'init_pts_found'}};
 c.flushMediaClock=()=>{c.oldGenerationFlushes=(c.oldGenerationFlushes||0)+1;};
 vm.runInContext(`let videoClockPts=null; const currentHls=hlsPlayer;\n${html.slice(ptsStart,ptsEnd)}`,c);
 c.onInitPts('init_pts_found',{id:'main',initPTS:720000,timescale:90000});
 assert.equal(c.timelineMapped,true);
 assert.deepEqual(Array.from(c.queuedMediaMessages),[]);
 assert.equal(c.subs.length,1);
 assert.equal(c.closed,true);
 assert.equal(c.playbackGuard.done,true);
 assert.equal(c.terminalReceived,true);
});

test('only automatic original-caption recovery on an active live HLS source preserves the media player',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const start=html.indexOf('  function shouldPreserveLiveMedia(');
 const end=html.indexOf('\n\n  async function connect(',start);
 const c={};vm.createContext(c);vm.runInContext(html.slice(start,end),c);
 const source={};
 assert.equal(c.shouldPreserveLiveMedia({automaticRecovery:true,live:true,originalCaptionMode:true,
   sharedViewerActive:false,hlsPlayer:source}),true);
 for(const changed of [
   {automaticRecovery:false,live:true,originalCaptionMode:true,sharedViewerActive:false,hlsPlayer:source},
   {automaticRecovery:true,live:false,originalCaptionMode:true,sharedViewerActive:false,hlsPlayer:source},
   {automaticRecovery:true,live:true,originalCaptionMode:false,sharedViewerActive:false,hlsPlayer:source},
   {automaticRecovery:true,live:true,originalCaptionMode:true,sharedViewerActive:true,hlsPlayer:source},
   {automaticRecovery:true,live:true,originalCaptionMode:true,sharedViewerActive:false,hlsPlayer:null},
 ]) assert.equal(c.shouldPreserveLiveMedia(changed),false);
});

test('a long local pause reconnects before reusing a potentially expired ASR socket',()=>{const b=browser();b.context.pauseStartedAt=0;b.context.now=16000;assert.equal(b.context.resumeClosedCloudAfterPause(),true);assert.deepEqual(Array.from(b.context.reconnected),['test.mp4',null,10]);});
test('a short local pause continues on the existing open recognition connection',()=>{const b=browser();b.context.pauseStartedAt=0;b.context.now=3000;assert.equal(b.context.resumeClosedCloudAfterPause(),false);assert.equal(b.context.reconnected,undefined);});

test('original captions follow source word times rather than the translated reading queue',()=>{const b=browser();b.context.player.currentTime=.2;b.push({translated:false,beg:0,end:10,text:'别太装。我们还是很期待的。',words:[[0,.2,'别'],[.2,.4,'太'],[.4,.8,'装'],[8,8.2,'我'],[8.2,8.4,'们'],[8.4,8.6,'还'],[8.6,8.8,'是'],[8.8,9,'很'],[9,9.2,'期'],[9.2,9.4,'待'],[9.4,10,'的']]});b.tick(0);assert.equal(b.current.textContent,'别太装。');b.context.player.currentTime=2;b.tick(100);assert.equal(b.current.textContent,'');b.context.player.currentTime=8.5;b.tick(200);assert.equal(b.current.textContent.replace(/\s+/gu,''),'我们还是很期待的。');assert.equal(b.context.captionPresenter.queue.length,0);});
test('completed original captions resume locally without opening another billed session',()=>{const b=browser();b.context.originalCaptionsComplete=true;b.context.pauseStartedAt=0;b.context.now=16000;assert.equal(b.context.resumeClosedCloudAfterPause(),false);assert.equal(b.context.reconnected,undefined);});

function bufferBrowser(){const b=browser(),c=b.context;const {CaptionPlaybackGuard}=require('../tools/webui_static/playback_guard.js');c.playbackGuard=new CaptionPlaybackGuard();c.playbackGuard.reset({now:0,live:true});c.showCaptionRecovery=message=>{c.status=message};Object.assign(c,{sourceBufferingSince:null,useCaptionBuffer:true,originalCaptionMode:true,holdingForBuffer:false,uiPaused:false,playbackLimit:10,urlEl:{value:'https://stream.test/live.m3u8'},setStatus(t){c.status=t},audioEl:{pause(){},play(){return Promise.resolve()}},player:{currentTime:10,paused:false,pause(){this.paused=true},play(){this.paused=false;return Promise.resolve()}}});const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');vm.runInContext(html.slice(html.indexOf('function syncPlaybackToBuffer()'),html.indexOf('// 字幕整句快照')),c);return b;}
test('live original captions release the playback hold after the two-second failure budget',()=>{const b=bufferBrowser();b.context.syncPlaybackToBuffer();assert.equal(b.context.player.paused,true);b.context.now=2100;b.context.syncPlaybackToBuffer();assert.equal(b.context.player.paused,false);assert.match(b.context.status,/部分缺失|暂不可用/);b.context.now=2400;b.context.syncPlaybackToBuffer();assert.equal(b.context.player.paused,false);});
test('a live source starts after the bounded wait even when no first caption or ready event arrives',()=>{const b=bufferBrowser(),c=b.context;c.playStarted=false;c.player.currentTime=0;c.player.paused=true;c.playbackLimit=5;c.playbackGuard.reset({now:0,live:true});c.now=0;c.syncPlaybackToBuffer();assert.equal(c.holdingForBuffer,true);assert.equal(c.player.paused,true);c.now=1999;c.syncPlaybackToBuffer();assert.equal(c.player.paused,true);c.now=2000;c.syncPlaybackToBuffer();assert.equal(c.playStarted,true);assert.equal(c.player.paused,false);assert.match(c.status,/部分缺失|暂不可用/);});
test('an unfinished local file holds at its caption boundary rather than playing ahead',()=>{const b=bufferBrowser();b.context.urlEl.value='test.mp4';b.context.playbackGuard.live=false;b.context.syncPlaybackToBuffer();assert.equal(b.context.player.paused,true);});

const {CaptionPlaybackGuard}=require('../tools/webui_static/playback_guard.js');
test('preparing captions times out even if only heartbeats arrive',()=>{const g=new CaptionPlaybackGuard();g.reset({now:0,live:false});assert.equal(g.tick({now:15001,time:0,limit:0,ready:false,userPaused:false}),'timeout');assert.equal(g.tick({now:30000,time:0,limit:0,ready:false,userPaused:false}),'none');});
test('a user pause does not consume the caption failure timeout',()=>{const g=new CaptionPlaybackGuard();g.reset({now:0,live:false});g.tick({now:1000,time:0,limit:0,ready:false,userPaused:true});assert.equal(g.tick({now:40000,time:0,limit:0,ready:false,userPaused:false}),'none');});
test('a late result and feed watermark cannot clear a degraded live caption fault',()=>{const g=new CaptionPlaybackGuard();g.reset({now:0,live:true});assert.equal(g.tick({now:0,time:10,limit:10,ready:true}),'hold');assert.equal(g.tick({now:2000,time:10,limit:10,ready:true}),'degrade');g.progress(10.1,2100);assert.equal(g.tick({now:4000,time:12,limit:10.1,ready:true}),'none');assert.equal(g.tick({now:5000,time:13,limit:15,ready:true}),'none');assert.equal(g.degraded,true);});

test('explicit stop disables the original-caption timeout and cancels a pending recovery',async()=>{const b=bufferBrowser(),c=b.context;const els=new Map();c.document={getElementById:id=>{if(!els.has(id))els.set(id,{hidden:false,textContent:''});return els.get(id)}};const {RecoveryController}=require('../tools/webui_static/recovery_controller.js');let timer;const recoveryController=new RecoveryController({setTimeout(fn){timer=fn;return 1;},clearTimeout(){timer=null;}});recoveryController.begin();recoveryController.schedule('recognition',true,()=>assert.fail('stopped recovery fired'));Object.assign(c,{recoveryController,stopBtn:{style:{}},startBtn:{style:{}},shareCopyBtn:{style:{}},shareAddressEl:{hidden:false},activeCloudSession:null,activeShareToken:null,connectionGeneration:1,stallTimer:null,hlsPlayer:null,ws:null,pendingLocalSeek:null,pendingLocalSeekTimer:null,pendingSeekOrigin:null,restoringSeek:null,skipReconnectOnNextPlay:false,setSourceBuffering(){},clearTimeout(){}});Object.assign(c.player,{removeAttribute(){},load(){}});Object.assign(c.audioEl,{removeAttribute(){},load(){}});c.currentEl.classList.remove=()=>{};const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');vm.runInContext(html.slice(html.indexOf('stopBtn.onclick = async'),html.indexOf('const sharedPath =')),c);await c.stopBtn.onclick();if(timer)timer();assert.equal(recoveryController.cancelled,true);assert.equal(c.playbackGuard.done,true);assert.equal(c.cloudPlayback,false);assert.equal(c.document.getElementById('captionActions').hidden,true);});
test('stopping cancels a debounced local seek before it can ask or restart',async()=>{const b=bufferBrowser(),c=b.context;c.recoveryController={cancel(){}};const els=new Map();c.document={getElementById:id=>{if(!els.has(id))els.set(id,{hidden:false,textContent:''});return els.get(id)}};const cleared=[];Object.assign(c,{stopBtn:{style:{}},startBtn:{style:{}},shareCopyBtn:{style:{}},shareAddressEl:{hidden:false},activeCloudSession:null,activeShareToken:null,connectionGeneration:1,stallTimer:null,hlsPlayer:null,ws:null,pendingLocalSeek:{destination:8,origin:2,resumeAfterCancel:true},pendingLocalSeekTimer:17,setSourceBuffering(){},clearTimeout:id=>cleared.push(id)});Object.assign(c.player,{removeAttribute(){},load(){}});Object.assign(c.audioEl,{removeAttribute(){},load(){}});c.currentEl.classList.remove=()=>{};const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');const helperStart=html.indexOf('  function cancelPendingLocalSeek()');const helperEnd=html.indexOf('  function preparedLocalPosition(',helperStart);vm.runInContext(html.slice(helperStart,helperEnd),c);vm.runInContext(html.slice(html.indexOf('stopBtn.onclick = async'),html.indexOf('const sharedPath =')),c);await c.stopBtn.onclick();assert.equal(c.pendingLocalSeek,null);assert.equal(c.pendingLocalSeekTimer,null);assert.deepEqual(cleared,[17]);});

test('explicit stop unloads media sources and destroys HLS to release local file handles',async()=>{
 const b=bufferBrowser(),c=b.context,mediaActions=[];
 const els=new Map();
 c.document={getElementById:id=>{if(!els.has(id))els.set(id,{hidden:false,textContent:''});return els.get(id)}};
 Object.assign(c,{recoveryController:{cancel(){}},stopBtn:{style:{}},startBtn:{style:{}},
   shareCopyBtn:{style:{}},shareAddressEl:{hidden:false},activeCloudSession:null,activeShareToken:null,
   connectionGeneration:1,stallTimer:null,ws:null,activeInput:'capacity.mp4',pendingLocalSeek:null,
   pendingLocalSeekTimer:null,cancelPendingLocalSeek(){},setSourceBuffering(){},
   clearTimeout(){},clearInterval(){},cloudRequest:async()=>({}),fetch:async()=>({})});
 c.player.removeAttribute=name=>mediaActions.push(['video-remove',name]);
 c.player.load=()=>mediaActions.push(['video-load']);
 c.audioEl={pause(){},removeAttribute:name=>mediaActions.push(['audio-remove',name]),
   load:()=>mediaActions.push(['audio-load'])};
 c.hlsPlayer={destroy(){mediaActions.push(['hls-destroy']);}};
 c.currentEl.dataset={};c.currentEl.classList.remove=()=>{};
 c.captionPresenter={reset(){}};
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 vm.runInContext(html.slice(html.indexOf('stopBtn.onclick = async'),html.indexOf('const sharedPath =')),c);
 await c.stopBtn.onclick();
 assert.deepEqual(mediaActions,[['hls-destroy'],['video-remove','src'],['video-load'],
   ['audio-remove','src'],['audio-load']]);
 assert.equal(c.hlsPlayer,null);
});

test('deleting cloud configuration cancels pending retries and closes its recognition socket',async()=>{
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const {RecoveryController}=require('../tools/webui_static/recovery_controller.js');
 const els=new Map(),calls=[];let timer;
 const button={onclick:null};
 const recoveryController=new RecoveryController({setTimeout(fn){timer=fn;return 1;},clearTimeout(){timer=null;}});
 recoveryController.begin();recoveryController.schedule('recognition',true,()=>assert.fail('deleted session retried'));
 const socket={onmessage:()=>{},onclose:()=>{},closed:false,close(){this.closed=true;}};
 const c={RecoveryController,recoveryController,connectionGeneration:4,activeCloudSession:'session-token',
   activeShareToken:'share-token',cloudPlayback:true,originalCaptionMode:true,recognitionOnlyStopped:false,
   ws:socket,cloudRequest:async(path,method)=>{calls.push([path,method]);return {};},refreshCloud:async()=>{},
   setStatus(message){c.status=message;},document:{getElementById(id){if(id==='clearCloud')return button;if(!els.has(id))els.set(id,{value:'',textContent:''});return els.get(id);}}};
 vm.createContext(c);
 const start=html.indexOf("  document.getElementById('clearCloud').onclick = async () => {");
 const end=html.indexOf('  const subToggleBtn =',start);
 vm.runInContext(html.slice(start,end),c);
 await button.onclick();if(timer)timer();
 assert.equal(recoveryController.cancelled,true);assert.equal(socket.closed,true);
 assert.equal(c.activeCloudSession,null);assert.equal(c.activeShareToken,null);
 assert.deepEqual(calls,[['/api/cloud','DELETE']]);
});

test('only-play cancels pending recovery while leaving the media player running',async()=>{
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const {RecoveryController}=require('../tools/webui_static/recovery_controller.js');
 const els=new Map([['captionActions',{hidden:false}],['playOnly',{onclick:null}]]);let timer;
 const recoveryController=new RecoveryController({setTimeout(fn){timer=fn;return 1;},clearTimeout(){timer=null;}});
 recoveryController.begin();recoveryController.schedule('recognition',true,()=>assert.fail('only-play recovery fired'));
 const player={paused:true,pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}};
 const currentEl={textContent:'',classList:{remove(){}}};
 const c={connectionGeneration:2,recoveryController,recognitionOnlyStopped:false,playbackGuard:{done:false},
   cloudResumeRequired:false,pauseStartedAt:0,cloudPlayback:true,originalCaptionsComplete:false,
   holdingForSync:true,holdingForBuffer:true,playStarted:false,playbackLimit:5,
   activeCloudSession:null,activeShareToken:null,ws:null,player,audioEl:{pause(){}},subs:[],previewSubs:[],
   captionPresenter:{reset(){}},currentEl,startBtn:{style:{}},stopBtn:{style:{}},shareCopyBtn:{style:{}},
   shareAddressEl:{hidden:false},setStatus(message){c.status=message;},cancelPendingLocalSeek(){},
   document:{getElementById(id){return els.get(id);}}};
 vm.createContext(c);
 const start=html.indexOf('  async function cancelCaptionProcessing(');
 const end=html.indexOf('  function showCaptionRecovery(',start);
 vm.runInContext(html.slice(start,end),c);
 const playOnlyStart=html.indexOf("  document.getElementById('playOnly').onclick =");
 const playOnlyEnd=html.indexOf('  startBtn.onclick = connect;',playOnlyStart);
 vm.runInContext(html.slice(playOnlyStart,playOnlyEnd),c);
 await els.get('playOnly').onclick();if(timer)timer();
 assert.equal(recoveryController.cancelled,true);assert.equal(player.paused,false);
 assert.equal(els.get('captionActions').hidden,true);
});

test('verified silent audio advances progress without a fabricated subtitle',()=>{const g=new CaptionPlaybackGuard();g.reset({now:0,live:false});g.progress(20,19000);assert.equal(g.tick({now:20000,time:18,limit:20,ready:true}),'none');});
function recoveryBrowser(){
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const Hls=require('../tools/webui_static/hls.min.js');
 const {RecoveryController}=require('../tools/webui_static/recovery_controller.js');
 let now=0,nextId=1;const timers=new Map(),actions=[],recoveryOrder=[];
 const c={recoveries:0,cancellations:0,preserveLiveMedia:false,sharedViewerActive:false,recognitionOnlyStopped:false,
   originalCaptionMode:true,uiPaused:false,activeInput:'https://stream.test/live.m3u8',
   audioClockPts:null,timelineMapped:false,queuedMediaMessages:[],
   connectionGeneration:1,generation:1,playbackLimit:0,playbackGuard:{live:true},player:{currentTime:8},
   RecoveryController,recoveryController:null,performance:{now:()=>now},
   setTimeout(callback,delay){const id=nextId++;timers.set(id,{callback,at:now+delay});return id;},
   clearTimeout(id){timers.delete(id);},logEvent(){},setStatus(message){c.status=message;},
   showCaptionRecovery(){c.cancellations++;},showRecognitionFailure(message){c.cancellations++;c.status=message;},
   holdCaptionForCloudReconnect(){return false;},
   connect(...args){actions.push(['recognition',args]);c.connectionGeneration++;},
   document:{getElementById:()=>({hidden:false})},
   currentEl:{textContent:'',classList:{contains:()=>false,add(){},remove(){}}},Hls};
 c.recoveryController=new RecoveryController({setTimeout:c.setTimeout,clearTimeout:c.clearTimeout});
 c.recoveryController.begin();
 const schedule=c.recoveryController.schedule.bind(c.recoveryController);
 c.recoveryController.schedule=(...args)=>{recoveryOrder.push('schedule');return schedule(...args);};
 const source={on(_event,callback){c.callback=callback;},stopLoad(){c.stoppedLoads=(c.stoppedLoads||0)+1;recoveryOrder.push('stopLoad');},
   startLoad(){c.recoveries++;actions.push(['media']);},
   recoverMediaError(){c.recoveries++;actions.push(['media']);},destroy(){c.destroyed=true;}};
 c.hlsPlayer=source;c.currentHls=source;
 vm.createContext(c);
 loadShareHistoryWarning(html,c);
 const helperStart=html.indexOf('  let stoppedHlsLoaderForRecovery = null;');
 const helperEnd=html.indexOf('  async function cloudRequest(',helperStart);
 vm.runInContext(html.slice(helperStart,helperEnd),c);
 const mediaStart=html.indexOf('      const hlsNetworkLoadErrors = new Set([');
 const mediaEnd=html.indexOf('      player.muted = false;  // HLS 直播',mediaStart);
 vm.runInContext(`if (true) {\n${html.slice(mediaStart,mediaEnd)}`,c);
 const wsStart=html.indexOf('    function receiveMessage(input) {');
 const wsEnd=html.indexOf('    ws.onmessage = ',wsStart);
 vm.runInContext(html.slice(wsStart,wsEnd),c);
 return {context:c,actions,recoveryOrder,
   mediaFailure(){c.callback(null,{fatal:true,type:c.Hls.ErrorTypes.NETWORK_ERROR});},
   mediaDecodeFailure(){c.callback(null,{fatal:true,type:c.Hls.ErrorTypes.MEDIA_ERROR});},
   mediaLoaderFailure(details,code=503){c.callback(null,{fatal:false,type:c.Hls.ErrorTypes.NETWORK_ERROR,
     details,response:{code}});},
   recognitionFailure(retryable=true){c.receiveMessage({type:'error',message:'连接中断',retryable});},
   advance(ms){now+=ms;for(const [id,job] of [...timers])if(job.at<=now){timers.delete(id);job.callback();}}};
}
test('an old HLS fatal event cannot stop a newly selected source',()=>{const b=recoveryBrowser(),c=b.context;c.hlsPlayer={};b.mediaFailure();assert.equal(c.cancellations,0);assert.equal(c.recoveries,0);});
test('the preserved HLS instance can recover a media error after ASR reconnect advances the generation',()=>{
 const b=recoveryBrowser(),c=b.context;
 c.connectionGeneration=2; // automatic ASR reconnect; the active HLS instance is unchanged
 b.mediaFailure();
 assert.equal(c.recoveryController.pending.attempt,1);
 b.advance(1000);
 assert.equal(c.recoveries,1);
 assert.equal(c.destroyed,undefined);
});
test('a nonfatal HLS fragment load failure consumes the shared media recovery budget',()=>{
 const b=recoveryBrowser(),c=b.context;
 b.mediaLoaderFailure(c.Hls.ErrorDetails.FRAG_LOAD_ERROR);
 assert.equal(c.recoveryController.attempts,1);
 assert.equal(c.recoveryController.pending.delayMs,1000);
 assert.equal(c.stoppedLoads,1);
 assert.deepEqual(b.recoveryOrder,['stopLoad','schedule']);
 b.advance(1000);
 assert.deepEqual(b.actions.map(action=>action[0]),['media']);
});
test('nonfatal HLS manifest, level, fragment, key, and track load errors share the budget',()=>{
 const Hls=require('../tools/webui_static/hls.min.js');
 const detailNames=['MANIFEST_LOAD_ERROR','MANIFEST_LOAD_TIMEOUT','LEVEL_LOAD_ERROR','LEVEL_LOAD_TIMEOUT',
   'FRAG_LOAD_ERROR','FRAG_LOAD_TIMEOUT','KEY_LOAD_ERROR','KEY_LOAD_TIMEOUT',
   'AUDIO_TRACK_LOAD_ERROR','AUDIO_TRACK_LOAD_TIMEOUT','SUBTITLE_LOAD_ERROR','SUBTITLE_TRACK_LOAD_TIMEOUT'];
 for(const name of detailNames){
  const b=recoveryBrowser(),c=b.context,detail=Hls.ErrorDetails[name];
  assert.ok(detail,`HLS ${name} must exist in the vendored version`);
  b.mediaLoaderFailure(detail);
  assert.equal(c.recoveryController.attempts,1,`${name} should use the shared media budget`);
  assert.equal(c.stoppedLoads,1,`${name} should stop implicit HLS actions`);
 }
});
test('a nonfatal HLS buffer-stall warning does not consume recovery budget',()=>{
 const b=recoveryBrowser(),c=b.context;
 c.callback(null,{fatal:false,type:c.Hls.ErrorTypes.MEDIA_ERROR,
   details:c.Hls.ErrorDetails.BUFFER_STALLED_ERROR});
 assert.equal(c.recoveryController.attempts,0);
 assert.equal(c.stoppedLoads,undefined);
});
test('media and recognition failures share the same visible two-attempt budget',()=>{
 const b=recoveryBrowser(),c=b.context;
 b.mediaFailure();assert.equal(c.recoveryController.pending.delayMs,1000);
 b.advance(1000);assert.equal(c.recoveries,1);
 b.recognitionFailure();assert.equal(c.recoveryController.pending.attempt,2);
 assert.equal(c.recoveryController.pending.delayMs,2000);
 b.mediaFailure();assert.equal(c.recoveryController.attempts,2);
 b.advance(2000);assert.equal(c.recoveries,2);assert.deepEqual(b.actions.map(a=>a[0]),['media','media','recognition']);
 b.recognitionFailure();assert.equal(c.recoveryController.attempts,2);
 assert.match(c.status,/字幕|恢复|重试|识别/);
});
test('simultaneous media and recognition faults resume HLS and ASR in one shared attempt',()=>{
 const b=recoveryBrowser(),c=b.context;
 b.mediaFailure();
 b.recognitionFailure();
 assert.equal(c.recoveryController.attempts,1);
 assert.equal(c.recoveryController.pending.delayMs,1000);
 b.advance(1000);
 assert.deepEqual(b.actions.map(action=>action[0]),['media','recognition']);
 assert.equal(c.recoveries,1);
 assert.equal(b.actions.filter(action=>action[0]==='recognition').length,1);
});
test('simultaneous HLS decode and recognition faults execute both recovery actions',()=>{
 const b=recoveryBrowser(),c=b.context;
 b.recognitionFailure();
 b.mediaDecodeFailure();
 assert.equal(c.recoveryController.attempts,1);
 b.advance(1000);
 assert.deepEqual(b.actions.map(action=>action[0]),['media','recognition']);
 assert.equal(c.recoveries,1);
});
test('a paused session shows nonretryable provider errors for user action',()=>{
 const b=recoveryBrowser(),c=b.context;c.uiPaused=true;c.holdCaptionForCloudReconnect=()=>{c.holdCalled=true;return true;};
 b.recognitionFailure(false);
 assert.equal(c.recoveryController.attempts,0);assert.equal(c.recoveryController.pending,null);
 assert.equal(c.holdCalled,undefined);assert.equal(c.cancellations,1);
});
test('a nonretryable live recognition error keeps the healthy media player moving',()=>{
 const b=recoveryBrowser(),c=b.context,els=new Map(),cloudRequests=[];
 for(const id of ['captionActions','retryCaptions'])els.set(id,{hidden:true});
 Object.assign(c,{cloudPlayback:true,playStarted:true,originalCaptionMode:true,activeCloudSession:'session-token',
   activeShareToken:'share-token',connectionGeneration:1,recognitionOnlyStopped:false,playbackLimit:8,
   playbackGuard:{live:true,done:false},holdingForSync:false,holdingForBuffer:false,
   pauseStartedAt:null,cloudResumeRequired:false,subs:[],previewSubs:[],captionPresenter:{reset(){}},
   cancelPendingLocalSeek(){},player:{currentTime:8,paused:false,pause(){this.paused=true;},play(){this.paused=false;return Promise.resolve();}},
   audioEl:{pause(){}},ws:{readyState:1,close(){}},startBtn:{style:{}},stopBtn:{style:{}},
   shareCopyBtn:{style:{}},shareAddressEl:{hidden:false},
   document:{getElementById:id=>els.get(id)},cloudRequest:async(path,method)=>{cloudRequests.push([path,method]);return {};},fetch:async()=>({})});
 const start=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 vm.runInContext(start.slice(start.indexOf('  async function cancelCaptionProcessing('),
   start.indexOf('  document.getElementById(\'retryCaptions\').onclick')),c);
 b.recognitionFailure(false);
 assert.equal(c.player.paused,false);
 assert.match(c.status,/部分缺失|暂不可用/);
 assert.equal(c.activeCloudSession,null);assert.equal(c.activeShareToken,'share-token');
 assert.deepEqual(cloudRequests,[['/api/cloud/sessions/session-token','DELETE']]);
 assert.equal(els.get('retryCaptions').hidden,false);
});
test('switching content cancels an old generation recovery before it can reconnect',()=>{
 const b=recoveryBrowser(),c=b.context;b.recognitionFailure();
 assert.equal(c.recoveryController.pending.attempt,1);
 c.connectionGeneration++;c.recoveryController.begin();b.advance(5000);
 assert.equal(c.recoveryController.attempts,0);
 assert.equal(b.actions.some(action=>action[0]==='recognition'),false);
});

test('late first ready never rewinds an already playing live source, but initial alignment remains available',()=>{
 for(const state of [
  {live:true,started:true,preserve:false,expected:40.833},
  {live:true,started:false,preserve:false,expected:35.160},
  {live:false,started:true,preserve:false,expected:35.160},
  {live:true,started:true,preserve:true,expected:40.833},
 ]){
  const {context:c}=lateHlsClockViewer();
  c.preserveLiveMedia=state.preserve;c.playStarted=state.started;
  c.playbackGuard.live=state.live;c.player.currentTime=40.833;
  c.receiveMessage({type:'ready',original:true,buffered:true,start_at:35.160});
  assert.equal(c.player.currentTime,state.expected,JSON.stringify(state));
  assert.equal(c.playStarted,true);
 }
});
