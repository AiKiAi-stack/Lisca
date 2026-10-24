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
  guard.captionProgress('sentence-1', 10, 10.4, 2100);
  assert.equal(guard.tick({now:2100, time:10.2, limit:20, ready:true, userPaused:false}), 'none');
  guard.captionProgress('sentence-2', 10.4, 11, 2200);
  assert.equal(guard.tick({now:2200, time:10.3, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2300, time:10.4, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2400, time:10.5, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2500, time:10.6, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2600, time:10.7, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2700, time:10.8, limit:20, ready:true, userPaused:false}), 'recovered');
  assert.equal(guard.degraded, false);
});

test('a recovered caption fault stays degraded until buffered audio also stays ahead', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});
  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:2000, time:10, limit:10, ready:true, userPaused:false}), 'degrade');
  guard.captionProgress('sentence-1', 10, 11, 2100);
  assert.equal(guard.tick({now:2100, time:10.1, limit:10.5, ready:true, userPaused:false}), 'none');
  guard.captionProgress('sentence-2', 11, 12, 2200);
  assert.equal(guard.tick({now:2200, time:10.2, limit:10.5, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2300, time:10.3, limit:10.5, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2400, time:10.4, limit:10.5, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2500, time:10.5, limit:10.5, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2600, time:10.6, limit:10.5, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2700, time:10.7, limit:10.5, ready:true, userPaused:false}), 'none');
  assert.equal(guard.degraded, true);
  assert.equal(guard.tick({now:2800, time:10.8, limit:20, ready:true, userPaused:false}), 'recovered');
});

test('short caption catch-ups charge the same live fault hold budget', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});
  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  guard.captionProgress('sentence-a', 9.5, 10.5, 500);
  assert.equal(guard.tick({now:500, time:10, limit:20, ready:true, userPaused:false}), 'resume');
  assert.equal(guard.tick({now:700, time:10.2, limit:10.2, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:2199, time:10.2, limit:10.2, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2200, time:10.2, limit:10.2, ready:true, userPaused:false}), 'degrade');
  assert.equal(guard.degraded, true);
});

test('a future isolated caption does not cover the current playback time', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});
  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  guard.captionProgress('future', 20, 22, 100);
  assert.equal(guard.tick({now:100, time:10, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.holdAt, 0);
});

test('forward caption ranges can recover without per-second subtitle callbacks', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});
  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:2000, time:10, limit:10, ready:true, userPaused:false}), 'degrade');
  guard.captionProgress('sentence-b', 10, 12, 2100);
  assert.equal(guard.tick({now:2100, time:10.1, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:3900, time:11.8, limit:20, ready:true, userPaused:false}), 'none');
  guard.captionProgress('sentence-c', 12, 13, 4000);
  assert.equal(guard.tick({now:4000, time:11.9, limit:20, ready:true, userPaused:false}), 'recovered');
});

test('one late short caption does not clear a live caption fault', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});
  assert.equal(guard.tick({now:0, time:1.8, limit:1.8, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:2000, time:1.8, limit:1.8, ready:true, userPaused:false}), 'degrade');
  guard.captionProgress('late-sentence', 1.5, 2.5, 2100);
  assert.equal(guard.tick({now:2100, time:1.8, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2200, time:1.9, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2300, time:2, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2400, time:2.1, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2500, time:2.2, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2600, time:2.3, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2700, time:2.4, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2800, time:2.5, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.degraded, true);
});

test('lost timed coverage restarts a live caption fault even while feed stays ahead', () => {
  const guard = new CaptionPlaybackGuard();
  guard.reset({now:0, live:true});
  assert.equal(guard.tick({now:0, time:10, limit:10, ready:true, userPaused:false}), 'hold');
  assert.equal(guard.tick({now:2000, time:10, limit:10, ready:true, userPaused:false}), 'degrade');
  guard.captionProgress('sentence-1', 10, 11, 2100);
  assert.equal(guard.tick({now:2100, time:10.1, limit:20, ready:true, userPaused:false}), 'none');
  guard.captionProgress('sentence-2', 11, 12, 2200);
  assert.equal(guard.tick({now:2200, time:10.2, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2300, time:10.3, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2400, time:10.4, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2500, time:10.5, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2600, time:10.6, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:2700, time:10.7, limit:20, ready:true, userPaused:false}), 'recovered');
  assert.equal(guard.degraded, false);
  assert.equal(guard.tick({now:2800, time:12.3, limit:20, ready:true, userPaused:false}), 'none');
  assert.equal(guard.tick({now:3400, time:12.9, limit:20, ready:true, userPaused:false}), 'degrade');
  assert.equal(guard.degraded, true);
});
