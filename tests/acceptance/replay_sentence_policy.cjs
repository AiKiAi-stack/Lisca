#!/usr/bin/env node
/* Offline first-commit policy candidate. Does not drive ASR or a media player. */
'use strict';
const fs=require('node:fs');
const path=require('node:path');
const {createHash}=require('node:crypto');

const hash=text=>createHash('sha256').update(text).digest('hex');
function distribution(values){
 const sorted=values.slice().sort((a,b)=>a-b);
 if(!sorted.length)return null;
 return {n:sorted.length,min:sorted[0],p50:sorted[Math.ceil(sorted.length*.5)-1],
  p95:sorted[Math.ceil(sorted.length*.95)-1],max:sorted.at(-1)};
}
function stablePrefix(previous,current){
 let common=0;
 while(common<previous.length&&common<current.length&&previous[common]===current[common])common++;
 const segmenter=new Intl.Segmenter(/\p{Script=Han}/u.test(current)?'zh':'en',{granularity:'word'});
 let end=0;
 for(const token of segmenter.segment(current)){
  const finish=token.index+token.segment.length;
  if(finish>common)break;
  if(token.isWordLike||(!/\s/u.test(token.segment)&&end))end=finish;
 }
 return current.slice(0,end).trimEnd();
}
function analyse(events,waitMs,includeText){
 const states=new Map();
 let missingIdentity=0;
 let observationEnd=0;
 const complete=/(?:[。！？.!?])[”’"'）)】\]]*$/u;
 function commit(state,when,reason,text){
  state.result={status:'committed',commit_reason:reason,committed_after_ms:when,
   wait_from_first_draft_ms:when-state.first,text_sha256:hash(text),characters:Array.from(text).length};
  if(includeText)state.result.text=text;
 }
 function deadlines(now,inclusive){
  for(const state of states.values()){
   const deadline=state.first+waitMs;
   if(state.result||!(inclusive?deadline<=now:deadline<now))continue;
   if(state.stable)commit(state,deadline,'timeout',state.stable);
   else state.result={status:'no_stable_at_deadline',deadline_after_ms:deadline};
  }
 }
 // Final events at the exact deadline win over the timer. All events sharing
 // a timestamp are processed together before expiring that timestamp.
 for(let index=0;index<events.length;){
  const when=events[index].observed_after_ms;
  if(when<observationEnd)throw new Error('recording timestamps must be monotonic');
  observationEnd=when;
  deadlines(when,false);
  while(index<events.length&&events[index].observed_after_ms===when){
   const event=events[index++];
   if(event.type!=='subtitle')continue;
   if(!['number','string'].includes(typeof event.sentence_id)){
    missingIdentity++;continue;
   }
   const key=`${typeof event.sentence_id}:${event.sentence_id}`;
   let state=states.get(key);
   if(!state){
    state={id:event.sentence_id,first:when,previous:'',stable:'',result:null};
    states.set(key,state);
   }
   const text=typeof event.text==='string'?event.text:'';
   state.stable=stablePrefix(state.previous,text);
   state.previous=text;
   if(event.is_final===true&&state.firstFinal==null)state.firstFinal=when;
   if(state.result)continue;
   if(event.is_final===true){
    if(text.trim())commit(state,when,'provider_final',text);
    else state.result={status:'withdrawn',withdrawn_after_ms:when};
   }else if(Number.isFinite(waitMs)&&complete.test(state.stable)){
    commit(state,when,'stable_punctuation_signal',state.stable);
   }
  }
  deadlines(when,true);
 }
 const sentences=Array.from(states.values(),state=>({sentence_id:state.id,
  first_draft_after_ms:state.first,first_provider_final_after_ms:state.firstFinal??null,
  ...(state.result||{status:'right_censored',deadline_after_ms:Number.isFinite(waitMs)?state.first+waitMs:null})}));
 const committed=sentences.filter(row=>row.status==='committed');
 const count=field=>Object.fromEntries(Array.from(new Set(sentences.map(row=>row[field]))).map(value=>
  [value,sentences.filter(row=>row[field]===value).length]));
 return {policy:Number.isFinite(waitMs)?`sentence_or_timeout_${waitMs}ms`:'provider_final_only',
  wait_budget_from_first_draft_ms:Number.isFinite(waitMs)?waitMs:null,
  observed_through_ms:observationEnd,missing_identity_event_count:missingIdentity,
  status_counts:count('status'),commit_reason_counts:Object.fromEntries(
   ['provider_final','stable_punctuation_signal','timeout'].map(reason=>
    [reason,committed.filter(row=>row.commit_reason===reason).length])),
  wait_from_first_draft_ms:distribution(committed.map(row=>row.wait_from_first_draft_ms)),sentences};
}
function main(){
 const recordings=[],budgets=[];
 let output,includeText=false;
 const args=process.argv.slice(2);
 for(let i=0;i<args.length;i++){
  if(args[i]==='--recording')recordings.push(args[++i]);
  else if(args[i]==='--wait-ms')budgets.push(Number(args[++i]));
  else if(args[i]==='--output')output=args[++i];
  else if(args[i]==='--include-text')includeText=true;
  else throw new Error(`unknown option: ${args[i]}`);
 }
 if(!recordings.length||!budgets.length||!output||budgets.some(value=>!Number.isFinite(value)||value<=0))
  throw new Error('usage: --recording FILE [--recording FILE] --wait-ms POSITIVE_MS [--wait-ms POSITIVE_MS] --output FILE [--include-text]');
 const result={schema_version:1,scope:'first_commit_per_provider_sentence_only',
  time_origin:'recording observed_after_ms; wait starts at first draft, not source ingress or user click',
  new_asr_calls:0,word_boundary_method:'Intl.Segmenter on the latest text; an estimate, not an independent reference',
  limitations:['No player, acoustic synchronization, coverage or resource acceptance is assessed.',
   'Stable punctuation is a candidate signal; semantic sentence completeness is not assessed.',
   'Only first commits are modelled; later revisions, withdrawals and subsequent timeout chunks require separate validation.',
   'Right-censored observations remain unknown; deadlines are never advanced beyond recorded observation.',
   'Reported wait budgets are experiment parameters, not approved product thresholds.'],recordings:[]};
 for(const filename of recordings){
  const raw=fs.readFileSync(filename);
  const data=JSON.parse(raw);
  if(!Array.isArray(data.events))throw new Error('recording must contain an events array');
  if(data.events.some(event=>event.type==='subtitle'&&!Number.isFinite(event.observed_after_ms)))
   throw new Error('subtitle event has no valid timestamp');
  const events=data.events.filter(event=>Number.isFinite(event.observed_after_ms));
  if(!events.length)throw new Error('recording has no timed events');
  result.recordings.push({recording:path.basename(filename),recording_sha256:hash(raw),
   simulated_recognition:data.simulated_recognition===true,
   policies:[...budgets,Infinity].map(wait=>analyse(events,wait,includeText))});
 }
 fs.writeFileSync(output,JSON.stringify(result,null,2)+'\n');
}
try{main();}catch(error){process.stderr.write(`${error.message}\n`);process.exitCode=1;}
