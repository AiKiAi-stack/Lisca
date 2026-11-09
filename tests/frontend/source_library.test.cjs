const {test} = require('node:test');
const assert = require('node:assert/strict');
const {RecentChannels} = require('../../lisca/web/static/source_library.js');
function storage(initial='[]') {
  return {value:initial,getItem(){return this.value;},setItem(_,value){this.value=value;},removeItem(){this.value=null;}};
}
test('played channels survive reload, deduplicate, and keep only the latest twenty',()=>{
  const saved=storage(),recent=new RecentChannels(saved);
  for(let i=0;i<25;i++)recent.remember({name:'News '+i,url:`https://example.com/${i}.m3u8`});
  recent.remember({name:'Renamed',url:'https://example.com/10.m3u8'});
  const loaded=new RecentChannels(saved);
  assert.equal(loaded.items.length,20);
  assert.equal(loaded.items[0].name,'Renamed');
  assert.equal(loaded.items.filter(item=>item.url.endsWith('/10.m3u8')).length,1);
  loaded.clear();assert.deepEqual(new RecentChannels(saved).items,[]);
});
test('signed addresses stay in memory and local paths or embedded credentials are never recorded',()=>{
  const saved=storage(),recent=new RecentChannels(saved);
  recent.remember({url:'https://example.com/live.m3u8?token=secret'});
  recent.remember({url:'https://example.com/live.m3u8#secret'});
  for(const url of ['/uploads/private.mp4','file:///home/private.mp4','javascript:alert(1)','https://user:pass@example.com/live'])recent.remember({url});
  assert.equal(recent.items.length,2);
  assert.doesNotMatch(saved.value,/secret|private|pass/);
  assert.equal(new RecentChannels(saved).items.length,0);
});
test('malformed or unavailable browser storage leaves the library usable',()=>{
  assert.deepEqual(new RecentChannels(storage('{broken')).items,[]);
  assert.deepEqual(new RecentChannels(storage('[null,{},42,{"url":"javascript:alert(1)"}]')).items,[]);
  const recent=new RecentChannels({getItem(){throw Error('blocked');},setItem(){throw Error('full');},removeItem(){throw Error('blocked');}});
  recent.remember({url:'https://example.com/live.m3u8'});assert.equal(recent.items.length,1);
  assert.doesNotThrow(()=>recent.clear());assert.equal(recent.items.length,0);
});
