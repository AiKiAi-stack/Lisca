"""TLS trust and nested HTTPS HLS decoding at the public StreamSource seam."""
from fractions import Fraction
import io
import shutil
import ssl
import subprocess
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from unittest.mock import MagicMock

import av
import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tools.stream_source import (
    MAX_REDIRECTED_HLS_BYTES, StreamSource, _HlsHttpSession, _rewrite_hls_playlist,
)

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


@pytest.fixture
def rolling_redirect_https_hls(local_tls_material):
    certificate, private_key = local_tls_material["trusted"]
    segment = _make_mpegts_segment()
    state = {"requests": [], "rolling_sequences": []}

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status, content_type, body, headers=()):
            self.send_response(status)
            if content_type:
                self.send_header("Content-Type", content_type)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state["requests"].append(self.path)
            if self.path == "/master.m3u8":
                self._respond(302, None, b"", (("Location", "/cdn/a/master.m3u8"),))
            elif self.path == "/cdn/a/master.m3u8":
                body = (
                    "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=128000\n"
                    "variant/live.m3u8\n"
                ).encode()
                self._respond(200, "application/vnd.apple.mpegurl", body)
            elif self.path == "/cdn/a/variant/live.m3u8":
                self._respond(302, None, b"", (("Location", "/actual/dir/live.m3u8"),))
            elif self.path == "/actual/dir/live.m3u8":
                body = (
                    "#EXTM3U\n#EXT-X-TARGETDURATION:1\n#EXTINF:0.13,\n"
                    "seg0.ts\n#EXT-X-ENDLIST\n"
                ).encode()
                self._respond(200, "application/vnd.apple.mpegurl", body)
            elif self.path == "/actual/dir/seg0.ts" or self.path.startswith("/rolling/seg"):
                self._respond(200, "video/mp2t", segment)
            elif self.path == "/live.m3u8":
                sequence = len(state["rolling_sequences"])
                state["rolling_sequences"].append(sequence)
                body = (
                    "#EXTM3U\n#EXT-X-TARGETDURATION:1\n"
                    f"#EXT-X-MEDIA-SEQUENCE:{sequence}\n"
                    + "".join(
                        f"#EXTINF:0.13,\n/rolling/seg{item}.ts\n"
                        for item in range(sequence, sequence + 3)
                    )
                    + ("#EXT-X-ENDLIST\n" if sequence >= 1 else "")
                ).encode()
                self._respond(
                    200, "application/vnd.apple.mpegurl", body,
                    (("Cache-Control", "no-cache"),),
                )
            else:
                self._respond(404, "text/plain", b"not found")

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
        yield f"https://{address}:{port}", state
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
    original_handle_request = httpx.HTTPTransport.handle_request
    opened_responses = []

    def tracked_request(transport, request):
        response = original_handle_request(transport, request)
        opened_responses.append(response)
        return response

    monkeypatch.setattr("httpx.HTTPTransport.handle_request", tracked_request)

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
    assert all(response.is_closed for response in opened_responses)


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


def test_hls_https_environment_proxy_keeps_system_ca_trust(
    tmp_path, local_tls_material, monkeypatch
):
    from test_stream_source_hls_startup import _make_hls_fixture
    from test_stream_source_hls_transport import (
        _RollingServer, _clear_proxy_environment, _running_origin,
    )

    certificate, private_key = local_tls_material["trusted"]
    _playlist, segments = _make_hls_fixture(tmp_path)
    proxy = _RollingServer(segments, cold_delay=0, static=True)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate), str(private_key))
    proxy.socket = context.wrap_socket(proxy.socket, server_side=True)
    _clear_proxy_environment(monkeypatch)
    monkeypatch.setenv("SSL_CERT_FILE", str(certificate))
    with _running_origin(proxy) as address:
        monkeypatch.setenv("http_proxy", address.replace("http://", "https://", 1))
        pcm = b"".join(StreamSource("http://source.invalid/live.m3u8").iter_chunks())
    assert pcm
    assert len(proxy.requests) == 4
    assert proxy.accepted == proxy.finished == 1


def _install_http_response(monkeypatch, response):
    class Body(httpx.SyncByteStream):
        def __iter__(self):
            while True:
                chunk = response.read(16)
                if not chunk:
                    break
                yield chunk

        def close(self):
            response.close()

    def handle_request(_transport, request):
        if str(request.url) != response.geturl():
            return httpx.Response(302, headers={"Location": response.geturl(),
                                                "Content-Length": "0"}, stream=httpx.ByteStream(b""))
        return httpx.Response(200, headers=getattr(response, "headers", {}), stream=Body())

    monkeypatch.setattr("httpx.HTTPTransport.handle_request", handle_request)


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
    _install_http_response(monkeypatch, response)
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
    _install_http_response(monkeypatch, response)

    def fail_open(*_args, **_kwargs):
        raise RuntimeError("invalid HLS manifest")

    monkeypatch.setattr("tools.stream_source.av.open", fail_open)
    with pytest.raises(RuntimeError, match="invalid HLS manifest"):
        StreamSource("https://asr.test/live/index.m3u8")

    assert response.closed


def test_redirected_nested_playlist_uses_effective_url_for_relative_segment(
    rolling_redirect_https_hls, local_tls_material, monkeypatch
):
    base_url, state = rolling_redirect_https_hls
    trusted_certificate, _ = local_tls_material["trusted"]
    monkeypatch.setenv("SSL_CERT_FILE", str(trusted_certificate))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)

    source = StreamSource(f"{base_url}/master.m3u8", chunk_sec=0.05)
    try:
        pcm = b"".join(source.iter_chunks())
    finally:
        source.close()

    assert len(pcm) >= 3200
    assert "/actual/dir/live.m3u8" in state["requests"]
    assert "/actual/dir/seg0.ts" in state["requests"]
    assert "/cdn/a/variant/seg0.ts" not in state["requests"]


def test_redirected_playlist_rewrites_uri_attributes_and_relative_lines():
    playlist = (
        '\ufeff#EXTM3U\r\n'
        '#EXT-X-KEY:METHOD=AES-128,URI="keys/key.bin?part=a,b"\r\n'
        "#EXT-X-MAP:URI=init.mp4\r\n"
        "../segments/seg0.ts\r\n"
        "https://cdn.example.test/absolute.ts\r\n"
    ).encode("utf-8")

    rewritten = _rewrite_hls_playlist(
        playlist, "https://origin.example.test/actual/dir/live.m3u8"
    )

    assert rewritten.decode("utf-8") == (
        '\ufeff#EXTM3U\r\n'
        '#EXT-X-KEY:METHOD=AES-128,URI="https://origin.example.test/actual/dir/keys/key.bin?part=a,b"\r\n'
        "#EXT-X-MAP:URI=https://origin.example.test/actual/dir/init.mp4\r\n"
        "https://origin.example.test/actual/segments/seg0.ts\r\n"
        "https://cdn.example.test/absolute.ts\r\n"
    )


def test_redirected_binary_resource_is_not_buffered_or_rewritten(monkeypatch):
    class Headers(dict):
        def get_content_type(self):
            return self["Content-Type"]

    class Response:
        headers = Headers({"Content-Type": "video/mp2t"})
        read_calls = 0
        closed = False

        def geturl(self):
            return "https://cdn.example.test/storage/segment.ts"

        def read(self, _size=-1):
            self.read_calls += 1
            return b"segment bytes"

        def close(self):
            self.closed = True

    response = Response()
    source = _bare_stream_source()
    _install_http_response(monkeypatch, response)

    opened = source._open_nested_http("https://origin.example.test/segment.ts", 0, {})

    assert opened.geturl() == response.geturl()
    assert response.read_calls == 0
    assert not response.closed
    opened.close()
    source._hls_http.close()


def test_redirected_manifest_read_is_bounded_and_response_closed(monkeypatch):
    class Headers(dict):
        def get_content_type(self):
            return self["Content-Type"]

    class Response:
        headers = Headers({
            "Content-Type": "application/vnd.apple.mpegurl",
            "Content-Length": str(MAX_REDIRECTED_HLS_BYTES + 8),
        })
        read_bytes = 0
        closed = False

        def geturl(self):
            return "https://cdn.example.test/live.m3u8"

        def read(self, size=-1):
            self.read_bytes += size
            return b"#EXTM3U\n" + b" " * max(0, size - 8)

        def close(self):
            self.closed = True

    response = Response()
    source = _bare_stream_source()
    _install_http_response(monkeypatch, response)

    with pytest.raises(ValueError, match="exceeds the size limit"):
        source._open_nested_http("https://origin.example.test/live.m3u8", 0, {})

    assert response.read_bytes == 16
    assert response.closed
    source._hls_http.close()


def _bare_stream_source():
    source = object.__new__(StreamSource)
    source._http_headers = {"User-Agent": "test"}
    source._tls_context = object()
    source._is_hls = True
    source._closed = False
    source._hls_open_recovery_used = False
    source._hls_terminal_error = None
    source._hls_open_retry_cancel = threading.Event()
    source._hls_open_lock = threading.Lock()
    source._hls_http = _HlsHttpSession(ssl.create_default_context())
    return source


def test_https_live_playlist_refreshes_and_reads_new_segments(
    rolling_redirect_https_hls, local_tls_material, monkeypatch
):
    base_url, state = rolling_redirect_https_hls
    trusted_certificate, _ = local_tls_material["trusted"]
    monkeypatch.setenv("SSL_CERT_FILE", str(trusted_certificate))
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    monkeypatch.delenv("REQUESTS_CA_BUNDLE", raising=False)
    monkeypatch.delenv("CURL_CA_BUNDLE", raising=False)
    monkeypatch.setattr("tools.stream_source.OPEN_TIMEOUT_SEC", 1.0)
    monkeypatch.setattr("tools.stream_source.READ_TIMEOUT_SEC", 1.0)

    source = StreamSource(f"{base_url}/live.m3u8", chunk_sec=0.05)
    iterator = source.iter_chunks()
    pcm = bytearray()
    deadline = time.monotonic() + 3.0
    try:
        while time.monotonic() < deadline:
            try:
                pcm.extend(next(iterator))
            except StopIteration:
                break
    finally:
        iterator.close()
        source.close()

    assert len(state["rolling_sequences"]) >= 2
    assert state["rolling_sequences"][-1] > state["rolling_sequences"][0]
    assert "/rolling/seg3.ts" in state["requests"]
    assert len(pcm) >= 3200


@pytest.mark.parametrize("failure_stage", ["seek", "resampler"])
def test_hls_constructor_failure_closes_opened_resources(
    monkeypatch, tmp_path, failure_stage
):
    class Response:
        closed = False

        def geturl(self):
            return "https://asr.test/live/index.m3u8"

        def close(self):
            self.closed = True

    response = Response()
    container = MagicMock()
    container.streams.audio = [object()]
    ca_file = tmp_path / "ca-bundle.pem"
    ca_file.write_text("temporary test CA bundle", encoding="ascii")
    monkeypatch.setattr(
        "tools.stream_source._https_trust",
        lambda: (object(), str(ca_file), str(ca_file)),
    )
    _install_http_response(monkeypatch, response)
    monkeypatch.setattr("tools.stream_source.av.open", lambda *_a, **_k: container)

    if failure_stage == "seek":
        container.seek.side_effect = RuntimeError("seek failed")
        start_sec = 1
    else:
        def fail_resampler(**_kwargs):
            raise RuntimeError("resampler failed")

        monkeypatch.setattr("tools.stream_source.av.AudioResampler", fail_resampler)
        start_sec = 0

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        StreamSource("https://asr.test/live/index.m3u8", start_sec=start_sec)

    assert container.close.called
    assert response.closed
    assert not ca_file.exists()


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
