const test = require('node:test');
const assert = require('node:assert/strict');
const {CaptionPlaybackGuard} = require('../tools/webui_static/playback_guard.js');

test('an audio feed watermark alone does not end a degraded live caption fault', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});

  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:2000, time:10, limit:10, ready:true, userPaused:false}), 'degrade');
  assert.equal(guard.tick({now:2100, time:10, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.degraded, true);
});

test('feed progress cannot clear the original two-second hold budget', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});

  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:1900, time:10, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2000, time:10, limit:20, ready:true, userPaused:false}), 'degrade');
});

test('a live fault recovers only after caption coverage catches up and continues advancing', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});

  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:2000, time:10, limit:10, ready:true, userPaused:false}), 'degrade');
  guard.progress(10.2, 2100);
  assert.equal(guard.tick({now:2100, time:10.2, limit:20, ready:true, userPaused:false}), 'none');
  guard.progress(10.6, 2200);
  assert.equal(guard.tick({now:2200, time:10.6, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2800, time:10.6, limit:20, ready:true, userPaused:false}), 'none');
  guard.progress(11.3, 2800);
  assert.equal(guard.tick({now:2800, time:10.9, limit:20, ready:true, userPaused:false}), 'recovered');
  assert.equal(guard.degraded, false);
});

test('a recovered caption fault stays degraded until buffered audio also stays ahead', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});
  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:2000, time:10, limit:10, ready:true, userPaused:false}), 'degrade');
  guard.progress(10.2, 2100);
  assert.equal(guard.tick({now:2100, time:10.2, limit:20, ready:true, userPaused:false}), 'none');
  guard.progress(10.6, 2200);
  assert.equal(guard.tick({now:2200, time:10.6, limit:20, ready:true, userPaused:false}), 'none');
  guard.progress(11.3, 2800);
  assert.equal(guard.tick({now:2800, time:10.9, limit:10.5, ready:true, userPaused:false}), 'none');
  assert.equal(guard.degraded, true);
  assert.equal(guard.tick({now:2801, time:10.9, limit:20, ready:true, userPaused:false}), 'recovered');
});
