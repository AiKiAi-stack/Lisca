const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {spawnSync}=require('node:child_process');

const command=path.resolve(__dirname,'../acceptance/replay_sentence_policy.cjs');
function replay(events,waitMs=6000){
 const directory=fs.mkdtempSync(path.join(os.tmpdir(),'lisca-sentence-replay-'));
 try{
  const input=path.join(directory,'recording.json');
  const output=path.join(directory,'result.json');
  fs.writeFileSync(input,JSON.stringify({simulated_recognition:true,events}));
  const run=spawnSync(process.execPath,[command,'--recording',input,'--wait-ms',String(waitMs),
   '--include-text','--output',output],{encoding:'utf8',timeout:5000});
  assert.equal(run.status,0,run.stderr);
  return JSON.parse(fs.readFileSync(output,'utf8'));
 }finally{fs.rmSync(directory,{recursive:true,force:true});}
}

test('a silent callback interval still commits all stable text at the deadline',()=>{
 const result=replay([
  {type:'subtitle',sentence_id:1,text:'We can read the whole stable passage',is_final:false,observed_after_ms:1000},
  {type:'subtitle',sentence_id:1,text:'We can read the whole stable passage and continue',is_final:false,observed_after_ms:2000},
  {type:'buffer',observed_after_ms:9000},
 ]);
 const row=result.recordings[0].policies[0].sentences[0];
 assert.equal(row.commit_reason,'timeout');
 assert.equal(row.committed_after_ms,7000);
 assert.equal(row.text,'We can read the whole stable passage');
 assert.equal(row.wait_from_first_draft_ms,6000);
});

test('a word that grows across the stable prefix stays out of a Chinese timeout commit',()=>{
 const result=replay([
  {type:'subtitle',sentence_id:1,text:'我们正关注未来发',is_final:false,observed_after_ms:1000},
  {type:'subtitle',sentence_id:1,text:'我们正关注未来发展趋势',is_final:false,observed_after_ms:2000},
  {type:'buffer',observed_after_ms:9000},
 ]);
 assert.equal(result.recordings[0].policies[0].sentences[0].text,'我们正关注未来');
});

test('a final result at the exact deadline wins and is committed once',()=>{
 const result=replay([
  {type:'subtitle',sentence_id:1,text:'The report is ready',is_final:false,observed_after_ms:1000},
  {type:'subtitle',sentence_id:1,text:'The report is ready today',is_final:false,observed_after_ms:2000},
  {type:'subtitle',sentence_id:1,text:'The report is ready today.',is_final:true,observed_after_ms:7000},
 ]);
 const policy=result.recordings[0].policies[0];
 assert.equal(policy.sentences.length,1);
 assert.equal(policy.sentences[0].commit_reason,'provider_final');
 assert.equal(policy.sentences[0].text,'The report is ready today.');
 assert.equal(policy.commit_reason_counts.timeout,0);
});

test('a recording ending before the deadline preserves the sentence as unknown',()=>{
 const result=replay([
  {type:'subtitle',sentence_id:1,text:'The report is ready',is_final:false,observed_after_ms:1000},
  {type:'subtitle',sentence_id:1,text:'The report is ready today',is_final:false,observed_after_ms:2000},
  {type:'buffer',observed_after_ms:6000},
 ]);
 assert.equal(result.recordings[0].policies[0].sentences[0].status,'right_censored');
 assert.equal(result.recordings[0].policies[0].wait_from_first_draft_ms,null);
});

test('a deadline with no stable text is reported rather than fabricating a subtitle',()=>{
 const result=replay([
  {type:'subtitle',sentence_id:1,text:'one uncertain draft',is_final:false,observed_after_ms:1000},
  {type:'buffer',observed_after_ms:9000},
 ]);
 const row=result.recordings[0].policies[0].sentences[0];
 assert.equal(row.status,'no_stable_at_deadline');
 assert.equal(row.text,undefined);
});

test('a subtitle without a timestamp cannot be silently removed from the evidence',()=>{
 assert.throws(()=>replay([
  {type:'subtitle',sentence_id:1,text:'The report is ready',is_final:false,observed_after_ms:1000},
  {type:'subtitle',sentence_id:1,text:'The report is ready.',is_final:true},
 ]),/subtitle event has no valid timestamp/);
});

test('a first result that is already final does not invent a zero-duration draft wait',()=>{
 const result=replay([
  {type:'subtitle',sentence_id:1,text:'The report is ready.',is_final:true,observed_after_ms:7000},
 ]);
 const policy=result.recordings[0].policies[0];
 assert.equal(policy.sentences[0].first_draft_after_ms,null);
 assert.equal(policy.sentences[0].wait_from_first_draft_ms,null);
 assert.equal(policy.wait_from_first_draft_ms,null);
 assert.equal(policy.sentences[0].commit_reason,'provider_final');
});

test('a final result wins over punctuation becoming stable at the same timestamp',()=>{
 const result=replay([
  {type:'subtitle',sentence_id:1,text:'The report is ready.',is_final:false,observed_after_ms:1000},
  {type:'subtitle',sentence_id:1,text:'The report is ready. Next',is_final:false,observed_after_ms:7000},
  {type:'subtitle',sentence_id:1,text:'The report was ready.',is_final:true,observed_after_ms:7000},
 ]);
 const row=result.recordings[0].policies[0].sentences[0];
 assert.equal(row.commit_reason,'provider_final');
 assert.equal(row.text,'The report was ready.');
});
