# Real-browser acceptance collector

`tests/acceptance/run_real_browser_acceptance.py` observes the configured WebUI at
`http://127.0.0.1:8002/` through a real browser. It does not read or change
recognition configuration. Provide only media the operator is authorized to
submit for real recognition.

Example for a bounded live observation:

```sh
python3 tests/acceptance/run_real_browser_acceptance.py \
  --media 'https://example.invalid/live.m3u8' --label zh-live \
  --seconds 300 --output logs/real-browser-acceptance
```

Example for a local recording with optional fixed-reference scoring and cached
subtitle seeks after recognition completes:

```sh
python3 tests/acceptance/run_real_browser_acceptance.py \
  --media /path/to/input.mp4 --label local-zh --timeout 420 \
  --reference /path/to/reference.json --language zh \
  --checkpoints 5.1,23.8 --output logs/real-browser-acceptance
```

Each run writes a sanitized `.recording.json` for the existing scorer, a
`.report.json` with raw timeline and visible-player samples, and (when scoring
was requested and completed) a `.score.json`. Session POSTs are counted without
retaining session identifiers. A live observation cap is represented as an
operator cancellation after the UI Stop action and successful DELETE response;
the collector does not fabricate a WebSocket `end` event. A missing stop
confirmation fails the run. Local completion requires both video EOF and the
normal recognition end event. Sampling gaps over 100 ms are reported as
`measurement_invalid` independently of recognition errors. Browser console and
optional share-endpoint failures are kept as sanitized diagnostics; page errors,
WebSocket errors, player/UI failures, and session-start failures remain fatal.

The report also has `metrics`, one entry per conclusion with `status`
(`observed`, `unknown`, or `failed`), `reason`, and `evidence`. For local files,
EOF, cache jumps, and continuity are reported only when the written
`.recording.json` gives `input_unchanged: true`. A changed file makes those
entries `failed`; an identity that could not be read or confirmed leaves them
`unknown`, each with the specific binding reason. Session count and DELETE
cancellation remain independent request-ledger facts. Remote streams keep
`source_kind: remote_stream` and are not required to have a fixed local-file
hash. EOF itself comes from a direct media-ended sample, and cache jumps require
target tolerance plus a saved screenshot and no new session POST per checkpoint.
Under a confirmed or remote source identity, those direct facts stay `observed`
even when a DOM gap makes the separate `sampling.status` `measurement_invalid`.
Continuity stays `unknown` for invalid sampling or insufficient samples;
readability, sync, and coverage stay `unknown` without their own independent
evidence, and resources stay `unknown` without a separate resource record. Sync is fixed `unknown` here and points to
the independent scorer at `tests/acceptance/score_acoustic_acceptance.py`; this
collector does not assess it. Per-metric statuses do not replace the global
sampling status or `exit_ok`.

For delayed remote playback, add `--observe-completed-playback` to keep sampling
after normal recognition completion until video EOF or `--seconds`. This captures
captions in buffered media that has not played when recognition ends. Reports
retain the recognition end event, `video_eof_observed`, and
`live_seconds_cap_reached` separately. A completed recognition session can still
have capped playback observation; use the actual sampled media range when
assessing its tail. Errors and cancellation keep their existing stop rules.

The artifact explicitly leaves acoustic synchronization and whether a human
heard the source unassessed. Accuracy is marked tested only when a completed
recording was scored against the supplied reference. Checkpoints use cached
captions, save paused browser screenshots with the source input masked, and
report any additional session POST. This collector does not by itself establish
long-duration live stability, source-intrinsic latency, or human-verified
accuracy.
