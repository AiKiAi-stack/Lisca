"""A paced live HLS source yields its first PCM before later segments arrive."""
from fractions import Fraction
import hashlib
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import av
import numpy as np

from lisca.stream_source import StreamSource


SAMPLE_RATE = 16000
SEGMENT_COUNT = 3
FIRST_PCM_LIMIT_SECONDS = 0.9
GATE_TIMEOUT_SECONDS = 8


def _make_hls_fixture(directory: Path, segment_count=SEGMENT_COUNT):
    """Create a short, real H.264/AAC transport-stream HLS asset."""
    playlist_path = directory / "all.m3u8"
    segment_pattern = directory / "seg%03d.ts"
    audio = (np.sin(2 * np.pi * 440 * np.arange(SAMPLE_RATE * segment_count) / SAMPLE_RATE)
             * 0.3).astype(np.float32)

    with av.open(str(playlist_path), mode="w", format="hls", options={
            "hls_time": "1",
            "hls_list_size": "0",
            "hls_flags": "independent_segments",
            "hls_segment_filename": str(segment_pattern),
    }) as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 160, 90, "yuv420p"
        video.codec_context.gop_size = 10
        video.codec_context.max_b_frames = 0
        video.codec_context.thread_count = 1
        video.options = {"preset": "ultrafast", "tune": "zerolatency", "sc_threshold": "0"}
        audio_stream = output.add_stream("aac", rate=SAMPLE_RATE)
        audio_stream.layout = "mono"
        audio_stream.bit_rate = 64000

        for index in range(segment_count * 10):
            frame = av.VideoFrame.from_ndarray(
                np.full((90, 160, 3), (20, 24, 28), dtype=np.uint8), format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame):
                output.mux(packet)

            start = index * SAMPLE_RATE // 10
            block = audio[start:start + SAMPLE_RATE // 10]
            pcm_frame = av.AudioFrame.from_ndarray(block[None, :], format="fltp", layout="mono")
            pcm_frame.sample_rate = SAMPLE_RATE
            pcm_frame.pts, pcm_frame.time_base = start, Fraction(1, SAMPLE_RATE)
            for packet in audio_stream.encode(pcm_frame):
                output.mux(packet)

        for stream in (video, audio_stream):
            for packet in stream.encode(None):
                output.mux(packet)

    playlist_lines = playlist_path.read_text(encoding="utf-8").splitlines()
    segments = []
    for index, line in enumerate(playlist_lines):
        if not line.startswith("#EXTINF:"):
            continue
        duration = float(line.split(":", 1)[1].rstrip(","))
        name = playlist_lines[index + 1]
        segment_path = directory / name
        with av.open(str(segment_path), format="mpegts") as container:
            stream = container.streams.audio[0]
            assert stream.codec_context.name == "aac"
            assert any(item.codec_context.name == "h264" for item in container.streams.video)
            frame_samples = stream.codec_context.frame_size or 1024
            audio_ends = []
            for packet in container.demux(stream):
                if packet.size <= 0 or packet.pts is None or packet.time_base is None:
                    continue
                duration_sec = (Fraction(packet.duration) * Fraction(packet.time_base)
                                if packet.duration > 0 else Fraction(
                                    frame_samples, stream.codec_context.sample_rate))
                audio_ends.append(Fraction(packet.pts) * Fraction(packet.time_base) + duration_sec)
        assert audio_ends, f"{name} contains no AAC packets"
        segments.append({
            "name": name,
            "payload": segment_path.read_bytes(),
            "playlist_duration": duration,
            "audio_end_seconds": float(max(audio_ends)),
        })

    assert len(segments) == segment_count, playlist_path.read_text(encoding="utf-8")
    return playlist_path, segments


def _media_playlist(segments, prefix, endlist):
    target_duration = math.ceil(max(item["playlist_duration"] for item in segments))
    lines = [
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        f"#EXT-X-TARGETDURATION:{target_duration}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-INDEPENDENT-SEGMENTS",
    ]
    for segment in segments:
        lines.extend((
            f"#EXTINF:{segment['playlist_duration']:.6f},",
            f"/{prefix}/{segment['name']}",
        ))
    if endlist:
        lines.append("#EXT-X-ENDLIST")
    return ("\n".join(lines) + "\n").encode("ascii")


class _PacedHlsServer(ThreadingHTTPServer):
    """Loopback HLS origin that gates future media bodies by time and event."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, segments):
        self.segments = segments
        self.requests = []
        self.release_future_segments = threading.Event()
        self.first_paced_body = threading.Event()
        self.clock_started_ns = None
        self.first_paced_body_ns = None
        self.future_release_ns = None
        self.first_future_body_ns = None
        self.first_future_segment_index = -1
        super().__init__(address, _PacedHlsHandler)


class _PacedHlsHandler(BaseHTTPRequestHandler):
    """Serve the fixture as nested HLS manifests and gated TS segments."""

    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        pass

    def _respond(self, body, content_type, before_body=None):
        """Write one complete HTTP response, timestamping its body if requested."""
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if before_body is not None:
            before_body()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        """Serve a manifest or one of its media segments."""
        path = urlsplit(self.path).path
        self.server.requests.append(path)
        if path == "/paced/master.m3u8":
            if self.server.clock_started_ns is None:
                self.server.clock_started_ns = time.monotonic_ns()
            self._respond(
                ("#EXTM3U\n#EXT-X-VERSION:6\n"
                 "#EXT-X-STREAM-INF:BANDWIDTH=200000\n/paced/variant.m3u8\n").encode(),
                "application/vnd.apple.mpegurl",
            )
            return

        if path == "/paced/variant.m3u8":
            self._respond(
                _media_playlist(self.server.segments, "paced",
                                endlist=self.server.release_future_segments.is_set()),
                "application/vnd.apple.mpegurl",
            )
            return

        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "paced":
            name = parts[1]
            segment_index = next((index for index, segment in enumerate(self.server.segments)
                                  if segment["name"] == name), None)
            if segment_index is None:
                self.send_error(404)
                return
            segment = self.server.segments[segment_index]
            if segment_index > 0:
                if not self.server.release_future_segments.wait(GATE_TIMEOUT_SECONDS):
                    self.send_error(504)
                    return
            deadline_ns = (self.server.clock_started_ns
                           + int(segment["audio_end_seconds"] * 1e9))
            while time.monotonic_ns() < deadline_ns:
                time.sleep(min(0.01, (deadline_ns - time.monotonic_ns()) / 1e9))

            def before_body():
                now_ns = time.monotonic_ns()
                if segment_index == 0:
                    self.server.first_paced_body_ns = now_ns
                    self.server.first_paced_body.set()
                elif self.server.first_future_body_ns is None:
                    self.server.first_future_body_ns = now_ns
                    self.server.first_future_segment_index = segment_index

            self._respond(segment["payload"], "video/mp2t", before_body=before_body)
            return

        self.send_error(404)


def test_live_hls_yields_before_gated_segments_and_preserves_full_pcm(tmp_path):
    """First PCM precedes gated media; releasing the tail preserves exact PCM."""
    playlist_path, segments = _make_hls_fixture(tmp_path)
    server = _PacedHlsServer(("127.0.0.1", 0), segments)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"

    baseline = StreamSource(str(playlist_path))
    try:
        expected_pcm = b"".join(baseline.iter_chunks())
        expected_first_pts = baseline.first_pts_sec
    finally:
        baseline.close()
    expected_digest = hashlib.sha256(expected_pcm).digest()

    first_pcm_ready = threading.Event()
    result = {}

    def decode_paced_source():
        source = iterator = None
        try:
            source = StreamSource(f"{base_url}/paced/master.m3u8")
            iterator = iter(source.iter_chunks())
            first = next(iterator)
            result["first_pcm_ns"] = time.monotonic_ns()
            result["first_pts_sec"] = source.first_pts_sec
            first_pcm_ready.set()
            result["pcm"] = first + b"".join(iterator)
        except Exception as error:  # surfaced in the test thread below
            result["error"] = error
            first_pcm_ready.set()
        finally:
            if iterator is not None:
                iterator.close()
            if source is not None:
                source.close()

    decode_thread = threading.Thread(target=decode_paced_source, daemon=True)
    decode_thread.start()
    try:
        assert server.first_paced_body.wait(timeout=5), (
            "origin did not make HLS segment 0 available"
        )
        first_pcm_before_release = first_pcm_ready.wait(timeout=FIRST_PCM_LIMIT_SECONDS)
        server.future_release_ns = time.monotonic_ns()
        server.release_future_segments.set()
        decode_thread.join(timeout=15)

        assert not decode_thread.is_alive(), "live HLS decoder did not finish after segment release"
        assert "error" not in result, repr(result.get("error"))
        assert len(result["pcm"]) == len(expected_pcm)
        assert hashlib.sha256(result["pcm"]).digest() == expected_digest
        segment_requests = [path for path in server.requests if path.startswith("/paced/seg")]
        assert segment_requests[0] == "/paced/seg000.ts"
        assert result["first_pts_sec"] == expected_first_pts
        assert expected_first_pts == 0
        assert server.first_paced_body_ns >= server.clock_started_ns + int(
            segments[0]["audio_end_seconds"] * 1e9
        )
        assert server.first_future_body_ns is not None
        assert server.first_future_body_ns >= server.future_release_ns
        assert server.first_future_segment_index >= 1
        assert server.first_future_body_ns >= server.clock_started_ns + int(
            segments[server.first_future_segment_index]["audio_end_seconds"] * 1e9
        )
        assert first_pcm_before_release, "first PCM waited for a later HLS segment"
        assert result["first_pcm_ns"] - server.first_paced_body_ns <= int(
            FIRST_PCM_LIMIT_SECONDS * 1e9
        )
    finally:
        server.release_future_segments.set()
        decode_thread.join(timeout=15)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
