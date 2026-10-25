"""Opt-in, provider-neutral timing hook for fixed real-time startup runs.

The hook timestamps the first non-empty 16 kHz PCM chunk yielded by the
application's StreamSource. It deliberately records no media URL, PCM data,
transcript, credential, or provider call. Enable it only in a dedicated WebUI
process by calling :func:`install_stream_source_probe` before ``webui.main``.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit


CASE_PATTERN = re.compile(r"(?:zh|en)-startup-[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_PATCHED = "_fixed_startup_probe_installed"


def _utc_now():
    return datetime.now(timezone.utc).isoformat()


def startup_case_from_url(value):
    """Return only an allow-listed case label from the non-secret query field."""
    try:
        values = parse_qs(urlsplit(str(value)).query).get("startup_case", [])
    except (TypeError, ValueError):
        return None
    if len(values) != 1 or not CASE_PATTERN.fullmatch(values[0]):
        return None
    return values[0]


def _append_json_line(path, row, lock):
    try:
        destination = Path(path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
        with lock:
            fd = os.open(destination, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.write(fd, payload.encode("utf-8"))
            finally:
                os.close(fd)
    except Exception:
        # Observation must not alter the recognition path if local logging fails.
        return False
    return True


def install_stream_source_probe(webui_module, output_path):
    """Wrap the WebUI's already-imported StreamSource class with numeric timing.

    The timestamp is taken after demux/decode has produced a non-empty PCM
    chunk, immediately before the WebUI's session loop receives it. The ASR
    engine and all provider-facing calls remain untouched.
    """
    source_class = getattr(webui_module, "StreamSource", None)
    if source_class is None:
        raise RuntimeError("WebUI module has no imported StreamSource class")
    if getattr(source_class, _PATCHED, False):
        raise RuntimeError("startup PCM probe is already installed")

    log_lock = threading.Lock()
    original_init = source_class.__init__
    original_iter_chunks = source_class.iter_chunks

    def observed_init(self, url_or_path, *args, **kwargs):
        self._fixed_startup_probe_case = startup_case_from_url(url_or_path)
        original_init(self, url_or_path, *args, **kwargs)
        self._fixed_startup_probe_live_hls = False
        if self._fixed_startup_probe_case is not None:
            parsed = urlsplit(str(url_or_path))
            container = getattr(self, "container", None)
            self._fixed_startup_probe_live_hls = bool(
                parsed.scheme in ("http", "https") and parsed.path.lower().endswith(".m3u8")
                and container is not None and container.format.name == "hls")

    def observed_iter_chunks(self, *args, **kwargs):
        case_id = getattr(self, "_fixed_startup_probe_case", None)
        if case_id is None:
            yield from original_iter_chunks(self, *args, **kwargs)
            return

        first_pcm_utc = None
        first_pcm_monotonic_ns = None
        first_chunk_bytes = None
        decoded_chunk_count = 0
        decoded_pcm_bytes = 0
        stream_status = "completed"
        try:
            for chunk in original_iter_chunks(self, *args, **kwargs):
                if chunk:
                    decoded_chunk_count += 1
                    decoded_pcm_bytes += len(chunk)
                    if first_pcm_monotonic_ns is None:
                        first_pcm_monotonic_ns = time.monotonic_ns()
                        first_pcm_utc = _utc_now()
                        first_chunk_bytes = len(chunk)
                yield chunk
        except GeneratorExit:
            stream_status = "closed"
            raise
        except BaseException:
            stream_status = "error"
            raise
        finally:
            row = {
                "schema_version": 1,
                "event": "stream_source_pcm_summary",
                "case_id": case_id,
                "process_id": os.getpid(),
                "is_live_hls": self._fixed_startup_probe_live_hls,
                "stream_status": stream_status,
                "first_decoded_pcm_utc": first_pcm_utc,
                "first_decoded_pcm_monotonic_ns": first_pcm_monotonic_ns,
                "first_chunk_bytes": first_chunk_bytes,
                "first_chunk_duration_ms": (
                    round(first_chunk_bytes / 32.0, 3) if first_chunk_bytes is not None else None
                ),
                "decoded_chunk_count": decoded_chunk_count,
                "decoded_pcm_bytes": decoded_pcm_bytes,
                "decoded_pcm_duration_ms": round(decoded_pcm_bytes / 32.0, 3),
                "sample_rate_hz": 16000,
                "channels": 1,
                "sample_format": "s16le",
                "recorded_at_utc": _utc_now(),
            }
            _append_json_line(output_path, row, log_lock)

    source_class.__init__ = observed_init
    source_class.iter_chunks = observed_iter_chunks
    setattr(source_class, _PATCHED, True)
    return {"installed": True, "output_path": str(Path(output_path).expanduser())}
