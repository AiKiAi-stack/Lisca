(function (root) {
  'use strict';
  class CaptionPlaybackGuard {
    constructor() { this.reset({now: 0, live: false}); }
    reset({now, live}) {
      this.live = live;
      this.lastProgress = now;
      this.through = -Infinity;
      this.holdAt = null;
      this.degraded = false;
      this.done = false;
      this.failed = false;
      this.pausedAt = null;
    }
    progress(through, now) {
      if (Number.isFinite(through) && through > this.through) {
        this.through = through;
        this.lastProgress = now;
      }
    }
    tick({now, time, limit, ready, userPaused}) {
      if (this.done || this.failed) return 'none';
      if (userPaused) {
        if (this.pausedAt == null) this.pausedAt = now;
        return 'none';
      }
      if (this.pausedAt != null) {
        this.lastProgress += now - this.pausedAt;
        if (this.holdAt != null) this.holdAt += now - this.pausedAt;
        this.pausedAt = null;
      }
      if (!ready || !this.live) {
        if (now - this.lastProgress >= 15000) {
          this.failed = true;
          return 'timeout';
        }
      }
      if (!ready || !Number.isFinite(limit)) return 'none';
      if (this.degraded) {
        if (limit >= time + 1) {
          this.degraded = false;
          this.holdAt = null;
          return 'recovered';
        }
        return 'none';
      }
      if (this.holdAt != null) {
        if (limit >= time + 1) { this.holdAt = null; return 'resume'; }
        if (this.live && now - this.holdAt >= 2000) {
          this.holdAt = null;
          this.degraded = true;
          return 'degrade';
        }
        return 'none';
      }
      if (time >= limit - 0.03) { this.holdAt = now; return 'hold'; }
      return 'none';
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {CaptionPlaybackGuard};
  else root.CaptionPlaybackGuard = CaptionPlaybackGuard;
})(typeof globalThis !== 'undefined' ? globalThis : this);
