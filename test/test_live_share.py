"""Black-box coverage for shared live sessions over HTTP and WebSocket."""

import os
import sys
import threading
from urllib.parse import urlencode

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import webui  # noqa: E402


SOURCE_URL = "http://stream.test/live.m3u8"


def _start_share(client):
    response = client.post("/share/start", json={"url": SOURCE_URL, "engine": "demo"})
    assert response.status_code == 200, response.text
    return response.json()


def test_lan_viewer_receives_captions_from_the_host_asr_session(monkeypatch):
    source_started = threading.Event()
    release_source = threading.Event()
    engine_creations = []

    class FakeStreamSource:
        timeline_offset_sec = 0.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            source_started.set()
            yield b"\x00" * 3200
            release_source.wait(timeout=2)

        def close(self):
            pass

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

    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    monkeypatch.setattr(webui, "create_engine", make_engine)

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
                host_events = []
                while not any(event.get("type") == "subtitle" for event in host_events):
                    host_events.append(host_socket.receive_json())

                with viewer.websocket_connect(
                    f"/share/{share['token']}/ws"
                ) as viewer_socket:
                    viewer_events = []
                    while not any(event.get("type") == "subtitle" for event in viewer_events):
                        viewer_events.append(viewer_socket.receive_json())

                assert any(event.get("text") == "shared caption" for event in viewer_events)
                assert len(engine_creations) == 1
                release_source.set()
                while not any(event.get("type") == "end" for event in host_events):
                    host_events.append(host_socket.receive_json())
            assert host.get(f"/share/{share['token']}").status_code == 404
    finally:
        release_source.set()


def test_share_stops_accepting_viewers_after_three_live_connections(monkeypatch):
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    with TestClient(webui.app) as host, TestClient(
        webui.app, client=("192.168.1.20", 50000)
    ) as viewer:
        share = _start_share(host)
        with viewer.websocket_connect(f"/share/{share['token']}/ws") as first:
            with viewer.websocket_connect(f"/share/{share['token']}/ws") as second:
                with viewer.websocket_connect(f"/share/{share['token']}/ws") as third:
                    with viewer.websocket_connect(
                        f"/share/{share['token']}/ws"
                    ) as fourth:
                        rejected = fourth.receive_json()
                    assert rejected["type"] == "error"
                    assert "最多允许 3 台" in rejected["message"]
        with viewer.websocket_connect(f"/share/{share['token']}/ws") as replacement:
            assert host.post(f"/share/stop/{share['token']}").status_code == 200
            assert replacement.receive_json()["type"] == "end"


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
            assert outside.get(f"/share/{share['token']}/info").status_code == 403
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
    class EmptyStreamSource:
        timeline_offset_sec = 0.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            return iter(())

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
    monkeypatch.setattr(webui, "StreamSource", EmptyStreamSource)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: EmptyEngine())

    with TestClient(webui.app) as host:
        share = _start_share(host)
        with host.websocket_connect(
            "/ws?" + urlencode({
                "url": SOURCE_URL,
                "engine": "demo",
                "share": share["token"],
            })
        ) as host_socket:
            host_events = []
            while not any(event.get("type") == "end" for event in host_events):
                host_events.append(host_socket.receive_json())

        assert host.get(f"/share/{share['token']}").status_code == 404


def test_share_proxy_rejects_media_not_registered_to_the_session(monkeypatch):
    monkeypatch.setattr(webui, "_lan_ipv4_candidates", lambda: ["192.168.1.2"])
    with TestClient(webui.app) as host:
        share = _start_share(host)
        response = host.get(f"/share/{share['token']}/media/not-registered")
        assert response.status_code == 404
        assert host.post(f"/share/stop/{share['token']}").status_code == 200
