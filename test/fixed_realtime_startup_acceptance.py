"""PRD 7.3 fixed real-time startup collector and offline analyzer.

serve is a loopback-only WAV origin paced in 100 ms PCM chunks. collect starts
one real WebUI session and requires explicit acknowledgement. analyze reads
saved artifacts only; it never contacts the WebUI or recognition provider.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit
import wave

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "logs" / "fixed-realtime-startup"
DEFAULT_ORIGIN = "http://127.0.0.1:8765"
PCM_RATE = 16000
PCM_BYTES_PER_SECOND = PCM_RATE * 2
CHUNK_SECONDS = 0.1
EXPECTED_RUNS = 20
APP_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
LABEL_RE = re.compile(r"(?P<lang>zh|en)-startup-(?P<n>\d{2})\Z")
CASE_RE = re.compile(r"(?:zh|en)-startup-[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SAMPLES = {
    "zh": {
        "source": ROOT / "uploads/acceptance-media/zh-single-speaker-reference.wav",
        "reference": ROOT / "docs/research/original-caption-reference-manifests/zh-reference.json",
        "source_sha256": "d40bb3a49d88cf35f52574650d593573048f89f95aba09b704b2fdbe25811317",
        "end_frame": 259028, "reference_items": 3,
    },
    "en": {
        "source": ROOT / "uploads/acceptance-media/en-single-speaker-reference.wav",
        "reference": ROOT / "docs/research/original-caption-reference-manifests/en-reference.json",
        "source_sha256": "4b7d55148fed309e1d71d941d75e89a64b1068a1473163f5f6a9ac4ab6fd7523",
        "end_frame": 264800, "reference_items": 4,
    },
}


def _requester_role(user_agent, referer):
    # WebUI's /video proxy uses the same DEFAULT_UA but supplies Referer;
    # StreamSource's app request uses that UA without a Referer.
    if user_agent == APP_UA:
        return "webui_browser_media_proxy" if referer else "application_stream_source"
    return "browser_or_other"


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path, value):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    return destination


def _append_jsonl(path, value, lock):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode()
    with lock:
        fd = os.open(destination, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(fd, payload)
        finally:
            os.close(fd)


def prepare_sample(language, output_dir=DEFAULT_OUTPUT):
    spec = SAMPLES[language]
    if not spec["source"].is_file():
        raise FileNotFoundError(f"fixed source missing: {spec['source']}")
    source_sha = _sha256(spec["source"])
    if source_sha != spec["source_sha256"]:
        raise ValueError(f"fixed {language} source hash changed: {source_sha}")
    reference = json.loads(spec["reference"].read_text(encoding="utf-8"))
    if reference.get("asset_sha256") != source_sha:
        raise ValueError("reference manifest does not bind to the fixed source")
    with spec["source"].open("rb") as stream:
        reader = wave.open(stream, "rb")
        channels, width, rate, frames = (reader.getnchannels(), reader.getsampwidth(),
                                         reader.getframerate(), reader.getnframes())
        if (channels, width, rate) != (1, 2, PCM_RATE):
            raise ValueError("fixed source must be mono 16 kHz 16-bit PCM WAV")
        if spec["end_frame"] > frames:
            raise ValueError("fixed prefix exceeds source duration")
        pcm = reader.readframes(spec["end_frame"])
        reader.close()
    if len(pcm) != spec["end_frame"] * width:
        raise ValueError("incomplete PCM extraction")
    clips_dir = Path(output_dir) / "samples"
    clips_dir.mkdir(parents=True, exist_ok=True)
    clip_path = clips_dir / f"{language}-fixed-prefix.wav"
    if clip_path.exists():
        with wave.open(str(clip_path), "rb") as reader:
            existing_format = (reader.getnchannels(), reader.getsampwidth(),
                               reader.getframerate(), reader.getnframes())
            expected_format = (channels, width, rate, spec["end_frame"])
            if existing_format != expected_format:
                raise ValueError(
                    f"existing clip WAV format differs from fixed sample: "
                    f"expected {expected_format}, got {existing_format}")
            existing = reader.readframes(reader.getnframes())
        if existing != pcm:
            raise ValueError(f"existing clip differs from fixed source interval: {clip_path}")
    else:
        with wave.Wave_write(str(clip_path)) as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(width)
            writer.setframerate(rate)
            writer.writeframes(pcm)
    manifest = {
        "schema_version": 1, "language": language,
        "source_path": str(spec["source"].relative_to(ROOT)),
        "source_sha256": source_sha, "source_frames": frames,
        "source_duration_seconds": frames / rate,
        "clip_path": str(clip_path), "clip_sha256": _sha256(clip_path),
        "clip_frames": spec["end_frame"],
        "clip_duration_seconds": spec["end_frame"] / rate,
        "source_interval_seconds": {"start": 0.0, "end": spec["end_frame"] / rate},
        "reference_items_in_prefix": spec["reference_items"],
        "reference_manifest_sha256": _sha256(spec["reference"]),
        "format": {"sample_rate_hz": rate, "channels": channels, "sample_width_bytes": width},
        "delivery": "100 ms PCM chunks paced at source duration; no application file prefetch",
    }
    _write_json(clips_dir / f"{language}-manifest.json", manifest)
    return manifest


def prepare_both(output_dir=DEFAULT_OUTPUT):
    return {language: prepare_sample(language, output_dir) for language in ("zh", "en")}


def _valid_label(label, language=None):
    match = LABEL_RE.fullmatch(str(label))
    return bool(match and 1 <= int(match.group("n")) <= EXPECTED_RUNS
                and (language is None or match.group("lang") == language))


def _wav_data_offset(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("fixed sample is not RIFF/WAVE")
    offset = 12
    while offset + 8 <= len(data):
        name = data[offset:offset + 4]
        size = int.from_bytes(data[offset + 4:offset + 8], "little")
        if name == b"data":
            if offset + 8 + size > len(data):
                raise ValueError("WAV data chunk is truncated")
            return offset + 8
        offset += 8 + size + (size & 1)
    raise ValueError("WAV data chunk not found")


class PacingOrigin(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, samples, log_path, chunk_seconds):
        self.samples, self.log_path = samples, Path(log_path)
        self.chunk_seconds, self.log_lock = chunk_seconds, threading.Lock()
        super().__init__(address, PacingHandler)


class PacingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        return

    def _identity(self):
        parts = urlsplit(self.path)
        language = parts.path.strip("/").removesuffix(".wav")
        labels = parse_qs(parts.query).get("startup_case", [])
        label = labels[0] if len(labels) == 1 and CASE_RE.fullmatch(labels[0]) else None
        if language not in SAMPLES or not label or not label.startswith(f"{language}-startup-"):
            return None
        role = _requester_role(self.headers.get("User-Agent", ""), self.headers.get("Referer"))
        return language, label, role

    def do_HEAD(self):
        identity = self._identity()
        if identity is None:
            self.send_error(404)
            return
        language, label, role = identity
        data = self.server.samples[language]["bytes"]
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self._record({"event": "origin_http_request", "method": "HEAD",
                      "language": language, "case_id": label, "requester_role": role,
                      "referer_present": bool(self.headers.get("Referer")),
                      "request_started_utc": _utc_now(),
                      "request_started_monotonic_ns": time.monotonic_ns(),
                      "pcm_bytes_sent": 0, "complete_response": False})

    def do_GET(self):
        identity = self._identity()
        if identity is None:
            self.send_error(404)
            self.close_connection = True
            return
        language, label, role = identity
        wav_data = self.server.samples[language]["bytes"]
        pcm_offset = self.server.samples[language]["data_offset"]
        pcm = memoryview(wav_data)[pcm_offset:]
        request_ns, request_utc = time.monotonic_ns(), _utc_now()
        first_send_ns = first_write_ns = None
        first_send_utc, sent, status = None, 0, "complete"
        try:
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav_data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(wav_data[:pcm_offset])
            self.wfile.flush()
            chunk_size = max(2, int(PCM_BYTES_PER_SECOND * self.server.chunk_seconds))
            chunk_size -= chunk_size % 2
            for offset in range(0, len(pcm), chunk_size):
                part = pcm[offset:offset + chunk_size]
                if first_send_ns is None:
                    # Conservative source-side bound, before first PCM write.
                    first_send_ns, first_send_utc = time.monotonic_ns(), _utc_now()
                self.wfile.write(part)
                self.wfile.flush()
                sent += len(part)
                if first_write_ns is None:
                    first_write_ns = time.monotonic_ns()
                if offset + len(part) < len(pcm):
                    time.sleep(len(part) / PCM_BYTES_PER_SECOND)
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            status = "client_disconnected"
        finally:
            self.close_connection = True
            self._record({
                "event": "origin_http_request", "method": "GET", "language": language,
                "case_id": label, "requester_role": role,
                "referer_present": bool(self.headers.get("Referer")),
                "request_started_utc": request_utc, "request_started_monotonic_ns": request_ns,
                "origin_first_pcm_write_started_utc": first_send_utc,
                "origin_first_pcm_write_started_monotonic_ns": first_send_ns,
                "first_pcm_write_completed_monotonic_ns": first_write_ns,
                "response_completed_utc": _utc_now(), "response_status": status,
                "pcm_bytes_sent": sent, "expected_pcm_bytes": len(pcm),
                "pcm_audio_seconds_sent": round(sent / PCM_BYTES_PER_SECOND, 3),
                "complete_response": sent == len(pcm),
                "pacing_chunk_seconds": self.server.chunk_seconds,
            })

    def _record(self, row):
        try:
            _append_jsonl(self.server.log_path, row, self.server.log_lock)
        except OSError:
            return


def serve(args):
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("media origin must bind to loopback")
    if not math.isfinite(args.chunk_seconds) or not 0.01 <= args.chunk_seconds <= 0.5:
        raise ValueError("chunk duration must be between 10 and 500 ms")
    manifests = prepare_both(args.output)
    samples = {}
    for language, manifest in manifests.items():
        data = Path(manifest["clip_path"]).read_bytes()
        samples[language] = {"bytes": data, "data_offset": _wav_data_offset(data)}
    log_path = Path(args.output) / "origin-requests.jsonl"
    server = PacingOrigin((args.host, args.port), samples, log_path, args.chunk_seconds)
    print(json.dumps({"origin": f"http://{args.host}:{args.port}",
                      "origin_log": str(log_path), "samples": manifests,
                      "recognition_started": False}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


class _CalibratedPage:
    def __init__(self, page, collector, state):
        self._page, self._collector, self._state = page, collector, state

    def __getattr__(self, name):
        return getattr(self._page, name)

    def evaluate(self, *args, **kwargs):
        result = self._page.evaluate(*args, **kwargs)
        if args and args[0] == self._collector.SAMPLER_START and "clock_calibration" not in self._state:
            try:
                before = time.monotonic_ns()
                value = self._page.evaluate(
                    "() => ({now: performance.now(), start: window.__realAcceptanceSampler?.started ?? null})")
                after = time.monotonic_ns()
                midpoint = (before + after) // 2
                now, start = float(value["now"]), float(value["start"])
                self._state["clock_calibration"] = {
                    "method": "bracketed_same_host_monotonic_to_browser_performance_clock",
                    "sampler_origin_monotonic_ns": round(midpoint - (now - start) * 1e6),
                    "bracket_ms": round((after - before) / 1e6, 3),
                    "uncertainty_ms": math.ceil(((after - before) / 2e6 + 1.0) * 1000) / 1000,
                    "captured_at_utc": _utc_now(),
                }
            except Exception:
                self._state["clock_calibration"] = None
        return result


class _CalibratedBrowser:
    def __init__(self, browser, collector, state):
        self._browser, self._collector, self._state = browser, collector, state

    def __getattr__(self, name):
        return getattr(self._browser, name)

    def new_page(self, *args, **kwargs):
        return _CalibratedPage(self._browser.new_page(*args, **kwargs), self._collector, self._state)


def _process_start_ticks(pid):
    stat = Path(f"/proc/{pid}/stat")
    if not stat.is_file():
        return None
    fields = stat.read_text(encoding="ascii").split()
    return int(fields[21]) if len(fields) > 21 else None


def collect_one(args, *, media_suffix=".wav"):
    if media_suffix not in (".wav", ".m3u8"):
        raise ValueError("unsupported fixed startup media suffix")
    if not args.allow_real_recognition:
        raise ValueError("collect starts real ASR; pass --allow-real-recognition explicitly")
    if not _valid_label(args.label, args.language):
        raise ValueError("label must be {language}-startup-01 through -20")
    if _process_start_ticks(args.service_pid) is None:
        raise ValueError("service PID must be a running local process")
    if args.startup_class == "cold" and not args.cold_first_session_confirmed:
        raise ValueError("cold requires confirmation that this is the first session after a fresh service start")
    if args.startup_class == "cold" and args.label != f"{args.language}-startup-01":
        raise ValueError("the cold case must use startup-01")
    if args.startup_class == "warm" and args.label == f"{args.language}-startup-01":
        raise ValueError("startup-01 is reserved for the cold case")
    parsed = urlsplit(args.origin)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("origin must be HTTP on loopback")
    output = Path(args.output)
    manifest = prepare_sample(args.language, output)
    runs_dir = output / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    sidecar = runs_dir / f"{args.label}.json"
    if sidecar.exists():
        raise ValueError(f"refusing to overwrite existing evidence: {sidecar}")
    stop_file = output / "stop-signals" / f"{args.label}.stop"
    if stop_file.exists():
        raise ValueError(f"refusing to reuse stop signal: {stop_file}")
    previous = [json.loads(path.read_text(encoding="utf-8")) for path in runs_dir.glob("*.json")]
    previous = [run for run in previous if run.get("language") == args.language]
    if len(previous) >= EXPECTED_RUNS:
        raise ValueError("20 attempts already exist; no extra recognition session started")
    if args.startup_class == "cold" and previous:
        raise ValueError("cold must be the first measured session for this language")
    if args.startup_class == "warm":
        cold = next((run for run in previous if run.get("startup_class") == "cold"), None)
        if cold is None or cold.get("service_pid") != args.service_pid:
            raise ValueError("warm starts must follow the cold case on the same process")

    from run_real_browser_acceptance import collect, parse_args
    import run_real_browser_acceptance as collector
    state = {}
    old_launch, old_write_json = collector.launch_browser, collector._write_json

    def calibrated_launch(playwright):
        return _CalibratedBrowser(old_launch(playwright), collector, state)

    def calibrated_write(path, value):
        if isinstance(value, dict) and "timeline_samples" in value:
            value = dict(value)
            value["clock_calibration"] = state.get("clock_calibration")
        return old_write_json(path, value)

    collector.launch_browser, collector._write_json = calibrated_launch, calibrated_write
    run_data = {
        "schema_version": 1, "label": args.label, "language": args.language,
        "startup_class": args.startup_class,
        "media_suffix": media_suffix,
        "input_pipeline": "live_hls" if media_suffix == ".m3u8" else "paced_http_wav",
        "startup_class_evidence": ("operator_confirmed_first_session_after_fresh_service_start"
                                   if args.startup_class == "cold"
                                   else "same_service_process_after_cold_case"),
        "cold_first_session_confirmed": bool(args.cold_first_session_confirmed),
        "service_pid": args.service_pid,
        "service_process_start_ticks": _process_start_ticks(args.service_pid),
        "collector_started_at_utc": _utc_now(),
        "source_sha256": manifest["source_sha256"], "clip_sha256": manifest["clip_sha256"],
        "source_interval_seconds": manifest["source_interval_seconds"],
        "simulated_recognition": False, "recognition_started": True,
    }
    _write_json(sidecar, run_data)
    result_box, exception_box = {}, []

    def run_collector():
        try:
            media_url = f"{args.origin.rstrip('/')}/{args.language}{media_suffix}?startup_case={args.label}"
            collector_args = parse_args(["--media", media_url, "--label", args.label,
                                         "--seconds", str(args.seconds),
                                         "--stop-file", str(stop_file),
                                         "--output", str(output / "collector")])
            result_box["result"] = collect(collector_args)
        except Exception as exc:
            exception_box.append(exc)

    worker = threading.Thread(target=run_collector, name=f"startup-{args.label}", daemon=True)
    try:
        worker.start()
        progress_path = output / "collector" / f"{args.label}.progress.json"
        stop_requested = False
        deadline = time.monotonic() + args.seconds + 25
        while worker.is_alive() and time.monotonic() < deadline:
            if not stop_requested and progress_path.is_file():
                try:
                    progress = json.loads(progress_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    progress = {}
                if progress.get("caption_visible") is True and progress.get("sample_count", 0) >= 3:
                    # Allow the 50 ms sampler to establish the same-text run;
                    # the analyzer independently requires >=100 ms continuity.
                    time.sleep(0.15)
                    if worker.is_alive():
                        stop_file.parent.mkdir(parents=True, exist_ok=True)
                        stop_file.write_text("stop after first visible startup caption\n", encoding="utf-8")
                        stop_requested = True
            worker.join(timeout=0.05)
        if worker.is_alive():
            stop_file.parent.mkdir(parents=True, exist_ok=True)
            stop_file.write_text("collector wall limit reached\n", encoding="utf-8")
            worker.join(timeout=10)
        if worker.is_alive():
            raise TimeoutError("browser collector did not finish after its bounded stop")
        if exception_box:
            raise exception_box[0]
        result = result_box.get("result", {})
        run_data["collector_result"] = result
        run_data["report_path"] = result.get("report_path")
        run_data["recording_path"] = result.get("recording_path")
        run_data["clock_calibration"] = state.get("clock_calibration")
        run_data["early_stop_requested"] = stop_requested
    except Exception as exc:
        run_data["collector_exception_type"] = type(exc).__name__
        raise
    finally:
        collector.launch_browser, collector._write_json = old_launch, old_write_json
        _write_json(sidecar, run_data)
    return run_data


def _read_jsonl(path):
    destination = Path(path)
    if not destination.is_file():
        return []
    rows = []
    for line in destination.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def stable_caption_sample(samples, minimum_ms=100):
    active, first_ms, previous_ms, count = None, None, None, 0
    for sample in samples:
        after = sample.get("observed_after_ms")
        text = str(sample.get("visible_text") or "").strip()
        if (not isinstance(after, (int, float)) or not sample.get("visible") or not text
                or (previous_ms is not None and after - previous_ms > 100)):
            active, first_ms, count = None, None, 0
            previous_ms = after if isinstance(after, (int, float)) else None
            continue
        if text != active:
            active, first_ms, count = text, after, 1
        else:
            count += 1
        if first_ms is not None and count >= 3 and after - first_ms >= minimum_ms:
            return sample
        previous_ms = after
    return None


def _freeze_stats(samples):
    if len(samples) < 2:
        return {"freeze_interval_count": None, "freeze_duration_ms": None,
                "longest_freeze_ms": None}
    intervals = []
    for left, right in zip(samples, samples[1:]):
        a, b = left.get("observed_after_ms"), right.get("observed_after_ms")
        if (not isinstance(a, (int, float)) or not isinstance(b, (int, float))
                or not 0 < b - a <= 100 or left.get("paused") is not False
                or right.get("paused") is not False or left.get("ended") or right.get("ended")):
            continue
        media_a, media_b = left.get("media_time"), right.get("media_time")
        frozen = bool(left.get("buffering") or right.get("buffering"))
        if isinstance(media_a, (int, float)) and isinstance(media_b, (int, float)):
            frozen = frozen or (b - a >= 100 and media_b - media_a <= 0.002)
        if frozen:
            intervals.append((a, b))
    merged = []
    for start, end in intervals:
        if merged and start <= merged[-1][1] + 100:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    lengths = [end - start for start, end in merged]
    return {"freeze_interval_count": len(merged), "freeze_duration_ms": round(sum(lengths), 1),
            "longest_freeze_ms": round(max(lengths, default=0), 1)}


def _percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def _median(values):
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def analyze(args, *, hls_evidence_validated=False):
    output = Path(args.output)
    fixed_sample = prepare_sample(args.language, output)
    runs_dir = output / "runs"
    runs = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(runs_dir.glob("*.json"))]
    runs = [run for run in runs if run.get("language") == args.language]
    origin_rows = _read_jsonl(args.origin_log or output / "origin-requests.jsonl")
    decode_rows = _read_jsonl(args.decoded_log or output / "decoded-arrivals.jsonl")
    cases = []
    for run in runs:
        label = run.get("label")
        report_path, recording_path = run.get("report_path"), run.get("recording_path")
        report = json.loads(Path(report_path).read_text(encoding="utf-8")) if report_path and Path(report_path).is_file() else {}
        recording = json.loads(Path(recording_path).read_text(encoding="utf-8")) if recording_path and Path(recording_path).is_file() else {}
        origins = [row for row in origin_rows if row.get("case_id") == label
                   and row.get("requester_role") == "application_stream_source" and row.get("method") == "GET"]
        decodes = [row for row in decode_rows if row.get("case_id") == label
                   and row.get("event") == "stream_source_pcm_summary"
                   and row.get("first_decoded_pcm_monotonic_ns") is not None]
        calibration = report.get("clock_calibration") or run.get("clock_calibration") or {}
        samples = report.get("timeline_samples") or []
        stable = stable_caption_sample(samples)
        sampler_origin = calibration.get("sampler_origin_monotonic_ns")
        visible_ns = (int(sampler_origin + stable["observed_after_ms"] * 1e6)
                      if stable is not None and isinstance(sampler_origin, (int, float)) else None)
        decode_ns = int(decodes[0]["first_decoded_pcm_monotonic_ns"]) if len(decodes) == 1 else None
        service_pid = run.get("service_pid")
        decode_pid = decodes[0].get("process_id") if len(decodes) == 1 else None
        valid_service_pid = (isinstance(service_pid, int) and not isinstance(service_pid, bool)
                             and service_pid > 0)
        decoder_process_matches_service = bool(
            valid_service_pid and isinstance(decode_pid, int) and not isinstance(decode_pid, bool)
            and decode_pid == service_pid)
        decoded_latency_ms = ((visible_ns - decode_ns) / 1e6
                              if visible_ns is not None and decode_ns is not None else None)
        origin_bound_ms = None
        origin_ns = None
        if len(origins) == 1 and visible_ns is not None:
            origin_ns = origins[0].get("origin_first_audio_body_write_started_monotonic_ns",
                                       origins[0].get("origin_first_pcm_write_started_monotonic_ns"))
            if isinstance(origin_ns, (int, float)):
                origin_bound_ms = (visible_ns - int(origin_ns)) / 1e6
        uncertainty_ms = calibration.get("uncertainty_ms")
        valid_uncertainty = (isinstance(uncertainty_ms, (int, float))
                             and not isinstance(uncertainty_ms, bool)
                             and 0 <= uncertainty_ms <= 50 and math.isfinite(uncertainty_ms))
        # Gate the latest possible visibility time; round upward to keep the bound conservative.
        latency_ms = (math.ceil((origin_bound_ms + uncertainty_ms) * 10) / 10
                      if origin_bound_ms is not None and valid_uncertainty else None)
        events = recording.get("events") or []
        ready = next((event for event in events if event.get("type") == "ready"), None)
        playing_ms = report.get("preparation", {}).get("first_playing_after_ms")
        playing_after_decode = (
            (sampler_origin + playing_ms * 1e6 - decode_ns) / 1e6
            if isinstance(sampler_origin, (int, float)) and isinstance(playing_ms, (int, float))
            and decode_ns is not None else None)
        playback_lags = []
        if len(origins) == 1 and isinstance(sampler_origin, (int, float)):
            origin_ns = origins[0].get("origin_first_audio_body_write_started_monotonic_ns",
                                       origins[0].get("origin_first_pcm_write_started_monotonic_ns"))
            if isinstance(origin_ns, (int, float)):
                for item in samples:
                    elapsed, media_time = item.get("observed_after_ms"), item.get("media_time")
                    if item.get("paused") is False and isinstance(elapsed, (int, float)) and isinstance(media_time, (int, float)):
                        now_ns = sampler_origin + elapsed * 1e6
                        live_pos = min(fixed_sample["clip_duration_seconds"],
                                       max(0, (now_ns - origin_ns) / 1e9))
                        playback_lags.append(live_pos - media_time)
        sampling, errors = report.get("sampling") or {}, report.get("errors") or []
        operator_stop = report.get("operator_stop") or {}
        controlled_stop = (report.get("recording_status") == "cancelled"
                           and operator_stop.get("trigger") == "operator_signal"
                           and operator_stop.get("delete_status") == 200
                           and stable is not None)
        origin_decode_correlation = bool(
            len(origins) == 1 and len(decodes) == 1
            and decoder_process_matches_service
            and origins[0].get("referer_present") is False
            and isinstance(origin_ns, (int, float))
            and decode_ns is not None and decode_ns >= int(origin_ns))
        valid = bool(
            run.get("recognition_started") is True and run.get("simulated_recognition") is False
            and run.get("source_sha256") == fixed_sample["source_sha256"]
            and run.get("clip_sha256") == fixed_sample["clip_sha256"]
            and LABEL_RE.fullmatch(str(label or ""))
            and report.get("simulated_recognition") is False
            and (report.get("recording_status") == "completed" or controlled_stop)
            and report.get("session_post_start_count") == 1
            and sampling.get("status") == "valid" and sampling.get("sample_count", 0) >= 2
            and origin_decode_correlation
            and (decodes[0].get("stream_status") == "completed" or controlled_stop)
            and stable is not None and latency_ms is not None and latency_ms >= 0
            and decoded_latency_ms is not None and decoded_latency_ms >= 0
            and calibration.get("method") == "bracketed_same_host_monotonic_to_browser_performance_clock"
            and valid_uncertainty and not errors)
        cases.append({
            "label": label, "startup_class": run.get("startup_class"),
            "service_pid": run.get("service_pid"),
            "decoder_process_matches_service_pid": decoder_process_matches_service,
            "service_process_start_ticks": run.get("service_process_start_ticks"),
            "cold_first_session_confirmed": run.get("cold_first_session_confirmed"),
            "case_valid": valid,
            "hls_live_pipeline_observed": bool(
                run.get("input_pipeline") == "live_hls" and run.get("media_suffix") == ".m3u8"
                and len(decodes) == 1 and decodes[0].get("is_live_hls") is True),
            "recording_status": report.get("recording_status"),
            "sampling_status": sampling.get("status"),
            "clock_calibration_uncertainty_ms": uncertainty_ms,
            "first_ready_after_collection_start_ms": report.get("preparation", {}).get("first_ready_after_ms"),
            "acceptance_latency_upper_bound_from_origin_first_audio_body_write_ms": latency_ms,
            "acceptance_latency_upper_bound_from_origin_first_PCM_write_ms": (
                latency_ms if not (len(decodes) == 1 and decodes[0].get("is_live_hls") is True) else None),
            "decoded_PCM_yield_to_visible_ms_diagnostic": round(decoded_latency_ms, 1) if decoded_latency_ms is not None else None,
            "application_arrival_boundary": (
                "origin pre-write timestamp for the unique correlated app audio-body observation; actual app receipt is later or equal"
                if origin_decode_correlation else "unobserved or uncorrelated"),
            "first_playing_after_first_decoded_pcm_ms": round(playing_after_decode, 1) if playing_after_decode is not None else None,
            "configured_ready_delay_seconds": ready.get("delay") if ready else None,
            "playback_lag_from_origin_clock_median_seconds_proxy": round(_median(playback_lags), 3) if playback_lags else None,
            **_freeze_stats(samples), "errors": errors,
        })
    labels = {case["label"] for case in cases}
    expected_labels = {f"{args.language}-startup-{index:02d}"
                       for index in range(1, EXPECTED_RUNS + 1)}
    unique = len(labels) == len(cases) and labels == expected_labels
    cold = [case for case in cases if case.get("startup_class") == "cold"]
    warm = [case for case in cases if case.get("startup_class") == "warm"]
    classes_ok = (len(cold) == 1 and len(warm) == EXPECTED_RUNS - 1
                  and cold[0].get("label") == f"{args.language}-startup-01"
                  and cold[0].get("cold_first_session_confirmed") is True
                  and cold[0].get("service_pid") is not None
                  and cold[0].get("service_process_start_ticks") is not None
                  and all(case.get("service_pid") == cold[0].get("service_pid")
                          and case.get("service_process_start_ticks")
                          == cold[0].get("service_process_start_ticks")
                          and case.get("service_process_start_ticks") is not None for case in warm))
    valid_latency = [case["acceptance_latency_upper_bound_from_origin_first_audio_body_write_ms"]
                     for case in cases if case.get("case_valid")]
    p95 = _percentile(valid_latency, .95)
    all_valid = len(cases) == EXPECTED_RUNS and unique and all(case.get("case_valid") for case in cases)
    live_pipeline = bool(cases) and all(case["hls_live_pipeline_observed"] for case in cases)
    metrics = all(case.get("configured_ready_delay_seconds") is not None
                  and case.get("first_ready_after_collection_start_ms") is not None
                  and case.get("first_playing_after_first_decoded_pcm_ms") is not None
                  and case.get("playback_lag_from_origin_clock_median_seconds_proxy") is not None
                  and case.get("freeze_interval_count") is not None for case in cases)
    blockers = []
    if not all_valid:
        blockers.append("requires exactly 20 valid non-simulated independent starts")
    if not live_pipeline or not hls_evidence_validated:
        blockers.append("requires observed HLS live pipeline; paced HTTP WAV is diagnostic only")
    if not classes_ok:
        blockers.append("requires one confirmed cold and 19 same-process warm starts")
    if not metrics:
        blockers.append("application playback delay and freeze metrics are required for every run")
    if p95 is None or p95 > 3000:
        blockers.append("P95 must be at most 3000 ms")
    result = {
        "schema_version": 1, "language": args.language, "created_at_utc": _utc_now(),
        "requirement": "PRD 7.3 fixed real-time startup", "expected_starts": EXPECTED_RUNS,
        "attempted_starts": len(cases), "cold_starts": len(cold), "warm_starts": len(warm),
        "cold_warm_classification": "operator-declared; cold means first session after fresh service start",
        "valid_correlated_upper_bound_starts": sum(bool(case.get("case_valid")) for case in cases),
        "latency_ms": {"p50": round(_median(valid_latency), 1) if valid_latency else None,
                       "p95_nearest_rank": round(p95, 1) if p95 is not None else None,
                       "max": round(max(valid_latency), 1) if valid_latency else None,
                       "n": len(valid_latency), "limit": 3000},
        "latency_endpoint": "third consecutive 50 ms browser sample with identical non-placeholder visible text",
        "latency_origin": "origin monotonic timestamp immediately before the correlated first app audio-body write",
        "hls_live_pipeline_observed_for_all_starts": live_pipeline,
        "hls_evidence_validated": bool(hls_evidence_validated),
        "latency_is_conservative_upper_bound": True,
        "raw_wire_arrival_observed": False,
        "application_delay_reported_separately": metrics, "freeze_reported_separately": metrics,
        "origin_clock_lag_is_proxy": True, "origin_bound_used_for_latency_gate": True,
        "sample": fixed_sample, "acceptance_gate_passed": bool(
            all_valid and live_pipeline and hls_evidence_validated
            and classes_ok and metrics and p95 is not None and p95 <= 3000),
        "gate_blockers": blockers, "cases": cases,
    }
    path = output / f"{args.language}-startup-analysis.json"
    _write_json(path, result)
    result["report_path"] = str(path)
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--language", choices=tuple(SAMPLES))
    prep.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    origin = sub.add_parser("serve")
    origin.add_argument("--host", default="127.0.0.1")
    origin.add_argument("--port", type=int, default=8765)
    origin.add_argument("--chunk-seconds", type=float, default=CHUNK_SECONDS)
    origin.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--language", choices=tuple(SAMPLES), required=True)
    collect_parser.add_argument("--label", required=True)
    collect_parser.add_argument("--startup-class", choices=("cold", "warm"), required=True)
    collect_parser.add_argument("--service-pid", type=int, required=True)
    collect_parser.add_argument("--cold-first-session-confirmed", action="store_true")
    collect_parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    collect_parser.add_argument("--seconds", type=float, default=25)
    collect_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    collect_parser.add_argument("--allow-real-recognition", action="store_true",
                                help="required because this starts a real provider session")
    analyzer = sub.add_parser("analyze")
    analyzer.add_argument("--language", choices=tuple(SAMPLES), required=True)
    analyzer.add_argument("--origin-log", type=Path)
    analyzer.add_argument("--decoded-log", type=Path)
    analyzer.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        result = ({args.language: prepare_sample(args.language, args.output)} if args.language
                  else prepare_both(args.output))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        serve(args)
        return 0
    if args.command == "collect":
        if not math.isfinite(args.seconds) or args.seconds <= 0:
            raise ValueError("--seconds must be positive")
        result = collect_one(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        collector_result = result.get("collector_result") or {}
        if collector_result.get("exit_ok"):
            return 0
        report_path = result.get("report_path")
        if report_path and Path(report_path).is_file():
            report = json.loads(Path(report_path).read_text(encoding="utf-8"))
            controlled_stop = (report.get("recording_status") == "cancelled"
                               and (report.get("operator_stop") or {}).get("trigger") == "operator_signal"
                               and (report.get("operator_stop") or {}).get("delete_status") == 200
                               and stable_caption_sample(report.get("timeline_samples") or []) is not None
                               and not report.get("errors"))
            if controlled_stop:
                return 0
        return 1
    result = analyze(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["acceptance_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
