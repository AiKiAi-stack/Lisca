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

## Browser clock engineering capture

Add `--capture-engineering-sync` to stream the actual audible media element's
Float32 PCM and save periodic caption pixels. It accepts local files
and direct HTTP(S) HLS. Optional `--reference` / `--language` still select the
existing fixed-reference recording and text scoring. An explicit `--output`
directory is required; put long PCM captures on the large evidence disk:

```sh
python3 tests/acceptance/run_real_browser_acceptance.py \
  --media /path/to/input.mp4 --label engineering-local --timeout 2400 \
  --capture-engineering-sync \
  --output /media/wbb/KEKE/LiscaAcceptance/engineering-local
```

Engineering runs reject an occupied label before opening the browser or writing
progress and reports. The owned PCM, ledger, pixel directory, recording, report,
text/engineering scores, progress and checkpoint directory must be absent. A
persistent `<label>.engineering-reservation.json` is created exclusively, with
creation time and process ID, so concurrent or interrupted runs cannot reuse the
label. Choose a fresh label for another attempt. The runner's precreated
`<label>.manifest.json`, `<label>.collector.log` and `<label>.monitor.log` are allowed.

The new report key is `engineering_sync`, with
`measurement_scope: browser_output_timestamp_estimate`. It retains the selected
element (`audioEl` for local playback, `player` for direct HLS), candidate
revision, installation route, audio integrity metadata and `pixel_capture` metadata.
The output files are:

- `<label>.playback.f32le`: raw little endian IEEE Float32, all channels
  interleaved by sample frame. Use the reported sample rate and channel count.
- `<label>.playback.frames.jsonl`: streamed records distinguished by `kind`.
  `pcm_batch` records contain sequence, exact `current_frame` / `frame_count`
  for every processing quantum, byte offset/count and SHA-256. `clock_sample`
  records contain raw output timestamp pairs and call brackets. `context_state`,
  `media_state`, `device_change` and `capture_error` retain interruptions.
  `capture_summary` reconciles the final worklet counters with the disk file.
- `<label>.pixels/pixel-*.png`: actual fixed caption-region observations,
  targeted every 100 ms, including blank states. `pixel_observation` entries in
  the same JSONL contain image hashes, before/after time brackets, viewport and
  region geometry. `pixel_capture.metadata_path` / `metadata_sha256` identify
  that ledger; `ledger_kind` selects these records.

At 48 kHz, 30 minutes requires about 330 MiB per channel. The browser retains at
most four unacknowledged PCM batches; the Python sink writes each batch to disk.
Gaps, overlap, sequence/format changes, truncated payloads, overflow or a missing
tail make the capture `unknown`; missing audio is never padded with zeros.

Available AudioContext `sinkchange` and mediaDevices `devicechange` signals each
start a new clock segment and record a `device_change` mapping barrier with its
`source`. They do not request microphone permission or read device names/IDs.
Installation and capture summaries retain `device_change_observability`: support
for both signals, `status: best_effort` (or `unknown` when unavailable), and
`system_output_route: not_independently_observed`. An absent event does not prove
the system route remained unchanged. Saving removes both device listeners.
Existing evidence files are protected from overwrite.

Sample `j` in a quantum has context time `(current_frame + j) / sample_rate_hz`.
`delivered_after_ms` only audits delivery. Output timestamp performance times
and screenshot observation times share the sampler's browser performance origin.
Use raw `usable` clock samples within the same `clock_segment`; context state
changes and device changes are mapping barriers. Media pauses are recorded
separately while the audio context can continue rendering silence.

The fixed image region is derived from the player, two-line font geometry,
padding and caption bottom position, with an 8 CSS pixel shadow margin. Each
observation checks that the actual caption bounds and margin fit inside it.
Viewport, player or font geometry changes, clipped content and out-of-region
captions produce an explicit capture error. PNGs retain the whole region even
while blank; OCR determines the text actually present. DOM identity is retained
as a diagnostic, and a change during a screenshot is marked
`dom_state_stable: false`. Use the saved image brackets and actual cadence when
bounding visible transitions.

The clock API provides an output-time estimate. Reports retain
`output_clock_error_bound_ms: null`, `physical_output_latency: unknown` and
`acceptance_assessed: false`. [ADR-0011](../adr/0011-browser-clock-engineering-sync.md)
defines this engineering scope. Existing formal acoustic acceptance is assessed
separately. The two earlier capture flags keep their existing MediaRecorder
behavior.

For a zero-ASR bridge self-check, this command opens the existing 8002 origin,
replaces its page with synthetic media and a dummy Start button, and blocks
recognition requests:

```sh
python3 tests/browser/check_engineering_pcm_capture.py \
  --output /media/wbb/KEKE/LiscaAcceptance/pcm-self-check-new
```

The self-check verifies literal stereo Float32 samples, onset presence, frame
and byte continuity, hashes, the final partial batch, media/context pause
records and periodic blank/changed caption PNGs. It does not establish a numerical error
bound for the browser's output timestamp API.
