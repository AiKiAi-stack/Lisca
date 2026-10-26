const {test}=require('node:test');
const assert=require('node:assert/strict');
const {spawnSync}=require('node:child_process');
const {CaptionLayout}=require('../tools/webui_static/caption_presenter.js');

function measure(text){
 return Array.from(text).reduce((sum,char)=>sum+(/\p{Script=Han}/u.test(char)?20:10),0);
}
function words(text){
 return Array.from(text.matchAll(/[\p{Script=Latin}\p{N}]+(?:[-’'][\p{Script=Latin}\p{N}]+)*/gu),match=>match[0].toLowerCase());
}
function withoutSpaces(text){return text.replace(/\s+/gu,'');}
function assertPages(pages,maxWidth){
 assert.ok(pages.length>1);
 for(const page of pages){
  const lines=page.text.split('\n');
  assert.ok(lines.length<=2, page.text);
  for(const line of lines) assert.ok(measure(line)<=maxWidth, line);
 }
}

test('preview pagination preserves English words across mixed-language switching and narrow screens',()=>{
 const english='Researchers tested a durable laboratory filter, recorded pressure readings at several flow rates, and compared clean water samples with untreated controls.';
 const mixed="I think it's amazing; filters feel good for sports, 每天客流量大概在100多组的样子，然后成交率达到25%左右。";
 for(const [text,maxWidth] of [[english,140],[mixed,140],[mixed,76]]){
  const pages=CaptionLayout.paginateText(text,{maxWidth,measureText:measure,maxLines:2});
  assertPages(pages,maxWidth);
  assert.equal(withoutSpaces(pages.map(page=>page.text).join(' ')),withoutSpaces(text));
  assert.deepEqual(words(pages.map(page=>page.text).join(' ')),words(text));
 }
});

test('tail balancing does not count carried soft wraps as one rendered row',()=>{
 const text='Journalists reported on a regional flood response, reviewing public records and speaking with families, first responders, and local officials.';
 const narrowMeasure=value=>Array.from(value).reduce((sum,char)=>sum+(/[il]/u.test(char)?4:8),0);
 const pages=CaptionLayout.paginateText(text,{maxWidth:80,measureText:narrowMeasure,maxLines:2});
 assert.ok(pages.length>1);
 for(const page of pages){
  const rows=page.text.split('\n');
  assert.ok(rows.length<=2,page.text);
  for(const row of rows)assert.ok(narrowMeasure(row)<=80,row);
 }
 assert.equal(withoutSpaces(pages.map(page=>page.text).join(' ')),withoutSpaces(text));
 assert.deepEqual(words(pages.map(page=>page.text).join(' ')),words(text));
});

test('timed pagination keeps complete ASR words and maps pages to their actual word times',()=>{
 const text='The durable filter works well.';
 const spans=[
  {beg:0.0,end:0.2,rawStart:0,rawEnd:3},
  {beg:0.3,end:0.7,rawStart:4,rawEnd:11},
  {beg:0.8,end:1.1,rawStart:12,rawEnd:18},
  {beg:1.2,end:1.4,rawStart:19,rawEnd:24},
  {beg:1.5,end:1.8,rawStart:25,rawEnd:30},
 ];
 const pages=CaptionLayout.paginateTimedWords(text,spans,
  {maxWidth:80,measureText:measure,maxLines:2});
 assertPages(pages,80);
 assert.equal(withoutSpaces(pages.map(page=>page.text).join(' ')),withoutSpaces(text));
 assert.deepEqual(words(pages.map(page=>page.text).join(' ')),words(text));
 assert.equal(pages[0].beg,0);
 assert.equal(pages.at(-1).end,1.8);
 for(let i=0;i<pages.length;i++){
  assert.ok(pages[i].end>pages[i].beg);
  if(i) assert.ok(pages[i].beg>=pages[i-1].beg);
 }
});

test('cold long-caption pagination stays bounded and preserves every word',()=>{
 const modulePath=JSON.stringify(require.resolve('../tools/webui_static/caption_presenter.js'));
 const script=`
  const {CaptionLayout}=require(${modulePath});
  const text=Array(1000).fill('laboratory').join(' ');
  const measure=value=>Array.from(value).length*10;
  const pages=CaptionLayout.paginateText(text,{maxWidth:180,measureText:measure,maxLines:2});
  const actual=pages.flatMap(page=>page.text.split(/\\s+/u).filter(Boolean));
  if(actual.length!==1000||actual.some(word=>word!=='laboratory'))process.exit(2);
  if(pages.some(page=>page.text.split('\\n').length>2||page.text.split('\\n').some(line=>measure(line)>180)))process.exit(3);
 `;
 const child=spawnSync(process.execPath,['-e',script],{encoding:'utf8',timeout:5000});
 assert.equal(child.error?.code,undefined,'1000-word cold pagination must finish within five seconds');
 assert.equal(child.status,0,child.stderr||`child exited ${child.status}`);
});
