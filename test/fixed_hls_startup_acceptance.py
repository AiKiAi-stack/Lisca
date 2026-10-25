"""PRD 7.3 fixed-input live-HLS startup collector and analyzer.

The source is the hash-pinned reference WAV prefix already used by the WAV
diagnostic. PyAV encodes that audio at its original rate as AAC in H.264/AAC
MPEG-TS segments. The loopback origin reveals each completed segment only
after its source-clock end time; it does not expose a complete VOD playlist or
start recognition. Only the explicit collect command starts a real provider session.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import wave

import fixed_realtime_startup_acceptance as startup


DEFAULT_OUTPUT = startup.DEFAULT_OUTPUT
DEFAULT_ORIGIN = "http://127.0.0.1:8766"
SEGMENT_RE = re.compile(r"seg(?P<index>\d{3,})\.ts\Z")
LOOPBACKS = {"127.0.0.1", "localhost", "::1"}
SOURCE_CLOCK_TOLERANCE_SECONDS = 0.01


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def _packet_hash(packet):
    return _sha256_bytes(bytes(packet))


def _pcm_from_wav(path):
    with wave.open(str(path), "rb") as reader:
        if (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) != (
                1, 2, startup.PCM_RATE):
            raise ValueError("fixed HLS input must be mono 16 kHz 16-bit PCM WAV")
        frames = reader.readframes(reader.getnframes())
        if len(frames) != reader.getnframes() * 2:
            raise ValueError("fixed HLS input WAV is truncated")
        return frames, reader.getnframes() / startup.PCM_RATE


def _encode_fixed_hls(wav_path, playlist_path, segment_pattern):
    import av
    import numpy as np
    from fractions import Fraction

    pcm, duration = _pcm_from_wav(wav_path)
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    with av.open(str(playlist_path), "w", format="hls", options={
            "hls_time": "0.5",
            "hls_list_size": "0",
            "hls_flags": "independent_segments",
            "hls_segment_filename": str(segment_pattern),
    }) as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
        video.codec_context.gop_size = 5
        video.codec_context.max_b_frames = 0
        video.codec_context.thread_count = 1
        video.options = {"preset": "ultrafast", "tune": "zerolatency", "sc_threshold": "0"}
        audio = output.add_stream("aac", rate=startup.PCM_RATE)
        audio.layout = "mono"
        audio.bit_rate = 64000

        video_frames = math.ceil(duration * 10)
        for index in range(video_frames):
            frame = av.VideoFrame.from_ndarray(
                np.full((180, 320, 3), (20, 24, 28), dtype=np.uint8), format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame):
                output.mux(packet)

            start = index * startup.PCM_RATE // 10
            block = samples[start:min(start + startup.PCM_RATE // 10, len(samples))]
            if len(block):
                audio_frame = av.AudioFrame.from_ndarray(
                    block[None, :], format="fltp", layout="mono")
                audio_frame.sample_rate = startup.PCM_RATE
                audio_frame.pts = start
                audio_frame.time_base = Fraction(1, startup.PCM_RATE)
                for packet in audio.encode(audio_frame):
                    output.mux(packet)

        for stream in (video, audio):
            for packet in stream.encode(None):
                output.mux(packet)


def _parse_encoded_playlist(path, directory):
    import av

    # Some standalone MPEG-TS segments do not retain enough program metadata
    # for FFmpeg to report the AAC packet duration. Read the same packets
    # through the complete HLS playlist first, where FFmpeg sees the stream's
    # AAC configuration, then use that duration only for an exact PTS/size
    # match. This preserves the final nonempty AAC packet instead of treating
    # a duration-zero packet as empty or guessing its length.
    packet_durations = {}
    with av.open(str(path)) as container:
        if container.format.name != "hls" or not container.streams.audio:
            raise ValueError("generated HLS must expose AAC audio through its playlist")
        audio_stream = container.streams.audio[0]
        if (audio_stream.codec_context.name != "aac"
                or audio_stream.codec_context.sample_rate != startup.PCM_RATE):
            raise ValueError("generated HLS playlist audio is not 16 kHz AAC")
        for packet in container.demux(audio_stream):
            if packet.size <= 0 or packet.pts is None or packet.time_base is None:
                continue
            if packet.duration is None or packet.duration <= 0:
                raise ValueError("generated HLS has an AAC packet with unknown duration")
            key = (packet.pts, packet.size)
            duration = float(packet.duration * packet.time_base)
            candidates = packet_durations.setdefault(key, [])
            packet_hash = _packet_hash(packet)
            prior = next((item[1] for item in candidates if item[0] == packet_hash), None)
            if prior is not None and abs(prior - duration) > 1e-9:
                raise ValueError("generated HLS has conflicting AAC packet durations")
            if prior is None:
                candidates.append((packet_hash, duration))

    entries = re.findall(
        r"(?m)^#EXTINF:([0-9]+(?:\.[0-9]+)?),?\s*\n([^\r\n]+)",
        Path(path).read_text(encoding="utf-8"),
    )
    segments = []
    start = 0.0
    for duration_text, filename in entries:
        filename = Path(filename.strip()).name
        match = SEGMENT_RE.fullmatch(filename)
        duration = float(duration_text)
        segment_path = Path(directory) / filename
        if not match or duration <= 0 or not segment_path.is_file():
            raise ValueError("generated HLS playlist has an invalid or missing MPEG-TS segment")
        playlist_end = start + duration
        packet_duration_fallback_count = 0
        with av.open(str(segment_path)) as container:
            if not container.streams.audio:
                raise ValueError("generated MPEG-TS segment has no AAC audio stream")
            audio_stream = container.streams.audio[0]
            audio_bounds = []
            for packet in container.demux(audio_stream):
                time_base = packet.time_base or audio_stream.time_base
                if packet.size and packet.pts is not None and time_base is not None:
                    packet_duration = packet.duration or 0
                    if packet_duration <= 0:
                        candidates = packet_durations.get((packet.pts, packet.size), [])
                        if len(candidates) == 1:
                            full_playlist_duration = candidates[0][1]
                        else:
                            packet_hash = _packet_hash(packet)
                            matching = [item[1] for item in candidates
                                        if item[0] == packet_hash]
                            full_playlist_duration = matching[0] if len(matching) == 1 else None
                        if full_playlist_duration is None:
                            raise ValueError(
                                "standalone MPEG-TS AAC packet is not bound to the full HLS stream")
                        packet_duration = round(full_playlist_duration / float(time_base))
                        if packet_duration <= 0:
                            raise ValueError("full HLS AAC packet duration cannot be represented")
                        packet_duration_fallback_count += 1
                    packet_start = float(packet.pts * time_base)
                    packet_end = float((packet.pts + packet_duration) * time_base)
                    audio_bounds.append((packet_start, packet_end))
        if not audio_bounds:
            raise ValueError("generated MPEG-TS segment has no timestamped AAC packets")
        audio_start = min(item[0] for item in audio_bounds)
        audio_end = max(item[1] for item in audio_bounds)
        segments.append({
            "index": int(match.group("index")),
            "filename": filename,
            "start_seconds": round(start, 6),
            "duration_seconds": duration,
            "playlist_end_seconds": round(playlist_end, 6),
            "audio_start_seconds": round(audio_start, 6),
            "audio_end_seconds": round(audio_end, 6),
            "end_seconds": round(max(playlist_end, audio_end), 6),
            "packet_duration_fallback_count": packet_duration_fallback_count,
            "sha256": startup._sha256(segment_path),
            "bytes": segment_path.stat().st_size,
        })
        start += duration
    if not segments or [item["index"] for item in segments] != list(range(len(segments))):
        raise ValueError("generated HLS segments must be contiguous from segment zero")
    if abs(segments[0]["audio_start_seconds"]) > 0.1 or any(
            abs(right["audio_start_seconds"] - left["audio_end_seconds"]) > 0.002
            for left, right in zip(segments, segments[1:])):
        raise ValueError("generated AAC packet timestamps have a gap or overlap between segments")
    return segments, start


def _decode_pcm_provenance(playlist_path):
    import av

    digest = hashlib.sha256()
    sample_count = 0
    first_decoded_pts_seconds = None
    with av.open(str(playlist_path)) as container:
        if container.format.name != "hls" or not container.streams.audio:
            raise ValueError("generated HLS must decode through PyAV with an audio stream")
        audio_stream = container.streams.audio[0]
        if (audio_stream.codec_context.name != "aac"
                or audio_stream.codec_context.sample_rate != startup.PCM_RATE):
            raise ValueError("generated HLS decoded stream is not 16 kHz AAC")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=startup.PCM_RATE)
        for frame in container.decode(audio_stream):
            if (first_decoded_pts_seconds is None and frame.pts is not None
                    and frame.time_base is not None):
                first_decoded_pts_seconds = float(frame.pts * frame.time_base)
            for decoded in resampler.resample(frame):
                raw = decoded.to_ndarray()[0].astype("<i2", copy=False).tobytes()
                digest.update(raw)
                sample_count += len(raw) // 2
        for decoded in resampler.resample(None):
            raw = decoded.to_ndarray()[0].astype("<i2", copy=False).tobytes()
            digest.update(raw)
            sample_count += len(raw) // 2
    if sample_count <= 0 or first_decoded_pts_seconds is None:
        raise ValueError("generated HLS has no timestamped decoded PCM")
    return (digest.hexdigest(), sample_count, sample_count / startup.PCM_RATE,
            first_decoded_pts_seconds)


def _provenance_digest(provenance):
    fields = (
        "language", "source_wav_sha256", "fixed_wav_clip_sha256", "fixed_wav_pcm_sha256",
        "fixed_wav_pcm_sample_count", "encoded_hls_playlist_sha256", "encoded_segments",
        "pyav_version", "libav_versions",
        "decoded_from_playlist_sha256", "decoded_pcm_sha256", "decoded_pcm_sample_count",
        "decoded_pcm_duration_seconds",
        "first_aac_packet_pts_seconds", "first_decoded_aac_frame_pts_seconds",
        "decoder_initial_offset_seconds", "source_interval_seconds", "aac_lossy",
        "time_stretch_applied", "transcription_accuracy_claim",
    )
    payload = {key: provenance[key] for key in fields}
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256_bytes(data.encode("utf-8"))


def prepare_hls_sample(language, output_dir=DEFAULT_OUTPUT):
    """Create or verify AAC HLS derived from the existing fixed WAV prefix."""
    import av

    fixed = startup.prepare_sample(language, output_dir)
    sample_directory = Path(output_dir) / "samples" / "hls" / language
    playlist_path = sample_directory / "encoded.m3u8"
    provenance_path = sample_directory / "provenance.json"
    segment_pattern = sample_directory / "seg%03d.ts"

    existing_assets = (playlist_path.exists() or provenance_path.exists()
                       or any(sample_directory.glob("seg*.ts")))
    if existing_assets:
        if not playlist_path.is_file() or not provenance_path.is_file():
            raise ValueError("incomplete existing fixed HLS sample; refusing to replace evidence")
    else:
        _encode_fixed_hls(fixed["clip_path"], playlist_path, segment_pattern)

    clip_pcm, _ = _pcm_from_wav(fixed["clip_path"])
    segments, encoded_duration = _parse_encoded_playlist(playlist_path, sample_directory)
    decoded_hash, decoded_count, decoded_duration, decoded_first_pts = (
        _decode_pcm_provenance(playlist_path))
    source_duration = fixed["source_interval_seconds"]["end"]
    if abs(encoded_duration - source_duration) > 0.25:
        raise ValueError("AAC HLS duration differs from the fixed WAV prefix by over 250 ms")
    provenance = {
        "schema_version": 1,
        "language": language,
        "source_wav_sha256": fixed["source_sha256"],
        "fixed_wav_clip_path": str(fixed["clip_path"]),
        "fixed_wav_clip_sha256": fixed["clip_sha256"],
        "fixed_wav_pcm_sha256": _sha256_bytes(clip_pcm),
        "fixed_wav_pcm_sample_count": len(clip_pcm) // 2,
        "source_interval_seconds": fixed["source_interval_seconds"],
        "sample_directory": str(sample_directory),
        "encoded_hls_playlist_path": str(playlist_path),
        "encoded_hls_playlist_sha256": startup._sha256(playlist_path),
        "encoded_segments": segments,
        "encoded_duration_seconds": round(encoded_duration, 6),
        "pyav_version": av.__version__,
        "libav_versions": {name: list(version)
                           for name, version in sorted(av.library_versions.items())},
        "decoded_from_playlist_sha256": startup._sha256(playlist_path),
        "decoded_pcm_sha256": decoded_hash,
        "decoded_pcm_sample_count": decoded_count,
        "decoded_pcm_duration_seconds": round(decoded_duration, 6),
        "decoded_pcm_format": "s16le mono 16000 Hz",
        "first_aac_packet_pts_seconds": segments[0]["audio_start_seconds"],
        "first_decoded_aac_frame_pts_seconds": round(decoded_first_pts, 6),
        "decoder_initial_offset_seconds": round(
            decoded_first_pts - segments[0]["audio_start_seconds"], 6),
        "audio_codec": "AAC",
        "container": "MPEG-TS HLS with H.264 video",
        "aac_lossy": True,
        "time_stretch_applied": False,
        "transcription_accuracy_claim": False,
        "lineage": "hash-pinned fixed WAV prefix -> AAC MPEG-TS segments -> decoded 16 kHz PCM",
    }
    provenance["provenance_sha256"] = _provenance_digest(provenance)
    if provenance_path.is_file():
        previous = json.loads(provenance_path.read_text(encoding="utf-8"))
        if (previous.get("pyav_version") != provenance["pyav_version"]
                or previous.get("libav_versions") != provenance["libav_versions"]):
            raise ValueError("fixed HLS sample uses a different PyAV/libav runtime")
        if previous != provenance:
            raise ValueError("existing fixed HLS media or provenance changed")
    else:
        startup._write_json(provenance_path, provenance)
    return provenance


def _visible_segments(provenance, elapsed_seconds):
    elapsed = max(0.0, float(elapsed_seconds))
    return [segment for segment in provenance["encoded_segments"]
            if segment["end_seconds"] <= elapsed + 0.001]


def render_live_playlist(provenance, case_id, elapsed_seconds):
    """Render the currently available live window; segment queries carry the case ID."""
    if not startup.CASE_RE.fullmatch(str(case_id)) or not str(case_id).startswith(
            f"{provenance['language']}-startup-"):
        raise ValueError("invalid startup case for HLS playlist")
    segments = _visible_segments(provenance, elapsed_seconds)
    target_duration = max(1, math.ceil(max(s["duration_seconds"] for s in provenance["encoded_segments"])))
    lines = [
        "#EXTM3U", "#EXT-X-VERSION:6", f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0", "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for segment in segments:
        lines.extend((
            f"#EXTINF:{segment['duration_seconds']:.6f},",
            f"/{provenance['language']}/{segment['filename']}?startup_case={case_id}",
        ))
    return "\n".join(lines) + "\n"


class PacedHLSOrigin(ThreadingHTTPServer):
    """Loopback media-only origin that publishes completed HLS segments by source time."""
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, samples, log_path):
        self.samples = samples
        self.log_path = Path(log_path)
        self.log_lock = threading.Lock()
        self.case_lock = threading.Lock()
        self.case_started_ns = {}
        self.first_app_audio_body = set()
        super().__init__(address, PacedHLSHandler)

    def start_case(self, case_id, started_ns):
        with self.case_lock:
            return self.case_started_ns.setdefault(case_id, started_ns)

    def case_start(self, case_id):
        with self.case_lock:
            return self.case_started_ns.get(case_id)

    def wait_for_case_start(self, case_id, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            started = self.case_start(case_id)
            if started is not None:
                return started
            time.sleep(0.01)
        return None

    def claim_first_app_audio_body(self, case_id):
        with self.case_lock:
            if case_id in self.first_app_audio_body:
                return False
            self.first_app_audio_body.add(case_id)
            return True

    def record(self, row):
        startup._append_jsonl(self.log_path, row, self.log_lock)


class PacedHLSHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        return

    def _identity(self):
        parts = urlsplit(self.path)
        fields = parts.path.strip("/").split("/")
        query_cases = parse_qs(parts.query).get("startup_case", [])
        if len(fields) == 1 and fields[0].endswith(".m3u8"):
            language = fields[0][:-5]
            kind, segment_index = "playlist", None
        elif len(fields) == 2:
            language = fields[0]
            match = SEGMENT_RE.fullmatch(fields[1])
            kind, segment_index = "audio_video_segment", int(match.group("index")) if match else None
        else:
            return None
        if (language not in self.server.samples or len(query_cases) != 1
                or not startup.CASE_RE.fullmatch(query_cases[0])
                or not query_cases[0].startswith(f"{language}-startup-")):
            return None
        sample = self.server.samples[language]
        if kind == "audio_video_segment" and (
                segment_index is None or segment_index >= len(sample["encoded_segments"])):
            return None
        role = startup._requester_role(self.headers.get("User-Agent", ""),
                                       self.headers.get("Referer"))
        return language, query_cases[0], role, kind, segment_index

    def do_HEAD(self):
        identity = self._identity()
        if identity is None:
            self.send_error(404)
            return
        language, case_id, role, kind, segment_index = identity
        sample = self.server.samples[language]
        if kind == "playlist":
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.server.record(self._request_row(language, case_id, role, kind, {
                "method": "HEAD", "response_status": 200,
                "case_source_clock_started_monotonic_ns": self.server.case_start(case_id),
                "playlist_has_endlist": False,
            }))
        else:
            segment = sample["encoded_segments"][segment_index]
            path = Path(sample["sample_directory"]) / segment["filename"]
            self.send_response(200)
            self.send_header("Content-Type", "video/mp2t")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.server.record(self._request_row(language, case_id, role, kind, {
                "method": "HEAD", "response_status": 200,
                "segment_index": segment_index, "case_query_present": True,
            }))
        self.close_connection = True

    def do_GET(self):
        identity = self._identity()
        if identity is None:
            self.send_error(404)
            self.close_connection = True
            return
        language, case_id, role, kind, segment_index = identity
        request_ns = time.monotonic_ns()
        request_utc = datetime.now(timezone.utc).isoformat()
        if kind == "playlist":
            self._serve_playlist(language, case_id, role, request_ns, request_utc)
        else:
            self._serve_segment(language, case_id, role, segment_index, request_ns, request_utc)

    def _serve_playlist(self, language, case_id, role, request_ns, request_utc):
        if role == "application_stream_source":
            source_clock_ns = self.server.start_case(case_id, request_ns)
        else:
            source_clock_ns = self.server.wait_for_case_start(case_id)
        if source_clock_ns is None:
            self.send_error(503, "application HLS source clock did not start")
            self.close_connection = True
            self.server.record(self._request_row(language, case_id, role, "playlist", {
                "method": "GET", "request_started_utc": request_utc,
                "request_started_monotonic_ns": request_ns,
                "case_source_clock_started_monotonic_ns": None,
                "response_status": 503, "response_complete": False,
                "playlist_has_endlist": False,
            }))
            return
        sample = self.server.samples[language]
        first_end_ns = source_clock_ns + int(sample["encoded_segments"][0]["end_seconds"] * 1e9)
        delay_ns = first_end_ns - time.monotonic_ns()
        if delay_ns > 0:
            time.sleep(delay_ns / 1e9)
        now_ns = time.monotonic_ns()
        elapsed = max(0.0, (now_ns - source_clock_ns) / 1e9)
        visible = _visible_segments(sample, elapsed)
        body = render_live_playlist(sample, case_id, elapsed).encode("utf-8")
        status = "complete"
        try:
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
            status = "client_disconnected"
        finally:
            self.close_connection = True
            self.server.record(self._request_row(language, case_id, role, "playlist", {
                "method": "GET", "request_started_utc": request_utc,
                "request_started_monotonic_ns": request_ns,
                "case_source_clock_started_monotonic_ns": source_clock_ns,
                "source_elapsed_seconds": round(elapsed, 6),
                "response_body_started_monotonic_ns": time.monotonic_ns(),
                "visible_segment_indices": [item["index"] for item in visible],
                "visible_segment_count": len(visible), "playlist_has_endlist": False,
                "playlist_sha256": _sha256_bytes(body), "response_status": 200,
                "response_complete": status == "complete",
            }))

    def _serve_segment(self, language, case_id, role, segment_index, request_ns, request_utc):
        sample = self.server.samples[language]
        segment = sample["encoded_segments"][segment_index]
        source_clock_ns = self.server.wait_for_case_start(case_id)
        status, sent, body_start_ns, body_end_ns = "origin_clock_missing", 0, None, None
        first_app_audio = False
        body_hash = hashlib.sha256()
        path = Path(sample["sample_directory"]) / segment["filename"]
        if source_clock_ns is not None:
            available_ns = source_clock_ns + int(segment["end_seconds"] * 1e9)
            delay_ns = available_ns - time.monotonic_ns()
            if delay_ns > 0:
                time.sleep(delay_ns / 1e9)
            try:
                payload = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "video/mp2t")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                first_app_audio = (
                    role == "application_stream_source"
                    and self.server.claim_first_app_audio_body(case_id))
                body_start_ns = time.monotonic_ns()
                self.wfile.write(payload)
                self.wfile.flush()
                body_hash.update(payload)
                sent = len(payload)
                body_end_ns = time.monotonic_ns()
                status = "complete" if sent == len(payload) else "incomplete"
            except (BrokenPipeError, ConnectionResetError, TimeoutError, OSError):
                status = "client_disconnected"
            finally:
                self.close_connection = True
                self.server.record(self._request_row(language, case_id, role, "audio_video_segment", {
                    "method": "GET", "request_started_utc": request_utc,
                    "request_started_monotonic_ns": request_ns,
                    "case_source_clock_started_monotonic_ns": source_clock_ns,
                    "case_query_present": True,
                    "segment_index": segment_index,
                    "segment_start_seconds": segment["start_seconds"],
                    "segment_duration_seconds": segment["duration_seconds"],
                    "expected_body_sha256": segment["sha256"],
                    "body_sha256": body_hash.hexdigest() if sent else None,
                    "body_bytes_expected": path.stat().st_size,
                    "body_bytes_sent": sent,
                    "first_audio_body_write_started_monotonic_ns": body_start_ns,
                    "first_audio_body_write_completed_monotonic_ns": body_end_ns,
                    "first_app_audio_body": first_app_audio if source_clock_ns is not None else False,
                    "response_status": status,
                    "complete_response": status == "complete",
                }))
        else:
            self.send_error(503, "live source clock did not start")
            self.close_connection = True
            self.server.record(self._request_row(language, case_id, role, "audio_video_segment", {
                "method": "GET", "request_started_utc": request_utc,
                "request_started_monotonic_ns": request_ns,
                "response_status": 503, "complete_response": False,
                "case_query_present": True, "segment_index": segment_index,
            }))

    def _request_row(self, language, case_id, role, kind, fields):
        return {
            "schema_version": 1,
            "event": "origin_http_request",
            "language": language,
            "case_id": case_id,
            "requester_role": role,
            "referer_present": bool(self.headers.get("Referer")),
            "case_query_present": True,
            "resource_kind": kind,
            **fields,
        }


def serve(args):
    if args.host not in LOOPBACKS:
        raise ValueError("HLS media origin must bind to loopback")
    samples = {language: prepare_hls_sample(language, args.output)
               for language in ("zh", "en")}
    log_path = Path(args.output) / "origin-requests.jsonl"
    server = PacedHLSOrigin((args.host, args.port), samples, log_path)
    print(json.dumps({
        "origin": f"http://{args.host}:{server.server_port}",
        "origin_log": str(log_path),
        "samples": {language: sample["provenance_sha256"]
                    for language, sample in samples.items()},
        "recognition_started": False,
        "live_playlist": True,
        "segment_availability": "each completed segment is released at its source-clock end time",
    }, ensure_ascii=False), flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def collect_one(args):
    if not args.allow_real_recognition:
        raise ValueError("collect starts real ASR; pass --allow-real-recognition explicitly")
    provenance = prepare_hls_sample(args.language, args.output)
    hls_runs = Path(args.output) / "hls-runs"
    hls_runs.mkdir(parents=True, exist_ok=True)
    hls_sidecar = hls_runs / f"{args.label}.json"
    if hls_sidecar.exists():
        raise ValueError(f"refusing to overwrite HLS evidence: {hls_sidecar}")
    run = startup.collect_one(args, media_suffix=".m3u8")
    startup._write_json(hls_sidecar, {
        "schema_version": 1,
        "label": args.label,
        "language": args.language,
        "source_wav_sha256": provenance["source_wav_sha256"],
        "fixed_wav_clip_sha256": provenance["fixed_wav_clip_sha256"],
        "fixed_wav_pcm_sha256": provenance["fixed_wav_pcm_sha256"],
        "fixed_wav_pcm_sample_count": provenance["fixed_wav_pcm_sample_count"],
        "hls_provenance_sha256": provenance["provenance_sha256"],
        "encoded_hls_playlist_sha256": provenance["encoded_hls_playlist_sha256"],
        "encoded_segments": provenance["encoded_segments"],
        "decoded_from_playlist_sha256": provenance["decoded_from_playlist_sha256"],
        "pyav_version": provenance["pyav_version"],
        "libav_versions": provenance["libav_versions"],
        "decoded_pcm_sha256": provenance["decoded_pcm_sha256"],
        "decoded_pcm_sample_count": provenance["decoded_pcm_sample_count"],
        "decoded_pcm_duration_seconds": provenance["decoded_pcm_duration_seconds"],
        "first_aac_packet_pts_seconds": provenance["first_aac_packet_pts_seconds"],
        "first_decoded_aac_frame_pts_seconds": provenance[
            "first_decoded_aac_frame_pts_seconds"],
        "decoder_initial_offset_seconds": provenance["decoder_initial_offset_seconds"],
        "aac_lossy": True,
        "time_stretch_applied": False,
        "transcription_accuracy_claim": False,
        "collector_report_path": run.get("report_path"),
    })
    return run


def _read_json(path):
    destination = Path(path)
    if not destination.is_file():
        return {}
    try:
        value = json.loads(destination.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _valid_manifest_observation(row, provenance, case_id):
    elapsed = row.get("source_elapsed_seconds")
    visible = row.get("visible_segment_indices")
    if (row.get("method") != "GET" or row.get("response_status") != 200
            or row.get("playlist_has_endlist") is not False
            or not isinstance(elapsed, (int, float)) or not math.isfinite(elapsed)
            or not isinstance(visible, list)):
        return False
    expected = [item["index"] for item in provenance["encoded_segments"]
                if item["end_seconds"] <= elapsed + 0.001]
    expected_body = render_live_playlist(provenance, case_id, elapsed).encode("utf-8")
    return (visible == expected and bool(visible)
            and row.get("playlist_sha256") == _sha256_bytes(expected_body))


def _visible_caption_ns(report):
    calibration = report.get("clock_calibration") or {}
    sampler_ns = calibration.get("sampler_origin_monotonic_ns")
    sample = startup.stable_caption_sample(report.get("timeline_samples") or [])
    if sample is None or not isinstance(sampler_ns, (int, float)):
        return None
    return int(sampler_ns + sample["observed_after_ms"] * 1e6)


def _validate_case_hls(label, run, output, provenance, requests, decode_rows):
    blockers, normalized, summary = [], None, {"label": label, "validated": False}
    hls_record = _read_json(Path(output) / "hls-runs" / f"{label}.json")
    report = _read_json(run.get("report_path"))
    visible_ns = _visible_caption_ns(report)
    if (run.get("input_pipeline") != "live_hls" or run.get("media_suffix") != ".m3u8"):
        blockers.append(f"{label}: input is not marked as live HLS")
    expected_hls = {
        "source_wav_sha256": provenance["source_wav_sha256"],
        "fixed_wav_clip_sha256": provenance["fixed_wav_clip_sha256"],
        "fixed_wav_pcm_sha256": provenance["fixed_wav_pcm_sha256"],
        "fixed_wav_pcm_sample_count": provenance["fixed_wav_pcm_sample_count"],
        "hls_provenance_sha256": provenance["provenance_sha256"],
        "encoded_hls_playlist_sha256": provenance["encoded_hls_playlist_sha256"],
        "encoded_segments": provenance["encoded_segments"],
        "decoded_from_playlist_sha256": provenance["decoded_from_playlist_sha256"],
        "pyav_version": provenance["pyav_version"],
        "libav_versions": provenance["libav_versions"],
        "decoded_pcm_sha256": provenance["decoded_pcm_sha256"],
        "decoded_pcm_sample_count": provenance["decoded_pcm_sample_count"],
        "decoded_pcm_duration_seconds": provenance["decoded_pcm_duration_seconds"],
        "first_aac_packet_pts_seconds": provenance["first_aac_packet_pts_seconds"],
        "first_decoded_aac_frame_pts_seconds": provenance[
            "first_decoded_aac_frame_pts_seconds"],
        "decoder_initial_offset_seconds": provenance["decoder_initial_offset_seconds"],
        "aac_lossy": True,
        "time_stretch_applied": False,
        "transcription_accuracy_claim": False,
    }
    if any(hls_record.get(name) != value for name, value in expected_hls.items()):
        blockers.append(f"{label}: original/encoded/decoded audio provenance is missing or mismatched")

    case_rows = [row for row in requests if row.get("case_id") == label]
    app = [row for row in case_rows if row.get("requester_role") == "application_stream_source"]
    browser = [row for row in case_rows if row.get("requester_role") == "webui_browser_media_proxy"]
    app_playlists = [row for row in app if row.get("resource_kind") == "playlist"]
    browser_playlists = [row for row in browser if row.get("resource_kind") == "playlist"]
    app_segments = [row for row in app if row.get("resource_kind") == "audio_video_segment"]
    browser_segments = [row for row in browser if row.get("resource_kind") == "audio_video_segment"]
    if not app_playlists or not browser_playlists:
        blockers.append(f"{label}: both application and browser-proxy HLS manifests are required")
    for row in app_playlists + browser_playlists:
        if not _valid_manifest_observation(row, provenance, label):
            blockers.append(f"{label}: manifest exposed future segments or EOF")
            break
        if row.get("case_query_present") is not True:
            blockers.append(f"{label}: manifest request did not carry its case ID")
            break
    if not app_segments or not browser_segments:
        blockers.append(f"{label}: both application and browser-proxy HLS media segments are required")
    if any(row.get("referer_present") is not False for row in app):
        blockers.append(f"{label}: application stream requests must have no Referer")
    if any(row.get("referer_present") is not True for row in browser):
        blockers.append(f"{label}: browser media requests must carry the proxy Referer")
    if any(row.get("case_query_present") is not True for row in app_segments + browser_segments):
        blockers.append(f"{label}: nested segment request lost the case ID")

    app_roots = [row for row in app_playlists if row.get("method") == "GET"
                 and isinstance(row.get("request_started_monotonic_ns"), int)]
    if not app_roots:
        blockers.append(f"{label}: no application HLS manifest GET started the source clock")
        source_clock_ns = None
    else:
        source_clock_ns = min(row["request_started_monotonic_ns"] for row in app_roots)
    manifest_bad = any(row.get("case_source_clock_started_monotonic_ns") != source_clock_ns
                       for row in app_roots)
    if manifest_bad:
        blockers.append(f"{label}: application HLS manifest clock evidence is inconsistent")
    if any(row.get("case_source_clock_started_monotonic_ns") != source_clock_ns
           for row in browser_playlists if row.get("method") == "GET"):
        blockers.append(f"{label}: browser proxy manifest did not share the application source clock")

    first_app_bodies = [row for row in app_segments if row.get("first_app_audio_body") is True]
    if len(first_app_bodies) != 1:
        blockers.append(f"{label}: ambiguous first application audio-body correlation")
        selected = None
    else:
        selected = first_app_bodies[0]
        timestamp = selected.get("first_audio_body_write_started_monotonic_ns")
        if (selected.get("method") != "GET" or selected.get("referer_present") is not False
                or selected.get("case_query_present") is not True
                or selected.get("complete_response") is not True
                or not isinstance(timestamp, int)
                or selected.get("body_sha256") != selected.get("expected_body_sha256")):
            blockers.append(f"{label}: first app audio body is incomplete or unverified")
        else:
            segment_index = selected.get("segment_index")
            segment = next((item for item in provenance["encoded_segments"]
                            if item["index"] == segment_index), None)
            if segment is None or selected.get("body_sha256") != segment["sha256"]:
                blockers.append(f"{label}: first app audio body does not match encoded segment SHA-256")
            elif segment_index != 0:
                blockers.append(f"{label}: application startup skipped the fixed audio prefix")
            elif source_clock_ns is None:
                blockers.append(f"{label}: first app audio body has no source-clock anchor")
            else:
                if timestamp < source_clock_ns + int(segment["end_seconds"] * 1e9) - int(
                        SOURCE_CLOCK_TOLERANCE_SECONDS * 1e9):
                    blockers.append(f"{label}: first app audio body preceded its segment source-clock end")
                normalized = {
                    "case_id": label,
                    "method": "GET",
                    "requester_role": "application_stream_source",
                    "referer_present": False,
                    "resource_kind": "audio_video_segment",
                    "segment_index": segment_index,
                    "origin_first_audio_body_write_started_monotonic_ns": timestamp,
                }
                summary.update({
                    "first_app_audio_segment_index": segment_index,
                    "first_app_audio_body_write_monotonic_ns": timestamp,
                    "first_app_audio_source_clock_delay_ms": round(
                        (timestamp - source_clock_ns) / 1e6, 1),
                })

    for row in app_segments + browser_segments:
        index = row.get("segment_index")
        segment = next((item for item in provenance["encoded_segments"]
                        if item["index"] == index), None)
        if segment is None:
            blockers.append(f"{label}: request refers to an unknown HLS segment")
            break
        timestamp = row.get("first_audio_body_write_started_monotonic_ns")
        is_late_cleanup = (visible_ns is not None and isinstance(timestamp, int)
                           and timestamp > visible_ns and row.get("response_status") == "client_disconnected")
        if is_late_cleanup:
            continue
        if (row.get("response_status") != "complete" or row.get("complete_response") is not True
                or row.get("body_sha256") != segment["sha256"]):
            blockers.append(f"{label}: an app or browser media segment failed integrity")
            break
        row_clock = row.get("case_source_clock_started_monotonic_ns")
        if (source_clock_ns is None or row_clock != source_clock_ns or not isinstance(timestamp, int)
                or timestamp < source_clock_ns + int(segment["end_seconds"] * 1e9)
                - int(SOURCE_CLOCK_TOLERANCE_SECONDS * 1e9)):
            blockers.append(f"{label}: an app or browser segment arrived before its source-clock end")
            break

    browser_report_played = (
        report.get("preparation", {}).get("first_playing_after_ms") is not None
        and any(sample.get("paused") is False
                and isinstance(sample.get("media_time"), (int, float))
                and sample.get("media_time") > 0
                for sample in report.get("timeline_samples") or []))
    if not browser_report_played:
        blockers.append(f"{label}: browser HLS playback was not observed advancing")

    decodes = [row for row in decode_rows if row.get("case_id") == label
               and row.get("event") == "stream_source_pcm_summary"]
    if (len(decodes) != 1 or decodes[0].get("is_live_hls") is not True
            or decodes[0].get("process_id") != run.get("service_pid")):
        blockers.append(f"{label}: first decoded PCM is not uniquely correlated to this service's live HLS")
    elif selected is None or decodes[0].get("first_decoded_pcm_monotonic_ns") is None or (
            decodes[0]["first_decoded_pcm_monotonic_ns"]
            < selected.get("first_audio_body_write_started_monotonic_ns", float("inf"))):
        blockers.append(f"{label}: app audio-body to first-decoded-PCM correlation is missing")

    summary["application_manifest_gets"] = len(app_playlists)
    summary["browser_proxy_manifest_gets"] = len(browser_playlists)
    summary["application_audio_video_segment_gets"] = len(app_segments)
    summary["browser_proxy_audio_video_segment_gets"] = len(browser_segments)
    summary["source_clock_started_monotonic_ns"] = source_clock_ns
    summary["browser_playback_observed"] = browser_report_played
    summary["validated"] = not blockers
    return not blockers, blockers, normalized, summary


def _write_jsonl(path, rows):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                                       for row in rows), encoding="utf-8")


def analyze(args):
    """Validate HLS provenance and requests before using shared startup statistics."""
    output = Path(args.output)
    provenance = prepare_hls_sample(args.language, output)
    runs_dir = output / "runs"
    runs = [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(runs_dir.glob("*.json"))] if runs_dir.is_dir() else []
    runs = [run for run in runs if run.get("language") == args.language]
    requests = startup._read_jsonl(args.origin_log or output / "origin-requests.jsonl")
    decode_rows = startup._read_jsonl(args.decoded_log or output / "decoded-arrivals.jsonl")
    hls_cases, hls_blockers, normalized_rows = [], [], []
    for run in runs:
        valid, blockers, normalized, summary = _validate_case_hls(
            run.get("label"), run, output, provenance, requests, decode_rows)
        hls_cases.append(summary)
        hls_blockers.extend(blockers)
        if normalized is not None and valid:
            normalized_rows.append(normalized)
    all_hls_valid = bool(runs) and len(hls_cases) == len(runs) and all(
        case["validated"] for case in hls_cases)
    if not runs:
        hls_blockers.append("no recorded live-HLS startup cases")
    normalized_path = output / "analysis-inputs" / f"{args.language}-first-app-audio-bodies.jsonl"
    _write_jsonl(normalized_path, normalized_rows)
    common_args = SimpleNamespace(
        language=args.language,
        output=output,
        origin_log=normalized_path,
        decoded_log=args.decoded_log or output / "decoded-arrivals.jsonl",
    )
    result = startup.analyze(common_args, hls_evidence_validated=all_hls_valid)
    hls_by_label = {case["label"]: case for case in hls_cases}
    segment_delays = []
    for case in result["cases"]:
        evidence = hls_by_label.get(case.get("label"), {})
        source_clock_ns = evidence.get("source_clock_started_monotonic_ns")
        report_path = next((run.get("report_path") for run in runs
                            if run.get("label") == case.get("label")), None)
        report = _read_json(report_path) if report_path else {}
        calibration = report.get("clock_calibration") or {}
        sampler_ns = calibration.get("sampler_origin_monotonic_ns")
        source_clock_lags = []
        if isinstance(source_clock_ns, int) and isinstance(sampler_ns, (int, float)):
            for sample in report.get("timeline_samples") or []:
                elapsed, media_time = sample.get("observed_after_ms"), sample.get("media_time")
                if (sample.get("paused") is False and isinstance(elapsed, (int, float))
                        and isinstance(media_time, (int, float))):
                    now_ns = sampler_ns + elapsed * 1e6
                    live_position = min(
                        provenance["source_interval_seconds"]["end"],
                        max(0.0, (now_ns - source_clock_ns) / 1e9),
                    )
                    source_clock_lags.append(live_position - media_time)
        source_clock_lag = startup._median(source_clock_lags)
        if source_clock_lag is not None:
            case["playback_lag_from_origin_clock_median_seconds_proxy"] = round(
                source_clock_lag, 3)
            case["playback_lag_clock_basis"] = (
                "first application manifest GET; includes completed-segment availability delay")
        delay = evidence.get("first_app_audio_source_clock_delay_ms")
        if isinstance(delay, (int, float)):
            segment_delays.append(delay)
    result["hls_media_provenance"] = {
        "source_wav_sha256": provenance["source_wav_sha256"],
        "fixed_wav_clip_sha256": provenance["fixed_wav_clip_sha256"],
        "fixed_wav_pcm_sha256": provenance["fixed_wav_pcm_sha256"],
        "fixed_wav_pcm_sample_count": provenance["fixed_wav_pcm_sample_count"],
        "encoded_hls_playlist_sha256": provenance["encoded_hls_playlist_sha256"],
        "encoded_segments": provenance["encoded_segments"],
        "decoded_from_playlist_sha256": provenance["decoded_from_playlist_sha256"],
        "pyav_version": provenance["pyav_version"],
        "libav_versions": provenance["libav_versions"],
        "decoded_pcm_sha256": provenance["decoded_pcm_sha256"],
        "decoded_pcm_sample_count": provenance["decoded_pcm_sample_count"],
        "decoded_pcm_duration_seconds": provenance["decoded_pcm_duration_seconds"],
        "first_aac_packet_pts_seconds": provenance["first_aac_packet_pts_seconds"],
        "first_decoded_aac_frame_pts_seconds": provenance[
            "first_decoded_aac_frame_pts_seconds"],
        "decoder_initial_offset_seconds": provenance["decoder_initial_offset_seconds"],
        "aac_lossy": True,
        "time_stretch_applied": False,
        "transcription_accuracy_claim": False,
    }
    result["hls_evidence_validated"] = all_hls_valid
    result["hls_gate_blockers"] = sorted(set(hls_blockers))
    result["hls_cases"] = hls_cases
    result["hls_segment_availability_delay_ms"] = {
        "p50": round(startup._median(segment_delays), 1) if segment_delays else None,
        "p95_nearest_rank": round(startup._percentile(segment_delays, 0.95), 1)
        if segment_delays else None,
        "max": round(max(segment_delays), 1) if segment_delays else None,
        "n": len(segment_delays),
        "included_in_startup_latency_gate": False,
    }
    result["hls_audit_logs"] = {
        "full_origin_request_log": str(args.origin_log or output / "origin-requests.jsonl"),
        "normalized_first_app_audio_body_log": str(normalized_path),
        "decoded_pcm_observation_log": str(args.decoded_log or output / "decoded-arrivals.jsonl"),
    }
    if not all_hls_valid:
        result["acceptance_gate_passed"] = False
        result["gate_blockers"] = sorted(set(result["gate_blockers"] + [
            "requires validated fixed-input HLS provenance, app and browser requests, and source-clock pacing",
        ]))
    report_path = Path(result["report_path"])
    startup._write_json(report_path, result)
    return result


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--language", choices=("zh", "en"))
    prepare.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    origin = sub.add_parser("serve")
    origin.add_argument("--host", default="127.0.0.1")
    origin.add_argument("--port", type=int, default=8766)
    origin.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--language", choices=("zh", "en"), required=True)
    collect_parser.add_argument("--label", required=True)
    collect_parser.add_argument("--startup-class", choices=("cold", "warm"), required=True)
    collect_parser.add_argument("--service-pid", type=int, required=True)
    collect_parser.add_argument("--cold-first-session-confirmed", action="store_true")
    collect_parser.add_argument("--origin", default=DEFAULT_ORIGIN)
    collect_parser.add_argument("--seconds", type=float, default=35)
    collect_parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    collect_parser.add_argument("--allow-real-recognition", action="store_true",
                                help="required because this starts a real provider session")
    analyzer = sub.add_parser("analyze")
    analyzer.add_argument("--language", choices=("zh", "en"), required=True)
    analyzer.add_argument("--origin-log", type=Path)
    analyzer.add_argument("--decoded-log", type=Path)
    analyzer.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "prepare":
        languages = (args.language,) if args.language else ("zh", "en")
        results = {language: prepare_hls_sample(language, args.output) for language in languages}
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0
    if args.command == "serve":
        serve(args)
        return 0
    if args.command == "collect":
        if not math.isfinite(args.seconds) or args.seconds <= 0:
            raise ValueError("--seconds must be a finite positive duration")
        result = collect_one(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        collector = result.get("collector_result") or {}
        report_path = result.get("report_path")
        report = _read_json(report_path) if report_path else {}
        controlled_stop = (
            report.get("recording_status") == "cancelled"
            and (report.get("operator_stop") or {}).get("trigger") == "operator_signal"
            and (report.get("operator_stop") or {}).get("delete_status") == 200
            and startup.stable_caption_sample(report.get("timeline_samples") or []) is not None
            and not report.get("errors")
        )
        return 0 if collector.get("exit_ok") or controlled_stop else 1
    result = analyze(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["acceptance_gate_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
