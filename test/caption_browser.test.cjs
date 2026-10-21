const {test}=require('node:test'); const assert=require('node:assert/strict'); const fs=require('node:fs'); const vm=require('node:vm'); const path=require('node:path');
function browser(){
 const html=fs.readFileSync(path.join(__dirname,'../tools/webui_static/index.html'),'utf8');
 const element=()=>({textContent:'',hidden:true,dataset:{},classList:{contains:()=>false},style:{},remove(){}});
 const current=element(), late=element();
 const context={player:{currentTime:10,paused:false},activeInput:'test.mp4',sharedViewerActive:false,currentEl:current,subEnabled:true,playStarted:true,translatedSession:false,subs:[],previewSubs:[],seekPending:false,bilingualEl:{checked:false},historyItems:[],document:{getElementById:id=>id==='lateCaption'?late:null},fitCaptionWidth(){},logEvent(){},appendHistory:element,updateHistory(){},captionSegments:s=>[s],performance:{now:()=>context.now},now:0};
 const moduleFile=path.join(__dirname,'../tools/webui_static/caption_presenter.js');
 if(fs.existsSync(moduleFile)){context.CaptionPresenter=require(moduleFile).CaptionPresenter;context.lines=()=>[];context.captionPresenter=new context.CaptionPresenter();}
 vm.createContext(context);
 vm.runInContext(html.slice(html.indexOf('function syncSubtitle()'),html.indexOf('function onPreview(')),context);
 return {context,current,late,push:cue=>context.onSubtitle({type:'subtitle',translated:true,sentence_id:1,beg:0,end:2,is_final:true,text:'这句译文必须出现在视频内。',...cue}),tick(ms){context.now=ms;context.syncSubtitle();}};
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
