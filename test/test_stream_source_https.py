"""TLS trust and nested HTTPS HLS decoding at the public StreamSource seam."""
from fractions import Fraction
import io
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from unittest.mock import MagicMock

import av
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tools.stream_source import StreamSource

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import webui
from cloud_translation import CloudConfigStore


@pytest.fixture(scope="module")
def local_tls_material(tmp_path_factory):
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("OpenSSL is required to create a local HTTPS fixture")

    directory = tmp_path_factory.mktemp("hls-tls")
    certificates = {}
    for label in ("trusted", "untrusted"):
        certificate = directory / f"{label}.pem"
        private_key = directory / f"{label}.key"
        subprocess.run(
            [
                openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                "-keyout", str(private_key), "-out", str(certificate),
                "-sha256", "-days", "3650", "-subj", "/CN=127.0.0.1",
                "-addext", "subjectAltName=IP:127.0.0.1",
                "-addext", "basicConstraints=critical,CA:TRUE",
                "-addext", "keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        certificates[label] = (certificate, private_key)
    return certificates


@pytest.fixture
def https_hls(local_tls_material):
    certificate, private_key = local_tls_material["trusted"]
    segment = _make_mpegts_segment()
    direct_audio = _make_wav()
    resources = {
        "/master.m3u8": (
            "application/vnd.apple.mpegurl",
            b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000\nlevel/index.m3u8\n",
        ),
        "/level/index.m3u8": (
            "application/vnd.apple.mpegurl",
            b"#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:1.0,\nseg0.ts\n#EXT-X-ENDLIST\n",
        ),
        "/level/seg0.ts": ("video/mp2t", segment),
        "/audio.wav": ("audio/wav", direct_audio),
    }
    requested = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requested.append(self.path)
            content_type, body = resources.get(self.path, (None, None))
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(certificate), str(private_key))
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        address, port = server.server_address
        yield f"https://{address}:{port}/master.m3u8", requested
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_trusted_https_hls_decodes_audio_from_nested_playlists(
    https_hls, local_tls_material, monkeypatch
):
    url, requested = https_hls
    trusted_certificate, _ = local_tls_material["trusted"]
    monkeypatch.setenv("SSL_CERT_FILE", str(trusted_certificate))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    original_urlopen = urllib.request.urlopen
    opened_responses = []

    class TrackedResponse:
        def __init__(self, response):
            self.response = response
            self.closed = False
            opened_responses.append(self)

        def read(self, size=-1):
            return self.response.read(size)

        def geturl(self):
            return self.response.geturl()

        def close(self):
            self.closed = True
            self.response.close()

        def __getattr__(self, name):
            return getattr(self.response, name)

    def tracked_urlopen(*args, **kwargs):
        return TrackedResponse(original_urlopen(*args, **kwargs))

    monkeypatch.setattr("tools.stream_source.urllib.request.urlopen", tracked_urlopen)

    source = StreamSource(url, chunk_sec=0.05)
    try:
        chunks = list(source.iter_chunks())
        assert list(source.iter_chunks()) == []
    finally:
        source.close()

    pcm = b"".join(chunks)
    assert len(pcm) >= 3200
    assert np.frombuffer(pcm, dtype=np.int16).any()
    assert source.first_pts_sec is not None
    assert requested == ["/master.m3u8", "/level/index.m3u8", "/level/seg0.ts"]
    assert len(opened_responses) == 3
    assert all(response.closed for response in opened_responses)


def test_untrusted_https_hls_peer_is_rejected(https_hls, local_tls_material, monkeypatch):
    url, requested = https_hls
    untrusted_certificate, _ = local_tls_material["untrusted"]
    monkeypatch.setenv("SSL_CERT_FILE", str(untrusted_certificate))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)

    with pytest.raises(URLError) as failure:
        StreamSource(url)

    assert isinstance(failure.value.reason, ssl.SSLCertVerificationError)
    assert requested == []


def test_hls_manifest_response_is_closed_with_source(monkeypatch):
    class Response:
        closed = False

        def read(self, _size=-1):
            return b""

        def geturl(self):
            return "https://asr.test/live/index.m3u8"

        def close(self):
            self.closed = True

    response = Response()
    container = MagicMock()
    container.streams.audio = [object()]
    monkeypatch.setattr("tools.stream_source.urllib.request.urlopen", lambda *_a, **_k: response)
    monkeypatch.setattr("tools.stream_source.av.open", lambda *_a, **_k: container)

    source = StreamSource("https://asr.test/live/index.m3u8")
    source.close()
    source.close()

    assert response.closed
    container.close.assert_called_once()


def test_hls_manifest_response_is_closed_when_open_fails(monkeypatch):
    class Response:
        closed = False

        def geturl(self):
            return "https://asr.test/live/index.m3u8"

        def close(self):
            self.closed = True

    response = Response()
    monkeypatch.setattr("tools.stream_source.urllib.request.urlopen", lambda *_a, **_k: response)

    def fail_open(*_args, **_kwargs):
        raise RuntimeError("invalid HLS manifest")

    monkeypatch.setattr("tools.stream_source.av.open", fail_open)
    with pytest.raises(RuntimeError, match="invalid HLS manifest"):
        StreamSource("https://asr.test/live/index.m3u8")

    assert response.closed


def test_trusted_https_non_hls_media_still_decodes(
    https_hls, local_tls_material, monkeypatch
):
    playlist_url, _requested = https_hls
    trusted_certificate, _ = local_tls_material["trusted"]
    monkeypatch.setenv("SSL_CERT_FILE", str(trusted_certificate))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)

    media_url = playlist_url.rsplit("/", 1)[0] + "/audio.wav"
    source = StreamSource(media_url)
    try:
        chunks = list(source.iter_chunks())
    finally:
        source.close()

    pcm = b"".join(chunks)
    assert len(pcm) >= 32000
    assert np.frombuffer(pcm, dtype=np.int16).any()


def test_untrusted_https_hls_session_stops_before_recognizer(
    https_hls, local_tls_material, monkeypatch, tmp_path
):
    url, _requested = https_hls
    untrusted_certificate, _ = local_tls_material["untrusted"]
    monkeypatch.setenv("SSL_CERT_FILE", str(untrusted_certificate))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    recognizer_calls = []

    def forbidden_recognizer(*_args, **_kwargs):
        recognizer_calls.append("called")
        raise AssertionError("untrusted media must fail before recognition starts")

    monkeypatch.setattr(webui, "create_engine", forbidden_recognizer)
    with TestClient(webui.app) as client:
        configured = client.put("/api/cloud", json={"api_key": "test-only-placeholder"})
        assert configured.status_code == 200
        ticket = client.post("/api/cloud/sessions", json={"url": url})
        assert ticket.status_code == 200
        with client.websocket_connect("/ws?session=" + ticket.json()["session"]) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    assert any(event["type"] == "error" for event in events)
    assert events[-1] == {"type": "end", "reason": "error"}
    assert recognizer_calls == []


def _make_mpegts_segment():
    output = io.BytesIO()
    with av.open(output, mode="w", format="mpegts") as container:
        stream = container.add_stream("mp2", rate=44100)
        stream.layout = "mono"
        for index in range(5):
            phase = np.arange(1152, dtype=np.float64)
            samples = (np.sin(2 * np.pi * 440 * phase / 44100) * 10000).astype(np.int16)
            frame = av.AudioFrame.from_ndarray(samples[None, :], format="s16p", layout="mono")
            frame.sample_rate = 44100
            frame.pts = index * 1152
            frame.time_base = Fraction(1, 44100)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return output.getvalue()


def _make_wav():
    output = io.BytesIO()
    samples = (np.sin(2 * np.pi * 440 * np.arange(16000) / 16000) * 10000).astype(np.int16)
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(samples.tobytes())
    return output.getvalue()
