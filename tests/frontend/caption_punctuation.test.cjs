const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const html=fs.readFileSync(path.join(__dirname,'../../lisca/web/static/index.html'),'utf8');
const START=html.indexOf('  function syncSubtitle() {');
const END=html.indexOf('  // 历史原地修订 partial');

function render(sourceText,pageText,{fastPath=false}={}){
  const el={textContent:'',classList:{contains:()=>false},dataset:{},originalPage:null};
  const preview={sentenceId:1,beg:0,end:2,text:'女尸。'};
  const context={
    player:{currentTime:1,paused:false,ended:false,seeking:false},
    activeInput:'https://media.test/live.m3u8',sharedViewerActive:false,subEnabled:true,
    translatedSession:false,playStarted:true,previewSubs:[],seekPending:false,
    bilingualEl:{checked:false},
    subs:[{beg:0,end:2,text:sourceText,isFinal:true,translated:false}],
    captionSegments:()=>[{beg:0,end:2,text:pageText??sourceText}],
    captionLayoutOptions:()=>({key:'k'}),
    setUiText:()=>{},document:{getElementById:()=>null},currentEl:el,
  };
  if(fastPath){context.previewSubs=[preview];el.originalPage={source:preview,beg:0,end:2,text:'女尸。',at:1,layoutKey:'k'};}
  vm.createContext(context);
  assert.ok(START>0&&END>START,'punctuation display code slice is present');
  vm.runInContext(html.slice(START,END),context);
  context.syncSubtitle();
  return {context,el};
}

test('Chinese sentence-final full stop is not shown on the video caption',()=>{
  const {context,el}=render('警方找到疑似女尸。');
  assert.equal(el.textContent,'警方找到疑似女尸');
  assert.equal(context.subs[0].text,'警方找到疑似女尸。','history source stays verbatim');
});

test('English sentence-final period is not shown',()=>{
  assert.equal(render('Police found her on the left.').el.textContent,'Police found her on the left');
});

test('mid-sentence punctuation stays; only the page-final mark is removed',()=>{
  assert.equal(render('广州女大学生登山失联4天，警方找到疑似女尸。').el.textContent,
    '广州女大学生登山失联4天，警方找到疑似女尸');
  assert.equal(render('广州女大学生登山失联4天，','广州女大学生登山失联4天，').el.textContent,
    '广州女大学生登山失联4天，','pagination middle page keeps its comma');
});

test('ellipsis and closing quote are handled without eating content',()=>{
  assert.equal(render('他犹豫了很久……').el.textContent,'他犹豫了很久……');
  assert.equal(render('他说“走吧。”').el.textContent,'他说“走吧”');
  assert.equal(render('这样很好。').el.textContent,'这样很好');
});

test('the fast path that re-displays an already measured page also strips it',()=>{
  const {el}=render('女尸。','女尸。',{fastPath:true});
  assert.equal(el.textContent,'女尸');
});
