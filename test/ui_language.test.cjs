const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createI18n}=require('../tools/webui_static/i18n.js');
function host(query='',stored='zh'){
 const values=new Map([['lisca-ui-language',stored]]);
 return {location:{search:query,href:'http://localhost:8002/'+query},
   localStorage:{getItem:key=>values.get(key),setItem:(key,value)=>values.set(key,value)},
   history:{replaceState(_,__,url){this.url=String(url);}}};
}
test('explicit UI links override saved choice; unavailable storage keeps Chinese usable',()=>{
 assert.equal(createI18n(host('?lang=en')).language,'en');
 assert.equal(createI18n(host('?lang=zh','en')).language,'zh');
 assert.equal(createI18n(host('?lang=invalid','en')).language,'en');
 const englishBrowser=host('',null);englishBrowser.navigator={language:'en-GB'};
 assert.equal(createI18n(englishBrowser).language,'en');
 const privateHost=host();privateHost.localStorage={getItem(){throw Error('blocked');},setItem(){throw Error('blocked');}};
 const ui=createI18n(privateHost);assert.equal(ui.language,'zh');assert.doesNotThrow(()=>ui.setLanguage('en'));
});
test('UI translations preserve source names, counters and retry budgets',()=>{
 const ui=createI18n(host('?lang=en'));
 assert.equal(ui.t('已选择 设置，点击“播放并生成字幕”开始观看'),'Selected 设置. Click “Play with captions” to watch.');
 assert.equal(ui.t('正在恢复识别（1/2，0.5 秒后）…'),'Recovering recognition (1/2; retry in 0.5 s)…');
 assert.equal(ui.t('上传 我的影片.mp4…'),'Uploading 我的影片.mp4…');
 assert.equal(ui.t('  字号  '),'  Size  ');
 assert.equal(ui.t('语音识别失败：请先配置云端 API Key'),'Speech recognition failed: Configure your cloud API key first');
 ui.setLanguage('zh');assert.equal(ui.t('Captions off'),'字幕 关');
});
function node(text,id=''){
 return {nodeType:1,id,textContent:text,childNodes:[],dataset:{},hasAttribute(){return false;},
   matches(){return ['history','current','selectionStatus'].includes(id);}};
}
test('language changes update bound UI messages without translating captions or source names',()=>{
 const h=host('?lang=en'),ui=createI18n(h);
 const status=node(''),caption=node('设置','current'),history=node('字幕 开','history'),source=node('Settings','selectionStatus');
 ui.setText(status,'已停止');assert.equal(status.textContent,'Stopped');
 ui.setLanguage('zh');ui.apply(status);ui.apply(caption);ui.apply(history);ui.apply(source);
 assert.equal(status.textContent,'已停止');assert.equal(caption.textContent,'设置');assert.equal(history.textContent,'字幕 开');assert.equal(source.textContent,'Settings');
 ui.setText(caption,'正在准备字幕…');ui.setLanguage('en');ui.apply(caption);assert.equal(caption.textContent,'Preparing captions…');
 ui.forget(caption);caption.textContent='已停止';ui.apply(caption);assert.equal(caption.textContent,'已停止');
});
test('viewer links inherit the interface language without changing the session capability',()=>{
 const ui=createI18n(host('?lang=en'));
 assert.equal(ui.link('http://192.168.1.2:8002/share/token?x=1'),'http://192.168.1.2:8002/share/token?x=1&lang=en');
});

test('English UI translates safe provider failures without claiming an unknown cause',()=>{
 const ui=createI18n(host('?lang=en'));
 assert.match(ui.t('识别失败；现有服务商证据不足，原因未知'),/does not establish the cause/);
 assert.match(ui.t('识别服务认证失败，请检查 API Key'),/authentication failed/);
 assert.match(ui.t('识别网络连接暂时中断，可重试'),/interrupted/);
});
