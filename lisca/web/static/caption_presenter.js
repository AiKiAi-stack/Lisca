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
    for (let index = 0; index < tokens.length; index++) {
      const token = tokens[index];
      if (/^[\p{Ps}\p{Pi}]+$/u.test(token)) { opening += token; continue; }
      const previous = result.at(-1);
      const joinsSpelling = previous && !opening && (
        /^[’'ʼ‐‑-]$/u.test(token) && /[\p{Script=Latin}\p{N}]$/u.test(previous)
          && /^[\p{Script=Latin}\p{N}]/u.test(tokens[index + 1] || '')
        || /[\p{Script=Latin}\p{N}][’'ʼ‐‑-]$/u.test(previous)
          && /^[\p{Script=Latin}\p{N}]/u.test(token));
      const quantityUnit = /^(?:[%％‰℃℉]|°[CF]?|kg|mg|km|cm|mm|ms|Hz|kHz|MHz|GHz|KB|MB|GB|TB|g|m|s|h|公斤|千克|克|斤|吨|公里|千米|米|厘米|毫米|升|毫升|小时|分钟|秒|毫秒|年|月|天|度|元|万元|亿元|个|人|组|次)$/u;
      const joinsUnit = previous && !opening && /\p{N}(?:多|余|几)?$/u.test(previous)
        && (quantityUnit.test(token) || /^(?:多|余|几)$/u.test(token) && quantityUnit.test(tokens[index + 1] || ''));
      if (joinsSpelling || joinsUnit) {
        result[result.length - 1] += token;
        continue;
      }
      if (previous && !opening && /^\p{Script=Han}$/u.test(token)
          && /^\p{Script=Han}$/u.test(tokens[index - 1] || '')
          && /^[\p{Ps}\p{Pi}]*\p{Script=Han}$/u.test(previous)) {
        result[result.length - 1] += token;
        continue;
      }
      if (/^[,.;:!?，。！？；：、\p{Pe}\p{Pf}…]+$/u.test(token) && result.length) {
        let gap = '';
        if (/^\s+$/u.test(result[result.length - 1])) gap = result.pop();
        result[result.length - 1] += gap + token;
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
  function completeRange(sourceText, start, end) {
    let offset = 0;
    let safeStart = start, safeEnd = end;
    for (const token of captionTokens(sourceText)) {
      const next = offset + Array.from(token).length;
      const wordEnd = offset + Array.from(token.replace(/[,.;:!?，。！？；：、\p{Pe}\p{Pf}…]+$/u, '')).length;
      if (offset < start && start < wordEnd) safeStart = next;
      if (offset < end && end < wordEnd) safeEnd = offset;
      offset = next;
    }
    return {start:safeStart, end:Math.max(safeStart, safeEnd)};
  }
  function extendReadBoundary(sourceText, words, readText, time) {
    if (!Array.isArray(words) || typeof readText !== 'string' || !Number.isFinite(time)) return time;
    const normalize = text => text.replace(/[^\p{L}\p{N}]/gu, '').toLowerCase();
    let offset = 0;
    const timed = words.filter(word => Array.isArray(word) && Number.isFinite(word[0])
      && Number.isFinite(word[1]) && typeof word[2] === 'string').map(word => {
      const start = offset;
      offset += normalize(word[2]).length;
      return {beg:word[0], end:word[1], start, through:offset};
    }).filter(word => word.through > word.start);
    if (offset !== normalize(sourceText).length
        || normalize(words.map(word => word?.[2] || '').join('')) !== normalize(sourceText)) return time;
    const current = timed.find(word => word.beg <= time && time < word.end);
    if (!current) return time;
    const read = normalize(readText);
    offset = 0;
    for (const token of captionTokens(sourceText)) {
      const unit = normalize(token), start = offset;
      offset += unit.length;
      if (!unit || start > current.start || current.through > offset || !read.endsWith(unit)) continue;
      const last = timed.find(word => word.through === offset);
      if (last && last.end > time && timed.some(word => word.start === start)) return last.end;
    }
    return time;
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
  function wrapTokenRows(tokens, maxWidth, measureText) {
    const lines = [];
    let line = '';
    let pendingSpace = '';
    let tokenStart = -1;
    let tokenEnd = -1;
    let splitToken = false;
    const flush = () => {
      if (line) lines.push({text:line.trimEnd(), tokenStart, tokenEnd, splitToken});
      line = '';
      pendingSpace = '';
      tokenStart = -1;
      tokenEnd = -1;
      splitToken = false;
    };
    for (let index = 0; index < tokens.length; index++) {
      const token = tokens[index];
      if (/^\s+$/u.test(token)) {
        if (line) pendingSpace += token.replace(/[\r\n]+/gu, ' ');
        continue;
      }
      if (measureText(token) > maxWidth) {
        flush();
        const pieces = splitWideToken(token, maxWidth, measureText);
        if (pieces.length > 1) {
          lines.push(...pieces.slice(0, -1).map(text => ({text, tokenStart:index, tokenEnd:index + 1, splitToken:true})));
        }
        line = pieces.at(-1) || '';
        tokenStart = index;
        tokenEnd = index + 1;
        splitToken = pieces.length > 1;
        continue;
      }
      const candidate = line ? line + pendingSpace + token : token.trimStart();
      if (line && measureText(candidate) > maxWidth) {
        flush();
        line = token.trimStart();
        tokenStart = index;
        tokenEnd = index + 1;
      } else {
        line = candidate;
        if (tokenStart < 0) tokenStart = index;
        tokenEnd = index + 1;
      }
      pendingSpace = '';
    }
    flush();
    return lines;
  }
  function wrapTokens(tokens, maxWidth, measureText) {
    return wrapTokenRows(tokens, maxWidth, measureText).map(row => row.text);
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
    const detailedRows = wrapTokenRows(tokens, maxWidth, measureText);
    const pages = Array.from({length:Math.ceil(detailedRows.length / maxLines)}, (_, i) =>
      detailedRows.slice(i * maxLines, (i + 1) * maxLines).map(row => row.text));
    if (pages.length < 2 || pages.at(-1).length === maxLines) return pages;

    const tailPageCount = Math.min(3, pages.length);
    const tailRows = detailedRows.slice((pages.length - tailPageCount) * maxLines);
    if (tailRows.some(row => row.splitToken)) return pages;
    const tailTokens = tokens.slice(tailRows[0].tokenStart, tailRows.at(-1).tokenEnd);
    const preceding = pages.slice(0, -tailPageCount);
    const tailPages = balanceTailPages(tailTokens, maxWidth, measureText, maxLines, tailPageCount);
    if (!tailPages) return pages;
    return [...preceding, ...tailPages];
  }
  function endsClause(text) {
    return /[,，;；:：.!?。！？][”’"'）)】\]]*$/u.test(text.trim())
      && !/\b(?:Mr|Mrs|Ms|Dr|Prof|Sr|Jr|St|vs|etc)\.$/iu.test(text.trim());
  }
  function naturalGroups(tokens, maxWidth, measureText, maxLines, textOf=token=>token) {
    const groups = [];
    let pending = [];
    let clause = [];
    const fits = units => wrapTokens(units.map(textOf), maxWidth, measureText).length <= maxLines;
    const appendClause = () => {
      if (!clause.length) return;
      if (pending.length && !fits([...pending, ...clause])) {
        groups.push(pending);
        pending = [];
      }
      pending.push(...clause);
      clause = [];
    };
    for (const token of tokens) {
      clause.push(token);
      if (endsClause(textOf(token))) appendClause();
    }
    appendClause();
    if (pending.length) groups.push(pending);
    return groups;
  }
  function paginateText(text, {maxWidth, measureText, maxLines=2} = {}) {
    if (typeof measureText !== 'function') throw new TypeError('measureText callback is required');
    maxWidth = Math.max(1, Number(maxWidth) || 1);
    maxLines = Math.max(1, Math.floor(Number(maxLines) || 2));
    const pages = naturalGroups(captionTokens(String(text || '')), maxWidth, measureText, maxLines)
      .flatMap(tokens => balancedTextPages(tokens, maxWidth, measureText, maxLines));
    return pages.map(rows => ({text:rows.join('\n'),
      weight:Math.max(1, rows.reduce((sum, row) => sum + measureText(row), 0))}));
  }
  function paginateTimedWords(sourceText, words, {maxWidth, measureText, maxLines=2, breakTimes=[]} = {}) {
    if (typeof measureText !== 'function') throw new TypeError('measureText callback is required');
    maxWidth = Math.max(1, Number(maxWidth) || 1);
    maxLines = Math.max(1, Math.floor(Number(maxLines) || 2));
    // Provider timestamps can describe characters or spelling fragments. Derive
    // reading units from the sentence before assigning those original times.
    const readingWords = [];
    let tokenOffset = 0;
    let wordIndex = 0;
    for (const token of captionTokens(sourceText)) {
      const tokenStart = tokenOffset;
      tokenOffset += token.length;
      if (/^\s+$/u.test(token)) continue;
      while (wordIndex < words.length && words[wordIndex].rawEnd <= tokenStart) wordIndex++;
      let index = wordIndex;
      const overlaps = [];
      while (index < words.length && words[index].rawStart < tokenOffset) {
        overlaps.push(words[index++]);
      }
      if (!overlaps.length) continue;
      const previous = readingWords.at(-1);
      const unit = {beg:overlaps[0].beg, end:overlaps.at(-1).end,
        rawStart:Math.max(tokenStart, words[0].rawStart),
        rawEnd:Math.min(tokenOffset, words.at(-1).rawEnd)};
      if (previous && previous.rawEnd > overlaps[0].rawStart) {
        previous.end = Math.max(previous.end, unit.end);
        previous.rawEnd = unit.rawEnd;
      } else readingWords.push(unit);
    }
    words = readingWords.flatMap(word => {
      const text = sourceText.slice(word.rawStart, word.rawEnd);
      const rows = splitWideToken(text, maxWidth, measureText);
      if (rows.length <= maxLines) return [word];
      // An indivisible unit that exceeds a page must split at graphemes. Its
      // original interval is apportioned explicitly, rather than replayed.
      const pieces = [];
      let offset = word.rawStart;
      for (let index = 0; index < rows.length; index += maxLines) {
        const rawStart = offset;
        offset += rows.slice(index, index + maxLines).join('').length;
        pieces.push({...word, rawStart, rawEnd:offset, splitUnit:true,
          beg:word.beg + (word.end - word.beg) * (rawStart - word.rawStart) / text.length,
          end:offset === word.rawEnd ? word.end
            : word.beg + (word.end - word.beg) * (offset - word.rawStart) / text.length});
      }
      return pieces;
    });
    const forcedEdges = new Set();
    for (let index = 1; index < words.length; index++) {
      if (breakTimes.some(cut => words[index - 1].end <= cut && cut < words[index].end))
        forcedEdges.add(index);
    }
    const choices = Array(words.length + 1).fill(null);
    choices[words.length] = {score:0};
    for (let start = words.length - 1; start >= 0; start--) {
      const tokens = [];
      for (let end = start + 1; end <= words.length; end++) {
        if (end > start + 1 && forcedEdges.has(end - 1)) break;
        const word = words[end - 1];
        if (end > start + 1) tokens.push(sourceText.slice(words[end - 2].rawEnd, word.rawStart));
        tokens.push(sourceText.slice(word.rawStart, word.rawEnd));
        const rows = wrapTokens(tokens, maxWidth, measureText);
        if (rows.length > maxLines) break;
        const duration = word.end - words[start].beg;
        const shortPenalty = duration + 1e-7 < .6 ? 100000 + (.6 - duration) * 1000 : 0;
        const boundaryPenalty = end < words.length && !forcedEdges.has(end) && !endsClause(tokens.at(-1)) ? 10 : 0;
        const score = 1 + shortPenalty + boundaryPenalty + choices[end].score;
        if (score <= (choices[start]?.score ?? Infinity))
          choices[start] = {score, end, rows};
      }
    }
    const pages = [];
    for (let start = 0; start < words.length;) {
      const {end, rows} = choices[start];
      const splitUnit = words.slice(start, end).some(word => word.splitUnit);
      pages.push({beg:words[start].beg, end:words[end - 1].end,
        text:rows.join('\n'), weight:Math.max(1, rows.reduce((sum, row) => sum + measureText(row), 0)),
        ...(splitUnit ? {splitUnit:true, timing:'estimated'} : {})});
      start = end;
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
  const CaptionLayout = {paginateText, paginateTimedWords, completeRange, extendReadBoundary, createTextMeasurer};
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
