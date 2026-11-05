"""Prepare an offline human annotation page without recognition or scoring.

Existing clip bytes must match their declared intervals in the full WAV. The
page starts with empty annotations and exports a separate, hash-bound draft.
"""
import argparse
from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import wave


PAGE = r'''<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>独立语音标注草稿</title>
<style>
body{font:17px/1.6 system-ui,sans-serif;max-width:1000px;margin:24px auto;padding:0 16px;background:#f8fafc;color:#172434}
header{position:sticky;top:0;background:#f8fafc;padding:8px 0;z-index:1;border-bottom:1px solid #b6c2cf}
article{background:white;border:1px solid #cbd5e1;border-radius:10px;padding:18px;margin:20px 0}
audio,canvas{width:100%}canvas{height:100px;cursor:crosshair;background:#eef2f7}
button,input,select,textarea{font:inherit}button{cursor:pointer;padding:5px 10px;margin:4px}
textarea{display:block;width:95%;min-height:54px}input[type=number]{width:100px}
.muted{color:#475569;font-size:14px}.error{color:#a31515}label{display:inline-block;margin:4px 8px 4px 0}
</style>
<h1 id="title"></h1>
<p>逐条听原速音频，核对参考文字，并标出实际有声区间。波形只用于定位，静音阈值和原片段边界不作为语音起止答案。停顿可拆成多个区间；不得把听不清的内容记为确认。</p>
<p>这份草稿不计算字幕同步或发布通过。后续还需独立对照屏幕录像中的对应字幕；旧英文浏览器采样无效的限制继续保留。</p>
<header>
<label>标注者 <input id="reviewer" autocomplete="off"></label>
<button id="export">导出标注 JSON</button>
<span id="progress"></span><div id="message" role="status"></div>
</header>
<p class="muted">请在浏览器中打开本页。修改会尝试保存在本浏览器；定期导出 JSON 留存。所有音频均在本机，没有识别请求。</p>
<main></main>
<script type="application/json" id="packet">__PACKET__</script>
<script>
'use strict';
const packet=JSON.parse(document.querySelector('#packet').textContent);
const storageKey='acoustic-review-v1:'+packet.packet_sha256;
const initial=()=>({reviewer:'',items:packet.items.map(()=>({reference_status:'pending',
  corrected_reference:'',notes:'',listened:false,voiced_spans:[],spans_complete:false}))});
let state=initial();
try {const saved=JSON.parse(localStorage.getItem(storageKey));
  if(saved && saved.items?.length===packet.items.length && typeof saved.reviewer==='string') {
    const statuses=['pending','correct','corrected','uncertain'];
    if(saved.items.every((s,i)=>s && statuses.includes(s.reference_status) &&
      typeof s.corrected_reference==='string' && typeof s.notes==='string' &&
      typeof s.listened==='boolean' && typeof s.spans_complete==='boolean' &&
      validSpans(s.voiced_spans,packet.items[i].duration_seconds))) state=saved;
  }
} catch {document.querySelector('#message').textContent='未能读取浏览器草稿，请使用导出文件留存。';}
function validSpans(spans,duration) {
  if(!Array.isArray(spans)) return false;
  let end=0;
  return spans.every(s=>Array.isArray(s)&&s.length===2&&s.every(Number.isFinite)&&
    s[0]>=end&&s[0]<s[1]&&s[1]<=duration&&(end=s[1])>0);
}
function save() {
  try {localStorage.setItem(storageKey,JSON.stringify(state));}
  catch {document.querySelector('#message').textContent='浏览器未保存草稿，请及时导出 JSON。';}
  const refs=state.items.filter(s=>s.listened&&(s.reference_status==='correct'||
    (s.reference_status==='corrected'&&s.corrected_reference.trim()))).length;
  const spans=state.items.filter(s=>s.listened&&s.spans_complete&&s.voiced_spans.length).length;
  document.querySelector('#progress').textContent=`文字已核对 ${refs}/${state.items.length}；有声区间已标注 ${spans}/${state.items.length}（待核验）`;
}
function element(tag,text,parent) {
  const el=document.createElement(tag);if(text!==null)el.textContent=text;
  if(parent)parent.append(el);return el;
}
document.querySelector('#title').textContent=packet.label+'：独立听审与有声区间';
const reviewer=document.querySelector('#reviewer');reviewer.value=state.reviewer;
reviewer.oninput=()=>{state.reviewer=reviewer.value;save();};
packet.items.forEach((item,index)=>{
  const s=state.items[index],box=element('article',null,document.querySelector('main'));
  element('h2',String(index+1).padStart(3,'0')+' · '+item.source_id,box);
  element('p','参考文字（源注释）：'+item.reference,box);
  const audio=element('audio',null,box);audio.controls=true;audio.preload='metadata';audio.src=item.clip_url;
  const canvas=element('canvas',null,box);canvas.width=960;canvas.height=100;
  canvas.setAttribute('aria-label','音频波形，点击定位；时间也可在输入框填写');
  const cursor=element('p','',box);cursor.className='muted';
  function draw(){
    const c=canvas.getContext('2d');c.clearRect(0,0,960,100);
    c.fillStyle='#c6e9d5';for(const [a,b] of s.voiced_spans)c.fillRect(a/item.duration_seconds*960,0,(b-a)/item.duration_seconds*960,100);
    c.strokeStyle='#284f78';c.beginPath();item.peaks.forEach((v,j)=>{
      const x=j*960/item.peaks.length;c.moveTo(x,50-v*47);c.lineTo(x,50+v*47);
    });c.stroke();c.fillStyle='#bd2929';c.fillRect(audio.currentTime/item.duration_seconds*960,0,2,100);
    cursor.textContent=`本段 ${audio.currentTime.toFixed(3)} / ${item.duration_seconds.toFixed(3)} 秒；输入时间 = ${(item.asset_start_seconds+audio.currentTime).toFixed(3)} 秒`;
  }
  audio.ontimeupdate=draw;audio.onloadedmetadata=draw;
  audio.onplay=()=>document.querySelectorAll('audio').forEach(other=>{if(other!==audio)other.pause();});
  canvas.onclick=e=>{audio.currentTime=Math.max(0,Math.min(item.duration_seconds,
    (e.clientX-canvas.getBoundingClientRect().left)/canvas.getBoundingClientRect().width*item.duration_seconds));draw();};
  const heardLabel=element('label',null,box),heard=element('input',null,heardLabel);heard.type='checkbox';heard.checked=s.listened;
  heardLabel.append(' 我已实际试听本条');heard.onchange=()=>{s.listened=heard.checked;save();};
  const select=element('select',null,box);select.setAttribute('aria-label','参考核对结果');
  for(const [v,t] of [['pending','参考未核对'],['correct','参考无误'],['corrected','参考需修正'],['uncertain','听不清／有疑点']]){
    const option=element('option',t,select);option.value=v;
  }select.value=s.reference_status;select.onchange=()=>{s.reference_status=select.value;save();};
  const corrected=element('textarea',null,box);corrected.placeholder='需要修正时，填写实际听到的参考文字；原参考保留不改';
  corrected.value=s.corrected_reference;corrected.oninput=()=>{s.corrected_reference=corrected.value;save();};
  const controls=element('div',null,box);
  const start=element('input',null,controls),end=element('input',null,controls);
  for(const [input,label] of [[start,'有声开始秒'],[end,'有声结束秒']]){
    input.type='number';input.min=0;input.max=item.duration_seconds;input.step='0.001';input.placeholder=label;input.setAttribute('aria-label',label);
  }
  const markStart=element('button','当前位置 → 开始',controls),markEnd=element('button','当前位置 → 结束',controls);
  markStart.onclick=()=>{start.value=audio.currentTime.toFixed(3);};markEnd.onclick=()=>{end.value=audio.currentTime.toFixed(3);};
  const add=element('button','添加有声区间',controls),error=element('span','',controls);error.className='error';
  const list=element('div',null,box),completeLabel=element('label',null,box),complete=element('input',null,completeLabel);
  complete.type='checkbox';complete.checked=s.spans_complete;completeLabel.append(' 已听审并完整标记本条全部有声区间（含短停顿拆分）');
  complete.onchange=()=>{
    if(complete.checked&&(!s.listened||!s.voiced_spans.length)){
      complete.checked=false;error.textContent='请先实际试听并标记有声区间。';return;
    }s.spans_complete=complete.checked;save();
  };
  function renderSpans(){
    list.replaceChildren();s.voiced_spans.forEach(([a,b],i)=>{
      const row=element('div',`${a.toFixed(3)}–${b.toFixed(3)} 秒（输入 ${(item.asset_start_seconds+a).toFixed(3)}–${(item.asset_start_seconds+b).toFixed(3)}）`,list);
      element('button','删除',row).onclick=()=>{s.voiced_spans.splice(i,1);s.spans_complete=false;complete.checked=false;renderSpans();save();draw();};
    });
  }
  add.onclick=()=>{
    const span=[Number(start.value),Number(end.value)],proposed=[...s.voiced_spans,span].sort((a,b)=>a[0]-b[0]);
    if(!start.value||!end.value||!validSpans(proposed,item.duration_seconds)){
      error.textContent='区间须在音频范围内、开始小于结束，且不能重叠。';return;
    }
    s.voiced_spans=proposed;s.spans_complete=false;complete.checked=false;error.textContent='';start.value='';end.value='';renderSpans();save();draw();
  };
  const notes=element('textarea',null,box);notes.placeholder='否定、数字、指代、串音或其他疑点';notes.value=s.notes;
  notes.oninput=()=>{s.notes=notes.value;save();};renderSpans();draw();
});
function exportDraft(){
  if(!state.reviewer.trim())throw Error('请填写实际标注者。');
  return {schema_version:1,kind:'independent_audio_annotation_draft',
    exported_at:new Date().toISOString(),reviewer:state.reviewer.trim(),
    packet_sha256:packet.packet_sha256,input_sha256:packet.input_sha256,
    review_status_sha256:packet.review_status_sha256,label:packet.label,
    acoustic_sync_assessed:false,release_gate_passed:false,
    items:packet.items.map((item,i)=>{
      const s=state.items[i];return {item:item.item,source_id:item.source_id,
        clip_sha256:item.clip_sha256,reference_sha256:item.reference_sha256,
        source_reference:item.reference,asset_start_seconds:item.asset_start_seconds,
        duration_seconds:item.duration_seconds,...s,
        human_reference_confirmed:s.listened&&s.reference_status==='correct',
        voiced_spans_input_seconds:s.voiced_spans.map(([a,b])=>[item.asset_start_seconds+a,item.asset_start_seconds+b])};
    })};
}
document.querySelector('#export').onclick=()=>{
  try {const blob=new Blob([JSON.stringify(exportDraft(),null,2)],{type:'application/json'});
    const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;
    link.download=packet.label+'-human-audio-draft.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    document.querySelector('#message').textContent='已导出草稿；尚未评估字幕同步。';
  }catch(e){document.querySelector('#message').textContent=e.message;}
};save();
</script></html>'''


def digest(data):
    """Return an immutable content identifier."""
    return hashlib.sha256(data).hexdigest()


def prepare(status_path, media_path, output_path):
    """Verify full input and clips, then render a blank annotation page."""
    raw = status_path.read_bytes()
    status = json.loads(raw)
    if digest(media_path.read_bytes()) != status['input_sha256']:
        raise ValueError('Full input SHA-256 mismatch')
    items = []
    with wave.open(str(media_path), 'rb') as source:
        if source.getsampwidth() != 2 or source.getnchannels() != 1:
            raise ValueError('Review waveform requires mono 16-bit PCM')
        rate = source.getframerate()
        for row in status['items']:
            clip_path = Path(row['clip'])
            if digest(clip_path.read_bytes()) != row['clip_sha256']:
                raise ValueError(f"Clip SHA-256 mismatch: {row['item']}")
            begin = round(row['asset_start_seconds'] * rate)
            end = round(row['asset_end_seconds'] * rate)
            if not 0 <= begin < end <= source.getnframes():
                raise ValueError('Invalid source interval')
            source.setpos(begin)
            with wave.open(str(clip_path), 'rb') as clip:
                pcm = clip.readframes(clip.getnframes())
                if (clip.getframerate(), clip.getsampwidth(), clip.getnchannels()) != (rate, 2, 1):
                    raise ValueError('Clip format differs from full input')
            if pcm != source.readframes(end - begin):
                raise ValueError('Clip PCM does not match source interval')
            samples = array('h', pcm)
            if sys.byteorder != 'little':
                samples.byteswap()
            width = math.ceil(len(samples) / 960)
            peaks = [round(max(abs(v) for v in samples[i:i + width]) / 32768, 4)
                     for i in range(0, len(samples), width)]
            items.append({
                'item': row['item'], 'source_id': row['source_id'],
                'reference': row['reference'],
                'reference_sha256': digest(row['reference'].encode('utf-8')),
                'asset_start_seconds': begin / rate,
                'duration_seconds': len(samples) / rate,
                'clip_sha256': row['clip_sha256'],
                'clip_url': Path(os.path.relpath(clip_path, output_path.parent)).as_posix(),
                'peaks': peaks,
            })
    packet = {'label': status['label'], 'input_sha256': status['input_sha256'],
              'review_status_sha256': digest(raw), 'items': items}
    packet['packet_sha256'] = digest(json.dumps(packet, ensure_ascii=False,
                                              sort_keys=True).encode('utf-8'))
    payload = json.dumps(packet, ensure_ascii=False).replace('<', r'\u003c')
    output_path.write_text(PAGE.replace('__PACKET__', payload), encoding='utf-8')
    return {'items': len(items), 'packet_sha256': packet['packet_sha256'],
            'output': str(output_path), 'real_asr_calls': 0,
            'acoustic_sync_assessed': False}


def main():
    """Build only the requested local annotation artifact."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', type=Path, required=True)
    parser.add_argument('--media', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if output.suffix.lower() != '.html':
        parser.error('--output must be an HTML file')
    if output.exists():
        parser.error('Output already exists; use a new file to preserve prior drafts')
    print(json.dumps(prepare(args.status.resolve(), args.media.resolve(), output),
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
