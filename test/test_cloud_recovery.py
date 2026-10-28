"""Recovery behavior through the owner HTTP session and WebSocket interfaces."""
import os
import sys
import ssl
import threading
import time
from urllib.error import HTTPError, URLError

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import webui  # noqa: E402
from cloud_translation import CloudConfigStore  # noqa: E402
from streamparser.recognition_process import RecognitionCapacityError  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})


class StructuredProviderFailure(Exception):
    def __init__(self, status_code, code):
        super().__init__("sensitive provider detail")
        self.status_code = status_code
        self.code = code


@pytest.mark.parametrize(("failure", "category", "retryable"), [
    (TimeoutError("private read detail"), "media_network", True),
    (URLError(TimeoutError("private read detail")), "media_network", True),
    (ConnectionResetError("private read detail"), "media_network", True),
    (HTTPError("https://media.test/private", 503, "private", {}, None), "media_network", True),
    (HTTPError("https://media.test/private", 403, "private", {}, None), "media_decode", False),
    (URLError(ssl.SSLCertVerificationError("private certificate detail")), "media_decode", False),
    (ValueError("private decoder detail"), "media_decode", False),
])
def test_media_read_failure_exposes_safe_recovery_evidence_and_releases_session(
        monkeypatch, failure, category, retryable):
    closed = {"source": 0, "engine": 0}

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x01\x01" * 1600
            raise failure

        def close(self):
            closed["source"] += 1

    class Engine:
        def transcribe_chunk(self, _audio):
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def close(self):
            closed["engine"] += 1

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/live.m3u8",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    assert error["failure_category"] == category
    assert error["retryable"] is retryable
    assert "private" not in str(events)
    assert "TEST_ONLY_KEY" not in str(events)
    assert events[-1] == {"type": "end", "reason": "error"}
    assert closed == {"source": 1, "engine": 1}


def test_partial_asr_send_failure_is_reported_by_public_cloud_websocket(monkeypatch):
    from streamparser import asr_client
    from engines.aliyun_funasr import AliyunFunASREngine

    closed = {"source": 0, "recognition": 0}

    class Recognition:
        def __init__(self, **_kwargs):
            self.stopped = False

        def start(self):
            pass

        def send_audio_frame(self, _data):
            raise OSError("TEST_ONLY_PRIVATE_TRANSPORT_DETAIL")

        def stop(self, **_kwargs):
            if not self.stopped:
                closed["recognition"] += 1
                self.stopped = True

        def cancel(self, **kwargs):
            self.stop(**kwargs)

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x00\x00" * 800

        def close(self):
            closed["source"] += 1

    monkeypatch.setattr(asr_client, "Recognition", Recognition)
    monkeypatch.setattr(webui, "StreamSource", Source)

    def create_engine(_name, **kwargs):
        return AliyunFunASREngine(chunk_size=3200, **kwargs)

    monkeypatch.setattr(webui, "create_engine", create_engine)
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/clip.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    assert error["failure_category"] == "network"
    assert error["retryable"] is True
    assert "TEST_ONLY_PRIVATE_TRANSPORT_DETAIL" not in str(events)
    assert "TEST_ONLY_KEY" not in str(events)
    assert events[-1] == {"type": "end", "reason": "error"}
    assert closed == {"source": 1, "recognition": 1}


@pytest.mark.parametrize(("phase", "failure", "retryable"), [
    ("media_open", TimeoutError("private startup timeout"), True),
    ("media_open", HTTPError("https://media.test/private", 503, "private", {}, None), True),
    ("media_open", HTTPError("https://media.test/private", 403, "private", {}, None), False),
    ("media_preflight", URLError(TimeoutError("private first-read timeout")), True),
    ("media_preflight", HTTPError("https://media.test/private", 429, "private", {}, None), True),
    ("media_preflight", URLError(ssl.SSLCertVerificationError("private certificate")), False),
    ("media_preflight", ValueError("private decoder detail"), False),
])
def test_startup_media_failures_expose_typed_recovery_and_release_source(
        monkeypatch, phase, failure, retryable):
    closed = {"source": 0}
    engine_created = []

    class Source:
        def __init__(self, *_args, **_kwargs):
            if phase == "media_open":
                raise failure

        def iter_chunks(self):
            raise failure

        def close(self):
            closed["source"] += 1

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine",
                        lambda *_args, **_kwargs: engine_created.append("created"))
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/live.m3u8",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    expected_message = ("无法打开媒体流：请检查来源、网络和访问权限；未启动识别"
                        if phase == "media_open" else "输入音频解码失败，未启动识别")
    assert error["message"] == expected_message
    assert error["failure_category"] == ("media_network" if retryable else "media_decode")
    assert error["retryable"] is retryable
    assert "private" not in str(events)
    assert "TEST_ONLY_KEY" not in str(events)
    assert events[-1] == {"type": "end", "reason": "error"}
    assert engine_created == []
    assert closed["source"] == (1 if phase == "media_preflight" else 0)


def test_cloud_ws_exposes_provider_permission_evidence_without_raw_error(monkeypatch):
    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x00" * 3200

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            raise StructuredProviderFailure(403, "Model.AccessDenied")

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/clip.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    assert error["failure_category"] == "permission"
    assert error["provider_status"] == 403
    assert error["provider_code"] == "Model.AccessDenied"
    assert "sensitive provider detail" not in str(events)
    assert "TEST_ONLY_KEY" not in str(events)
    assert events[-1] == {"type": "end", "reason": "error"}


@pytest.mark.parametrize(("failure", "category", "retryable", "code"), [
    (StructuredProviderFailure(401, "InvalidApiKey"), "authentication", False, "InvalidApiKey"),
    (StructuredProviderFailure(429, "BudgetLimitExceeded"), "quota", False,
     "BudgetLimitExceeded"),
    (StructuredProviderFailure(429, "Throttling.RateQuota"), "rate_limited", True,
     "Throttling.RateQuota"),
    (ConnectionError("private network detail"), "network", True, None),
    (RecognitionCapacityError("private memory detail"), "capacity", False, None),
    (StructuredProviderFailure(503, "UnknownServiceCode"), "temporarily_unavailable", True, None),
    (StructuredProviderFailure(400, "UnknownPrivateCode"), "unknown", False, None),
])
def test_cloud_ws_classifies_failures_only_from_provider_evidence(
        monkeypatch, failure, category, retryable, code):
    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x00" * 3200

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            raise failure

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/clip.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    assert error["failure_category"] == category
    assert error["retryable"] is retryable
    assert error.get("provider_code") == code
    assert "private" not in str(events)
    assert "TEST_ONLY_KEY" not in str(events)


def test_cloud_stop_failure_is_reported_without_claiming_clean_cancellation(monkeypatch):
    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x01\x00" * 1600

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        finish = poll

        def close(self):
            raise TimeoutError("private shutdown detail TEST_ONLY_KEY")

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/clip.mp4", "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while not events or events[-1]["type"] != "end":
                events.append(ws.receive_json())
    error = next(event for event in events if event["type"] == "error")
    assert error["failure_category"] == "recognition_stop"
    assert error["retryable"] is False
    assert events[-1] == {"type": "end", "reason": "error"}
    assert "private" not in str(events)
    assert "TEST_ONLY_KEY" not in str(events)


@pytest.mark.parametrize("cancel_source", ["owner_disconnect", "cloud_config_delete"])
def test_owner_disconnect_or_cloud_config_delete_allows_only_inflight_recognition(
        monkeypatch, cancel_source):
    import threading

    started = threading.Event()
    release = threading.Event()
    source_closed = threading.Event()
    calls = {"transcribe": 0, "poll": 0, "finish": 0}

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            while True:
                yield b"\x00" * 3200

        def close(self):
            source_closed.set()

    class Engine:
        def transcribe_chunk(self, _audio):
            calls["transcribe"] += 1
            started.set()
            assert release.wait(2)
            return (0.0, 0.1, "in-flight", None, True, 1)

        def poll(self):
            calls["poll"] += 1
            return (None, None, "", None)

        def finish(self):
            calls["finish"] += 1
            return (None, None, "", None)

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/live.mp4",
            "source": "auto",
        }).json()
        token = ticket["session"]
        with webui._CLOUD_LOCK:
            stop = webui._CLOUD_JOBS[token]["stop"]
        with client.websocket_connect("/ws?session=" + token):
            assert started.wait(2)
            if cancel_source == "cloud_config_delete":
                assert client.delete("/api/cloud").status_code == 200
        assert stop.wait(2), "session cancellation did not reach the worker"
        release.set()
        assert source_closed.wait(2), "worker did not release the media source"

    assert calls == {"transcribe": 1, "poll": 0, "finish": 0}


def test_non_hls_http_no_progress_watchdog_is_nonretryable_through_public_websocket(monkeypatch):
    clock = {"now": 0.0}

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x01" * 3200

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            clock["now"] = 16.0
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return self.poll()

        def close(self):
            pass

    monkeypatch.setattr(webui, "_session_monotonic", lambda: clock["now"])
    monkeypatch.setattr(webui, "_session_wait", lambda stop, _seconds: stop.is_set())
    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/recording.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next((event for event in events if event["type"] == "error"), None)
    assert error is not None, events[-5:]
    assert error["failure_category"] == "no_progress"
    assert error["retryable"] is False
    assert error["message"].startswith("识别连续 15 秒没有字幕或静音处理进展")
    assert events[-1] == {"type": "end", "reason": "error"}


def test_live_no_progress_can_recover_after_twenty_seconds_through_public_websocket(monkeypatch):
    clock = {"now": 0.0}
    audio = b"\x00\x10" * 8000  # 0.5s of non-silent PCM; audio delivery is not progress.
    calls = []

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for _ in range(41):  # 20s with no captions, then a recoverable late result.
                yield audio

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            calls.append(clock["now"])
            clock["now"] += 0.5
            if len(calls) == 41:
                return (20.0, 20.5, "Recovered caption", None, True, 1)
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return self.poll()

        def close(self):
            pass

    monkeypatch.setattr(webui, "_session_monotonic", lambda: clock["now"])
    monkeypatch.setattr(webui, "_session_wait", lambda stop, _seconds: stop.is_set())
    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/live.m3u8",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    assert not any(event["type"] == "error" for event in events), events[-5:]
    assert any(event["type"] == "subtitle" and event["text"] == "Recovered caption"
               for event in events), events[-5:]
    assert len(calls) == 41, calls
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_ws_event_backlog_overflow_is_visible_and_not_normal_completion(monkeypatch):
    import asyncio
    import threading
    from collections import deque

    monkeypatch.setattr(webui, "_MAX_WS_EVENT_BACKLOG", 4, raising=False)
    monkeypatch.setattr(webui, "_SERVER_LOGS", deque())
    send_json = webui.WebSocket.send_json
    delayed = {"done": False}

    async def delay_first_owner_send(socket, data, mode="text"):
        if socket.scope["path"] == "/ws" and not delayed["done"]:
            delayed["done"] = True
            await asyncio.sleep(0.1)
        await send_json(socket, data, mode)

    monkeypatch.setattr(webui.WebSocket, "send_json", delay_first_owner_send)
    emitted = {"count": 0}

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x01" * 3200

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (None, None, "", None)

        def poll(self):
            if emitted["count"] >= 100:
                return (None, None, "", None)
            emitted["count"] += 1
            index = emitted["count"]
            return (index * 0.1, index * 0.1 + 0.1, f"caption {index}", None,
                    True, index)

        def finish(self):
            return (None, None, "", None)

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/live.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    assert error["failure_category"] == "capacity"
    assert error["retryable"] is False
    assert events[-1] == {"type": "end", "reason": "error"}
    assert emitted["count"] < 100
    assert "TEST_ONLY_KEY" not in str(events)


def test_disconnected_sessions_cannot_grow_unbounded_blocked_workers(monkeypatch):
    started = 0
    closed = 0
    counts_lock = threading.Lock()
    all_sources_started = threading.Condition(counts_lock)
    all_sources_closed = threading.Condition(counts_lock)
    release_sources = threading.Event()

    class BlockingSource:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            nonlocal started
            with counts_lock:
                started += 1
                source_number = started
                all_sources_started.notify_all()
            if source_number <= 8:
                release_sources.wait()
            return
            yield b"\x01" * 3200

        def close(self):
            nonlocal closed
            with counts_lock:
                closed += 1
                all_sources_closed.notify_all()

    monkeypatch.setattr(webui, "StreamSource", BlockingSource)
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        try:
            for expected_started in range(1, 9):
                ticket = client.post("/api/cloud/sessions", json={
                    "mode": "transcribe", "url": "https://media.test/live.mp4",
                    "source": "auto",
                }).json()
                with client.websocket_connect("/ws?session=" + ticket["session"]):
                    with all_sources_started:
                        assert all_sources_started.wait_for(
                            lambda: started >= expected_started, timeout=2)

            ticket = client.post("/api/cloud/sessions", json={
                "mode": "transcribe", "url": "https://media.test/live.mp4",
                "source": "auto",
            }).json()
            with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
                received = []
                while True:
                    event = ws.receive_json()
                    received.append(event)
                    if event["type"] == "end":
                        break
            terminal = [event for event in received if event["type"] in ("error", "end")]
            assert terminal == [
                {"type": "error", "failure_category": "capacity", "retryable": False,
                 "message": "后台识别任务达到安全上限，已有任务仍在关闭；请稍后重试"},
                {"type": "end", "reason": "error"},
            ]
            with counts_lock:
                assert started == 8
        finally:
            release_sources.set()
            with all_sources_closed:
                assert all_sources_closed.wait_for(lambda: closed == started, timeout=3)

        # The worker slots remain occupied until each blocked source has
        # actually closed. Once they unwind, the public session path admits a
        # fresh worker instead of leaking capacity permanently.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            ticket = client.post("/api/cloud/sessions", json={
                "mode": "transcribe", "url": "https://media.test/live.mp4",
                "source": "auto",
            }).json()
            with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
                reopened_events = []
                while True:
                    event = ws.receive_json()
                    reopened_events.append(event)
                    if event["type"] == "end":
                        break
            reopened_errors = [event for event in reopened_events if event["type"] == "error"]
            if not any(event.get("failure_category") == "capacity" for event in reopened_errors):
                break
            time.sleep(0.01)
        assert reopened_errors, "a released worker slot did not admit a public session"
        assert reopened_errors[0].get("failure_category") is None
        with all_sources_closed:
            assert all_sources_closed.wait_for(lambda: closed == started, timeout=2)


def test_ws_caption_identity_capacity_keeps_existing_finals_and_reports_incomplete(monkeypatch):
    monkeypatch.setattr(webui, "_MAX_SESSION_CAPTIONS", 3, raising=False)
    emitted = {"count": 0}

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x01" * 3200

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (None, None, "", None)

        def poll(self):
            if emitted["count"] >= 100:
                return (None, None, "", None)
            emitted["count"] += 1
            index = emitted["count"]
            return (index * 0.1, index * 0.1 + 0.1, f"caption {index}", None,
                    True, index)

        def finish(self):
            return (None, None, "", None)

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/live.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    captions = [event for event in events if event["type"] == "subtitle"]
    assert error["failure_category"] == "capacity"
    assert error["retryable"] is False
    assert events[-1] == {"type": "end", "reason": "error"}
    assert [event["sentence_id"] for event in captions] == [1, 2, 3]
    assert emitted["count"] == 4


@pytest.mark.parametrize(("limit_name", "same_id", "is_final"), [
    ("_MAX_SESSION_CAPTION_VARIANTS", True, True),
    ("_MAX_PENDING_CAPTIONS", False, False),
])
def test_ws_caption_variant_and_pending_limits_are_reported(
        monkeypatch, limit_name, same_id, is_final):
    monkeypatch.setattr(webui, limit_name, 2, raising=False)
    emitted = {"count": 0}

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x01" * 3200

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (None, None, "", None)

        def poll(self):
            if emitted["count"] >= 10:
                return (None, None, "", None)
            emitted["count"] += 1
            index = emitted["count"]
            sentence_id = 1 if same_id else index
            return (index * 0.1, index * 0.1 + 0.1, f"caption {index}", None,
                    is_final, sentence_id)

        def finish(self):
            return (None, None, "", None)

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "TEST_ONLY_KEY"})
        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/live.mp4",
            "source": "auto",
        }).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    error = next(event for event in events if event["type"] == "error")
    captions = [event for event in events if event["type"] == "subtitle"]
    assert error["failure_category"] == "capacity"
    assert error["retryable"] is False
    assert events[-1] == {"type": "end", "reason": "error"}
    assert len(captions) == 2
    assert emitted["count"] == 3
