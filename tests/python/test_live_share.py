"""Black-box coverage for shared live sessions over HTTP and WebSocket."""

import threading
import time
from contextlib import ExitStack
from urllib.parse import urlencode

from fastapi.testclient import TestClient


import webui  # noqa: E402
import live_share  # noqa: E402


SOURCE_URL = "http://stream.test/live.m3u8"


def _start_share(client):
    response = client.post("/share/start", json={"url": SOURCE_URL, "engine": "demo"})
    assert response.status_code == 200, response.text
    return response.json()


def test_lan_viewer_receives_captions_from_the_host_asr_session(monkeypatch):
    source_started = threading.Event()
    release_first_chunk = threading.Event()
    release_source = threading.Event()
    source_closed = threading.Event()
    viewer_unsubscribed = threading.Event()
    engine_creations = []

    class FakeStreamSource:
        timeline_offset_sec = 0.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            source_started.set()
            if not release_first_chunk.wait(timeout=5):
                return
            yield b"\x00" * 3200
            release_source.wait(timeout=5)

        def close(self):
            source_closed.set()

    class FakeEngine:
        batch_mode = False

        def __init__(self):
            self.sent = False

        def transcribe_chunk(self, _audio):
            if self.sent:
                return (None, None, "", None)
            self.sent = True
            return (0.0, 0.1, "shared caption", [(0.0, 0.1, "shared caption")])

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return (None, None, "", None)

    def make_engine(*_args, **_kwargs):
        engine_creations.append(True)
        return FakeEngine()

    unsubscribe = live_share.LiveShare.unsubscribe

    def track_unsubscribe(share, viewer_id):
        unsubscribe(share, viewer_id)
        viewer_unsubscribed.set()

    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    monkeypatch.setattr(webui, "create_engine", make_engine)
    monkeypatch.setattr(live_share.LiveShare, "unsubscribe", track_unsubscribe)

    share = None
    share_state = None
    with TestClient(webui.app) as host:
        viewer = TestClient(webui.app, client=("192.168.1.20", 50000))
        # Both peers use Uvicorn's single ASGI event loop while keeping distinct client IPs.
        viewer.portal = host.portal
        try:
            assert viewer.portal is host.portal
            share = _start_share(host)
            share_state = webui._SHARES[share["token"]]
            with host.websocket_connect(
                "/ws?" + urlencode({
                    "url": SOURCE_URL,
                    "engine": "demo",
                    "share": share["token"],
                })
            ) as host_socket:
                assert source_started.wait(timeout=1)
                joined = viewer.post(f"/share/{share['token']}/join")
                assert joined.status_code == 200
                with viewer.websocket_connect(
                    f"/share/{share['token']}/ws?viewer_id={joined.json()['viewer_id']}"
                ) as viewer_socket:
                    release_first_chunk.set()
                    host_events = []
                    while not any(
                        event.get("type") == "subtitle" for event in host_events
                    ):
                        host_events.append(host_socket.receive_json())

                    viewer_events = []
                    while not any(
                        event.get("type") == "subtitle" for event in viewer_events
                    ):
                        viewer_events.append(viewer_socket.receive_json())
                    assert any(
                        event.get("text") == "shared caption" for event in viewer_events
                    )
                    assert len(engine_creations) == 1
                    # Disconnect while the ASGI task is active; TestClient's context exit
                    # immediately cancels that task after sending its disconnect message.
                    viewer_socket.close()
                    assert viewer_unsubscribed.wait(timeout=1)
                    assert not source_closed.is_set()

                assert not source_closed.is_set()
                release_source.set()
                while not any(event.get("type") == "end" for event in host_events):
                    host_events.append(host_socket.receive_json())
            assert host.get(f"/share/{share['token']}").status_code == 404
        finally:
            release_first_chunk.set()
            release_source.set()
            if share_state is not None:
                share_state.stop_event.set()
                share_state.active = False
            if share is not None:
                with webui._SHARE_LOCK:
                    webui._SHARES.pop(share["token"], None)
            try:
                if source_started.is_set():
                    assert source_closed.wait(timeout=1)
            finally:
                viewer.close()


def test_departed_viewer_releases_capacity_without_interrupting_others(monkeypatch):
    async def fake_proxy(*_args, **_kwargs):
        from fastapi import Response
        return Response("media")

    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    monkeypatch.setattr(webui, "_proxy_stream", fake_proxy)
    with TestClient(webui.app) as host, TestClient(
        webui.app, client=("192.168.1.20", 50000)
    ) as viewer:
        share = _start_share(host)
        admissions = [viewer.post(f"/share/{share['token']}/join") for _ in range(3)]
        assert all(response.status_code == 200 for response in admissions)
        viewer_ids = [response.json()["viewer_id"] for response in admissions]
        full = viewer.post(f"/share/{share['token']}/join")
        assert full.status_code == 429
        assert "最多允许 3 台" in full.json()["detail"]

        with ExitStack() as sockets:
            first, second, third = [
                sockets.enter_context(viewer.websocket_connect(
                    f"/share/{share['token']}/ws?viewer_id={viewer_id}"
                ))
                for viewer_id in viewer_ids
            ]
            assert first and second and third

            first.close()
            departed_at = time.monotonic()
            deadline = departed_at + 0.9
            replacement = None
            while time.monotonic() < deadline:
                response = viewer.post(f"/share/{share['token']}/join")
                if response.status_code == 200:
                    replacement = response
                    break
                assert response.status_code == 429, response.text
                time.sleep(0.025)

            assert replacement is not None, "departed viewer slot was not released within one second"
            assert time.monotonic() - departed_at < 1
            assert viewer.get(admissions[0].json()["video"]).status_code == 404

            for media_path in (
                admissions[1].json()["video"],
                admissions[2].json()["video"],
                replacement.json()["video"],
            ):
                assert viewer.get(media_path).status_code == 200

            second.close()
            third.close()
            released_at = time.monotonic()
            deadline = released_at + 0.9
            released_slots = []
            while len(released_slots) < 2 and time.monotonic() < deadline:
                response = viewer.post(f"/share/{share['token']}/join")
                if response.status_code == 200:
                    released_slots.append(response)
                else:
                    assert response.status_code == 429, response.text
                    time.sleep(0.025)
            assert len(released_slots) == 2, "closed viewer slots were not released within one second"
            assert viewer.post(f"/share/{share['token']}/join").status_code == 429

            with viewer.websocket_connect(
                f"/share/{share['token']}/ws?viewer_id={replacement.json()['viewer_id']}"
            ) as replacement_socket:
                assert host.post(f"/share/stop/{share['token']}").status_code == 200
                assert replacement_socket.receive_json() == {"type": "end", "reason": "cancelled"}


def test_slow_viewer_gets_explicit_capacity_end_without_evicted_captions(monkeypatch):
    import asyncio

    monkeypatch.setattr(live_share, "MAX_VIEWER_EVENT_BACKLOG", 16, raising=False)
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    send_json = webui.WebSocket.send_json

    async def delay_first_viewer_event(socket, data, mode="text"):
        if socket.scope["path"].endswith("/ws") and socket.scope["path"].startswith("/share/") \
                and not getattr(socket.state, "delayed_first_share_event", False):
            socket.state.delayed_first_share_event = True
            await asyncio.sleep(0.1)
        await send_json(socket, data, mode)

    monkeypatch.setattr(webui.WebSocket, "send_json", delay_first_viewer_event)
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
            if emitted["count"] >= 400:
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
    try:
        with TestClient(webui.app) as host, TestClient(
            webui.app, client=("192.168.1.20", 50000)
        ) as viewer:
            share = _start_share(host)
            joined = viewer.post(f"/share/{share['token']}/join")
            assert joined.status_code == 200, joined.text
            with viewer.websocket_connect(
                f"/share/{share['token']}/ws?viewer_id={joined.json()['viewer_id']}"
            ) as viewer_socket:
                with host.websocket_connect("/ws?" + urlencode({
                    "url": SOURCE_URL, "engine": "demo", "share": share["token"],
                })) as host_socket:
                    host_events = []
                    while True:
                        event = host_socket.receive_json()
                        host_events.append(event)
                        if event["type"] == "end":
                            break
                viewer_events = []
                while True:
                    event = viewer_socket.receive_json()
                    viewer_events.append(event)
                    if event["type"] == "end":
                        break

        subtitles = [event for event in viewer_events if event["type"] == "subtitle"]
        error = next(event for event in viewer_events if event["type"] == "error")
        assert subtitles[0]["sentence_id"] == 1
        assert [event["sentence_id"] for event in subtitles] == list(
            range(1, len(subtitles) + 1))
        assert len(subtitles) < emitted["count"]
        assert error["failure_category"] == "capacity"
        assert error["retryable"] is False
        assert viewer_events[-1] == {"type": "end", "reason": "error"}
        assert host_events[-1] == {"type": "end", "reason": "completed"}
    finally:
        webui._SHARES.clear()


def test_late_viewer_is_told_when_share_caption_history_was_truncated(monkeypatch):
    captions_drained = threading.Event()
    release_source = threading.Event()
    emit_future_caption = threading.Event()
    emitted = {"count": 0}
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x01" * 3200
            release_source.wait(timeout=3)

        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (None, None, "", None)

        def poll(self):
            if emitted["count"] >= 300 and emitted.get("future_emitted"):
                return (None, None, "", None)
            if emitted["count"] >= 300:
                captions_drained.set()
                if not emit_future_caption.wait(timeout=3):
                    return (None, None, "", None)
                emitted["future_emitted"] = True
                emitted["count"] += 1
                return (30.0, 30.1, "caption after history snapshot", None, True, 301)
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
    try:
        with TestClient(webui.app) as host, TestClient(
            webui.app, client=("192.168.1.20", 50000)
        ) as viewer:
            share = _start_share(host)
            with host.websocket_connect("/ws?" + urlencode({
                "url": SOURCE_URL, "engine": "demo", "share": share["token"],
            })) as host_socket:
                assert captions_drained.wait(timeout=2)
                joined = viewer.post(f"/share/{share['token']}/join")
                assert joined.status_code == 200, joined.text
                with viewer.websocket_connect(
                    f"/share/{share['token']}/ws?viewer_id={joined.json()['viewer_id']}"
                ) as viewer_socket:
                    viewer_events = []
                    warning_seen = False
                    deadline = time.monotonic() + 3
                    while time.monotonic() < deadline:
                        event = viewer_socket.receive_json()
                        viewer_events.append(event)
                        warning_seen |= event.get("failure_category") == "share_history_truncated"
                        captions = [item for item in viewer_events if item["type"] == "subtitle"]
                        if warning_seen and len(captions) == live_share.MAX_SHARE_HISTORY_EVENTS:
                            break
                    emit_future_caption.set()
                    while not any(event.get("sentence_id") == 301 for event in viewer_events):
                        viewer_events.append(viewer_socket.receive_json())
                    release_source.set()
                    while not any(event["type"] == "end" for event in viewer_events):
                        viewer_events.append(viewer_socket.receive_json())
                while True:
                    event = host_socket.receive_json()
                    if event["type"] == "end":
                        break

        warning = next(event for event in viewer_events
                       if event.get("failure_category") == "share_history_truncated")
        captions = [event for event in viewer_events if event["type"] == "subtitle"]
        future_caption = next(event for event in captions if event.get("sentence_id") == 301)
        assert warning["message"] == "较早字幕记录未保留，当前直播继续"
        assert warning["type"] == "status"
        assert len(captions) == live_share.MAX_SHARE_HISTORY_EVENTS + 1
        assert captions[0]["sentence_id"] == 61
        assert captions[-2]["sentence_id"] == 300
        assert future_caption["text"] == "caption after history snapshot"
    finally:
        release_source.set()
        webui._SHARES.clear()


def test_fourth_viewer_is_rejected_before_receiving_media_access(monkeypatch):
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    with TestClient(webui.app) as host, TestClient(
        webui.app, client=("192.168.1.20", 50000)
    ) as viewer:
        share = _start_share(host)
        joins = [
            viewer.post(f"/share/{share['token']}/join")
            for _ in range(4)
        ]

        assert [response.status_code for response in joins] == [200, 200, 200, 429]
        assert "最多允许 3 台" in joins[3].json()["detail"]
        assert "viewer_id" in joins[0].json()
        assert "/viewer/" in joins[0].json()["video"]
        assert host.post(f"/share/stop/{share['token']}").status_code == 200


def test_viewer_media_url_cannot_be_reused_from_another_lan_device(monkeypatch):
    proxy_calls = []

    async def fake_proxy(*_args, **_kwargs):
        proxy_calls.append(True)
        from fastapi import Response
        return Response("media")

    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    monkeypatch.setattr(webui, "_proxy_stream", fake_proxy)
    with TestClient(webui.app) as host, TestClient(
        webui.app, client=("192.168.1.20", 50000)
    ) as admitted_device, TestClient(
        webui.app, client=("192.168.1.21", 50001)
    ) as other_device:
        share = _start_share(host)
        joined = admitted_device.post(f"/share/{share['token']}/join")
        media_path = joined.json()["video"]

        assert admitted_device.get(media_path).status_code == 200
        assert other_device.get(media_path).status_code == 404
        assert len(proxy_calls) == 1
        with other_device.websocket_connect(
            f"/share/{share['token']}/ws?viewer_id={joined.json()['viewer_id']}"
        ) as other_socket:
            rejection = other_socket.receive_json()
            assert rejection["type"] == "error"
            assert "绑定到其他设备" in rejection["message"]
        assert host.post(f"/share/stop/{share['token']}").status_code == 200


def test_expired_viewer_reservation_cannot_fetch_media(monkeypatch):
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    monkeypatch.setattr(live_share, "VIEWER_RESERVATION_SECONDS", 0.01)
    with TestClient(webui.app) as host, TestClient(
        webui.app, client=("192.168.1.20", 50000)
    ) as viewer:
        share = _start_share(host)
        joined = viewer.post(f"/share/{share['token']}/join")
        assert joined.status_code == 200

        time.sleep(0.03)

        assert viewer.get(joined.json()["video"]).status_code == 404
        assert host.post(f"/share/stop/{share['token']}").status_code == 200


def test_viewer_routes_are_lan_only_and_admin_routes_are_local_only(monkeypatch):
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    with TestClient(webui.app) as host:
        share = _start_share(host)
        with TestClient(webui.app, client=("192.168.1.20", 50000)) as viewer:
            assert viewer.get(f"/share/{share['token']}").status_code == 200
            assert viewer.get("/").status_code == 403
            assert viewer.post("/share/start", json={"url": SOURCE_URL}).status_code == 403
        with TestClient(webui.app, client=("8.8.8.8", 50000)) as outside:
            assert outside.get(f"/share/{share['token']}").status_code == 403
            assert outside.post(f"/share/{share['token']}/join").status_code == 403
        assert host.post(f"/share/stop/{share['token']}").status_code == 200


def test_stopped_share_link_expires_and_next_session_gets_a_new_link(monkeypatch):
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    with TestClient(webui.app) as host:
        first = _start_share(host)
        assert host.get(f"/share/{first['token']}").status_code == 200

        assert host.post(f"/share/stop/{first['token']}").status_code == 200
        assert host.get(f"/share/{first['token']}").status_code == 404

        second = _start_share(host)
        assert second["token"] != first["token"]
        assert host.post(f"/share/stop/{second['token']}").status_code == 200


def test_natural_stream_end_invalidates_the_viewer_link(monkeypatch):
    source_started = threading.Event()
    release_source = threading.Event()

    class ControlledStreamSource:
        timeline_offset_sec = 0.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            source_started.set()
            yield b"\x00" * 3200
            release_source.wait(timeout=2)

        def close(self):
            pass

    class EmptyEngine:
        batch_mode = False

        def transcribe_chunk(self, _audio):
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return (None, None, "", None)

    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    monkeypatch.setattr(webui, "StreamSource", ControlledStreamSource)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: EmptyEngine())

    try:
        with TestClient(webui.app) as host, TestClient(
            webui.app, client=("192.168.1.20", 50000)
        ) as viewer:
            share = _start_share(host)
            with host.websocket_connect(
                "/ws?" + urlencode({
                    "url": SOURCE_URL,
                    "engine": "demo",
                    "share": share["token"],
                })
            ) as host_socket:
                assert source_started.wait(timeout=1)
                joined = viewer.post(f"/share/{share['token']}/join")
                assert joined.status_code == 200
                with viewer.websocket_connect(
                    f"/share/{share['token']}/ws?viewer_id={joined.json()['viewer_id']}"
                ) as viewer_socket:
                    release_source.set()
                    viewer_events = []
                    while not any(event.get("type") == "end" for event in viewer_events):
                        viewer_events.append(viewer_socket.receive_json())
                assert any(event.get("type") == "end" for event in viewer_events)

                host_events = []
                while not any(event.get("type") == "end" for event in host_events):
                    host_events.append(host_socket.receive_json())

            assert host.get(f"/share/{share['token']}").status_code == 404
    finally:
        release_source.set()


def test_share_proxy_rejects_media_not_registered_to_the_session(monkeypatch):
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    with TestClient(webui.app) as host:
        share = _start_share(host)
        joined = host.post(f"/share/{share['token']}/join")
        viewer_id = joined.json()["viewer_id"]
        response = host.get(
            f"/share/{share['token']}/viewer/{viewer_id}/media/not-registered"
        )
        assert response.status_code == 404
        assert host.post(f"/share/stop/{share['token']}").status_code == 200


def test_local_viewer_can_watch_when_no_lan_ipv4_address_is_available(monkeypatch):
    monkeypatch.setattr(webui, '_lan_ipv4_candidates', lambda: [])
    monkeypatch.setattr(webui, '_SHARES', {})
    with TestClient(webui.app) as client:
        response = client.post('/share/start', json={'url':SOURCE_URL})
        assert response.status_code == 200, response.text
        share = response.json()
        assert share['url'].startswith('http://127.0.0.1:')
        assert client.get('/share/' + share['token']).status_code == 200
        assert client.post('/share/stop/' + share['token']).json() == {'ok':True}
