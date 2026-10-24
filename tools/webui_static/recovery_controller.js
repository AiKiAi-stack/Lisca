(function (root) {
  'use strict';
  class RecoveryController {
    constructor({maxAttempts = 2, backoffMs = [1000, 2000], stableMs = 10000,
                 progressGapMs = 2000, setTimeout: schedule = setTimeout,
                 clearTimeout: cancel = clearTimeout} = {}) {
      this.maxAttempts = maxAttempts;
      this.backoffMs = [...backoffMs];
      this.stableMs = stableMs;
      this.progressGapMs = progressGapMs;
      this.scheduleTimer = schedule;
      this.cancelTimer = cancel;
      this.attempts = 0;
      this.pending = null;
      this.cancelled = false;
      this.healthySince = null;
      this.lastProgressAt = null;
      this.lastProgressMediaTime = null;
    }
    _clearPending() {
      if (this.pending) this.cancelTimer(this.pending.timerId);
      this.pending = null;
    }
    begin({preserveAttempts = false} = {}) {
      this._clearPending();
      this.cancelled = false;
      if (!preserveAttempts) this.attempts = 0;
      this.healthySince = null;
      this.lastProgressAt = null;
      this.lastProgressMediaTime = null;
    }
    schedule(kind, retryable, recover) {
      if (this.cancelled) return {status: 'cancelled', attempts: this.attempts,
                                 maxAttempts: this.maxAttempts};
      this.healthySince = null;
      this.lastProgressAt = null;
      if (retryable !== true) return {status: 'manual', attempts: this.attempts,
                                      maxAttempts: this.maxAttempts};
      if (this.pending) {
        if (typeof recover === 'function') this.pending.actions.set(kind, recover);
        return {status: 'waiting', attempt: this.pending.attempt,
                delayMs: this.pending.delayMs, attempts: this.attempts,
                maxAttempts: this.maxAttempts};
      }
      if (this.attempts >= this.maxAttempts) {
        return {status: 'exhausted', attempts: this.attempts,
                maxAttempts: this.maxAttempts};
      }
      this.attempts += 1;
      const attempt = this.attempts;
      const delayMs = this.backoffMs[Math.min(attempt - 1, this.backoffMs.length - 1)];
      const pending = {attempt, delayMs, actions: new Map()};
      if (typeof recover === 'function') pending.actions.set(kind, recover);
      this.pending = pending;
      pending.timerId = this.scheduleTimer(() => {
        if (this.pending !== pending) return;
        this.pending = null;
        if (this.cancelled) return;
        // One retry attempt must cause one source/session recovery, even if
        // media and ASR report the same outage before the backoff expires.
        // Rebuilding the recognition session also recreates its media source;
        // when only media failed, use its lighter in-place recovery callback.
        const actionKind = pending.actions.has('recognition')
          ? 'recognition' : pending.actions.keys().next().value;
        const action = pending.actions.get(actionKind);
        if (typeof action === 'function') action({kind: actionKind,
          kinds: [...pending.actions.keys()], attempt, delayMs,
          attempts: this.attempts, maxAttempts: this.maxAttempts});
      }, delayMs);
      return {status: 'scheduled', attempt, delayMs, attempts: this.attempts,
              maxAttempts: this.maxAttempts};
    }
    markProgress(now, {mediaTime, coverageTime} = {}) {
      if (this.cancelled || this.attempts === 0 || !Number.isFinite(now)
          || !Number.isFinite(mediaTime) || !Number.isFinite(coverageTime)
          || coverageTime < mediaTime - 0.75
          || (this.lastProgressMediaTime != null
            && mediaTime <= this.lastProgressMediaTime + 0.05)) return false;
      if (this.lastProgressAt == null || now - this.lastProgressAt > this.progressGapMs) {
        this.healthySince = now;
      }
      this.lastProgressAt = now;
      this.lastProgressMediaTime = mediaTime;
      if (this.healthySince != null && now - this.healthySince >= this.stableMs) {
        this.attempts = 0;
        this.healthySince = null;
        this.lastProgressAt = null;
        this.lastProgressMediaTime = null;
        return true;
      }
      return false;
    }
    cancel() {
      this._clearPending();
      this.cancelled = true;
      this.healthySince = null;
      this.lastProgressAt = null;
      this.lastProgressMediaTime = null;
    }
  }
  function disableHlsAutomaticRetries(defaultConfig) {
    const policies = {};
    for (const [name, policy] of Object.entries(defaultConfig || {})) {
      if (!name.endsWith('LoadPolicy') || !policy?.default) continue;
      const noRetry = retry => retry == null ? retry : {
        ...retry, maxNumRetry: 0, retryDelayMs: 0, maxRetryDelayMs: 0,
      };
      policies[name] = {...policy, default: {
        ...policy.default,
        timeoutRetry: noRetry(policy.default.timeoutRetry),
        errorRetry: noRetry(policy.default.errorRetry),
      }};
    }
    return policies;
  }
  if (typeof module !== 'undefined' && module.exports)
    module.exports = {RecoveryController, disableHlsAutomaticRetries};
  else {
    root.RecoveryController = RecoveryController;
    root.disableHlsAutomaticRetries = disableHlsAutomaticRetries;
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
