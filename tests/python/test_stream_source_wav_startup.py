import hashlib
import io
import struct
import threading
import time
import wave
from fractions import Fraction
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import av
import numpy as np

from tools.stream_source import StreamSource


SAMPLE_RATE = 16000
CHUNK_BYTES = SAMPLE_RATE * 2 // 10
PREFIX_SECONDS = 2.4
TAIL_GATE_HOLD_SECONDS = 6
SOURCE_SECONDS = 4


def _pcm_fixture():
    samples = struct.pack("<8h", 0, 1234, -1234, 9999, -9999, 32767, -32768, 321)
    return samples * (SAMPLE_RATE * SOURCE_SECONDS // 8)


def _wav_fixture(pcm):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(SAMPLE_RATE)
        writer.writeframes(pcm)
    wav_bytes = buffer.getvalue()
    offset = 12
    while offset + 8 <= len(wav_bytes):
        chunk_name = wav_bytes[offset:offset + 4]
        chunk_size = int.from_bytes(wav_bytes[offset + 4:offset + 8], "little")
        if chunk_name == b"data":
            return wav_bytes, offset + 8
        offset += 8 + chunk_size + (chunk_size & 1)
    raise AssertionError("generated WAV has no data chunk")


def _mpegts_fixture():
    output = io.BytesIO()
    with av.open(output, mode="w", format="mpegts") as container:
        stream = container.add_stream("mp2", rate=44100)
        stream.layout = "mono"
        phase = np.arange(1152, dtype=np.float64)
        samples = (np.sin(2 * np.pi * 440 * phase / 44100) * 10000).astype(np.int16)
        for index in range(20):
            frame = av.AudioFrame.from_ndarray(
                samples[None, :], format="s16p", layout="mono"
            )
            frame.sample_rate = 44100
            frame.pts = index * 1152
            frame.time_base = Fraction(1, 44100)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


class _GatedWavServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, wav_bytes, data_offset):
        self.wav_bytes = wav_bytes
        self.data_offset = data_offset
        self.prefix_bytes = int(PREFIX_SECONDS * SAMPLE_RATE * 2)
        self.prefix_sent = threading.Event()
        self.release_tail = threading.Event()
        super().__init__(address, _GatedWavHandler)


class _GatedWavHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        wav_bytes = self.server.wav_bytes
        pcm = memoryview(wav_bytes)[self.server.data_offset:]
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav_bytes)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(wav_bytes[:self.server.data_offset])
            self.wfile.flush()
            for offset in range(0, self.server.prefix_bytes, CHUNK_BYTES):
                part = pcm[offset:offset + CHUNK_BYTES]
                self.wfile.write(part)
                self.wfile.flush()
                if offset + len(part) < self.server.prefix_bytes:
                    time.sleep(0.1)
            self.server.prefix_sent.set()
            self.server.release_tail.wait(timeout=TAIL_GATE_HOLD_SECONDS)
            for offset in range(self.server.prefix_bytes, len(pcm), CHUNK_BYTES):
                part = pcm[offset:offset + CHUNK_BYTES]
                self.wfile.write(part)
                self.wfile.flush()
                if offset + len(part) < len(pcm):
                    time.sleep(0.1)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.close_connection = True


class _StaticMediaServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, payload, content_type):
        self.payload = payload
        self.content_type = content_type
        super().__init__(address, _StaticMediaHandler)


class _StaticMediaHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", self.server.content_type)
        self.send_header("Content-Length", str(len(self.server.payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(self.server.payload)


def test_remote_wav_yields_before_gated_tail_and_preserves_all_pcm():
    expected_pcm = _pcm_fixture()
    wav_bytes, data_offset = _wav_fixture(expected_pcm)
    server = _GatedWavServer(("127.0.0.1", 0), wav_bytes, data_offset)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    first_chunk_ready = threading.Event()
    result = {}
    url = f"http://127.0.0.1:{server.server_port}/Mixed.WAV?token=probe"

    def decode():
        source = iterator = None
        try:
            source = StreamSource(url)
            iterator = iter(source.iter_chunks())
            first_chunk = next(iterator)
            result["first_yield_ns"] = time.monotonic_ns()
            first_chunk_ready.set()
            result["pcm"] = first_chunk + b"".join(iterator)
        except Exception as error:  # surfaced in the test thread below
            result["error"] = error
            first_chunk_ready.set()
        finally:
            if iterator is not None:
                iterator.close()
            if source is not None:
                source.close()

    decode_thread = threading.Thread(target=decode, daemon=True)
    decode_thread.start()
    tail_released_ns = None
    try:
        assert server.prefix_sent.wait(timeout=5), "origin did not send the gated WAV prefix"
        first_chunk_ready.wait(timeout=1)
        tail_released_ns = time.monotonic_ns()
        yielded_before_tail = (
            "first_yield_ns" in result and result["first_yield_ns"] < tail_released_ns
        )
        server.release_tail.set()
        decode_thread.join(timeout=10)
        assert not decode_thread.is_alive(), "decoder did not drain after tail release"
        assert "error" not in result, repr(result.get("error"))
        assert yielded_before_tail, "first PCM arrived only after the origin released its tail"
        decoded_pcm = result["pcm"]
        assert len(decoded_pcm) == len(expected_pcm)
        assert hashlib.sha256(decoded_pcm).digest() == hashlib.sha256(expected_pcm).digest()
    finally:
        server.release_tail.set()
        decode_thread.join(timeout=10)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


def test_non_wav_remote_and_local_sources_keep_existing_pcm(monkeypatch, tmp_path):
    expected_pcm = _pcm_fixture()
    wav_bytes, _data_offset = _wav_fixture(expected_pcm)
    local_wav = tmp_path / "local.wav"
    local_wav.write_bytes(wav_bytes)
    mpegts_bytes = _mpegts_fixture()
    local_mpegts = tmp_path / "local.ts"
    local_mpegts.write_bytes(mpegts_bytes)

    server = _StaticMediaServer(("127.0.0.1", 0), mpegts_bytes, "video/MP2T")
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    real_open = av.open
    opened_options = []

    def observe_open(*args, **kwargs):
        opened_options.append(dict(kwargs.get("options") or {}))
        return real_open(*args, **kwargs)

    monkeypatch.setattr("tools.stream_source.av.open", observe_open)
    sources = (
        str(local_wav),
        str(local_mpegts),
        f"http://127.0.0.1:{server.server_port}/audio.ts?token=probe",
    )
    try:
        decoded_sources = []
        for source_path in sources:
            source = StreamSource(source_path)
            try:
                decoded_pcm = b"".join(source.iter_chunks())
            finally:
                source.close()
            decoded_sources.append(decoded_pcm)
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert len(decoded_sources[0]) == len(expected_pcm)
    assert hashlib.sha256(decoded_sources[0]).digest() == hashlib.sha256(expected_pcm).digest()
    assert len(decoded_sources[1]) > 0
    assert decoded_sources[2] == decoded_sources[1]
    assert len(opened_options) == 3
    assert all("analyzeduration" not in options for options in opened_options)
