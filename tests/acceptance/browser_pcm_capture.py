"""Lossless streamed PCM for the browser output timestamp engineering scope."""
import base64
import hashlib
import json
import os
from pathlib import Path


MEASUREMENT_SCOPE = "browser_output_timestamp_estimate"


class EngineeringPcmWriter:
    """Persist the browser bridge payload without keeping the audio in memory."""

    def __init__(self, output_path, report_root):
        self.path = Path(output_path).expanduser().resolve()
        self.root = Path(report_root).expanduser().resolve()
        self.path.relative_to(self.root)
        self.metadata_path = self.path.with_suffix(".frames.jsonl")
        if self.path.exists() or self.metadata_path.exists():
            raise FileExistsError("refusing to overwrite engineering PCM evidence")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.audio = self.path.open("xb")
        self.metadata = self.metadata_path.open("x", encoding="utf-8")
        self.digest = hashlib.sha256()
        self.batch_count = self.frame_count = self.byte_count = 0
        self.first_frame = self.end_frame = None
        self.context_id = self.sample_rate = self.channels = None
        self.errors = []
        self.clock_count = self.state_count = 0
        self.pixel_count = 0

    def _log(self, payload):
        self.metadata.write(json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n")
        self.metadata.flush()

    def _reject(self, reason, payload):
        if reason not in self.errors:
            self.errors.append(reason)
        self._log({"kind": "integrity_error", "reason": reason,
                   "rejected_metadata": {key: value for key, value in payload.items()
                                         if key != "pcm_base64"}
                   if isinstance(payload, dict) else {"payload_type": type(payload).__name__}})
        return {"status": "rejected", "reason": reason}

    @staticmethod
    def _integer(value, minimum=0, maximum=2**53 - 1):
        return type(value) is int and minimum <= value <= maximum

    def _validate_batch(self, payload):
        if self.errors:
            raise ValueError("capture_already_invalid")
        if payload.get("sequence") != self.batch_count:
            raise ValueError("batch_sequence_invalid")
        context = payload.get("context_id")
        rate, channels = payload.get("sample_rate_hz"), payload.get("channel_count")
        if (not isinstance(context, str) or not context or len(context) > 100
                or not self._integer(rate, 8000, 384000)
                or not self._integer(channels, 1, 32)):
            raise ValueError("pcm_format_invalid")
        if self.context_id is not None and context != self.context_id:
            raise ValueError("pcm_context_changed")
        if self.batch_count and (context, rate, channels) != (
                self.context_id, self.sample_rate, self.channels):
            raise ValueError("pcm_format_changed")
        quanta = payload.get("quanta")
        if not isinstance(quanta, list) or not 1 <= len(quanta) <= 256:
            raise ValueError("quantum_metadata_invalid")
        expected = self.end_frame
        frames = 0
        for quantum in quanta:
            frame, count = quantum.get("current_frame"), quantum.get("frame_count")
            if not self._integer(frame) or not self._integer(count, 1, 32768):
                raise ValueError("quantum_metadata_invalid")
            if expected is not None and frame != expected:
                raise ValueError("pcm_frame_gap_or_overlap")
            expected = frame + count
            frames += count
        encoded = payload.get("pcm_base64")
        if not isinstance(encoded, str) or len(encoded) > 2 * 1024 * 1024:
            raise ValueError("pcm_batch_size_invalid")
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != frames * channels * 4:
            raise ValueError("pcm_byte_count_invalid")
        return raw, frames

    def receive(self, payload):
        """Accept one browser batch; arrival time is delivery metadata only."""
        if not isinstance(payload, dict):
            return self._reject("bridge_event_invalid", payload)
        kind = payload.get("kind")
        if kind in ("context_state", "clock_sample", "media_state", "device_change", "capture_error", "pixel_observation"):
            context = payload.get("context_id")
            if not isinstance(context, str) or not context or (
                    self.context_id is not None and context != self.context_id):
                return self._reject("pcm_context_changed", payload)
            self.context_id = context
            self._log(payload)
            self.clock_count += kind == "clock_sample"
            self.state_count += kind == "context_state"
            self.pixel_count += kind == "pixel_observation"
            if kind == "capture_error":
                self.errors.append(payload.get("reason") or "browser_capture_error")
            return {"status": "accepted"}
        if kind != "pcm_batch":
            return self._reject("bridge_event_invalid", payload)
        try:
            raw, frames = self._validate_batch(payload)
        except (ValueError, TypeError, AttributeError) as error:
            return self._reject(str(error), payload)
        self.context_id = payload["context_id"]
        self.sample_rate = payload["sample_rate_hz"]
        self.channels = payload["channel_count"]
        if self.first_frame is None:
            self.first_frame = payload["quanta"][0]["current_frame"]
        last = payload["quanta"][-1]
        self.end_frame = last["current_frame"] + last["frame_count"]
        metadata = {key: value for key, value in payload.items() if key != "pcm_base64"}
        metadata.update(byte_offset=self.byte_count, byte_count=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest())
        self.audio.write(raw)
        self.digest.update(raw)
        self._log(metadata)
        self.batch_count += 1
        self.frame_count += frames
        self.byte_count += len(raw)
        return {"status": "accepted"}

    def finish(self, browser_metadata):
        """Close the files and publish their integrity identities."""
        expected = {"context_id": self.context_id, "batch_count": self.batch_count,
                    "frame_count": self.frame_count, "first_frame": self.first_frame,
                    "end_frame": self.end_frame, "sample_rate_hz": self.sample_rate,
                    "channel_count": self.channels}
        if not self.batch_count or any(browser_metadata.get(key) != value
                                       for key, value in expected.items()):
            self.errors.append("final_frame_accounting_mismatch")
        if browser_metadata.get("status") != "captured":
            self.errors.append("browser_capture_incomplete")
        self._log({**browser_metadata, "kind": "capture_summary"})
        self.audio.flush()
        os.fsync(self.audio.fileno())
        os.fsync(self.metadata.fileno())
        self.audio.close()
        self.metadata.close()
        return {
            **browser_metadata,
            "status": "captured" if not self.errors else "unknown",
            "integrity_errors": self.errors, "measurement_scope": MEASUREMENT_SCOPE,
            "format": "f32le", "layout": "interleaved", "byte_count": self.byte_count,
            "path": self.path.relative_to(self.root).as_posix(),
            "sha256": self.digest.hexdigest(),
            "metadata_path": self.metadata_path.relative_to(self.root).as_posix(),
            "metadata_sha256": hashlib.sha256(self.metadata_path.read_bytes()).hexdigest(),
            "batch_count": self.batch_count, "frame_count": self.frame_count,
            "first_frame": self.first_frame, "end_frame": self.end_frame,
            "sample_rate_hz": self.sample_rate, "channel_count": self.channels,
            "clock_sample_count": self.clock_count, "context_state_count": self.state_count,
            "pixel_observation_count": self.pixel_count,
            "clock_mapping_status": "browser_api_estimate_only",
            "output_clock_error_bound_ms": None, "physical_output_latency": "unknown",
        }


def install_engineering_capture(page, writer, media_element_id="audioEl", max_seconds=600):
    """Install the transparent output tap before the operator starts playback."""
    if media_element_id not in ("audioEl", "player"):
        raise ValueError("unsupported engineering capture element")
    page.expose_binding("__engineeringPcmWrite", lambda _source, payload: writer.receive(payload))
    script = Path(__file__).with_suffix(".js").read_text(encoding="utf-8")
    return page.evaluate(script, {"mediaElementId": media_element_id,
                                  "maxSeconds": max_seconds})


def save_engineering_capture(page, writer):
    """Flush the bounded bridge and reconcile its last frame with the disk file."""
    try:
        metadata = page.evaluate("() => window.__engineeringCapture?.save()")
    except Exception:
        metadata = {"status": "capture_failed"}
    return writer.finish(metadata if isinstance(metadata, dict) else {"status": "missing"})
