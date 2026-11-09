"""Bounded recovery for HLS HTTP opens before response bodies are read."""
from fractions import Fraction
import io
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest

from lisca.stream_source import OPEN_TIMEOUT_SEC, READ_TIMEOUT_SEC, StreamSource


HLS_URL = "https://media.example.test/live/index.m3u8?token=secret-url"
AUTH_HEADER = "Bearer secret-header"


class _Headers(dict):
    def get_content_type(self):
        return self.get("Content-Type", "application/octet-stream").split(";", 1)[0]


class _Response(httpx.SyncByteStream):
    def __init__(self, url, body=b"segment", content_type="video/mp2t", read_error=None):
        self.url = url
        self.body = body
        self.content_type = content_type
        self.read_error = read_error
        self.offset = 0
        self.read_calls = 0
        self.closed = False
        self.headers = _Headers({"Content-Type": content_type})

    def __iter__(self):
        while True:
            chunk = self.read(1)
            if not chunk:
                break
            yield chunk

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_calls += 1
        if self.read_error is not None and self.read_calls > 1:
            raise self.read_error
        end = len(self.body) if size is None or size < 0 else self.offset + size
        value = self.body[self.offset:end]
        self.offset += len(value)
        return value

    def close(self):
        self.closed = True


def _http_error(url, status, body=None):
    return HTTPError(url, status, "private response text", {}, body or io.BytesIO())


class _BarrierBody:
    def __init__(self, barrier):
        self.barrier = barrier
        self.closed = False

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.barrier.wait(timeout=3)


class _CloseErrorBody:
    def __init__(self):
        self.close_attempted = False

    def close(self):
        if self.close_attempted:
            return
        self.close_attempted = True
        raise OSError("private close detail")


def _pcm_frame(value, pts=0, samples=160):
    frame = MagicMock()
    frame.pts = pts
    frame.time_base = Fraction(1, 16000)
    frame.sample_rate = 16000
    frame.to_ndarray.return_value = np.full((1, samples), value, dtype=np.int16)
    return frame


def _install_audio(monkeypatch, frames):
    container = MagicMock()
    container.streams.audio = [object()]
    decoded = [object() for _ in frames]
    container.decode.return_value = decoded
    frame_by_input = dict(zip(decoded, frames))
    resampler = MagicMock()
    resampler.resample.side_effect = lambda frame: (
        [] if frame is None else [frame_by_input[frame]]
    )
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)
    return container


class _ErrorStream(httpx.SyncByteStream):
    def __init__(self, body):
        self.body = body

    def __iter__(self):
        raise AssertionError("HTTP error bodies must be closed without reading")
        yield b""

    def close(self):
        self.body.close()


def _install_http(monkeypatch, outcomes):
    calls = []
    pending = iter(outcomes)

    def handle_request(_transport, request):
        assert request.extensions["timeout"] == {
            "connect": 5.0, "read": 5.0, "write": 5.0, "pool": 5.0,
        }
        calls.append((str(request.url), request.extensions["timeout"]["connect"]))
        outcome = next(pending)
        if isinstance(outcome, HTTPError):
            return httpx.Response(outcome.code, headers=outcome.headers,
                                  stream=_ErrorStream(outcome.fp))
        if isinstance(outcome, BaseException):
            raise outcome
        return httpx.Response(200, headers=outcome.headers, stream=outcome)

    monkeypatch.setattr("httpx.HTTPTransport.handle_request", handle_request)
    return calls


def test_initial_manifest_timeout_recovers_and_yields_original_pcm(monkeypatch, caplog):
    manifest = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    calls = _install_http(
        monkeypatch,
        [httpx.ConnectTimeout("private timeout detail"), manifest],
    )
    frame = _pcm_frame(1234)
    container = _install_audio(monkeypatch, [frame])
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    source = StreamSource(HLS_URL, chunk_sec=0.01, http_headers={"Authorization": AUTH_HEADER})
    try:
        chunks = list(source.iter_chunks())
    finally:
        source.close()

    assert b"".join(chunks) == np.full(160, 1234, dtype=np.int16).tobytes()
    assert len(calls) == 2
    assert all(call[1] == OPEN_TIMEOUT_SEC == 5.0 for call in calls)
    message = " ".join(record.getMessage() for record in caplog.records)
    assert "attempt=1/1" in message
    assert "delay=1s" in message
    assert "timeout" in message.lower()
    assert HLS_URL not in message
    assert "secret-url" not in message
    assert AUTH_HEADER not in message
    assert "private timeout detail" not in message


def test_nested_playlist_open_uses_the_same_retry_boundary(monkeypatch):
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    child_url = "https://media.example.test/live/level.m3u8"
    playlist = _Response(
        child_url,
        b"#EXTM3U\n#EXTINF:1.0,\nsegment.ts\n",
        "application/vnd.apple.mpegurl",
    )
    calls = _install_http(
        monkeypatch,
        [initial, URLError(ConnectionError("private connection detail")), playlist],
    )
    container = _install_audio(monkeypatch, [])

    def av_open(_source, **kwargs):
        child = kwargs["io_open"](child_url, 0, {})
        child.close()
        return container

    monkeypatch.setattr("lisca.stream_source.av.open", av_open)
    source = StreamSource(HLS_URL)
    source.close()

    assert [call[0] for call in calls] == [HLS_URL, child_url, child_url]
    assert playlist.closed


def test_direct_connection_error_recovers_once(monkeypatch):
    manifest = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    calls = _install_http(
        monkeypatch,
        [ConnectionError("private connection detail"), manifest],
    )
    container = _install_audio(monkeypatch, [])
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    source = StreamSource(HLS_URL)
    source.close()

    assert len(calls) == 2


def test_second_open_failure_exhausts_the_single_recovery(monkeypatch):
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    segment_url = "https://media.example.test/live/segment.ts"
    first_failure = TimeoutError("first private failure")
    second_failure = TimeoutError("second private failure")
    calls = _install_http(monkeypatch, [initial, first_failure, second_failure])
    container = _install_audio(monkeypatch, [])

    def av_open(_source, **kwargs):
        kwargs["io_open"](segment_url, 0, {})
        return container

    monkeypatch.setattr("lisca.stream_source.av.open", av_open)
    with pytest.raises(TimeoutError, match="second private failure"):
        StreamSource(HLS_URL)

    assert [call[0] for call in calls] == [HLS_URL, segment_url, segment_url]


def test_successful_other_resource_without_pcm_does_not_reset_budget(monkeypatch):
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    first_url = "https://media.example.test/live/first.ts"
    second_url = "https://media.example.test/live/second.ts"
    first_segment = _Response(first_url)
    calls = _install_http(
        monkeypatch,
        [initial, TimeoutError("first resource timeout"), first_segment,
         TimeoutError("second resource timeout")],
    )
    container = _install_audio(monkeypatch, [])

    def av_open(_source, **kwargs):
        first = kwargs["io_open"](first_url, 0, {})
        first.close()
        kwargs["io_open"](second_url, 0, {})
        return container

    monkeypatch.setattr("lisca.stream_source.av.open", av_open)
    with pytest.raises(TimeoutError, match="second resource timeout"):
        StreamSource(HLS_URL)

    assert [call[0] for call in calls] == [HLS_URL, first_url, first_url, second_url]


def test_new_pcm_resets_recovery_budget_for_later_hls_open(monkeypatch):
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    first_url = "https://media.example.test/live/first.ts"
    second_url = "https://media.example.test/live/second.ts"
    first_segment = _Response(first_url)
    second_segment = _Response(second_url)
    calls = _install_http(
        monkeypatch,
        [initial, TimeoutError("first resource timeout"), first_segment,
         TimeoutError("second resource timeout"), second_segment],
    )
    frames = [_pcm_frame(10, 0), _pcm_frame(20, 160)]
    container = _install_audio(monkeypatch, frames)
    decoded_frames = list(container.decode.return_value)

    def av_open(_source, **kwargs):
        child = kwargs["io_open"](first_url, 0, {})
        child.close()
        io_open = kwargs["io_open"]

        def decode(_stream):
            yield from decoded_frames
            later = io_open(second_url, 0, {})
            later.close()

        container.decode.side_effect = decode
        return container

    monkeypatch.setattr("lisca.stream_source.av.open", av_open)
    source = StreamSource(HLS_URL, chunk_sec=0.01)
    try:
        pcm = b"".join(source.iter_chunks())
    finally:
        source.close()

    assert pcm == np.concatenate((np.full(160, 10, dtype=np.int16),
                                  np.full(160, 20, dtype=np.int16))).tobytes()
    assert [call[0] for call in calls] == [
        HLS_URL, first_url, first_url, second_url, second_url,
    ]


@pytest.mark.parametrize("status", [408, 503])
def test_temporary_http_status_recovers_once(monkeypatch, status):
    manifest = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    failure = _http_error(HLS_URL, status)
    response_body = failure.fp
    calls = _install_http(monkeypatch, [failure, manifest])
    container = _install_audio(monkeypatch, [])
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    source = StreamSource(HLS_URL)
    source.close()

    assert len(calls) == 2
    assert response_body.closed


def test_concurrent_temporary_http_errors_share_one_recovery(monkeypatch):
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    child_url = "https://media.example.test/live/segment.ts"
    close_barrier = threading.Barrier(2)
    first_body = _BarrierBody(close_barrier)
    second_body = _BarrierBody(close_barrier)
    first_error = _http_error(child_url, 503, first_body)
    second_error = _http_error(child_url, 503, second_body)
    recovered = _Response(child_url)
    calls = _install_http(
        monkeypatch, [initial, first_error, second_error, recovered,
                      _Response(child_url)],
    )
    container = _install_audio(monkeypatch, [])
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)
    source = StreamSource(HLS_URL)
    results = []
    failures = []

    def open_resource():
        try:
            response = source._open_nested_http(child_url, 0, {})
            response.close()
            results.append(True)
        except Exception as error:  # captured from workers for deterministic assertions
            failures.append(error)

    threads = [threading.Thread(target=open_resource, daemon=True) for _ in range(2)]
    try:
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=4)

        assert all(not thread.is_alive() for thread in threads)
        assert len(calls) - 1 == 3
        assert len(results) == 1
        assert len(failures) == 1
        assert isinstance(failures[0], HTTPError)
        assert failures[0].code == 503
        assert first_body.closed and second_body.closed
    finally:
        source.close()


def test_exhausted_http_recovery_closes_both_bodies_and_preserves_second_error(monkeypatch):
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    child_url = "https://media.example.test/live/segment.ts"
    first_error = _http_error(child_url, 503)
    second_error = _http_error(child_url, 503)
    first_body = first_error.fp
    second_body = second_error.fp
    calls = _install_http(monkeypatch, [initial, first_error, second_error])
    container = _install_audio(monkeypatch, [])
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)
    source = StreamSource(HLS_URL)

    try:
        with pytest.raises(HTTPError) as caught:
            source._open_nested_http(child_url, 0, {})

        assert caught.value.code == 503
        assert [call[0] for call in calls] == [HLS_URL, child_url, child_url]
        assert first_body.closed and second_body.closed
    finally:
        source.close()


def test_http_error_close_failure_does_not_replace_original(monkeypatch, caplog):
    caplog.set_level("DEBUG", logger="lisca.stream_source")
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    child_url = "https://media.example.test/live/segment.ts"
    body = _CloseErrorBody()
    failure = _http_error(child_url, 403, body)
    calls = _install_http(monkeypatch, [initial, failure])
    container = _install_audio(monkeypatch, [])
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)
    source = StreamSource(HLS_URL)

    try:
        with pytest.raises(HTTPError) as caught:
            source._open_nested_http(child_url, 0, {})

        assert caught.value.code == 403
        assert body.close_attempted
        assert len(calls) == 2
        messages = " ".join(record.getMessage() for record in caplog.records)
        assert "OSError" in messages
        assert "private close detail" not in messages
    finally:
        source.close()


@pytest.mark.parametrize(
    "failure",
    [
        URLError(ssl.SSLCertVerificationError("private certificate detail")),
        _http_error(HLS_URL, 403),
        _http_error(HLS_URL, 404),
        URLError("private non-network failure"),
        ValueError("private invalid input"),
    ],
    ids=["tls-certificate", "http-403", "http-404", "other-url-error", "other-error"],
)
def test_non_temporary_open_failures_are_not_retried(monkeypatch, failure):
    calls = _install_http(monkeypatch, [failure])

    with pytest.raises(type(failure)):
        StreamSource(HLS_URL)

    assert len(calls) == 1


def test_wrapped_httpx_certificate_failure_never_retries(monkeypatch):
    certificate = ssl.SSLCertVerificationError("private certificate detail")
    failure = httpx.ConnectError("private connect detail")
    failure.__cause__ = certificate
    calls = _install_http(monkeypatch, [failure])

    with pytest.raises(URLError) as caught:
        StreamSource(HLS_URL)

    assert caught.value.reason is certificate
    assert len(calls) == 1


def test_body_read_failure_never_reopens_or_replays_segment(monkeypatch):
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    segment_url = "https://media.example.test/live/segment.ts"
    segment = _Response(segment_url, body=b"partial", read_error=httpx.ReadTimeout("body timeout"))
    calls = _install_http(monkeypatch, [initial, segment])
    container = _install_audio(monkeypatch, [_pcm_frame(1)])

    def av_open(_source, **kwargs):
        response = kwargs["io_open"](segment_url, 0, {})

        def decode(_stream):
            try:
                assert response.read(1) == b"p"
                response.read(1)
                yield object()
            finally:
                response.close()

        container.decode.side_effect = decode
        return container

    monkeypatch.setattr("lisca.stream_source.av.open", av_open)
    source = StreamSource(HLS_URL)
    try:
        with pytest.raises(TimeoutError, match="HLS HTTP timed out"):
            list(source.iter_chunks())
    finally:
        source.close()

    assert [call[0] for call in calls] == [HLS_URL, segment_url]
    assert segment.read_calls == 2


def test_close_cancels_retry_wait_without_a_second_get(monkeypatch):
    initial = _Response(HLS_URL, b"#EXTM3U\n", "application/vnd.apple.mpegurl")
    calls = _install_http(monkeypatch, [initial, TimeoutError("private timeout")])
    container = _install_audio(monkeypatch, [])
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)
    source = StreamSource(HLS_URL)

    class SignaledEvent:
        def __init__(self):
            self.event = threading.Event()
            self.wait_started = threading.Event()
            self.timeout = None

        def wait(self, timeout=None):
            self.timeout = timeout
            self.wait_started.set()
            return self.event.wait(timeout)

        def set(self):
            self.event.set()

        def is_set(self):
            return self.event.is_set()

    cancel = SignaledEvent()
    source._hls_open_retry_cancel = cancel
    failures = []

    def open_segment():
        try:
            source._open_nested_http("https://media.example.test/live/segment.ts", 0, {})
        except Exception as error:  # captured from the worker to keep the test deterministic
            failures.append(error)

    worker = threading.Thread(target=open_segment)
    worker.start()
    assert cancel.wait_started.wait(timeout=2)
    closed_at = time.monotonic()
    source.close()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert time.monotonic() - closed_at < 1
    assert cancel.timeout == 1.0
    assert len(calls) == 2
    assert len(failures) == 1
    assert isinstance(failures[0], TimeoutError)
    assert str(failures[0]) == "private timeout"
    container.close.assert_called_once()


def test_non_hls_source_keeps_using_pyav_without_urlopen(monkeypatch):
    frame = _pcm_frame(99)
    container = _install_audio(monkeypatch, [frame])

    def unexpected_urlopen(*_args, **_kwargs):
        raise AssertionError("non-HLS source must not use urllib")

    monkeypatch.setattr("lisca.stream_source.urllib.request.urlopen", unexpected_urlopen)
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    source = StreamSource("https://media.example.test/live/audio.mp4", chunk_sec=0.01)
    try:
        pcm = b"".join(source.iter_chunks())
    finally:
        source.close()

    assert pcm == np.full(160, 99, dtype=np.int16).tobytes()


def test_non_hls_nested_timeout_keeps_original_single_open(monkeypatch):
    child_url = "https://media.example.test/audio/redirected.wav"
    calls = []
    container = _install_audio(monkeypatch, [])

    def urlopen(request, *, timeout, context):
        calls.append((request.full_url, timeout, context))
        raise TimeoutError("private non-HLS timeout")

    def av_open(_source, **kwargs):
        kwargs["io_open"](child_url, 0, {})
        return container

    monkeypatch.setattr("lisca.stream_source.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("lisca.stream_source.av.open", av_open)

    with pytest.raises(TimeoutError, match="private non-HLS timeout"):
        StreamSource("https://media.example.test/audio.wav")

    assert [(call[0], call[1]) for call in calls] == [(child_url, READ_TIMEOUT_SEC)]
