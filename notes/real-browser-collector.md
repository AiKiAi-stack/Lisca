# Real-browser acceptance collector

`test/run_real_browser_acceptance.py` observes the configured WebUI at
`http://127.0.0.1:8002/` through a real browser. It does not read or change
recognition configuration. Provide only media the operator is authorized to
submit for real recognition.

Example for a bounded live observation:

```sh
python3 test/run_real_browser_acceptance.py \
  --media 'https://example.invalid/live.m3u8' --label zh-live \
  --seconds 300 --output logs/real-browser-acceptance
```

Example for a local recording with optional fixed-reference scoring and cached
subtitle seeks after recognition completes:

```sh
python3 test/run_real_browser_acceptance.py \
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

The artifact explicitly leaves acoustic synchronization and whether a human
heard the source unassessed. Accuracy is marked tested only when a completed
recording was scored against the supplied reference. Checkpoints use cached
captions, save paused browser screenshots with the source input masked, and
report any additional session POST. This collector does not by itself establish
long-duration live stability, source-intrinsic latency, or human-verified
accuracy.
