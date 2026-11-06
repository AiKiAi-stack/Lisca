/* A small, bounded list of played channels. Signed URLs stay in page memory. */
(function (root) {
  'use strict';
  const EXAMPLES = [
    {name:'浙江国际', detail:'中文', url:'https://ali-m-l.cztv.com/channels/lantian/channel10/1080p.m3u8'},
    {name:'河北台 cp1', detail:'中文', url:'https://event.pull.hebtv.com/jishi/cp1.m3u8'},
    {name:'CGTN', detail:'English', url:'https://english-livebkali.cgtn.com/live/encgtn.m3u8'},
  ];
  const STORAGE_NAME = ['lisca', 'recent', 'channels', 'v1'].join('-');
  function channel(value) {
    if (!value || typeof value.url !== 'string' || value.url.length > 4096) return null;
    try {
      const url = new URL(value.url);
      if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password) return null;
      return {url:url.href, name:typeof value.name === 'string' && value.name.trim()
        ? value.name.trim().slice(0, 160) : url.hostname};
    } catch (_) { return null; }
  }
  function persistent(value) {
    const url = new URL(value.url);
    return !url.search && !url.hash;
  }
  class RecentChannels {
    constructor(storage) {
      this.storage = storage;
      this.items = [];
      try {
        const stored = JSON.parse(storage.getItem(STORAGE_NAME) || '[]');
        if (Array.isArray(stored)) {
          for (const value of stored.slice(0, 100)) {
            const item = channel(value);
            if (item && persistent(item) && !this.items.some(entry => entry.url === item.url)) this.items.push(item);
            if (this.items.length === 20) break;
          }
        }
      } catch (_) { /* Private/blocked storage still permits an in-memory list. */ }
    }
    remember(value) {
      const item = channel(value);
      if (!item) return;
      this.items = [item, ...this.items.filter(entry => entry.url !== item.url)].slice(0, 20);
      try { this.storage.setItem(STORAGE_NAME, JSON.stringify(this.items.filter(persistent))); } catch (_) { /* Keep memory usable. */ }
    }
    clear() {
      this.items = [];
      try { this.storage.removeItem(STORAGE_NAME); } catch (_) { /* Keep memory usable. */ }
    }
  }
  const api = {EXAMPLES, RecentChannels};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.LiscaSources = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
