# Paced WAV startup diagnostics — not the HLS live gate

This helper prepares one fixed prefix from each accepted five-minute reference
WAV, serves each prefix at real-time pace on a separate loopback media port,
and measures the first stable caption in the existing real browser collector.
It does not modify the provider or the existing collector. The optional WebUI
wrapper observes the first decoded PCM chunk at the decoder boundary, before
the chunk is yielded into the session loop; it does not observe wire arrival.

## Method correction (2026-10-26)

A paced HTTP WAV avoids backend local-file prefetch, but does **not** enter the
WebUI's HLS live branch. The two recorded cold-pilot timings describe ordinary
remote-WAV behavior; they cannot pass or fail PRD 7.3's live-HLS requirement.
The analyzer now requires independently observed native HLS plus the HLS URL
path, matching run metadata, and separately validated HLS provenance. Even 20
fast, otherwise valid WAV cases cannot pass. Keep the 3 s target unchanged.

## Fixed media

| Language | Source interval | Source SHA-256 | Clip SHA-256 | Duration |
| --- | --- | --- | --- | --- |
| zh | [0, 16.18925] s | d40bb3a49d88cf35f52574650d593573048f89f95aba09b704b2fdbe25811317 | 982e27e38c8e13847d8dd6aea33fb385bcd81b0f32a8aac41f08e7f72d3f5e07 | 16.18925 s |
| en | [0, 16.55] s | 4b7d55148fed309e1d71d941d75e89a64b1068a1473163f5f6a9ac4ab6fd7523 | bec9313949843b3d4ecd6978929febd9c6a04a7ec7551d8acc23db0bfd097684 | 16.55 s |

The sources are mono, 16 kHz, 16-bit PCM. The selected end frame follows a
reference utterance boundary in each manifest. The helper checks the full
source hash and reference-manifest binding before writing the clip. Output
clips and evidence go under ignored logs/fixed-realtime-startup/.

## Measurement boundaries

- The origin sends 100 ms PCM chunks on a 100 ms schedule. The app receives an
  HTTP URL, so this follows the remote-stream path instead of the local-file
  two-second prefetch path. The WebUI `/video` proxy and `StreamSource` use the
  same User-Agent. The origin identifies the app GET only when the User-Agent
  matches and `Referer` is absent; it separately labels the WebUI proxy request,
  which has a Referer. Evidence stores only whether a Referer was present.
- startup_timing_probe.py records the monotonic timestamp immediately before
  `StreamSource` yields its first non-empty decoded 16 kHz mono PCM chunk. This
  is a decoder-output timestamp, not wire arrival. It records numeric timings,
  byte counts, the non-secret case label, and no URL, transcript, PCM payload,
  API key, or provider call.
- The collector wrapper calibrates the browser sampler's performance.now()
  origin to the host monotonic clock with a before/after bracket. The measured
  caption endpoint is the third consecutive 50 ms browser sample with the
  same non-placeholder text, at least 100 ms after the first. This is DOM
  visibility, not a WebSocket event time.
- The primary startup latency is a conservative upper bound: stable browser
  visibility minus the origin's monotonic timestamp immediately before its
  first PCM body write for the one exact-UA, no-Referer `StreamSource` GET.
  The actual arrival cannot precede that pre-write timestamp. The same case
  must also have exactly one correlated app GET and one later decoded-PCM
  observation, or it is invalid. This bound can overstate latency, but it
  cannot give a false pass due to hidden AV demux probing or read-ahead. The
  actual wire-arrival timestamp is not observed. Decoded-PCM-yield to visible
  remains a diagnostic, not the gate endpoint.
- The gated upper bound adds the full reported clock-calibration uncertainty
  to the midpoint-mapped visibility time and rounds upward to 0.1 ms. Missing,
  non-finite, negative, or over-50-ms uncertainty invalidates the case. A
  midpoint below 3000 ms cannot pass if its uncertainty-adjusted P95 exceeds
  that limit.
- Each case reports app ready.delay, time to ready, time to first media
  playback, a source-clock playback-lag proxy, and observed freezes separately.
  The PRD gives no numeric threshold for these added-media-delay or freeze
  values; they remain visible alongside startup P95.
- Cold/warm labels are operator-declared. One cold case must be the first
  session after a fresh WebUI process start; 19 warm cases must use that same
  process. The helper checks labels, process ID, process start ticks, and that
  the cold case is first, but the operator assertion is the evidence that the
  cold process had no earlier recognition session.
- Before the product optimization (`666b595`), a full-duration offline probe against the fixed prefixes found baseline
  `av.open`/first-yield times around 3.1 s before any provider call: zh
  `3110.7/3111.8 ms`, en `3105.6/3106.3 ms`. The earlier source-bound probes
  measured origin pre-write to first decoded PCM at `3105.2 ms` (zh) and
  `3104.8 ms` (en). Those decoder settings already exceeded the 3 s limit before
  recognition could emit a caption; no paid 20-run batch was started on them.
- A no-provider, one-variable PyAV experiment on each actual prefix reduced
  `av.open` to about `2.0 s` with either `analyzeduration=100000` or
  `probesize=32768`, separately. For both languages and all three variants,
  the full decoded PCM byte count and SHA-256 matched baseline exactly:
  zh `518056` bytes, `c16e53f43f40e70cca8b2b0500ec9869cf59998edede38b2dd6bf8756420c7ca`;
  en `529600` bytes, `dbe9d66223ecdb09ab84d76d32894cedc094de44e3d5faa9a3af1b0d22ae07a7`.
  Each run made exactly one app GET and zero provider calls. Product commit
  `11ab08e` now applies only `analyzeduration=100000` to HTTP(S) WAV URL paths.
  The generated-WAV regression yields before a gated tail and checks full PCM;
  it passes on PyAV 17.1 and 19.0. Existing HLS/local/other-format behavior is
  retained. This decoder improvement does not prove visible-caption P95.

## Commands

Commands below use the persistent Linux environment at
`/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python`. The repository's
`.venv` is Windows-formatted; `.scratch/linux-venv` was a temporary alias to
the persistent environment and may not exist on a later run.

Prepare the fixed clips and verify the pinned source hashes:

    /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/fixed_realtime_startup_acceptance.py prepare \
      --output logs/fixed-realtime-startup

In a separate terminal, start only the media origin. It binds to loopback port
8765 and does not start recognition:

    /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/fixed_realtime_startup_acceptance.py serve \
      --host 127.0.0.1 --port 8765 --output logs/fixed-realtime-startup

Before any real session, run the bounded source-only probe on both actual fixed
prefixes. It measures PyAV open and first-yield timing against the paced HTTP
origin, sends the PCM to a dummy sink, and makes no provider call:

    /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/probe_realtime_wav_source.py \
      --language zh --output logs/fixed-realtime-startup-preflight
    /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/probe_realtime_wav_source.py \
      --language en --output logs/fixed-realtime-startup-preflight

Use a fresh output directory for each repeat; it refuses to overwrite prior
probe artifacts.

The actual fixed-prefix baseline and the two separate PyAV option experiments
were also run without a provider. To repeat them in a fresh output directory:

    /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/probe_realtime_wav_source.py \
      --language zh --output logs/fixed-realtime-startup-options --compare-open-options
    /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/probe_realtime_wav_source.py \
      --language en --output logs/fixed-realtime-startup-options --compare-open-options

Each command makes three paced requests (counterfactual default-analysis
baseline, `analyzeduration=100000`, then `probesize=32768`), fully decodes and
hashes each clip, and refuses to overwrite evidence. The diagnostic wrapper
first removes the product WAV limit before applying each single option; it
records and checks the actual option sets. It never changes product code or
qualifies the real-time gate. After integration, a fresh Chinese comparison
measured 3109.1 / 2011.2 / 2013.3 ms from the origin bound to first yield,
with identical full PCM and one app GET per variant.

The current ordinary WebUI must remain untouched during other active work.
After the operator chooses to begin the cold batch, start the same WebUI entry
point with the hook installed, using the existing service port:

    STREAM_ASR_STARTUP_PROBE_LOG=logs/fixed-realtime-startup/decoded-arrivals.jsonl \
      /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/run_webui_with_startup_probe.py \
      --host 127.0.0.1 --port 8002

This requires the operator to stop the current 8002 owner first, following
AGENTS.md. If its cloud configuration exists only in process memory, re-enter
it privately through the UI after restart; never put a key in the command line,
environment, source files, or evidence.

Do not run a paid 20-case WAV batch for the live requirement. The existing
one-case Chinese and English cold pilots remain diagnostic evidence only.
A real HLS producer must enter the native and browser HLS paths and separately
validate the generated media, full request log, source pacing, and earliest
app audio-body boundary before it can use the shared statistics.

Analyze each language from saved artifacts:

    /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/fixed_realtime_startup_acceptance.py analyze \
      --language zh --output logs/fixed-realtime-startup
    /home/wbb/.local/share/stream-with-asr/dev-venv/bin/python test/fixed_realtime_startup_acceptance.py analyze \
      --language en --output logs/fixed-realtime-startup

## Evidence status

The initial helper, fixed clips, source probes, and PyAV option experiment were
validated offline. Two later real WAV cold pilots are recorded in the linked
real acceptance log. They do not cover the HLS live pipeline. The shared live
acceptance result remains false unless that pipeline is independently verified and
each language has exactly 20 valid, non-simulated independent starts, the
measured stable-caption P95 is at most 3000 ms, cold/warm evidence is present,
and playback-delay and freeze observations are recorded. Missing captions,
collector errors, retryable WebSocket errors, sampling gaps, missing app-GET
to-decode correlation, or incomplete run sets cannot pass. The integrated WAV
analysis limit reduces decoder startup to about 2 s for the fixed inputs; the
visible-caption deadline includes the subsequent provider and display work.
Actual pilot results and service restarts are recorded separately in the
[real acceptance log](2026-10-24-original-caption-real-acceptance.md); offline
option comparisons are never live ASR acceptance evidence.


## Genuine fixed-input HLS method (2026-10-26)

`test/fixed_hls_startup_acceptance.py` reuses the calibrated collector and
statistics while requiring actual native HLS, browser proxy manifests/segments,
and browser playback. It encodes the same pinned, original-speed WAV prefixes
as H.264/AAC MPEG-TS. AAC is lossy: hashes preserve provenance, not transcript
accuracy. The full playlist and every segment are retained; decoded PCM hashes,
sample counts, initial PTS offsets, and PyAV/libav versions are recorded. Very
short final TS segments must explicitly use the MPEG-TS demuxer; auto-probing
can choose MP2. Every AAC packet's PTS, size, payload hash and duration must
match the full HLS stream, including the tail.

The source clock begins with the application's first manifest GET. A segment
is revealed only after both its playlist and AAC packet end times have elapsed.
The origin delivers its body normally, without claiming uniform byte pacing;
initial segment availability delay is reported separately. No ENDLIST or future
segments appear. Each nested request retains its case ID. A separate audit
requires complete manifests, verified segment bodies, the earliest app body
boundary at segment zero, native-HLS service PID, matching actual service
PyAV/libav versions, and advancing browser media before permitting HLS statistics.

Use a **fresh** private evidence directory, distinct from historical WAV runs:

    PY=/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python
    OUT="$(pwd)/logs/fixed-hls-startup/NEW-BATCH"
    "$PY" test/fixed_hls_startup_acceptance.py prepare --output "$OUT"
    "$PY" test/fixed_hls_startup_acceptance.py serve --output "$OUT"

After inspecting/stopping the sole 8002 owner and starting the regular WebUI
with the optional timing hook above, privately restore its memory-only cloud
configuration. Set `STREAM_ASR_STARTUP_PROBE_LOG` to the new batch's
`decoded-arrivals.jsonl`. Use the same absolute output path in every command;
provenance includes file paths and refuses inconsistent spellings. Collecting
explicitly starts a real provider session:

    "$PY" test/fixed_hls_startup_acceptance.py collect --language zh \
      --label zh-startup-01 --startup-class cold --service-pid ACTUAL_PID \
      --cold-first-session-confirmed --allow-real-recognition \
      --output "$OUT"
    "$PY" test/fixed_hls_startup_acceptance.py analyze --language zh \
      --output "$OUT"

Restart before each language's cold pilot. Warm starts 02–20 must use the same
service process as their language's cold start. One pilot is never a P95
acceptance set. Do not automatically spend on the remaining cases while the
pilot or method fails; preserve failed observations. After measurements, stop
the origin, restore the sole ordinary 8002 service, and check root/assets/API
responses and disabled fixture route. No source-only check starts recognition.


The browser's `/channels` precheck precedes session creation. Before the app
clock exists, its origin response therefore contains only HLS headers, no
segments or EOF. This probe is explicitly logged and can never substitute for
the required post-clock browser manifest, segment bodies, or playback evidence.
The auditor rejects probe-only, future-segment, and post-clock probe records.
Actual Chrome reached the single-HLS fallback and an intercepted session POST
with zero provider calls before the real pilots. The first actual HLS cold
pilots measured 10607.4 ms (Chinese) and 6958.6 ms (English) conservative bounds;
each is one attempt, not a 20-start P95. See the real acceptance log; both miss
the target, so no automatic remaining 38 calls were made.


## Partial-batch evidence reporting

`hls_live_pipeline_observed_for_all_starts` describes the recorded attempts,
independently of whether the required 20 valid cases have been collected.
A single independently validated HLS case reports the observed pipeline while
still failing the count, cold/warm and applicable latency requirements. Missing
native HLS or WAV input still cannot pass. This corrects the earlier misleading
“HLS not observed” blocker for incomplete HLS batches; it does not change the
20-start acceptance gate. Original saved analyses are retained before refreshing
derived reports, and no recognition call is made by re-analysis.
