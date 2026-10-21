/* Shared by the browser and node:test; time is injected for deterministic replay. */
(function (root) {
  'use strict';
  const width = text => Array.from(text).reduce((n, c) => n + (/[^\x00-\xff]/u.test(c) ? 2 : 1), 0);
  function lines(text, columns = 42) {
    const tokens = text.trim().match(/[\u3400-\u9fff\u3000-\u303f\uff00-\uffef]|[^\s\u3400-\u9fff\u3000-\u303f\uff00-\uffef]+\s*/gu) || [];
    const result = []; let line = '';
    for (const token of tokens) {
      for (const piece of width(token) > columns ? Array.from(token) : [token]) {
        if (line && width(line + piece) > columns) { result.push(line.trim()); line = ''; }
        line += piece;
      }
    }
    if (line.trim()) result.push(line.trim());
    return result;
  }
  function pages(text, columns = 42) { const rows = lines(text, columns); const result = []; for (let i=0;i<rows.length;i+=2) result.push(rows.slice(i,i+2).join('\n')); return result; }
  function readingMs(text) {
    const cjk = (text.match(/[\u3400-\u9fff]/gu) || []).length;
    const words = text.replace(/[\u3400-\u9fff]/gu, ' ').trim().split(/\s+/).filter(Boolean).length;
    return Math.max(2400, Math.min(8000, (cjk / 6 + words / 3) * 1000 + 650));
  }
  class CaptionPresenter {
    constructor() { this.columns=42; this.live=true; this.reset(); }
    setColumns(columns) {
      columns=Math.max(12,Math.floor(columns));
      if (columns === this.columns) return;
      this.columns=columns;
      const split=cue=>pages(cue.text,columns).map(text=>({...cue,text,hold:readingMs(text)}));
      this.queue=this.queue.flatMap(split);
      if (this.current) {
        const [first,...rest]=split(this.current);
        this.current=first; this.elapsed=0;
        this.queue.unshift(...rest);
      }
      this.trimBacklog();
    }
    trimBacklog() {
      if (!this.live) return;
      while (this.queue.length > 1 && this.queue.reduce((n,c)=>n+c.hold,0) > 14000) { this.queue.shift(); this.skipped++; }
    }
    reset() { this.queue=[]; this.current=null; this.seen=new Map(); this.elapsed=0; this.lastWall=null; this.skipped=0; }
    enqueue(cue) {
      if (cue.is_final === false || !cue.text?.trim()) return;
      const key = String(cue.sentence_id ?? cue.beg + ':' + cue.text);
      const revision=cue.revision ?? 0;
      if (this.seen.has(key) && this.seen.get(key) >= revision) return;
      const previousIndex=this.queue.findIndex(page=>String(page.sentence_id ?? page.beg + ':' + page.text) === key);
      const active=String(this.current?.sentence_id) === key;
      const revised=this.seen.has(key);
      this.seen.set(key,revision);
      this.queue=this.queue.filter(page=>String(page.sentence_id ?? page.beg + ':' + page.text) !== key);
      if (this.seen.size > 1000) this.seen.delete(this.seen.keys().next().value);
      // Finished old clauses are corrected in history, not replayed after newer speech.
      if (revised && previousIndex < 0 && !active) return;
      const position=active ? 0 : previousIndex < 0 ? this.queue.length : previousIndex;
      this.queue.splice(position,0,...pages(cue.text,this.columns).map(text=>({...cue,text,hold:readingMs(text)})));
      // Keep live captions near the speaker. Skipped pages remain in the transcript.
      this.trimBacklog();
    }
    remove(key, revision=0) {
      key=String(key);
      if ((this.seen.get(key) ?? -1) > revision) return;
      this.seen.set(key,revision);
      this.queue=this.queue.filter(page=>String(page.sentence_id) !== key);
      if (String(this.current?.sentence_id) === key) { this.current=null; this.elapsed=0; }
    }
    tick(mediaTime, wallTime, paused=false) {
      const delta = this.lastWall == null ? 0 : Math.max(0, wallTime - this.lastWall);
      this.lastWall = wallTime;
      if (paused) return this.current;
      if (this.current) this.elapsed += delta;
      if (this.current && this.elapsed >= this.current.hold) { this.current=null; this.elapsed=0; }
      if (!this.current && this.queue.length && (this.queue[0].beg ?? 0) <= mediaTime + 0.15) {
        this.current=this.queue.shift(); this.elapsed=0;
      }
      return this.current;
    }
  }
  const api={CaptionPresenter,pages,lines,readingMs};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else Object.assign(root,api);
})(typeof globalThis !== 'undefined' ? globalThis : this);
