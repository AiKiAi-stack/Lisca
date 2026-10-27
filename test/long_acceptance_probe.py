"""Opt-in source PCM capture and public DashScope SDK call counters."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import threading
import time
import weakref
import wave
from urllib.parse import urlsplit

CASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
SDK_METHODS = ("start", "send_audio_frame", "stop", "complete")


class ProbeFinalizationError(RuntimeError):
    """Raised when the final run receipt cannot be safely written."""


def _hash(data):
    return hashlib.sha256(data).hexdigest()


def _file_hash(path):
    try:
        digest = hashlib.sha256()
        with Path(path).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()
    except OSError:
        return None


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        n = os.write(fd, view)
        if n <= 0:
            raise OSError("short write")
        view = view[n:]


def _exclusive_json(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        _write_all(fd, (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode())
    finally:
        os.close(fd)


def _seconds(value):
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def load_case_manifest(path):
    """Validate exact input URL/path hashes; never include raw inputs in output."""
    raw = Path(path).read_bytes()
    document = json.loads(raw)
    if not isinstance(document, dict):
        raise ValueError("manifest must have schema_version 1 and nonempty cases")
    cases = document.get("cases")
    if document.get("schema_version") != 1 or not isinstance(cases, list) or not cases:
        raise ValueError("manifest must have schema_version 1 and nonempty cases")
    parsed, seen = [], set()
    for row in cases:
        if not isinstance(row, dict):
            raise ValueError("manifest case must be an object")
        allowed = {"case_id", "generation", "input_identity_kind",
                   "input_identity_sha256", "source_content_sha256"}
        if set(row) - allowed:
            raise ValueError("manifest case has unsupported fields; raw URLs or paths are forbidden")
        label, generation = row.get("case_id"), row.get("generation")
        if not isinstance(label, str) or not CASE_ID.fullmatch(label):
            raise ValueError("case_id must be a safe nonempty label")
        if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
            raise ValueError("generation must be a positive integer")
        if (label, generation) in seen:
            raise ValueError("case_id and generation must be unique")
        seen.add((label, generation))
        kind, identity = row.get("input_identity_kind"), row.get("input_identity_sha256")
        if kind not in ("url", "path"):
            raise ValueError("input_identity_kind must be url or path")
        if not isinstance(identity, str) or not SHA256.fullmatch(identity):
            raise ValueError("input_identity_sha256 must be a lowercase SHA-256")
        content_sha = row.get("source_content_sha256")
        if content_sha is not None and (not isinstance(content_sha, str) or not SHA256.fullmatch(content_sha)):
            raise ValueError("source_content_sha256 must be a lowercase SHA-256")
        parsed.append({
            "case_id": label, "generation": generation,
            "input_kind": kind, "input_identity_sha256": identity,
            "source_content_sha256": content_sha,
        })
    return parsed, _hash(raw)


class _WaveWriter:
    def __init__(self, path):
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        self.file = os.fdopen(fd, "wb")
        self.wav = wave.open(self.file, "wb")
        self.wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))

    def write(self, data):
        self.wav.writeframesraw(data)

    def close(self):
        try:
            self.wav.close()
        finally:
            self.file.close()


class _Capture:
    def __init__(self, probe, case):
        self.probe, self.case = probe, case
        stem = f"{case['case_id']}-g{case['generation']}"
        self.wav_path = probe.pcm_dir / f"{stem}.wav"
        self.log_path = probe.pcm_dir / f"{stem}.jsonl"
        self.lock = threading.RLock()
        self.q = queue.Queue(maxsize=probe.queue_size)
        self.finish_event = threading.Event()
        self.state_changed = threading.Event()
        self.stream_status, self.iter_depth, self.source_closed = "active", 0, False
        self.finalized = False
        self.invalid_reasons = []
        self.seen_samples = self.captured_samples = 0
        self.seen_bytes = self.captured_bytes = 0
        self.chunk_index = 0
        self.first_ns = self.last_ns = None
        self.first_pts = self.last_pts = self.timeline_offset = None
        self.pcm_digest = hashlib.sha256()
        self.sdk_lock = threading.Lock()
        self.sdk_inflight = {name: 0 for name in SDK_METHODS}
        self.complete_callback_expected = False
        self.sdk = {"start_calls": 0, "start_successes": 0,
                    "start_failures": 0,
                    "send_audio_frame_calls": 0, "send_audio_frame_successes": 0,
                    "send_audio_frame_failures": 0, "send_audio_frame_bytes": 0,
                    "stop_calls": 0, "stop_successes": 0, "stop_failures": 0,
                    "complete_calls": 0, "complete_successes": 0,
                    "complete_failures": 0, "first_send_monotonic_ns": None,
                    "last_send_monotonic_ns": None,
                    "complete_method_available": "complete" in probe.sdk_methods,
                    "complete_callbacks": 0,
                    "public_methods_present": list(probe.sdk_methods)}
        try:
            self.log_fd = os.open(self.log_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except OSError:
            self.log_fd = None
            self.invalidate("jsonl_open_failure")
        self.writer_factory = probe.writer_factory
        self.worker = threading.Thread(target=self.write_loop, daemon=True,
                                       name=f"long-pcm-{case['case_id']}")
        try:
            self.worker.start()
        except RuntimeError:
            self.invalidate("writer_thread_start_failure")

    def invalidate(self, reason):
        with self.lock:
            first = reason not in self.invalid_reasons
            if first:
                self.invalid_reasons.append(reason)
        if first:
            if not self.probe.signal_invalid(reason, self.case):
                with self.lock:
                    if "stop_signal_write_failure" not in self.invalid_reasons:
                        self.invalid_reasons.append("stop_signal_write_failure")
        self.state_changed.set()

    def add_chunk(self, chunk, pts, offset):
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            self.invalidate("non_bytes_pcm_chunk")
            return
        data = bytes(chunk)
        if not data:
            return
        now, pts, offset = time.monotonic_ns(), _seconds(pts), _seconds(offset)
        with self.lock:
            if self.finalized:
                return
            sample_start = self.seen_samples
            count = len(data) // 2
            self.seen_samples += count
            self.seen_bytes += len(data)
            self.first_ns = self.first_ns or now
            self.last_ns = now
            if self.first_pts is None and pts is not None:
                self.first_pts = pts
            if pts is not None:
                self.last_pts = pts
            if self.timeline_offset is None and offset is not None:
                self.timeline_offset = offset
            reason = None
            if len(data) % 2:
                reason = "odd_pcm_byte_count"
            elif self.invalid_reasons or self.finish_event.is_set():
                pass
            elif self.seen_bytes > self.probe.max_pcm_bytes:
                reason = "pcm_cap_exceeded"
            else:
                self.chunk_index += 1
                row = {"schema_version": 1, "event": "pcm_chunk",
                       "case_id": self.case["case_id"], "generation": self.case["generation"],
                       "input_identity_sha256": self.case["input_identity_sha256"],
                       "input_identity_kind": self.case["input_kind"],
                       "source_content_sha256": self.case["source_content_sha256"],
                       "chunk_index": self.chunk_index, "sample_start": sample_start,
                       "sample_end": sample_start + count, "observed_monotonic_ns": now,
                       "source_first_pts_sec": pts, "timeline_offset_sec": offset,
                       "pcm_bytes": len(data), "pcm_sha256": _hash(data)}
                try:
                    self.q.put_nowait((data, row))
                except queue.Full:
                    reason = "writer_queue_overflow"
        if reason:
            self.invalidate(reason)

    def set_status(self, status):
        with self.lock:
            if status == "error" or self.stream_status == "active":
                self.stream_status = status
        self.state_changed.set()

    def iterator_enter(self):
        with self.lock:
            self.iter_depth += 1
        self.state_changed.set()

    def iterator_exit(self):
        with self.lock:
            self.iter_depth -= 1
            if self.iter_depth < 0:
                self.iter_depth = 0
                self.invalid_reasons.append("source_iterator_depth_underflow")
            if self.source_closed and self.iter_depth == 0 and self.stream_status == "active":
                self.stream_status = "cancelled"
            should_finish = self.source_closed and self.iter_depth == 0
        self.state_changed.set()
        if should_finish:
            self.probe.maybe_finish(self)

    def note_source_closed(self, internal):
        with self.lock:
            self.source_closed = True
            if not internal and self.iter_depth == 0 and self.stream_status == "active":
                self.stream_status = "cancelled"
        self.state_changed.set()
        self.probe.maybe_finish(self)

    def expect_complete_callback(self):
        with self.sdk_lock:
            self.complete_callback_expected = True
        self.state_changed.set()

    def note_sdk(self, method, args, kwargs, succeeded=None):
        late_activity = False
        with self.lock:
            if self.finalized:
                late_activity = (succeeded is None
                                 and method in ("start", "send_audio_frame"))
            else:
                with self.sdk_lock:
                    if succeeded is None:
                        self.sdk[f"{method}_calls"] += 1
                        self.sdk_inflight[method] += 1
                        if method == "send_audio_frame":
                            data = args[0] if args else kwargs.get("data", b"")
                            try:
                                self.sdk["send_audio_frame_bytes"] += len(data)
                            except TypeError:
                                pass
                            now = time.monotonic_ns()
                            self.sdk["first_send_monotonic_ns"] = (
                                self.sdk["first_send_monotonic_ns"] or now
                            )
                            self.sdk["last_send_monotonic_ns"] = now
                    else:
                        self.sdk_inflight[method] = max(0, self.sdk_inflight[method] - 1)
                        outcome = "successes" if succeeded else "failures"
                        self.sdk[f"{method}_{outcome}"] += 1
        if late_activity:
            self.invalidate("sdk_activity_after_capture_finalize")
        else:
            self.state_changed.set()

    def sdk_snapshot(self):
        with self.sdk_lock:
            result = dict(self.sdk)
            result["inflight_calls"] = dict(self.sdk_inflight)
            result["complete_callback_expected"] = self.complete_callback_expected
            return result

    def note_complete_callback(self):
        with self.sdk_lock:
            if self.finalized:
                return
            self.sdk["complete_callbacks"] += 1
        self.state_changed.set()

    def sdk_shutdown_observed(self):
        with self.sdk_lock:
            if any(self.sdk_inflight.values()):
                return False
            if not self.sdk["start_successes"]:
                return True
            if "stop" in self.sdk["public_methods_present"]:
                stop_observed = self.sdk["stop_calls"] > 0
            else:
                stop_observed = self.sdk["complete_calls"] > 0
            callback_observed = (not self.complete_callback_expected
                                 or self.sdk["complete_callbacks"] > 0)
            return stop_observed and callback_observed

    def ready_to_finish(self):
        with self.lock:
            source_closed = self.source_closed and self.iter_depth == 0
        return source_closed and self.sdk_shutdown_observed()

    def write_event(self, row):
        if self.log_fd is None:
            return False
        try:
            _write_all(self.log_fd, (json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode())
            return True
        except OSError:
            self.invalidate("jsonl_write_failure")
            return False

    def write_loop(self):
        sink = None
        try:
            try:
                sink = self.writer_factory(self.wav_path)
            except Exception:
                self.invalidate("wav_open_failure")
            while True:
                try:
                    data, row = self.q.get(timeout=0.05)
                except queue.Empty:
                    if self.finish_event.is_set():
                        break
                    continue
                try:
                    if sink is None:
                        continue
                    sink.write(data)
                    self.pcm_digest.update(data)
                    self.captured_bytes += len(data)
                    self.captured_samples += len(data) // 2
                    self.write_event(row)
                except Exception:
                    self.invalidate("wav_write_failure")
                    try:
                        sink.close()
                    except Exception:
                        self.invalidate("wav_close_failure")
                    sink = None
                finally:
                    self.q.task_done()
        except Exception:
            self.invalidate("writer_failure")
        finally:
            if sink is not None:
                try:
                    sink.close()
                except Exception:
                    self.invalidate("wav_close_failure")
            if self.log_fd is not None:
                self.write_event(self.summary())
                try:
                    os.close(self.log_fd)
                except OSError:
                    self.invalidate("jsonl_close_failure")
                self.log_fd = None

    def summary(self):
        with self.lock:
            reasons = list(self.invalid_reasons)
            source_status = self.stream_status
            source_closed = self.source_closed
            iterator_depth = self.iter_depth
            observed, captured = self.seen_samples, self.captured_samples
            first_ns, last_ns = self.first_ns, self.last_ns
            first_pts, last_pts, offset = self.first_pts, self.last_pts, self.timeline_offset
        wav_sha = _file_hash(self.wav_path)
        sdk = self.sdk_snapshot()
        sdk_shutdown = self.sdk_shutdown_observed()
        source_finalized = source_closed and iterator_depth == 0
        valid = (not reasons and observed == captured and wav_sha is not None
                 and source_finalized and sdk_shutdown)
        return {"schema_version": 1, "event": "capture_summary",
                "case_id": self.case["case_id"], "generation": self.case["generation"],
                "input_identity_sha256": self.case["input_identity_sha256"],
                "input_identity_kind": self.case["input_kind"],
                "source_content_sha256": self.case["source_content_sha256"],
                "status": "invalid" if reasons else source_status,
                "source_stream_status": source_status, "valid": valid,
                "source_closed": source_closed, "source_iterator_depth": iterator_depth,
                "sdk_shutdown_observed": sdk_shutdown,
                "complete": valid and source_status == "completed",
                "invalid_reasons": reasons,
                "source_samples_observed": observed, "source_samples_captured": captured,
                "dropped_samples": max(0, observed - captured),
                "source_bytes_observed": self.seen_bytes, "pcm_bytes_captured": self.captured_bytes,
                "pcm_sha256": self.pcm_digest.hexdigest(), "wav_sha256": wav_sha,
                "first_ingress_monotonic_ns": first_ns, "last_ingress_monotonic_ns": last_ns,
                "first_source_pts_sec": first_pts, "last_source_pts_sec": last_pts,
                "timeline_offset_sec": offset, "sdk": sdk}

    def finish(self):
        with self.lock:
            if self.finalized:
                return
            self.finalized = True
        self.finish_event.set()
        if self.worker.ident is None:
            if self.log_fd is not None:
                self.write_event(self.summary())
                try:
                    os.close(self.log_fd)
                except OSError:
                    self.invalidate("jsonl_close_failure")
                self.log_fd = None
            return
        self.worker.join(timeout=self.probe.writer_join_timeout)
        if self.worker.is_alive():
            self.invalidate("writer_shutdown_timeout")


class _Probe:
    def __init__(self, cases, manifest_sha, output, cap, stop_file, qsize, sdk_methods,
                 sdk_callback_class,
                 writer_factory, join_timeout, shutdown_timeout):
        if isinstance(cap, bool) or not isinstance(cap, int) or cap < 2:
            raise ValueError("max_pcm_bytes must be at least two")
        if isinstance(qsize, bool) or not isinstance(qsize, int) or qsize < 1:
            raise ValueError("queue_size must be positive")
        if (not math.isfinite(join_timeout) or join_timeout <= 0
                or not math.isfinite(shutdown_timeout) or shutdown_timeout <= 0):
            raise ValueError("writer and shutdown timeouts must be finite and positive")
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.pcm_dir = self.output / "pcm"
        self.pcm_dir.mkdir(mode=0o700)
        self.stop_file = Path(stop_file)
        if self.stop_file.exists():
            raise ValueError("invalid stop signal already exists")
        self.max_pcm_bytes, self.queue_size = cap, qsize
        self.sdk_methods = list(sdk_methods)
        self.sdk_callback_class = sdk_callback_class
        self.writer_factory, self.writer_join_timeout = writer_factory, join_timeout
        self.shutdown_timeout = shutdown_timeout
        self.lock, self.active = threading.RLock(), None
        self.claimed, self.sdk_targets = set(), {}
        self.captures = []
        self.run_invalid_reasons = []
        self.sources = weakref.WeakKeyDictionary()
        self.stop_written = self.stop_write_failed = False
        safe_cases = [{"case_id": c["case_id"], "generation": c["generation"],
                       "input_identity_sha256": c["input_identity_sha256"],
                       "input_identity_kind": c["input_kind"],
                       "source_content_sha256": c["source_content_sha256"]}
                      for c in cases]
        _exclusive_json(self.output / "capture-manifest.json", {
            "schema_version": 1, "manifest_sha256": manifest_sha,
            "allowed_case_count": len(cases), "cases": safe_cases})
        self.cases = cases

    def signal_invalid(self, reason, case=None, identity=None):
        with self.lock:
            if reason not in self.run_invalid_reasons:
                self.run_invalid_reasons.append(reason)
            if self.stop_written:
                return not self.stop_write_failed
            self.stop_written = True
            row = {"schema_version": 1, "reason": reason,
                   "case_id": case["case_id"] if case else None,
                   "generation": case["generation"] if case else None,
                   "input_identity_sha256": case["input_identity_sha256"] if case else identity,
                   "observed_monotonic_ns": time.monotonic_ns()}
            try:
                _exclusive_json(self.stop_file, row)
            except OSError:
                self.stop_write_failed = True
                if "stop_signal_write_failure" not in self.run_invalid_reasons:
                    self.run_invalid_reasons.append("stop_signal_write_failure")
                return False
            return True

    def begin(self, value):
        value = str(value)
        identity = _hash(value.encode())
        kind = "url" if urlsplit(value).scheme.lower() in ("http", "https") else "path"
        with self.lock:
            if self.active is not None:
                self.active.invalidate("overlapping_source_instance")
                return None
            index = next((i for i, c in enumerate(self.cases)
                          if i not in self.claimed and c["input_identity_sha256"] == identity
                          and c["input_kind"] == kind), None)
            if index is None:
                self.signal_invalid("source_not_allowed", identity=identity)
                return None
            self.claimed.add(index)
            case = self.cases[index]
            capture = _Capture(self, case)
            self.captures.append(capture)
            self.active = capture
            return capture

    def maybe_finish(self, capture):
        with self.lock:
            if capture is not self.active or not capture.ready_to_finish():
                return
            self.active = None
        capture.finish()

    def shutdown(self):
        deadline = time.monotonic() + self.shutdown_timeout
        with self.lock:
            capture = self.active
            captures = list(self.captures)
        if capture is not None:
            while not capture.ready_to_finish():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                capture.state_changed.clear()
                if capture.ready_to_finish():
                    break
                capture.state_changed.wait(remaining)
            if capture.ready_to_finish():
                self.maybe_finish(capture)
            else:
                with capture.lock:
                    source_closed = capture.source_closed
                    iterator_open = capture.iter_depth > 0
                if not source_closed:
                    capture.invalidate("source_close_not_observed")
                if iterator_open:
                    capture.invalidate("source_iterator_not_closed")
                sdk = capture.sdk_snapshot()
                if any(sdk["inflight_calls"].values()):
                    capture.invalidate("sdk_calls_inflight_at_shutdown")
                has_stop = "stop" in sdk["public_methods_present"]
                if (sdk["start_successes"] and has_stop and sdk["stop_calls"] == 0):
                    capture.invalidate("sdk_stop_not_observed")
                elif (sdk["start_successes"] and not has_stop
                      and sdk["complete_calls"] == 0):
                    capture.invalidate("sdk_completion_method_not_observed")
                if (sdk["start_successes"] and sdk["complete_callback_expected"]
                        and sdk["complete_callbacks"] == 0):
                    capture.invalidate("sdk_completion_callback_not_observed")
                if not capture.invalid_reasons:
                    capture.invalidate("probe_shutdown_incomplete")
                with self.lock:
                    if self.active is capture:
                        self.active = None
                capture.finish()

        for capture in captures:
            remaining = deadline - time.monotonic()
            if capture.worker.ident is not None and remaining > 0:
                capture.worker.join(timeout=remaining)
            if capture.worker.is_alive():
                capture.invalidate("writer_shutdown_timeout")

    def write_run_closed_receipt(self):
        with self.lock:
            captures = list(self.captures)
            expected = [{"case_id": case["case_id"], "generation": case["generation"],
                         "input_identity_sha256": case["input_identity_sha256"],
                         "input_identity_kind": case["input_kind"],
                         "source_content_sha256": case["source_content_sha256"]}
                        for case in self.cases]
            reasons = list(self.run_invalid_reasons)
            marker_written = self.stop_written and not self.stop_write_failed
            marker_write_failed = self.stop_write_failed

        summaries = [capture.summary() for capture in captures]
        expected_keys = [(row["case_id"], row["generation"],
                          row["input_identity_sha256"]) for row in expected]
        actual_keys = [(row["case_id"], row["generation"],
                        row["input_identity_sha256"]) for row in summaries]
        captures_match = (len(expected_keys) == len(actual_keys)
                          and sorted(expected_keys) == sorted(actual_keys))
        safe_captures = []
        for capture, summary in zip(captures, summaries):
            writer_terminated = (capture.worker.ident is not None
                                 and not capture.worker.is_alive())
            safe_row = {key: summary[key] for key in (
                "case_id", "generation", "input_identity_sha256", "input_identity_kind",
                "source_content_sha256", "status", "valid", "complete",
                "source_samples_observed", "source_samples_captured",
                "sdk_shutdown_observed", "invalid_reasons")}
            safe_row["writer_terminated"] = writer_terminated
            safe_captures.append(safe_row)
            for reason in summary["invalid_reasons"]:
                if reason not in reasons:
                    reasons.append(reason)
            if not writer_terminated and "writer_not_terminated_at_close" not in reasons:
                reasons.append("writer_not_terminated_at_close")
            if not summary["valid"] and not summary["invalid_reasons"]:
                if "capture_invalid" not in reasons:
                    reasons.append("capture_invalid")
        if captures and not captures_match and "expected_captures_missing" not in reasons:
            reasons.append("expected_captures_missing")
        valid = (bool(captures) and captures_match and not reasons
                 and not marker_write_failed and all(row["valid"] for row in summaries)
                 and all(row["writer_terminated"] for row in safe_captures))
        status = "valid" if valid else "not_assessed" if not captures and not reasons else "invalid"
        receipt = {"schema_version": 1, "event": "run_closed", "status": status,
                   "valid": valid, "measurement_assessed": bool(captures),
                   "capture_count": len(safe_captures),
                   "expected_capture_count": len(expected),
                   "expected_captures": expected, "captures": safe_captures,
                   "invalid_reasons": reasons,
                   "stop_signal_written": marker_written,
                   "stop_signal_write_failed": marker_write_failed,
                   "closed_monotonic_ns": time.monotonic_ns()}
        try:
            _exclusive_json(self.output / "run-closed.json", receipt)
        except OSError:
            raise ProbeFinalizationError(
                "could not write run-closed receipt") from None


def install_probe(webui_module, manifest_path, output_dir, *, max_pcm_bytes,
                  invalid_stop_file, sdk_recognition_class=None, queue_size=64,
                  sdk_callback_class=None, writer_factory=None, writer_join_timeout=5.0,
                  shutdown_timeout=5.0):
    cases, manifest_sha = load_case_manifest(manifest_path)
    sdk_methods = [name for name in SDK_METHODS
                   if sdk_recognition_class is not None and
                   callable(getattr(sdk_recognition_class, name, None))]
    probe = _Probe(cases, manifest_sha, output_dir, max_pcm_bytes,
                   invalid_stop_file, queue_size, sdk_methods, sdk_callback_class,
                   writer_factory or _WaveWriter, writer_join_timeout, shutdown_timeout)
    source = getattr(webui_module, "StreamSource", None)
    if source is None:
        raise RuntimeError("WebUI module has no StreamSource")
    originals = {name: source.__dict__.get(name) for name in ("__init__", "iter_chunks", "close")}
    original_init, original_iter, original_close = source.__init__, source.iter_chunks, source.close

    def source_init(self, input_value, *args, **kwargs):
        try:
            capture = probe.begin(input_value)
        except Exception:
            probe.signal_invalid("source_observer_setup_failure")
            capture = None
        if capture:
            try:
                probe.sources[self] = capture
            except Exception:
                capture.invalidate("source_observer_setup_failure")
        try:
            original_init(self, input_value, *args, **kwargs)
        except BaseException:
            if capture:
                capture.set_status("error")
                capture.note_source_closed(internal=False)
            raise

    def iter_chunks(self, *args, **kwargs):
        capture = probe.sources.get(self)
        if capture is None:
            yield from original_iter(self, *args, **kwargs)
            return
        capture.iterator_enter()
        try:
            for chunk in original_iter(self, *args, **kwargs):
                try:
                    capture.add_chunk(chunk, getattr(self, "first_pts_sec", None),
                                      getattr(self, "timeline_offset_sec", None))
                except Exception:
                    capture.invalidate("source_observer_failure")
                yield chunk
            capture.set_status("completed")
        except GeneratorExit:
            if capture.stream_status == "active":
                capture.set_status("cancelled")
            raise
        except BaseException:
            capture.set_status("error")
            raise
        finally:
            capture.iterator_exit()

    def source_close(self, *args, **kwargs):
        capture = probe.sources.get(self)
        internal = capture is not None and capture.iter_depth > 0
        try:
            result = original_close(self, *args, **kwargs)
        except BaseException:
            if capture:
                capture.set_status("error")
                capture.note_source_closed(internal)
            raise
        if capture:
            capture.note_source_closed(internal)
        return result

    source.__init__, source.iter_chunks, source.close = source_init, iter_chunks, source_close
    restorers = [(source, originals)]
    sdk_originals = {}
    if sdk_recognition_class is not None:
        if sdk_callback_class is not None:
            original_sdk_init = sdk_recognition_class.__init__
            sdk_originals["__init__"] = sdk_recognition_class.__dict__.get("__init__")

            class ObservedCallback(sdk_callback_class):
                def __init__(self, callback, capture):
                    super().__init__()
                    self._callback = callback
                    self._capture = capture

                def on_open(self, *args, **kwargs):
                    return self._callback.on_open(*args, **kwargs)

                def on_close(self, *args, **kwargs):
                    return self._callback.on_close(*args, **kwargs)

                def on_error(self, *args, **kwargs):
                    return self._callback.on_error(*args, **kwargs)

                def on_event(self, *args, **kwargs):
                    return self._callback.on_event(*args, **kwargs)

                def on_complete(self, *args, **kwargs):
                    try:
                        self._capture.note_complete_callback()
                    except Exception:
                        self._capture.invalidate("sdk_counter_observer_failure")
                    return self._callback.on_complete(*args, **kwargs)

            def observed_init(instance, *args, **kwargs):
                capture = probe.active
                callback = kwargs.get("callback")
                callback_index = None
                if callback is None and len(args) > 1:
                    callback_index, callback = 1, args[1]
                if capture is not None and isinstance(callback, sdk_callback_class):
                    try:
                        wrapped = ObservedCallback(callback, capture)
                        capture.expect_complete_callback()
                        if callback_index is None:
                            kwargs = dict(kwargs)
                            kwargs["callback"] = wrapped
                        else:
                            args = list(args)
                            args[callback_index] = wrapped
                            args = tuple(args)
                    except Exception:
                        capture.invalidate("callback_observer_setup_failure")
                return original_sdk_init(instance, *args, **kwargs)

            sdk_recognition_class.__init__ = observed_init
        for method in sdk_methods:
            sdk_originals[method] = sdk_recognition_class.__dict__.get(method)
            original = getattr(sdk_recognition_class, method)

            def wrap(name, function):
                def observed(instance, *args, **kwargs):
                    with probe.lock:
                        active = probe.active
                        target = probe.sdk_targets.get(id(instance))
                        capture = (active if name == "start" and active is not None
                                   else target[1] if target and target[0] is instance else None)
                        if name == "start" and active is not None:
                            capture = active
                            probe.sdk_targets[id(instance)] = (instance, active)
                    if capture:
                        try:
                            capture.note_sdk(name, args, kwargs)
                        except Exception:
                            capture.invalidate("sdk_counter_observer_failure")
                    try:
                        result = function(instance, *args, **kwargs)
                    except BaseException:
                        if capture:
                            try:
                                capture.note_sdk(name, args, kwargs, False)
                            except Exception:
                                capture.invalidate("sdk_counter_observer_failure")
                            if name in ("stop", "complete"):
                                probe.maybe_finish(capture)
                        raise
                    if capture:
                        try:
                            capture.note_sdk(name, args, kwargs, True)
                        except Exception:
                            capture.invalidate("sdk_counter_observer_failure")
                        if name in ("stop", "complete"):
                            probe.maybe_finish(capture)
                    return result
                return observed
            setattr(sdk_recognition_class, method, wrap(method, original))
        restorers.append((sdk_recognition_class, sdk_originals))

    class ProbeInstallation:
        def __init__(self):
            self.restored = False

        def restore(self):
            if self.restored:
                return
            self.restored = True
            try:
                probe.shutdown()
            finally:
                for cls, values in reversed(restorers):
                    names = ("__init__", "iter_chunks", "close") if cls is source else values
                    for name in names:
                        value = values.get(name)
                        if value is None:
                            try:
                                delattr(cls, name)
                            except AttributeError:
                                pass
                        else:
                            setattr(cls, name, value)
                with probe.lock:
                    probe.sdk_targets.clear()
                probe.write_run_closed_receipt()

    return ProbeInstallation()
