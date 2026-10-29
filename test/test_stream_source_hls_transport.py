"""Source-owned HLS transport at the public decoder boundary."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError
from unittest.mock import MagicMock

import pytest
import httpx

from test_stream_source_hls_startup import _make_hls_fixture
from tools.stream_source import AudioTimelineError, StreamSource


def _observed_playlist(first, count=3, *, pdt=False, extra=b"", endlist=False):
    body = (b"#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-TARGETDURATION:1\n"
            + f"#EXT-X-MEDIA-SEQUENCE:{first}\n".encode() + extra)
    for seq in range(first, first + count):
        if pdt:
            body += f"#EXT-X-PROGRAM-DATE-TIME:2026-10-29T00:00:{seq:02d}.125Z\n".encode()
        body += f"#EXTINF:1.0,\nseg{seq:03d}.ts\n".encode()
    return body + (b"#EXT-X-ENDLIST\n" if endlist else b"")


def _install_decoder_probe(monkeypatch, replies, decoder):
    """Drive the public constructor across its external HTTP and decoder ports."""
    pending = iter(replies)
    requests = []

    def handle_request(_transport, request):
        requests.append(str(request.url))
        reply = next(pending)
        if isinstance(reply, bytes):
            reply = (200, reply, {})
        status, body, headers = reply
        stream = body if isinstance(body, httpx.SyncByteStream) else httpx.ByteStream(body)
        return httpx.Response(status, headers=headers, stream=stream)

    def open_decoder(response, **kwargs):
        decoder(response, kwargs["io_open"])
        container = MagicMock()
        container.streams.audio = [object()]
        return container

    monkeypatch.setattr("httpx.HTTPTransport.handle_request", handle_request)
    monkeypatch.setattr("tools.stream_source.av.open", open_decoder)
    return requests


class _RollingServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, segments, *, cold_delay=0.8, static=False, close_each=False):
        self.segments = segments
        self.cold_delay = cold_delay
        self.started = None
        self.accepted = 0
        self.requests = []
        self.request_headers = []
        self.raw_targets = []
        self.missing_paths = set()
        self.windows = []
        self.window = None
        self.roll_after_body = None
        self.static = static
        self.close_each = close_each
        self.finished = 0
        self.redirect_to = None
        self.extra_headers = {}
        self.gate_tail = None
        self.gate_prefix = 16
        self.gate_path = "/live.m3u8"
        self.tail_waiting = threading.Event()
        super().__init__(("127.0.0.1", 0), _RollingHandler)

    def get_request(self):
        connection, address = super().get_request()
        self.accepted += 1
        return connection, address


class _RollingHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.first_request = True

    def log_message(self, *_args):
        pass

    def finish(self):
        try:
            super().finish()
        finally:
            self.server.finished += 1

    def do_GET(self):
        server = self.server
        if server.started is None:
            server.started = time.monotonic()
        if self.first_request:
            self.first_request = False
            time.sleep(server.cold_delay)
        path = urlsplit(self.path).path
        server.requests.append(path)
        server.request_headers.append({name.lower(): value for name, value in self.headers.items()})
        server.raw_targets.append(self.path)
        if path in server.missing_paths:
            self.send_error(404)
            return
        if path == "/redirect.m3u8":
            self.send_response(302)
            self.send_header("Location", server.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/live.m3u8":
            if server.window is None:
                last = (len(server.segments) - 1 if server.static else min(
                    5 + int(time.monotonic() - server.started), len(server.segments) - 1))
                first = 0 if server.static else last - 5
                endlist = last == len(server.segments) - 1
            else:
                first, last, endlist = server.window
            server.windows.append((first, last))
            lines = ["#EXTM3U", "#EXT-X-VERSION:6", "#EXT-X-TARGETDURATION:1",
                     f"#EXT-X-MEDIA-SEQUENCE:{first}", "#EXT-X-INDEPENDENT-SEGMENTS"]
            for segment in server.segments[first:last + 1]:
                lines.extend((f"#EXTINF:{segment['playlist_duration']:.6f},", segment["name"]))
            if endlist:
                lines.append("#EXT-X-ENDLIST")
            body = ("\n".join(lines) + "\n").encode("ascii")
            content_type = "application/vnd.apple.mpegurl"
        else:
            segment = next((item for item in server.segments if path == "/" + item["name"]), None)
            if segment is None:
                self.send_error(404)
                return
            body, content_type = segment["payload"], "video/mp2t"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in server.extra_headers.items():
            self.send_header(name, value)
        if server.close_each:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        try:
            if path == server.roll_after_body:
                for index, piece in enumerate((body[:188], body[188:376],
                                               body[376:564], body[564:])):
                    if index:
                        time.sleep(2)
                    self.wfile.write(piece)
                    self.wfile.flush()
                server.window = (6, len(server.segments) - 1, True)
            elif server.gate_tail is not None and path == server.gate_path:
                self.wfile.write(body[:server.gate_prefix])
                self.wfile.flush()
                server.tail_waiting.set()
                server.gate_tail.wait(timeout=10)
                self.wfile.write(body[server.gate_prefix:])
            else:
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True


@contextmanager
def _running_origin(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        if server.gate_tail is not None:
            server.gate_tail.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_rolling_hls_preserves_pcm_with_cold_connection_cost(tmp_path):
    """A paced reader retains every segment while the six-item window advances."""
    playlist, segments = _make_hls_fixture(tmp_path, segment_count=18)
    lines = playlist.read_text(encoding="utf-8").splitlines()
    first_segment_line = lines.index(segments[3]["name"]) - 1
    expected_playlist = tmp_path / "expected.m3u8"
    expected_playlist.write_text(
        "\n".join(lines[:5] + lines[first_segment_line:]) + "\n", encoding="utf-8"
    )
    expected = StreamSource(str(expected_playlist))
    expected_pcm = b"".join(expected.iter_chunks())

    server = _RollingServer(segments)
    with _running_origin(server) as origin:
        source = StreamSource(f"{origin}/live.m3u8")
        pcm = bytearray()
        try:
            for chunk in source.iter_chunks():
                pcm.extend(chunk)
                time.sleep(len(chunk) / 32000)
        finally:
            source.close()

    assert len(pcm) == len(expected_pcm), (server.windows, server.requests)
    assert hashlib.sha256(pcm).digest() == hashlib.sha256(expected_pcm).digest()
    assert [path for path in server.requests if path.endswith(".ts")] == [
        "/" + item["name"] for item in segments[3:]
    ]
    assert len(server.windows) >= 3
    assert server.accepted <= 2, server.accepted


def test_delayed_hls_roll_keeps_observed_uris_and_exact_pcm(tmp_path):
    """A six-second body delay cannot hide still-available observed segments."""
    playlist, segments = _make_hls_fixture(tmp_path, segment_count=7)
    lines = playlist.read_text(encoding="utf-8").splitlines()
    first_segment_line = lines.index(segments[3]["name"]) - 1
    expected_playlist = tmp_path / "expected-tail.m3u8"
    expected_playlist.write_text(
        "\n".join(lines[:5] + lines[first_segment_line:]) + "\n", encoding="utf-8"
    )
    expected_pcm = b"".join(StreamSource(str(expected_playlist)).iter_chunks())

    server = _RollingServer(segments, cold_delay=0)
    server.window = (0, 5, False)
    server.roll_after_body = "/seg003.ts"
    with _running_origin(server) as origin:
        source = StreamSource(f"{origin}/live.m3u8")
        pcm = bytearray()
        try:
            for chunk in source.iter_chunks():
                pcm.extend(chunk)
                time.sleep(len(chunk) / 32000)
        finally:
            source.close()

    assert len(pcm) == len(expected_pcm), (server.windows, server.requests)
    assert hashlib.sha256(pcm).digest() == hashlib.sha256(expected_pcm).digest()
    assert [path for path in server.requests if path.endswith(".ts")] == [
        "/" + segment["name"] for segment in segments[3:]
    ]
    assert server.windows == [(0, 5), (6, 6)]
    assert server.accepted == 1


def test_delayed_hls_roll_does_not_replace_a_retained_origin_404_with_audio(tmp_path):
    _playlist, segments = _make_hls_fixture(tmp_path, segment_count=7)
    server = _RollingServer(segments, cold_delay=0)
    server.window = (0, 5, False)
    server.roll_after_body = "/seg003.ts"
    server.missing_paths = {"/seg004.ts"}
    with _running_origin(server) as origin:
        source = StreamSource(origin + "/live.m3u8")
        try:
            with pytest.raises(HTTPError) as failure:
                for chunk in source.iter_chunks():
                    time.sleep(len(chunk) / 32000)
            assert failure.value.code == 404
        finally:
            source.close()
    assert [path for path in server.requests if path.endswith(".ts")] == [
        "/seg003.ts", "/seg004.ts",
    ]


def test_hls_retained_pdt_lines_stay_with_the_following_segment(monkeypatch):
    initial = _observed_playlist(0, pdt=True)
    refreshed = _observed_playlist(3, count=1, pdt=True, endlist=True)
    delivered = []

    def decode(response, open_child):
        assert response.read() == initial
        assert open_child("http://origin.test/seg000.ts", 0, {}).read() == b"segment-zero"
        delivered.append(open_child("http://origin.test/live.m3u8", 0, {}).read())

    requests = _install_decoder_probe(monkeypatch, [initial, b"segment-zero", refreshed], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert delivered == [
        b"#EXTM3U\n#EXT-X-VERSION:6\n#EXT-X-TARGETDURATION:1\n#EXT-X-MEDIA-SEQUENCE:1\n"
        b"#EXT-X-PROGRAM-DATE-TIME:2026-10-29T00:00:01.125Z\n"
        b"#EXTINF:1.0,\nhttp://origin.test/seg001.ts\n"
        b"#EXT-X-PROGRAM-DATE-TIME:2026-10-29T00:00:02.125Z\n"
        b"#EXTINF:1.0,\nhttp://origin.test/seg002.ts\n"
        b"#EXT-X-PROGRAM-DATE-TIME:2026-10-29T00:00:03.125Z\n"
        b"#EXTINF:1.0,\nhttp://origin.test/seg003.ts\n#EXT-X-ENDLIST\n"
    ]
    assert requests == ["http://origin.test/live.m3u8", "http://origin.test/seg000.ts",
                        "http://origin.test/live.m3u8"]


@pytest.mark.parametrize("redirected", [False, True])
def test_hls_merge_preserves_current_tag_order_and_exact_row_tag_text(monkeypatch, redirected):
    initial = _observed_playlist(0).replace(
        b"#EXTINF:1.0,", b'#EXTINF:1.0,URI="literal-title"'
    ).replace(b"\n", b"\r\n")
    refreshed = _observed_playlist(3, count=1, endlist=True).replace(
        b"#EXTINF:1.0,", b'#EXTINF:1.0,URI="current-title"'
    ).replace(b"\n", b"\r\n")
    delivered = []

    def decode(response, open_child):
        assert response.read() == initial
        open_child("http://origin.test/seg000.ts", 0, {}).read()
        delivered.append(open_child("http://origin.test/live.m3u8", 0, {}).read())

    replies = [initial, b"zero"]
    if redirected:
        replies.append((302, b"", {"Location": "http://redirected.test/current/live.m3u8",
                                   "Content-Length": "0"}))
    replies.append(refreshed)
    _install_decoder_probe(monkeypatch, replies, decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert delivered[0].startswith(
        b"#EXTM3U\r\n#EXT-X-VERSION:6\r\n#EXT-X-TARGETDURATION:1\r\n"
        b"#EXT-X-MEDIA-SEQUENCE:1\r\n"
    )
    assert delivered[0].count(b'#EXTINF:1.0,URI="literal-title"\r\n') == 2
    assert b'#EXTINF:1.0,URI="current-title"\r\n' in delivered[0]


@pytest.mark.parametrize("status,headers,read_size,consumed", [
    (200, {}, -1, True),
    (200, {"Content-Range": "bytes 0-5/6"}, -1, False),
    (206, {"Content-Range": "bytes 0-5/6"}, -1, True),
    (206, {"Content-Range": "bytes 1-6/7"}, -1, False),
    (206, {"Content-Range": "bytes 0-5/*"}, -1, False),
    (206, {"Content-Range": "bytes 0-6/7"}, -1, False),
    (206, {}, -1, False),
    (200, {}, 2, False),
    (200, {"Content-Length": "6"}, 6, False),
])
def test_hls_only_acknowledges_complete_representation_eof(
    monkeypatch, status, headers, read_size, consumed
):
    delivered = []

    def decode(response, open_child):
        response.read()
        segment = open_child("http://origin.test/seg000.ts", 0, {})
        expected = b"abcdef"[:read_size] if read_size >= 0 else b"abcdef"
        assert segment.read(read_size) == expected
        segment.close()
        delivered.append(open_child("http://origin.test/live.m3u8", 0, {}).read())

    _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), (status, b"abcdef", headers),
        _observed_playlist(3, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert f"#EXT-X-MEDIA-SEQUENCE:{1 if consumed else 0}\n".encode() in delivered[0]
    assert (b"http://origin.test/seg000.ts\n" in delivered[0]) is not consumed


def test_hls_origin_404_propagates_and_blocks_followup_resource_requests(monkeypatch):
    def decode(response, open_child):
        response.read()
        with pytest.raises(HTTPError) as failure:
            open_child("http://origin.test/seg000.ts", 0, {})
        assert failure.value.code == 404
        with pytest.raises(HTTPError) as repeated:
            open_child("http://origin.test/live.m3u8", 0, {})
        assert repeated.value is failure.value

    requests = _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), (404, b"missing", {}),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert requests.count("http://origin.test/seg000.ts") == 1
    assert len(requests) == 2


def test_hls_recovered_origin_404_blocks_followup_resource_requests(monkeypatch):
    def decode(response, open_child):
        response.read()
        with pytest.raises(HTTPError) as failure:
            open_child("http://origin.test/seg000.ts", 0, {})
        assert failure.value.code == 404
        with pytest.raises(HTTPError) as repeated:
            open_child("http://origin.test/seg001.ts", 0, {})
        assert repeated.value is failure.value
        with pytest.raises(HTTPError) as refreshed:
            open_child("http://origin.test/live.m3u8", 0, {})
        assert refreshed.value is failure.value

    requests = _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), (503, b"temporary", {}), (404, b"missing", {}),
        b"unexpected followup",
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert requests == [
        "http://origin.test/live.m3u8", "http://origin.test/seg000.ts",
        "http://origin.test/seg000.ts",
    ]


def test_hls_segment_read_error_leaves_the_observed_row_pending(monkeypatch):
    class FailedBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b"partial"
            raise httpx.ReadError("fixture media body failure")

    delivered = []

    def decode(response, open_child):
        response.read()
        with pytest.raises(OSError):
            open_child("http://origin.test/seg000.ts", 0, {}).read()
        delivered.append(open_child("http://origin.test/live.m3u8", 0, {}).read())

    requests = _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), (200, FailedBody(), {}),
        _observed_playlist(3, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert b"#EXT-X-MEDIA-SEQUENCE:0\n" in delivered[0]
    assert requests.count("http://origin.test/seg000.ts") == 1


@pytest.mark.parametrize("status,partial,retained", [
    (206, False, True), (206, True, False), (200, False, False),
])
def test_hls_initial_history_requires_a_complete_manifest_representation(
    monkeypatch, status, partial, retained
):
    initial = _observed_playlist(0)
    size = len(initial)
    headers = {"Content-Range": f"bytes 0-{size - 1}/{size + (100 if partial else 0)}"}
    refreshed = _observed_playlist(3, count=1, endlist=True)
    delivered = []

    def decode(response, open_child):
        assert response.read() == initial
        delivered.append(open_child("http://origin.test/live.m3u8", 0, {}).read())

    _install_decoder_probe(monkeypatch, [(status, initial, headers), refreshed], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    if retained:
        assert b"#EXT-X-MEDIA-SEQUENCE:0\n" in delivered[0]
    else:
        assert delivered == [refreshed]


@pytest.mark.parametrize("extra", [
    b'#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\n',
    b'#EXT-X-MAP:URI="init.mp4"\n', b"#EXT-X-BYTERANGE:188@0\n",
    b"#EXT-X-DISCONTINUITY\n", b'#EXT-X-DATERANGE:ID="range"\n',
    b"#EXT-X-GAP\n", b'#EXT-X-PART:DURATION=0.2,URI="part.ts"\n',
    b"#EXT-X-FUTURE-SEMANTICS:1\n", b"#EXTINF:broken,\n",
])
def test_hls_unsupported_refresh_only_passes_while_recovery_is_unneeded(monkeypatch, extra):
    unsupported = _observed_playlist(0, extra=extra)

    def decode(response, open_child):
        response.read()
        assert open_child("http://origin.test/live.m3u8", 0, {}).read() == unsupported
        with pytest.raises(ValueError, match="recover"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), unsupported, _observed_playlist(3, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()


def test_hls_same_uri_at_two_sequences_is_never_acknowledged_or_merged(monkeypatch):
    ambiguous = _observed_playlist(0).replace(b"seg001.ts", b"seg000.ts")

    def decode(response, open_child):
        assert response.read() == ambiguous
        assert open_child("http://origin.test/seg000.ts", 0, {}).read() == b"one-body"
        with pytest.raises(ValueError, match="recover"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    _install_decoder_probe(monkeypatch, [
        ambiguous, b"one-body", _observed_playlist(3, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()


def test_hls_new_same_uri_sequence_during_required_merge_fails_closed(monkeypatch):
    refreshed = _observed_playlist(3, count=1).replace(b"seg003.ts", b"seg001.ts")

    def decode(response, open_child):
        response.read()
        assert open_child("http://origin.test/seg000.ts", 0, {}).read() == b"zero"
        with pytest.raises(ValueError, match="recover") as failure:
            open_child("http://origin.test/live.m3u8", 0, {})
        with pytest.raises(ValueError) as repeated:
            open_child("http://origin.test/seg001.ts", 0, {})
        assert repeated.value is failure.value

    requests = _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), b"zero", refreshed,
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert requests == [
        "http://origin.test/live.m3u8", "http://origin.test/seg000.ts",
        "http://origin.test/live.m3u8",
    ]


def test_hls_same_uri_outside_selected_tail_still_makes_acknowledgement_ambiguous(monkeypatch):
    initial = _observed_playlist(0, count=6).replace(b"seg002.ts", b"seg003.ts")

    def decode(response, open_child):
        response.read()
        open_child("http://origin.test/seg003.ts", 0, {}).read()
        with pytest.raises(ValueError, match="recover"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    _install_decoder_probe(monkeypatch, [
        initial, b"ambiguous", _observed_playlist(6, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()


def test_hls_missing_sequence_after_all_observed_rows_were_consumed_fails(monkeypatch):
    def decode(response, open_child):
        response.read()
        for seq in range(3):
            open_child(f"http://origin.test/seg{seq:03d}.ts", 0, {}).read()
        with pytest.raises(ValueError, match="gap"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), b"zero", b"one", b"two",
        _observed_playlist(4, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()


@pytest.mark.parametrize("changed", [
    _observed_playlist(0).replace(b"seg001.ts", b"conflict.ts"),
    _observed_playlist(4, count=1, endlist=True),
])
def test_hls_conflicting_mapping_or_required_sequence_gap_fails(monkeypatch, changed):
    def decode(response, open_child):
        response.read()
        with pytest.raises(ValueError, match="changed|gap"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    _install_decoder_probe(monkeypatch, [_observed_playlist(0), changed], decode)
    StreamSource("http://origin.test/live.m3u8").close()


def test_hls_completed_rows_are_not_reintroduced_by_overlapping_refresh(monkeypatch):
    initial = _observed_playlist(0)
    delivered = []

    def decode(response, open_child):
        response.read()
        open_child("http://origin.test/seg000.ts", 0, {}).read()
        assert open_child("http://origin.test/live.m3u8", 0, {}).read() == initial
        delivered.append(open_child("http://origin.test/live.m3u8", 0, {}).read())

    _install_decoder_probe(monkeypatch, [
        initial, b"segment", initial, _observed_playlist(3, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert b"#EXT-X-MEDIA-SEQUENCE:1\n" in delivered[0]
    assert b"seg000.ts" not in delivered[0]


def test_hls_master_variants_and_redirect_bases_keep_independent_pending_rows(monkeypatch):
    master = b"#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=200000\na/live.m3u8\n"
    delivered = []

    def decode(response, open_child):
        assert response.read() == master
        open_child("http://origin.test/a/live.m3u8", 0, {}).read()
        open_child("http://origin.test/b/live.m3u8", 0, {}).read()
        open_child("http://redirected.test/media/seg000.ts", 0, {}).read()
        for name in ("a", "b"):
            delivered.append(open_child(f"http://origin.test/{name}/live.m3u8", 0, {}).read())

    _install_decoder_probe(monkeypatch, [
        master,
        (302, b"", {"Location": "http://redirected.test/media/live.m3u8", "Content-Length": "0"}),
        _observed_playlist(0), _observed_playlist(10), b"segment",
        (302, b"", {"Location": "http://redirected.test/new/live.m3u8", "Content-Length": "0"}),
        _observed_playlist(3, count=1, endlist=True),
        _observed_playlist(13, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/master.m3u8").close()
    assert b"#EXT-X-MEDIA-SEQUENCE:1\n" in delivered[0]
    assert b"http://redirected.test/media/seg001.ts\n" in delivered[0]
    assert b"http://redirected.test/new/seg003.ts\n" in delivered[0]
    assert b"http://origin.test/b/" not in delivered[0]
    assert b"#EXT-X-MEDIA-SEQUENCE:10\n" in delivered[1]
    assert b"http://origin.test/b/seg010.ts\n" in delivered[1]
    assert b"redirected.test" not in delivered[1]


def test_hls_same_uri_in_distinct_variants_does_not_acknowledge_either(monkeypatch):
    first = _observed_playlist(0).replace(b"seg000.ts", b"http://origin.test/shared.ts")
    other = _observed_playlist(10).replace(b"seg010.ts", b"http://origin.test/shared.ts")

    def decode(response, open_child):
        response.read()
        open_child("http://origin.test/other.m3u8", 0, {}).read()
        open_child("http://origin.test/shared.ts", 0, {}).read()
        with pytest.raises(ValueError, match="recover"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    _install_decoder_probe(monkeypatch, [
        first, other, b"shared", _observed_playlist(3, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()


@pytest.mark.parametrize("pending_count", [256, 257])
def test_hls_pending_row_budget_never_evicts_known_segments(monkeypatch, pending_count):
    def decode(response, open_child):
        response.read()
        if pending_count > 256:
            with pytest.raises(ValueError, match="budget"):
                open_child("http://origin.test/live.m3u8", 0, {}).read()
        else:
            body = open_child("http://origin.test/live.m3u8", 0, {}).read()
            assert b"#EXT-X-MEDIA-SEQUENCE:0\n" in body
            assert body.count(b"#EXTINF:") == 256

    _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), _observed_playlist(3, pending_count - 3, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()


def test_hls_metadata_budget_counts_retained_uri_bytes_across_refreshes(monkeypatch):
    initial = _observed_playlist(0)
    for seq in range(3):
        initial = initial.replace(f"seg{seq:03d}.ts".encode(), b"a" * 250000 + str(seq).encode())
    refreshed = _observed_playlist(3, count=1, endlist=True).replace(
        b"seg003.ts", b"b" * 300000
    )

    def decode(response, open_child):
        assert response.read() == initial
        with pytest.raises(ValueError, match="budget"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    _install_decoder_probe(monkeypatch, [initial, refreshed], decode)
    StreamSource("http://origin.test/live.m3u8").close()


def test_hls_pending_row_budget_is_shared_by_variants_of_one_source(monkeypatch):
    def decode(response, open_child):
        response.read()
        open_child("http://origin.test/other.m3u8", 0, {}).read()
        with pytest.raises(ValueError, match="budget"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), _observed_playlist(1000),
        _observed_playlist(3, count=251, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()


def test_hls_pending_history_is_not_shared_between_sources(monkeypatch):
    delivered = []
    calls = []

    def decode(response, open_child):
        calls.append(response.read())
        if len(calls) == 2:
            delivered.append(open_child("http://origin.test/live.m3u8", 0, {}).read())

    _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), _observed_playlist(100),
        _observed_playlist(103, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    StreamSource("http://origin.test/live.m3u8").close()
    assert b"#EXT-X-MEDIA-SEQUENCE:100\n" in delivered[0]
    assert b"seg000.ts" not in delivered[0]


@pytest.mark.parametrize("initial", [
    _observed_playlist(0, count=300, endlist=True),
    _observed_playlist(0, count=300, extra=b"#EXT-X-PLAYLIST-TYPE:VOD\n"),
    _observed_playlist(0, count=300, extra=b"#EXT-X-UNKNOWN-SEMANTICS:1\n"),
    b"#EXTM3U\n" + b"#" + b"x" * (1024 * 1024) + b"\n",
    b"#EXTM3U\n#EXT-X-MEDIA-SEQUENCE:0\n#EXTINF:1.0,\n\xff.ts\n",
    b"#EXTM3U\n#EXTINF:bad,\nsegment.ts\n",
])
def test_hls_initial_uncertified_playlists_pass_without_history_caps(monkeypatch, initial):
    refreshed = _observed_playlist(1000, count=300, endlist=True)

    def decode(response, open_child):
        assert response.read() == initial
        assert open_child("http://origin.test/live.m3u8", 0, {}).read() == refreshed

    _install_decoder_probe(monkeypatch, [initial, refreshed], decode)
    StreamSource("http://origin.test/live.m3u8").close()


def test_hls_unneeded_refresh_remains_streaming_and_partial_close_does_not_drain(
    tmp_path, monkeypatch
):
    _playlist, segments = _make_hls_fixture(tmp_path)
    server = _RollingServer(segments, cold_delay=0)
    server.window = (0, 2, False)

    def decode(response, **kwargs):
        response.read()
        server.gate_tail = threading.Event()
        server.gate_prefix = 128
        refreshed = kwargs["io_open"](origin + "/live.m3u8", 0, {})
        assert server.tail_waiting.wait(timeout=1)
        assert refreshed.read(16).startswith(b"#EXTM3U")
        started = time.monotonic()
        refreshed.close()
        assert time.monotonic() - started < 0.3
        container = MagicMock()
        container.streams.audio = [object()]
        return container

    monkeypatch.setattr("tools.stream_source.av.open", decode)
    with _running_origin(server) as origin:
        StreamSource(origin + "/live.m3u8").close()
    assert server.requests == ["/live.m3u8", "/live.m3u8"]


def test_hls_required_merge_waits_for_bounded_body_before_returning_memory_response(
    tmp_path, monkeypatch
):
    _playlist, segments = _make_hls_fixture(tmp_path, segment_count=4)
    server = _RollingServer(segments, cold_delay=0)
    server.window = (0, 2, False)
    elapsed = []

    def decode(response, **kwargs):
        response.read()
        server.window = (3, 3, True)
        server.gate_tail = threading.Event()
        outcome = {}

        def open_refresh():
            try:
                outcome["response"] = kwargs["io_open"](origin + "/live.m3u8", 0, {})
            except BaseException as error:
                outcome["error"] = error

        started = time.monotonic()
        worker = threading.Thread(target=open_refresh, daemon=True)
        worker.start()
        try:
            assert server.tail_waiting.wait(timeout=1)
            time.sleep(0.3)
            assert worker.is_alive() and not outcome
        finally:
            server.gate_tail.set()
            worker.join(timeout=2)
        elapsed.append(time.monotonic() - started)
        assert not worker.is_alive() and "error" not in outcome
        refreshed = outcome["response"]
        assert refreshed.read(16).startswith(b"#EXTM3U")
        close_started = time.monotonic()
        refreshed.close()
        assert time.monotonic() - close_started < 0.1
        container = MagicMock()
        container.streams.audio = [object()]
        return container

    monkeypatch.setattr("tools.stream_source.av.open", decode)
    with _running_origin(server) as origin:
        StreamSource(origin + "/live.m3u8").close()
    assert elapsed[0] >= 0.3
    print(f"required_merge_body_wait_seconds={elapsed[0]:.6f}")
    assert server.requests == ["/live.m3u8", "/live.m3u8"]


def test_hls_merge_body_timeout_propagates_without_replaying_the_request(monkeypatch):
    class TimeoutBody(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield _observed_playlist(3, count=1, endlist=True)[:95]
            raise httpx.ReadTimeout("fixture body read timeout")

        def close(self):
            self.closed = True

    body = TimeoutBody()

    def decode(response, open_child):
        response.read()
        with pytest.raises(TimeoutError, match="HLS HTTP timed out"):
            open_child("http://origin.test/live.m3u8", 0, {}).read()

    requests = _install_decoder_probe(monkeypatch, [
        _observed_playlist(0), (200, body, {}), _observed_playlist(3, count=1, endlist=True),
    ], decode)
    StreamSource("http://origin.test/live.m3u8").close()
    assert requests == ["http://origin.test/live.m3u8"] * 2
    assert body.closed


def test_hls_close_releases_an_active_history_refresh_without_another_request(
    tmp_path, monkeypatch
):
    _playlist, segments = _make_hls_fixture(tmp_path, segment_count=4)
    server = _RollingServer(segments, cold_delay=0)
    server.window = (0, 2, False)
    decoder_port = {}

    def decode(response, **kwargs):
        response.read()
        decoder_port["open"] = kwargs["io_open"]
        container = MagicMock()
        container.streams.audio = [object()]
        return container

    monkeypatch.setattr("tools.stream_source.av.open", decode)
    with _running_origin(server) as origin:
        source = StreamSource(origin + "/live.m3u8")
        server.window = (3, 3, True)
        server.gate_tail = threading.Event()
        finished = threading.Event()

        def open_refresh():
            try:
                response = decoder_port["open"](origin + "/live.m3u8", 0, {})
                response.close()
            except (OSError, ValueError):
                pass
            finally:
                finished.set()

        worker = threading.Thread(target=open_refresh, daemon=True)
        worker.start()
        assert server.tail_waiting.wait(timeout=1)
        started = time.monotonic()
        source.close()
        assert finished.wait(timeout=6)
        worker.join(timeout=1)
        assert time.monotonic() - started < 6.5
        assert server.requests == ["/live.m3u8", "/live.m3u8"]


def test_hls_server_close_still_preserves_pcm(tmp_path):
    playlist, segments = _make_hls_fixture(tmp_path)
    expected_pcm = b"".join(StreamSource(str(playlist)).iter_chunks())
    server = _RollingServer(segments, cold_delay=0, static=True, close_each=True)
    with _running_origin(server) as origin:
        source = StreamSource(f"{origin}/live.m3u8")
        pcm = b"".join(source.iter_chunks())
        source.close()

    assert pcm == expected_pcm
    assert server.accepted == len(server.requests) == 4


@pytest.mark.parametrize("idle_seconds,expected_connections", [(6.1, 1), (30.1, 2)])
def test_hls_idle_reuses_or_expires_connection_without_changing_pcm(
    tmp_path, idle_seconds, expected_connections
):
    playlist, segments = _make_hls_fixture(tmp_path)
    expected_pcm = b"".join(StreamSource(str(playlist)).iter_chunks())
    server = _RollingServer(segments, cold_delay=0, static=True)
    with _running_origin(server) as origin:
        source = StreamSource(f"{origin}/live.m3u8")
        iterator = source.iter_chunks()
        try:
            first = next(iterator)
            requests_before_idle = len(server.requests)
            time.sleep(idle_seconds)
            pcm = first + b"".join(iterator)
        finally:
            iterator.close()
            source.close()

    assert len(server.requests) > requests_before_idle
    assert pcm == expected_pcm
    assert server.accepted == expected_connections


def test_real_six_second_hls_gap_is_still_rejected(tmp_path):
    _playlist, segments = _make_hls_fixture(tmp_path, segment_count=10)
    server = _RollingServer([segments[0], *segments[7:]], cold_delay=0, static=True)
    with _running_origin(server) as origin:
        source = StreamSource(f"{origin}/live.m3u8")
        with pytest.raises(AudioTimelineError, match=r"正向间隙 6\."):
            list(source.iter_chunks())
        source.close()


def test_hls_partial_manifest_close_does_not_drain_or_reuse_body(tmp_path, monkeypatch):
    """A caller that stops probing can close without waiting for a withheld tail."""
    _playlist, segments = _make_hls_fixture(tmp_path)
    server = _RollingServer(segments, cold_delay=0, static=True)
    server.gate_tail = threading.Event()

    def probe_prefix(response, **_kwargs):
        assert response.read(0) == b""
        assert response.read(16).startswith(b"#EXTM3U")
        container = MagicMock()
        container.streams.audio = [object()]
        return container

    monkeypatch.setattr("tools.stream_source.av.open", probe_prefix)
    with _running_origin(server) as origin:
        first = StreamSource(f"{origin}/live.m3u8")
        started = time.monotonic()
        first.close()
        first.close()
        assert time.monotonic() - started < 0.5
        second = StreamSource(f"{origin}/live.m3u8")
        second.close()

    assert server.accepted == server.finished == 2


def test_hls_body_read_timeout_is_bounded_and_never_replays(tmp_path, monkeypatch):
    _playlist, segments = _make_hls_fixture(tmp_path)
    server = _RollingServer(segments, cold_delay=0, static=True)
    server.gate_tail = threading.Event()

    def read_manifest(response, **_kwargs):
        response.read()
        raise AssertionError("withheld response tail must time out")

    monkeypatch.setattr("tools.stream_source.av.open", read_manifest)
    with _running_origin(server) as origin:
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="HLS HTTP timed out"):
            StreamSource(f"{origin}/live.m3u8")
        assert 4.5 <= time.monotonic() - started < 6.5
    assert server.requests == ["/live.m3u8"]


def test_hls_constructor_failure_closes_manifest_and_nested_response(tmp_path, monkeypatch):
    _playlist, segments = _make_hls_fixture(tmp_path)
    server = _RollingServer(segments, cold_delay=0, static=True)
    with _running_origin(server) as origin:
        def fail_after_nested_open(_response, **kwargs):
            child = kwargs["io_open"](f"{origin}/seg000.ts", 0, {})
            assert child.read(1)
            raise ValueError("invalid media fixture")

        monkeypatch.setattr("tools.stream_source.av.open", fail_after_nested_open)
        with pytest.raises(ValueError, match="invalid media fixture"):
            StreamSource(f"{origin}/live.m3u8")
    assert server.accepted == server.finished == 2


def test_hls_redirect_preserves_explicit_headers_and_ignores_set_cookie(
    tmp_path, caplog
):
    playlist, segments = _make_hls_fixture(tmp_path)
    expected_pcm = b"".join(StreamSource(str(playlist)).iter_chunks())
    redirected = _RollingServer(segments, cold_delay=0, static=True)
    # A false encoding marker catches accidental HTTP decompression of media bytes.
    redirected.extra_headers = {"Set-Cookie": "injected=unexpected; Path=/",
                                "Content-Encoding": "gzip"}
    entry = _RollingServer(segments, cold_delay=0)
    supplied = {"User-Agent": "source-test", "Referer": "https://referrer.test/page",
                "Authorization": "Bearer private-header", "Cookie": "explicit=1",
                "Range": "bytes=0-"}
    caplog.set_level("INFO")
    with _running_origin(redirected) as target, _running_origin(entry) as origin:
        entry.redirect_to = f"{target}/live.m3u8"
        source = StreamSource(f"{origin}/redirect.m3u8?token=private-query", http_headers=supplied)
        pcm = b"".join(source.iter_chunks())

    assert pcm == expected_pcm
    assert redirected.requests == ["/live.m3u8", "/seg000.ts", "/seg001.ts", "/seg002.ts"]
    for headers in entry.request_headers + redirected.request_headers:
        assert all(headers[name.lower()] == value for name, value in supplied.items())
        assert headers["accept-encoding"] == "identity"
    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "private-query" not in messages
    assert "private-header" not in messages


def test_hls_does_not_implicitly_retain_origin_cookies(tmp_path):
    _playlist, segments = _make_hls_fixture(tmp_path)
    server = _RollingServer(segments, cold_delay=0, static=True)
    server.extra_headers = {"Set-Cookie": "injected=unexpected; Path=/"}
    with _running_origin(server) as origin:
        list(StreamSource(f"{origin}/live.m3u8").iter_chunks())
    assert all("cookie" not in headers for headers in server.request_headers)


def _clear_proxy_environment(monkeypatch):
    for name in ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
                 "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)


def test_hls_uses_environment_http_proxy_for_manifest_and_segments(tmp_path, monkeypatch):
    _playlist, segments = _make_hls_fixture(tmp_path)
    proxy = _RollingServer(segments, cold_delay=0, static=True)
    _clear_proxy_environment(monkeypatch)
    with _running_origin(proxy) as address:
        monkeypatch.setenv("http_proxy", address)
        source = StreamSource("http://source.invalid/live.m3u8")
        assert b"".join(source.iter_chunks())
    assert len(proxy.requests) == 4
    assert all(target.startswith("http://source.invalid/") for target in proxy.raw_targets)


@pytest.mark.parametrize("bypass_with_port", [False, True])
def test_hls_keeps_urllib_proxy_bypass_and_does_not_enable_all_proxy(
    tmp_path, monkeypatch, bypass_with_port
):
    _playlist, segments = _make_hls_fixture(tmp_path)
    server = _RollingServer(segments, cold_delay=0, static=True)
    unused_proxy = _RollingServer(segments, cold_delay=0, static=True)
    _clear_proxy_environment(monkeypatch)
    with _running_origin(server) as origin, _running_origin(unused_proxy) as proxy:
        monkeypatch.setenv("HTTP_PROXY", proxy)
        monkeypatch.setenv("NO_PROXY", "127.0.0.1" + (
            f":{server.server_port}" if bypass_with_port else ""))
        assert b"".join(StreamSource(f"{origin}/live.m3u8").iter_chunks())
        monkeypatch.delenv("HTTP_PROXY")
        monkeypatch.delenv("NO_PROXY")
        monkeypatch.setenv("ALL_PROXY", proxy)
        assert b"".join(StreamSource(f"{origin}/live.m3u8").iter_chunks())
    assert unused_proxy.requests == []
    assert len(server.requests) == 8
    assert server.accepted == server.finished == 2


@pytest.mark.parametrize("blocked_read", [False, True])
def test_webui_stop_releases_real_hls_source_connections(tmp_path, monkeypatch, blocked_read):
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import webui
    from cloud_translation import CloudConfigStore

    _playlist, segments = _make_hls_fixture(tmp_path)
    server = _RollingServer(segments, cold_delay=0, static=True)
    if blocked_read:
        server.gate_tail = threading.Event()
        server.gate_path = "/seg001.ts"
    started = threading.Event()
    closed = threading.Event()

    class Engine:
        def transcribe_chunk(self, _audio):
            started.set()
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def close(self):
            closed.set()

    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with _running_origin(server) as origin, TestClient(webui.app) as client:
        assert client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"}).status_code == 200
        ticket = client.post("/api/cloud/sessions", json={"url": f"{origin}/live.m3u8"}).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as socket:
            assert started.wait(timeout=3)
            if blocked_read:
                assert server.tail_waiting.wait(timeout=3)
            stop_at = time.monotonic()
            assert client.delete("/api/cloud/sessions/" + ticket["session"]).status_code == 200
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break
            assert time.monotonic() - stop_at < (6.5 if blocked_read else 2)
        assert closed.wait(timeout=1)
        requests_at_end = len(server.requests)
        time.sleep(0.1)
        assert len(server.requests) == requests_at_end
    assert events[-1] == {"type": "end", "reason": "cancelled"}
    assert server.accepted == server.finished
    assert server.accepted <= (2 if blocked_read else 1)
    if blocked_read:
        assert server.requests.count("/seg001.ts") == 1
