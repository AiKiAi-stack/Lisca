(function (root) {
  'use strict';
  const COVERAGE_GAP_TOLERANCE = 0.15;
  const MAX_LIVE_HOLD_MS = 2000; // Bound application-induced live lag across recovered faults.
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
      this.heldForSessionMs = 0;
      this.faultActive = false;
      this.degraded = false;
      this.recoveryCandidate = null;
      this.watchingCoverage = false;
      this.uncoveredSince = null;
      this.done = false;
      this.failed = false;
      this.externalPauseAt = null;
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
    tick({now, time, limit, ready, userPaused, sourceBuffering = false}) {
      if (this.done || this.failed) return 'none';
      if (userPaused || sourceBuffering) {
        if (this.externalPauseAt == null) this.externalPauseAt = now;
        return 'none';
      }
      if (this.externalPauseAt != null) {
        this.lastProgress += now - this.externalPauseAt;
        if (this.holdAt != null) this.holdAt += now - this.externalPauseAt;
        this.recoveryCandidate = null;
        this.externalPauseAt = null;
      }
      if (!ready || !this.live) {
        if (now - this.lastProgress >= 15000) {
          this.failed = true;
          return 'timeout';
        }
      }
      if (this.watchingCoverage && !this.degraded && this.live && ready
          && this.bufferedAhead(time, limit)) {
        if (this.covers(time)) {
          this.uncoveredSince = null;
        } else if (this.uncoveredSince == null) {
          this.uncoveredSince = time;
        } else if (time - this.uncoveredSince >= 0.5) {
          this.degraded = true;
          this.faultActive = true;
          this.heldForFaultMs = 0;
          this.holdAt = null;
          this.recoveryCandidate = null;
          this.watchingCoverage = false;
          this.uncoveredSince = null;
          return 'degrade';
        }
      } else if (this.covers(time)) {
        this.uncoveredSince = null;
      }
      if (this.degraded) {
        if (this.sustainedCoverage(time, now) && this.bufferedAhead(time, limit)) {
          this.clearFault();
          return 'recovered';
        }
        return 'none';
      }
      if (!ready && this.live && Number.isFinite(limit) && this.holdAt == null) {
        if (!this.faultActive) {
          this.faultActive = true;
          this.heldForFaultMs = 0;
        }
        if (this.liveWaitBudgetSpent()) {
          this.degraded = true;
          this.recoveryCandidate = null;
          return 'degrade';
        }
        this.holdAt = now;
        this.recoveryCandidate = null;
        return 'hold';
      }
      if (this.holdAt != null) {
        const currentHoldMs = Math.max(0, now - this.holdAt);
        if (this.live && this.heldForSessionMs + this.heldForFaultMs + currentHoldMs >= MAX_LIVE_HOLD_MS) {
          this.heldForFaultMs += currentHoldMs;
          this.commitFaultWait();
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
      // A healthy live source may have media data before ASR emits `ready`.
      // Keep the same bounded two-second wait in that startup window; local
      // files still wait for their explicit recognition-ready signal.
      if ((!ready && !this.live) || !Number.isFinite(limit)) return 'none';
      if (this.faultActive && this.sustainedCoverage(time, now) && this.bufferedAhead(time, limit)) {
        this.clearFault();
        return 'recovered';
      }
      if (time >= limit - 0.03) {
        if (!this.faultActive) {
          this.faultActive = true;
          this.heldForFaultMs = 0;
        }
        if (this.liveWaitBudgetSpent()) {
          this.degraded = true;
          this.recoveryCandidate = null;
          return 'degrade';
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
      const captions = [...this.captionSpans.entries()].map(([key, span]) => ({...span, key}));
      const silence = this.silenceSpans.map((span, index) => ({...span, key:`silence:${index}`}));
      return [...captions, ...silence];
    }
    coveragePath(time) {
      const spans = this.allCoverageSpans().filter(span => span.end >= time - COVERAGE_GAP_TOLERANCE)
        .sort((a, b) => a.beg - b.beg);
      let through = time;
      let contributors = 0;
      for (const span of spans) {
        if (span.beg > through + COVERAGE_GAP_TOLERANCE) break;
        if (span.end > through + 0.02) {
          through = span.end;
          contributors++;
        }
      }
      return {through, contributors};
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
      const path = this.coveragePath(time);
      if (!this.recoveryCandidate) {
        this.recoveryCandidate = {since:now, mediaTime:time, lastMediaTime:time,
          lastMediaAdvanceAt:now, coverageThrough:path.through,
          forwardCoverage:path.contributors > 1};
        return false;
      }
      const candidate = this.recoveryCandidate;
      if (time < candidate.lastMediaTime - COVERAGE_GAP_TOLERANCE
          || !this.coversRange(candidate.mediaTime, time)) {
        this.recoveryCandidate = {since:now, mediaTime:time, lastMediaTime:time,
          lastMediaAdvanceAt:now, coverageThrough:path.through,
          forwardCoverage:path.contributors > 1};
        return false;
      }
      if (path.through < candidate.coverageThrough - COVERAGE_GAP_TOLERANCE) {
        this.recoveryCandidate = {since:now, mediaTime:time, lastMediaTime:time,
          lastMediaAdvanceAt:now, coverageThrough:path.through,
          forwardCoverage:path.contributors > 1};
        return false;
      }
      if (path.through > candidate.coverageThrough + 0.02) {
        candidate.coverageThrough = path.through;
        candidate.forwardCoverage = true;
      }
      if (time > candidate.lastMediaTime + 0.02) {
        candidate.lastMediaTime = time;
        candidate.lastMediaAdvanceAt = now;
      } else if (now - candidate.lastMediaAdvanceAt > 500) {
        this.recoveryCandidate = {since:now, mediaTime:time, lastMediaTime:time,
          lastMediaAdvanceAt:now};
        return false;
      }
      return candidate.forwardCoverage && now - candidate.since >= 600 && time - candidate.mediaTime >= 0.25
        && now - candidate.lastMediaAdvanceAt <= 500;
    }
    clearFault() {
      this.commitFaultWait();
      this.faultActive = false;
      this.heldForFaultMs = 0;
      this.holdAt = null;
      this.degraded = false;
      this.recoveryCandidate = null;
      this.watchingCoverage = this.live;
      this.uncoveredSince = null;
    }
    liveWaitBudgetSpent() {
      return this.heldForSessionMs + this.heldForFaultMs >= MAX_LIVE_HOLD_MS;
    }
    commitFaultWait() {
      this.heldForSessionMs += this.heldForFaultMs;
      this.heldForFaultMs = 0;
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = {CaptionPlaybackGuard};
  else root.CaptionPlaybackGuard = CaptionPlaybackGuard;
})(typeof globalThis !== 'undefined' ? globalThis : this);
