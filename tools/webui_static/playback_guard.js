(function (root) {
  'use strict';
  const COVERAGE_GAP_TOLERANCE = 0.15;
  class CaptionPlaybackGuard {
    constructor() { this.reset({now: 0, live: false}); }
    reset({now, live}) {
      this.live = live;
      this.lastProgress = now;
      this.through = -Infinity;
      this.captionSpans = new Map();
      this.silenceSpans = [];
      this.nextAnonymousSpan = 0;
      this.holdAt = null;
      this.heldForFaultMs = 0;
      this.faultActive = false;
      this.degraded = false;
      this.recoveryCandidate = null;
      this.done = false;
      this.failed = false;
      this.pausedAt = null;
    }
    progress(through, now) {
      this.noteProgress(through, now);
    }
    captionProgress(sentenceId, beg, end, now) {
      if (!Number.isFinite(beg) || !Number.isFinite(end) || end < beg) return false;
      const key = sentenceId == null ? `anonymous:${++this.nextAnonymousSpan}` : `caption:${String(sentenceId)}`;
      this.captionSpans.set(key, {beg, end});
      if (this.captionSpans.size > 500) this.captionSpans.delete(this.captionSpans.keys().next().value);
      this.noteProgress(end, now);
      return true;
    }
    verifiedSilence(from, through, now) {
      if (!Number.isFinite(from) || !Number.isFinite(through) || through < from) return false;
      this.noteProgress(through, now);
      const next = {beg:from, end:through};
      const merged = [];
      for (const span of this.silenceSpans) {
        if (span.end + COVERAGE_GAP_TOLERANCE < next.beg
            || next.end + COVERAGE_GAP_TOLERANCE < span.beg) {
          merged.push(span);
        } else {
          next.beg = Math.min(next.beg, span.beg);
          next.end = Math.max(next.end, span.end);
        }
      }
      merged.push(next);
      this.silenceSpans = merged.sort((a, b) => a.beg - b.beg).slice(-500);
      return true;
    }
    removeCaption(sentenceId) {
      if (sentenceId == null || !this.captionSpans.delete(`caption:${String(sentenceId)}`)) return;
      this.recoveryCandidate = null;
    }
    noteProgress(through, now) {
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
        if (this.sustainedCoverage(time, now) && this.bufferedAhead(time, limit)) {
          this.clearFault();
          return 'recovered';
        }
        return 'none';
      }
      if (this.holdAt != null) {
        const currentHoldMs = Math.max(0, now - this.holdAt);
        if (this.live && this.heldForFaultMs + currentHoldMs >= 2000) {
          this.heldForFaultMs += currentHoldMs;
          this.holdAt = null;
          this.degraded = true;
          this.recoveryCandidate = null;
          return 'degrade';
        }
        if (this.bufferedAhead(time, limit) && this.covers(time)) {
          this.heldForFaultMs += currentHoldMs;
          this.holdAt = null;
          this.recoveryCandidate = null;
          return 'resume';
        }
        return 'none';
      }
      if (!ready || !Number.isFinite(limit)) return 'none';
      if (this.faultActive && this.sustainedCoverage(time, now) && this.bufferedAhead(time, limit)) {
        this.clearFault();
        return 'recovered';
      }
      if (time >= limit - 0.03) {
        if (!this.faultActive) {
          this.faultActive = true;
          this.heldForFaultMs = 0;
        }
        this.holdAt = now;
        this.recoveryCandidate = null;
        return 'hold';
      }
      return 'none';
    }
    bufferedAhead(time, limit) {
      return Number.isFinite(limit) && limit >= time + 1;
    }
    allCoverageSpans() {
      return [...this.captionSpans.values(), ...this.silenceSpans];
    }
    coversRange(from, through) {
      if (!Number.isFinite(from) || !Number.isFinite(through) || through < from) return false;
      if (through - from <= COVERAGE_GAP_TOLERANCE) {
        return this.allCoverageSpans().some(span => span.beg <= from + COVERAGE_GAP_TOLERANCE
          && span.end >= through - COVERAGE_GAP_TOLERANCE);
      }
      const spans = this.allCoverageSpans().filter(span => span.end >= from - COVERAGE_GAP_TOLERANCE
        && span.beg <= through + COVERAGE_GAP_TOLERANCE).sort((a, b) => a.beg - b.beg);
      let coveredThrough = from;
      for (const span of spans) {
        if (span.beg > coveredThrough + COVERAGE_GAP_TOLERANCE) return false;
        coveredThrough = Math.max(coveredThrough, span.end);
        if (coveredThrough >= through - COVERAGE_GAP_TOLERANCE) return true;
      }
      return false;
    }
    covers(time) {
      return this.coversRange(time - COVERAGE_GAP_TOLERANCE, time + COVERAGE_GAP_TOLERANCE);
    }
    sustainedCoverage(time, now) {
      if (!this.covers(time)) {
        this.recoveryCandidate = null;
        return false;
      }
      if (!this.recoveryCandidate) {
        this.recoveryCandidate = {since:now, mediaTime:time, lastMediaTime:time,
          lastMediaAdvanceAt:now};
        return false;
      }
      const candidate = this.recoveryCandidate;
      if (time < candidate.lastMediaTime - COVERAGE_GAP_TOLERANCE
          || !this.coversRange(candidate.mediaTime, time)) {
        this.recoveryCandidate = {since:now, mediaTime:time, lastMediaTime:time,
          lastMediaAdvanceAt:now};
        return false;
      }
      if (time > candidate.lastMediaTime + 0.02) {
        candidate.lastMediaTime = time;
        candidate.lastMediaAdvanceAt = now;
      } else if (now - candidate.lastMediaAdvanceAt > 500) {
        this.recoveryCandidate = {since:now, mediaTime:time, lastMediaTime:time,
          lastMediaAdvanceAt:now};
        return false;
      }
      return now - candidate.since >= 600 && time - candidate.mediaTime >= 0.25
        && now - candidate.lastMediaAdvanceAt <= 500;
    }
    clearFault() {
      this.faultActive = false;
      this.heldForFaultMs = 0;
      this.holdAt = null;
      this.degraded = false;
      this.recoveryCandidate = null;
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {CaptionPlaybackGuard};
  else root.CaptionPlaybackGuard = CaptionPlaybackGuard;
})(typeof globalThis !== 'undefined' ? globalThis : this);
