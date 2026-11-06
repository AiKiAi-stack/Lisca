function uiContext(context){context.uiText=value=>String(value ?? '');context.setUiText=(element,value)=>{element.textContent=value;};return context;}
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'../../tools/webui_static/index.html'),'utf8');
function element(){return {children:[],classList:{add(){},remove(){}},setAttribute(){},appendChild(child){this.children.push(child);},close(){},focus(){}};}
test('channel selection is a primary source action and playback is a separate step',()=>{
 assert.match(html,/id="sourceSidebar"[\s\S]*id="chBtn"/);
 assert.match(html,/id="emptyUpload"/);
 assert.match(html,/id="start"/);
});
test('selecting a channel never starts paid playback',async()=>{
 const list=element(),url={value:''},selection={textContent:''};let connections=0;
 const c={activeInput:'',channels:[{name:'News',group:'Live',url:'https://media.test/news.m3u8'}],
   chList:list,chRows:new Map(),urlEl:url,connect(){connections++;},
   document:{createElement:element,getElementById:id=>id==='selectionStatus'?selection:element()},
   startBtn:element(),setStatus(){},logEvent(){}};
 vm.createContext(uiContext(c));
 const start=html.indexOf('  function renderChannels() {');
 vm.runInContext(html.slice(start,html.indexOf('\n  async function probeChannel',start)),c);
 c.renderChannels();
 const row=list.children.find(x=>x.className==='ch-row');
 await row.onclick();
 assert.equal(url.value,'https://media.test/news.m3u8');
 assert.equal(connections,0);
});
test('opening a local video selects it without starting recognition',async()=>{
 const file={files:[{name:'movie.mp4'}],addEventListener(_,callback){this.change=callback;}};
 let connections=0;
 const c={activeInput:'',fileEl:file,uploadBtn:{},urlEl:{},document:{getElementById:()=>element()},
   startBtn:element(),FormData:class{append(){}},setStatus(){},logEvent(){},
   fetch:async()=>({ok:true,json:async()=>({name:'movie.mp4',path:'/uploads/movie.mp4'})}),
   connect(){connections++;}};
 vm.createContext(uiContext(c));
 const start=html.indexOf('  uploadBtn.onclick = ');
 vm.runInContext(html.slice(start,html.indexOf("  urlEl.addEventListener('input'",start)),c);
 await file.change();
 assert.equal(c.urlEl.value,'/uploads/movie.mp4');
 assert.equal(connections,0);
});
test('Enter in source selection focuses the explicit play action without starting ASR',()=>{
 const listeners={};let connections=0,focused=false;
 const c={urlEl:{addEventListener:(event,fn)=>listeners[event]=fn},
   document:{getElementById:()=>({addEventListener(){}})},
   startBtn:{focus(){focused=true;}},connect(){connections++;}};
 vm.createContext(uiContext(c));
 const start=html.indexOf("  urlEl.addEventListener('input'");
 vm.runInContext(html.slice(start,html.indexOf('\n\n  function openDialog',start)),c);
 listeners.keydown({key:'Enter',preventDefault(){}});
 assert.equal(focused,true);assert.equal(connections,0);
});
