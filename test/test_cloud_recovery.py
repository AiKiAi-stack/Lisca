"""Recovery behavior through the owner HTTP session and WebSocket interfaces."""
import os
import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import webui  # noqa: E402
from cloud_translation import CloudConfigStore  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})


class StructuredProviderFailure(Exception):
    def __init__(self, status_code, code):
        super().__init__("sensitive provider detail")
        self.status_code = status_code
        self.code = code


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
    assert error["provider_code"] == code
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


def test_no_progress_watchdog_is_nonretryable_through_public_websocket(monkeypatch):
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

        def close(self):
            pass

    monkeypatch.setattr(webui, "_session_monotonic", lambda: clock["now"])
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
    assert error["failure_category"] == "no_progress"
    assert error["retryable"] is False
    assert error["message"].startswith("识别连续 15 秒没有字幕或静音处理进展")
    assert events[-1] == {"type": "end", "reason": "error"}


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
                release_sources.wait(timeout=10)
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
        for _ in range(8):
            ticket = client.post("/api/cloud/sessions", json={
                "mode": "transcribe", "url": "https://media.test/live.mp4",
                "source": "auto",
            }).json()
            with client.websocket_connect("/ws?session=" + ticket["session"]):
                with all_sources_started:
                    assert all_sources_started.wait_for(lambda: started > closed, timeout=2)

        ticket = client.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/live.mp4",
            "source": "auto",
        }).json()
        try:
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
