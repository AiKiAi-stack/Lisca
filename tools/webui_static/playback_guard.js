(function (root) {
  'use strict';
  class CaptionPlaybackGuard {
    constructor() { this.reset({now: 0, live: false}); }
    reset({now, live}) {
      this.live = live;
      this.lastProgress = now;
      this.through = -Infinity;
      this.coverageThrough = -Infinity;
      this.coverageAt = -Infinity;
      this.coverageVersion = 0;
      this.holdAt = null;
      this.degraded = false;
      this.recoveryCandidate = null;
      this.done = false;
      this.failed = false;
      this.pausedAt = null;
    }
    progress(through, now) {
      if (Number.isFinite(through) && through > this.through) {
        this.through = through;
        this.lastProgress = now;
      }
      this.coverageProgress(through, now);
    }
    verifiedSilence(through, now) {
      if (Number.isFinite(through) && through > this.through) {
        this.through = through;
        this.lastProgress = now;
      }
      this.coverageProgress(through, now);
    }
    coverageProgress(through, now) {
      if (!Number.isFinite(through) || through <= this.coverageThrough) return;
      this.coverageThrough = through;
      this.coverageAt = now;
      this.coverageVersion++;
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
        this.recoveryCandidate = null;
        this.pausedAt = null;
      }
      if (!ready || !this.live) {
        if (now - this.lastProgress >= 15000) {
          this.failed = true;
          return 'timeout';
        }
      }
      if (this.degraded) {
        if (this.sustainedCoverage(time, now) && Number.isFinite(limit) && limit >= time + 1) {
          this.degraded = false;
          this.holdAt = null;
          this.recoveryCandidate = null;
          return 'recovered';
        }
        return 'none';
      }
      if (this.holdAt != null) {
        if (this.live && now - this.holdAt >= 2000) {
          this.holdAt = null;
          this.degraded = true;
          this.recoveryCandidate = null;
          return 'degrade';
        }
        if (Number.isFinite(limit) && limit >= time + 1 && this.covers(time)) {
          this.holdAt = null;
          return 'resume';
        }
        return 'none';
      }
      if (!ready || !Number.isFinite(limit)) return 'none';
      if (time >= limit - 0.03) { this.holdAt = now; return 'hold'; }
      return 'none';
    }
    covers(time) {
      return this.coverageThrough >= time - 0.25;
    }
    sustainedCoverage(time, now) {
      if (!this.covers(time) || now - this.coverageAt > 1000) {
        this.recoveryCandidate = null;
        return false;
      }
      if (!this.recoveryCandidate) {
        this.recoveryCandidate = {since:now, mediaTime:time, through:this.coverageThrough,
          version:this.coverageVersion, lastProgressAt:this.coverageAt, advances:0};
        return false;
      }
      const candidate = this.recoveryCandidate;
      if (now - candidate.lastProgressAt > 1000) {
        this.recoveryCandidate = {since:now, mediaTime:time, through:this.coverageThrough,
          version:this.coverageVersion, lastProgressAt:this.coverageAt, advances:0};
        return false;
      }
      if (this.coverageVersion > candidate.version && this.coverageThrough > candidate.through) {
        candidate.version = this.coverageVersion;
        candidate.through = this.coverageThrough;
        candidate.lastProgressAt = this.coverageAt;
        candidate.advances++;
      }
      return candidate.advances > 0
        && now - candidate.since >= 600
        && candidate.lastProgressAt - candidate.since >= 500
        && time - candidate.mediaTime >= 0.25;
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {CaptionPlaybackGuard};
  else root.CaptionPlaybackGuard = CaptionPlaybackGuard;
})(typeof globalThis !== 'undefined' ? globalThis : this);
