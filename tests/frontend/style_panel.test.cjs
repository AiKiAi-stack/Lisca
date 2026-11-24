const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const root=path.join(__dirname,'../..');
const html=fs.readFileSync(path.join(root,'lisca/web/static/index.html'),'utf8');
const css=fs.readFileSync(path.join(root,'lisca/web/static/player.css'),'utf8');

test('the style panel is a page-level, non-modal control instead of a settings tab',()=>{
  const settingsStart=html.indexOf('<dialog id="settingsDialog"');
  const settingsEnd=html.indexOf('</dialog>',settingsStart);
  const settings=html.slice(settingsStart,settingsEnd);
  assert.ok(!settings.includes('id="stylePanel"'),'panel must leave the modal settings dialog');
  assert.ok(!html.includes('data-pane="captionSettings"'),'the removed settings tab must not linger');
  const toolsStart=html.indexOf('class="player-tools"');
  const toolsEnd=html.indexOf('</div></div>',toolsStart);
  assert.ok(html.slice(toolsStart,toolsEnd).includes('id="styleBtn"'),'style button belongs to the player tools');
  assert.ok(!html.includes('id="styleBtn" hidden'),'the dead hidden button is gone');
});

test('caption styling is driven by CSS variables on the document root',()=>{
  assert.match(css,/#current \{[^}]*font-size: var\(--caption-size/);
  assert.match(css,/#current \{[^}]*bottom: var\(--caption-bottom/);
  assert.ok(!css.includes('clamp(30px,3vw,50px)'),'fullscreen must keep the user size');
});

test('applyStyle writes variables and updates the caption size live',()=>{
  const elements=new Map();
  const element=id=>{ if(!elements.has(id)){
    elements.set(id,{value:'',textContent:'',hidden:false,style:{},setAttribute(){},focus(){},addEventListener(){}});
  } return elements.get(id); };
  const props={};
  const currentEl=element('current');
  const sizeRange=element('sizeRange');
  const context={
    document:{getElementById:element,documentElement:{style:{setProperty:(name,value)=>{props[name]=value;}}},
      addEventListener(){}},
    window:{addEventListener(){}},
    localStorage:{getItem:()=>null,setItem(){}},
    getComputedStyle:()=>({fontSize:'30px',paddingLeft:'0',paddingRight:'0',borderLeftWidth:'0',
      borderRightWidth:'0',maxWidth:'90%',fontStyle:'normal',fontWeight:'600',fontFamily:'x',letterSpacing:'0'}),
    captionPresenter:{setColumns(){}},
    currentEl,setUiText(){},logEvent(){},uiText:value=>String(value??''),
    console,
  };
  vm.createContext(context);
  const start=html.indexOf('  // 字幕样式：字体/大小/位置实时调整');
  const end=html.indexOf('  // 直播频道（ticket-19）');
  assert.ok(start>0&&end>start);
  vm.runInContext(html.slice(start,end),context);

  assert.equal(props['--caption-size'],'30px');
  assert.equal(props['--caption-bottom'],'12%');
  assert.equal(currentEl.style.fontSize,undefined,'no inline font-size may override the variable');

  sizeRange.value='44';
  sizeRange.oninput();
  assert.equal(props['--caption-size'],'44px','dragging the slider applies immediately');
  assert.equal(sizeRange.value,44);
});
