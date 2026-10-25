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
  const segmenters = new Map();
  function captionTokens(text) {
    const locale = /[\p{Script=Hiragana}\p{Script=Katakana}]/u.test(text) ? 'ja'
      : /\p{Script=Hangul}/u.test(text) ? 'ko'
        : /\p{Script=Han}/u.test(text) ? 'zh' : 'en';
    let tokens;
    if (typeof Intl !== 'undefined' && Intl.Segmenter) {
      let segmenter = segmenters.get(locale);
      if (!segmenter) {
        segmenter = new Intl.Segmenter(locale, {granularity:'word'});
        segmenters.set(locale, segmenter);
      }
      tokens = Array.from(segmenter.segment(text), item => item.segment);
    } else {
      tokens = text.match(/\s+|[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]|[\p{L}\p{N}]+(?:[’'ʼ-][\p{L}\p{N}]+)*|[^\s]/gu) || [];
    }
    const result = [];
    let opening = '';
    for (const token of tokens) {
      if (/^[([{（【《「『]+$/u.test(token)) { opening += token; continue; }
      if (/^[,.;:!?，。！？；：、)\]}）】》」』…]+$/u.test(token) && result.length) {
        let gap = '';
        if (/^\s+$/u.test(result[result.length - 1])) gap = result.pop();
        result[result.length - 1] += token;
        if (gap) result.push(gap);
        continue;
      }
      result.push(opening + token);
      opening = '';
    }
    if (opening) result.push(opening);
    return result;
  }
  function graphemes(text) {
    if (typeof Intl !== 'undefined' && Intl.Segmenter)
      return Array.from(new Intl.Segmenter(undefined, {granularity:'grapheme'}).segment(text), item => item.segment);
    return Array.from(text);
  }
  function splitWideToken(token, maxWidth, measureText) {
    const result = [];
    let line = '';
    for (const grapheme of graphemes(token)) {
      if (line && measureText(line + grapheme) > maxWidth) {
        result.push(line);
        line = '';
      }
      line += grapheme;
    }
    if (line) result.push(line);
    return result;
  }
  function wrapTokens(tokens, maxWidth, measureText) {
    const lines = [];
    let line = '';
    let pendingSpace = '';
    const flush = () => {
      if (line) lines.push(line.trimEnd());
      line = '';
      pendingSpace = '';
    };
    for (const token of tokens) {
      if (/^\s+$/u.test(token)) {
        if (line) pendingSpace += token;
        continue;
      }
      if (measureText(token) > maxWidth) {
        flush();
        const pieces = splitWideToken(token, maxWidth, measureText);
        if (pieces.length > 1) lines.push(...pieces.slice(0, -1));
        line = pieces.at(-1) || '';
        continue;
      }
      const candidate = line ? line + pendingSpace + token : token.trimStart();
      if (line && measureText(candidate) > maxWidth) {
        flush();
        line = token.trimStart();
      } else {
        line = candidate;
      }
      pendingSpace = '';
    }
    flush();
    return lines;
  }
  function balanceTailPages(tokens, maxWidth, measureText, maxLines, pageCount) {
    // A partial last page can flash too briefly. Reflow only the final few
    // pages; the bound keeps layout cost proportional to the caption length.
    const maxTailTokens = 64;
    if (tokens.length > maxTailTokens || pageCount < 2) return null;
    const pageWeight = rows => rows.reduce((sum, row) => sum + measureText(row), 0);
    const targetWeight = pageWeight(wrapTokens(tokens, maxWidth, measureText)) / pageCount;
    let best = null;
    const score = groups => {
      const weights = groups.map(pageWeight);
      if (weights.some(weight => weight <= 0)) return Infinity;
      return weights.reduce((sum, weight) => sum + (weight - targetWeight) ** 2, 0);
    };
    const hasText = range => range.some(token => !/^\s+$/u.test(token));

    if (pageCount === 2) {
      for (let cut = 1; cut < tokens.length; cut++) {
        const first = tokens.slice(0, cut);
        if (!hasText(first)) continue;
        const firstRows = wrapTokens(first, maxWidth, measureText);
        if (firstRows.length > maxLines) break;
        const second = tokens.slice(cut);
        if (!hasText(second)) continue;
        const secondRows = wrapTokens(second, maxWidth, measureText);
        if (secondRows.length > maxLines) continue;
        const groups = [firstRows, secondRows];
        const candidateScore = score(groups);
        if (candidateScore < (best?.score ?? Infinity)) best = {groups, score:candidateScore};
      }
    } else if (pageCount === 3) {
      for (let firstCut = 1; firstCut < tokens.length - 1; firstCut++) {
        const first = tokens.slice(0, firstCut);
        if (!hasText(first)) continue;
        const firstRows = wrapTokens(first, maxWidth, measureText);
        if (firstRows.length > maxLines) break;
        for (let secondCut = firstCut + 1; secondCut < tokens.length; secondCut++) {
          const second = tokens.slice(firstCut, secondCut);
          if (!hasText(second)) continue;
          const secondRows = wrapTokens(second, maxWidth, measureText);
          if (secondRows.length > maxLines) break;
          const third = tokens.slice(secondCut);
          if (!hasText(third)) continue;
          const thirdRows = wrapTokens(third, maxWidth, measureText);
          if (thirdRows.length > maxLines) continue;
          const groups = [firstRows, secondRows, thirdRows];
          const candidateScore = score(groups);
          if (candidateScore < (best?.score ?? Infinity)) best = {groups, score:candidateScore};
        }
      }
    }
    return best?.groups || null;
  }
  function balancedTextPages(tokens, maxWidth, measureText, maxLines) {
    const rows = wrapTokens(tokens, maxWidth, measureText);
    const pages = Array.from({length:Math.ceil(rows.length / maxLines)}, (_, i) =>
      rows.slice(i * maxLines, (i + 1) * maxLines));
    if (pages.length < 2 || pages.at(-1).length === maxLines) return pages;

    const tailPageCount = Math.min(3, pages.length);
    const tailText = pages.slice(-tailPageCount).map(page => page.join('\n')).join(' ');
    const tailTokens = captionTokens(tailText);
    const preceding = pages.slice(0, -tailPageCount);
    const tailPages = balanceTailPages(tailTokens, maxWidth, measureText, maxLines, tailPageCount);
    if (!tailPages) return pages;
    return [...preceding, ...tailPages];

    function pageWeightFor(page) { return page.reduce((sum, row) => sum + measureText(row), 0); }
  }
  function paginateText(text, {maxWidth, measureText, maxLines=2} = {}) {
    if (typeof measureText !== 'function') throw new TypeError('measureText callback is required');
    maxWidth = Math.max(1, Number(maxWidth) || 1);
    maxLines = Math.max(1, Math.floor(Number(maxLines) || 2));
    const pages = balancedTextPages(captionTokens(String(text || '')), maxWidth, measureText, maxLines);
    return pages.map(rows => ({text:rows.join('\n'),
      weight:Math.max(1, rows.reduce((sum, row) => sum + measureText(row), 0))}));
  }
  function paginateTimedWords(sourceText, words, {maxWidth, measureText, maxLines=2} = {}) {
    if (typeof measureText !== 'function') throw new TypeError('measureText callback is required');
    maxWidth = Math.max(1, Number(maxWidth) || 1);
    maxLines = Math.max(1, Math.floor(Number(maxLines) || 2));
    const rows = [];
    let line = null;
    const makeLine = units => {
      const text = units.map(unit => unit.text).join('');
      return {text, first:units[0].word, last:units.at(-1).word,
        weight:measureText(text), units};
    };
    const flush = () => {
      if (line) rows.push(makeLine(line));
      line = null;
    };
    for (let i = 0; i < words.length; i++) {
      const word = words[i];
      const text = sourceText.slice(word.rawStart, word.rawEnd);
      const separator = i ? sourceText.slice(words[i - 1].rawEnd, word.rawStart) : '';
      if (measureText(text) > maxWidth) {
        flush();
        for (const piece of splitWideToken(separator.trimStart() + text, maxWidth, measureText))
          rows.push(makeLine([{word, text:piece}]));
        continue;
      }
      const unitText = line ? separator + text : (i ? separator.trimStart() : '') + text;
      const combined = line ? line.map(unit => unit.text).join('') + unitText : unitText;
      if (line && measureText(combined) > maxWidth) {
        flush();
        line = [{word, text:separator.trimStart() + text}];
      } else if (line) {
        line.push({word, text:unitText});
      } else {
        line = [{word, text:unitText}];
      }
    }
    flush();
    if (maxLines === 2 && rows.length >= 3 && rows.length % 2 === 1) {
      const tailRow = rows.at(-1);
      const previousRowIndex = rows.length - 2;
      const previousPageStart = rows.length - 3;
      const previousPageRows = rows.slice(previousPageStart, rows.length - 1);
      if (tailRow.last.end - tailRow.first.beg < .6) {
        const previousUnits = rows[previousRowIndex].units.slice();
        const movedUnits = [];
        while (previousUnits.length > 1) {
          const unit = previousUnits.pop();
          movedUnits.unshift({...unit, text:unit.text.trimStart()});
          const shortenedRow = makeLine(previousUnits);
          const movedRow = makeLine(movedUnits);
          if (movedRow.weight > maxWidth) break;
          const priorRows = [...previousPageRows.slice(0, -1), shortenedRow];
          const priorDuration = priorRows.at(-1).last.end - priorRows[0].first.beg;
          const nextDuration = tailRow.last.end - movedRow.first.beg;
          if (priorDuration >= .6 && nextDuration >= .6) {
            rows[previousRowIndex] = shortenedRow;
            rows.splice(rows.length - 1, 0, movedRow);
            break;
          }
        }
      }
    }
    const pages = [];
    for (let i = 0; i < rows.length; i += maxLines) {
      const pageRows = rows.slice(i, i + maxLines);
      pages.push({beg:pageRows[0].first.beg, end:pageRows.at(-1).last.end,
        text:pageRows.map(row => row.text.trim()).join('\n'),
        weight:Math.max(1, pageRows.reduce((sum, row) => sum + row.weight, 0))});
    }
    return pages;
  }
  const textMeasurers = new Map();
  function createTextMeasurer(font, letterSpacing=0) {
    const key = `${font}|${letterSpacing}`;
    if (textMeasurers.has(key)) return textMeasurers.get(key);
    let context = null;
    if (typeof document !== 'undefined' && typeof document.createElement === 'function') {
      try { context = document.createElement('canvas').getContext('2d'); } catch (_) { context = null; }
    }
    const size = Number.parseFloat(String(font).match(/([\d.]+)px/)?.[1]) || 30;
    const spacing = Number(letterSpacing) || 0;
    const measure = text => {
      if (context) { context.font = font; return context.measureText(text).width + Math.max(0, Array.from(text).length - 1) * spacing; }
      return Array.from(text).reduce((sum, char) => sum + size * (/[^\x00-\xff]/u.test(char) ? 1 : 0.55) + spacing, 0);
    };
    if (textMeasurers.size >= 12) textMeasurers.delete(textMeasurers.keys().next().value);
    textMeasurers.set(key, measure);
    return measure;
  }
  const CaptionLayout = {paginateText, paginateTimedWords, createTextMeasurer};
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
  const api={CaptionPresenter,CaptionLayout,pages,lines,readingMs};
  if (typeof module !== 'undefined' && module.exports) module.exports=api;
  else Object.assign(root,api);
})(typeof globalThis !== 'undefined' ? globalThis : this);
