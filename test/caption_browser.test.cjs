const {test}=require('node:test'); const assert=require('node:assert/strict'); const fs=require('node:fs'); const vm=require('node:vm'); const path=require('node:path');
test('diagnostic log keeps source identity but hides signed URL query values',()=>{const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');const elements=new Map();const c={URL,document:{getElementById(id){if(!elements.has(id))elements.set(id,{hidden:true,value:'',onclick:null});return elements.get(id);}}};vm.createContext(c);const start=html.indexOf("const logBtn = document.getElementById('logBtn');"),end=html.indexOf('// 字幕开关',start);vm.runInContext(html.slice(start,end),c);c.logEvent('connect','https://media.test/video.mp4?token=private-token&sig=private-signature rtsp://media.test/live?auth=private-rtsp-auth');const line=vm.runInContext('logBuffer[0]',c);assert.match(line,/media\.test\/video\.mp4/);assert.match(line,/media\.test\/live/);assert.doesNotMatch(line,/private-token|private-signature|private-rtsp-auth/);});
function browser({playerWidth=260,fontSize=30}={}){
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const element=()=>({textContent:'',hidden:true,dataset:{},classList:{contains:()=>false},style:{},remove(){}});
 const current=element(), late=element();
 current.style.fontSize=`${fontSize}px`;const playerWrap={clientWidth:playerWidth};
 const context={player:{currentTime:10,paused:false,pause(){this.paused=true;}},activeInput:'test.mp4',sharedViewerActive:false,cloudPlayback:true,originalCaptionsComplete:false,cloudResumeRequired:false,pauseStartedAt:null,ws:{readyState:1},holdingForSync:false,setStatus(){},connect(...args){context.reconnected=args;},currentEl:current,subEnabled:true,playStarted:true,translatedSession:false,captionRevisions:new Map(),subs:[],previewSubs:[],seekPending:false,bilingualEl:{checked:false},historyItems:[],document:{getElementById:id=>id==='lateCaption'?late:id==='playerWrap'?playerWrap:null},getComputedStyle:element=>({fontSize:element.style.fontSize||'30px',paddingLeft:'10px',paddingRight:'10px'}),fitCaptionWidth(){},logEvent(){},appendHistory:element,updateHistory(){},captionSegments:s=>[s],performance:{now:()=>context.now},now:0};
 const moduleFile=path.join(__dirname,'../tools/webui_static/caption_presenter.js');
 if(fs.existsSync(moduleFile)){context.CaptionPresenter=require(moduleFile).CaptionPresenter;context.lines=()=>[];context.captionPresenter=new context.CaptionPresenter();}
 vm.createContext(context);
 vm.runInContext(html.slice(html.indexOf('function captionSegments('),html.indexOf('function onPreview(')),context);
 return {context,current,late,push:cue=>context.onSubtitle({type:'subtitle',translated:true,sentence_id:1,beg:0,end:2,is_final:true,text:'这句译文必须出现在视频内。',...cue}),tick(ms){context.now=ms;context.syncSubtitle();}};
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
 const c={generation:1,connectionGeneration:1,recognitionOnlyStopped:false,audioClockPts:null,
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
test('provider token drafts never become visible subtitles or a second reading frame',()=>{const b=browser();b.push({is_final:false,end:null,text:'这'});b.tick(0);assert.equal(b.current.textContent,'');assert.equal(b.late.hidden,true);});
test('a completed cue remains readable after its source interval has expired',()=>{const b=browser();b.context.player.currentTime=1.9;b.push({});b.tick(0);b.context.player.currentTime=2.1;b.tick(100);assert.match(b.current.textContent,/这句译文/);});

const {CaptionPresenter,pages,readingMs}=require('../tools/webui_static/caption_presenter.js');
test('fast paragraphs paginate to two target lines with complete words',()=>{const text='This is a very long sentence with several complete phrases that should remain readable. '.repeat(8).trim();const result=pages(text);assert.ok(result.length>3);assert.ok(result.every(p=>p.split('\n').length<=2));assert.equal(result.join(' ').replace(/\s+/g,' '),text);});
test('incoming fast speech never replaces the currently readable page',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:'first',beg:0,text:'第一句完整的译文。'});assert.equal(p.tick(2,0).text,'第一句完整的译文。');for(let i=0;i<20;i++)p.enqueue({sentence_id:i,beg:0,text:'随后到来的完整短句。'});assert.equal(p.tick(3,2399).text,'第一句完整的译文。');assert.ok(p.skipped>0);assert.notEqual(p.tick(4,2401).text,'第一句完整的译文。');});
test('pause freezes reading time and resume starts from the remaining duration',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:1,beg:0,text:'Hello there.'});p.tick(0,0);p.tick(1,500);assert.ok(p.tick(1,10000,true));assert.ok(p.tick(1,10100));assert.ok(p.tick(2,11000));assert.equal(p.tick(3,13000),null);});
test('source timestamps prevent early display and reset clears every old page',()=>{const p=new CaptionPresenter();p.enqueue({sentence_id:1,beg:10,text:'Future sentence.'});assert.equal(p.tick(1,0),null);assert.ok(p.tick(10,1000));p.reset();assert.equal(p.tick(10,1100),null);});
test('duplicate final revisions do not requeue a caption',()=>{const p=new CaptionPresenter();const cue={sentence_id:1,beg:0,text:'Hello there.'};p.enqueue(cue);p.enqueue(cue);assert.equal(p.queue.length,1);assert.equal(readingMs('Hi'),2400);});

test('narrow screens reflow complete captions without losing text',()=>{const p=new CaptionPresenter();p.setColumns(20);const text='这是一个用于验证小屏幕下长字幕不会超过两行并且不会丢失任何文字的完整句子。';p.enqueue({sentence_id:1,beg:0,text});const all=[p.tick(0,0),...p.queue];assert.ok(all.every(c=>c.text.split('\n').length<=2));assert.equal(all.map(c=>c.text.replace(/\n/g,'')).join(''),text);});
test('original captions split to two-line phrases at a narrow player width',()=>{const b=browser({playerWidth:260,fontSize:30});const text='窄屏字幕需要根据播放器实际宽度切分完整原文并确保每一页都不超过两行。'.repeat(2);const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assert.ok(segments.every(s=>s.text.length<=14),segments);assert.equal(segments.map(s=>s.text).join(''),text);});
test('large desktop caption settings reduce original phrase width to two lines',()=>{const b=browser({playerWidth:900,fontSize:64});const text='字幕字号调大之后每个原文短句也要按较窄可读行宽拆分以免遮挡画面内容'.repeat(3);const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assert.ok(segments.every(s=>s.text.length<=24),segments);assert.equal(segments.map(s=>s.text).join(''),text);});
test('maximum font size still fits two lines on the narrowest player',()=>{const b=browser({playerWidth:260,fontSize:64});const text='窄屏大字号字幕仍需要遵守播放器的真实可用宽度并切成能完整阅读的短句'.repeat(2);const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assert.ok(segments.every(s=>s.text.length<=7),segments);assert.equal(segments.map(s=>s.text).join(''),text);});
test('320px captions at maximum font size split at the actual two-line Han capacity',()=>{const b=browser({playerWidth:288,fontSize:64});const text='这是一段用于验证字幕在窄屏和放大字号后仍保持完整短句的原文内容';const segments=b.context.captionSegments({beg:0,end:10,text,words:[]});assert.ok(segments.every(s=>s.text.length<=6),segments);assert.equal(segments.map(s=>s.text).join(''),text);});
test('an unfinished local seek inside the prepared range reuses subtitles without reconnecting',()=>{const b=seekBrowser({position:2,playbackLimit:5});b.seek(4);assert.equal(b.context.reconnected,undefined);assert.equal(b.context.confirmMessages.length,0);});
test('an unfinished local seek outside the prepared range asks before a billed restart',()=>{const b=seekBrowser({position:2,playbackLimit:5});b.seek(8);assert.deepEqual(b.context.ws.messages,['pause']);assert.equal(b.context.reconnected,undefined);b.flushTimers();assert.equal(b.context.confirmMessages.length,1);assert.match(b.context.confirmMessages[0],/可能.*API|API.*计费/);assert.deepEqual(Array.from(b.context.reconnected),['local.mp4',null,8]);});
test('declining an unprepared seek restores the old position and playback without reconnecting',()=>{const b=seekBrowser({position:2,playbackLimit:5,paused:false,confirmResult:false});b.seek(8);b.flushTimers();assert.equal(b.context.player.currentTime,2);b.finishRestore();assert.equal(b.context.player.paused,false);assert.equal(b.context.skipReconnectOnNextPlay,true);assert.equal(b.context.reconnected,undefined);});
test('continuous unprepared seeks ask once and restart only at the final position',()=>{const b=seekBrowser({position:2,playbackLimit:5});b.seek(7);b.seek(8);b.seek(9);b.flushTimers();assert.equal(b.context.confirmMessages.length,1);assert.deepEqual(Array.from(b.context.reconnected),['local.mp4',null,9]);});
test('a short local resume clears its pause clock and uses the existing socket',()=>{const b=playBrowser();b.play();assert.equal(b.context.pauseStartedAt,null);assert.deepEqual(b.context.ws.messages,['resume']);assert.equal(b.context.reconnected,undefined);});
test('a completed worker end marks local original captions reusable',()=>{const c=endedSession('completed');assert.equal(c.originalCaptionsComplete,true);assert.equal(c.playbackLimit,Infinity);assert.equal(c.playbackGuard.done,true);});
test('cancelled, failed, and legacy worker ends do not claim completion or unbounded media',()=>{for(const reason of ['cancelled','error',undefined]){const c=endedSession(reason);assert.equal(c.originalCaptionsComplete,false);assert.equal(c.playbackLimit,5);assert.equal(c.playbackGuard.done,false);}});
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

test('a cloud disconnect during local pause preserves the caption and reconnects at the same position on play',()=>{const b=browser();b.push({});vm.runInContext('uiPaused=true',b.context);assert.equal(b.context.holdCaptionForCloudReconnect(),true);assert.match(b.current.textContent,/这句译文/);assert.equal(b.context.resumeClosedCloudAfterPause(),true);assert.deepEqual(Array.from(b.context.reconnected),['test.mp4',null,10]);assert.equal(b.context.cloudResumeRequired,false);assert.equal(b.context.resumeClosedCloudAfterPause(),false);});
test('cloud failures during normal playback remain visible errors rather than silent reconnect loops',()=>{const b=browser();assert.equal(b.context.holdCaptionForCloudReconnect(),false);assert.equal(b.context.cloudResumeRequired,false);});

test('a long local pause reconnects before reusing a potentially expired ASR socket',()=>{const b=browser();b.context.pauseStartedAt=0;b.context.now=16000;assert.equal(b.context.resumeClosedCloudAfterPause(),true);assert.deepEqual(Array.from(b.context.reconnected),['test.mp4',null,10]);});
test('a short local pause continues on the existing open recognition connection',()=>{const b=browser();b.context.pauseStartedAt=0;b.context.now=3000;assert.equal(b.context.resumeClosedCloudAfterPause(),false);assert.equal(b.context.reconnected,undefined);});

test('original captions follow source word times rather than the translated reading queue',()=>{const b=browser();b.context.player.currentTime=.2;b.push({translated:false,beg:0,end:10,text:'别太装。我们还是很期待的。',words:[[0,.2,'别'],[.2,.4,'太'],[.4,.8,'装'],[8,8.2,'我'],[8.2,8.4,'们'],[8.4,8.6,'还'],[8.6,8.8,'是'],[8.8,9,'很'],[9,9.2,'期'],[9.2,9.4,'待'],[9.4,10,'的']]});b.tick(0);assert.equal(b.current.textContent,'别太装。');b.context.player.currentTime=2;b.tick(100);assert.equal(b.current.textContent,'');b.context.player.currentTime=8.5;b.tick(200);assert.equal(b.current.textContent,'我们还是很期待的。');assert.equal(b.context.captionPresenter.queue.length,0);});
test('completed original captions resume locally without opening another billed session',()=>{const b=browser();b.context.originalCaptionsComplete=true;b.context.pauseStartedAt=0;b.context.now=16000;assert.equal(b.context.resumeClosedCloudAfterPause(),false);assert.equal(b.context.reconnected,undefined);});

function bufferBrowser(){const b=browser(),c=b.context;const {CaptionPlaybackGuard}=require('../tools/webui_static/playback_guard.js');c.playbackGuard=new CaptionPlaybackGuard();c.playbackGuard.reset({now:0,live:true});c.showCaptionRecovery=message=>{c.status=message};Object.assign(c,{useCaptionBuffer:true,originalCaptionMode:true,holdingForBuffer:false,uiPaused:false,playbackLimit:10,urlEl:{value:'https://stream.test/live.m3u8'},setStatus(t){c.status=t},audioEl:{pause(){},play(){return Promise.resolve()}},player:{currentTime:10,paused:false,pause(){this.paused=true},play(){this.paused=false;return Promise.resolve()}}});const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');vm.runInContext(html.slice(html.indexOf('function syncPlaybackToBuffer()'),html.indexOf('// 字幕整句快照')),c);return b;}
test('live original captions release the playback hold after the two-second failure budget',()=>{const b=bufferBrowser();b.context.syncPlaybackToBuffer();assert.equal(b.context.player.paused,true);b.context.now=2100;b.context.syncPlaybackToBuffer();assert.equal(b.context.player.paused,false);assert.match(b.context.status,/部分缺失|暂不可用/);b.context.now=2400;b.context.syncPlaybackToBuffer();assert.equal(b.context.player.paused,false);});
test('an unfinished local file holds at its caption boundary rather than playing ahead',()=>{const b=bufferBrowser();b.context.urlEl.value='test.mp4';b.context.playbackGuard.live=false;b.context.syncPlaybackToBuffer();assert.equal(b.context.player.paused,true);});

const {CaptionPlaybackGuard}=require('../tools/webui_static/playback_guard.js');
test('preparing captions times out even if only heartbeats arrive',()=>{const g=new CaptionPlaybackGuard();g.reset({now:0,live:false});assert.equal(g.tick({now:15001,time:0,limit:0,ready:false,userPaused:false}),'timeout');assert.equal(g.tick({now:30000,time:0,limit:0,ready:false,userPaused:false}),'none');});
test('a user pause does not consume the caption failure timeout',()=>{const g=new CaptionPlaybackGuard();g.reset({now:0,live:false});g.tick({now:1000,time:0,limit:0,ready:false,userPaused:true});assert.equal(g.tick({now:40000,time:0,limit:0,ready:false,userPaused:false}),'none');});
test('a late result cannot repeatedly pause a degraded live stream',()=>{const g=new CaptionPlaybackGuard();g.reset({now:0,live:true});assert.equal(g.tick({now:0,time:10,limit:10,ready:true}),'hold');assert.equal(g.tick({now:2000,time:10,limit:10,ready:true}),'degrade');g.progress(10.1,2100);assert.equal(g.tick({now:4000,time:12,limit:10.1,ready:true}),'none');assert.equal(g.tick({now:5000,time:13,limit:15,ready:true}),'recovered');});

test('explicit stop disables the original-caption timeout and hides recovery actions',async()=>{const b=bufferBrowser(),c=b.context;const els=new Map();c.document={getElementById:id=>{if(!els.has(id))els.set(id,{hidden:false,textContent:''});return els.get(id)}};Object.assign(c,{stopBtn:{style:{}},startBtn:{style:{}},shareCopyBtn:{style:{}},shareAddressEl:{hidden:false},activeCloudSession:null,activeShareToken:null,connectionGeneration:1,stallTimer:null,hlsPlayer:null,ws:null,pendingLocalSeek:null,pendingLocalSeekTimer:null,pendingSeekOrigin:null,restoringSeek:null,skipReconnectOnNextPlay:false,clearTimeout(){}});c.currentEl.classList.remove=()=>{};const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');vm.runInContext(html.slice(html.indexOf('stopBtn.onclick = async'),html.indexOf('const sharedPath =')),c);await c.stopBtn.onclick();assert.equal(c.playbackGuard.done,true);assert.equal(c.cloudPlayback,false);assert.equal(c.document.getElementById('captionActions').hidden,true);});
test('stopping cancels a debounced local seek before it can ask or restart',async()=>{const b=bufferBrowser(),c=b.context;const els=new Map();c.document={getElementById:id=>{if(!els.has(id))els.set(id,{hidden:false,textContent:''});return els.get(id)}};const cleared=[];Object.assign(c,{stopBtn:{style:{}},startBtn:{style:{}},shareCopyBtn:{style:{}},shareAddressEl:{hidden:false},activeCloudSession:null,activeShareToken:null,connectionGeneration:1,stallTimer:null,hlsPlayer:null,ws:null,pendingLocalSeek:{destination:8,origin:2,resumeAfterCancel:true},pendingLocalSeekTimer:17,clearTimeout:id=>cleared.push(id)});c.currentEl.classList.remove=()=>{};const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');const helperStart=html.indexOf('  function cancelPendingLocalSeek()');const helperEnd=html.indexOf('  function preparedLocalPosition(',helperStart);vm.runInContext(html.slice(helperStart,helperEnd),c);vm.runInContext(html.slice(html.indexOf('stopBtn.onclick = async'),html.indexOf('const sharedPath =')),c);await c.stopBtn.onclick();assert.equal(c.pendingLocalSeek,null);assert.equal(c.pendingLocalSeekTimer,null);assert.deepEqual(cleared,[17]);});

test('verified silent audio advances progress without a fabricated subtitle',()=>{const g=new CaptionPlaybackGuard();g.reset({now:0,live:false});g.progress(20,19000);assert.equal(g.tick({now:20000,time:18,limit:20,ready:true}),'none');});
function hlsFailureHandler(){const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');const c={recoveries:0,cancellations:0,mediaRecoveryAttempts:0,sharedViewerActive:false,recognitionOnlyStopped:false,logEvent(){},setStatus(){},showCaptionRecovery(){c.cancellations++},document:{getElementById:()=>({hidden:false})},Hls:{Events:{ERROR:'error'},ErrorTypes:{NETWORK_ERROR:'network',MEDIA_ERROR:'media'}}};const source={on(_event,callback){c.callback=callback},startLoad(){c.recoveries++},destroy(){c.destroyed=true}};c.hlsPlayer=source;c.currentHls=source;vm.createContext(c);vm.runInContext(html.slice(html.indexOf('hlsPlayer.on(Hls.Events.ERROR'),html.indexOf('player.muted = false;  // HLS 直播')),c);return c;}
test('an old HLS fatal event cannot stop a newly selected source',()=>{const c=hlsFailureHandler();c.hlsPlayer={};c.callback(null,{fatal:true,type:'network'});assert.equal(c.cancellations,0);assert.equal(c.recoveries,0);});
test('fatal HLS recovery uses one shared budget of two attempts',()=>{const c=hlsFailureHandler();for(let n=0;n<3;n++)c.callback(null,{fatal:true,type:'network'});assert.equal(c.recoveries,2);assert.equal(c.cancellations,1);});
