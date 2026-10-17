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

                joined = viewer.post(f"/share/{share['token']}/join")
                assert joined.status_code == 200
                with viewer.websocket_connect(
                    f"/share/{share['token']}/ws?viewer_id={joined.json()['viewer_id']}"
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
        admissions = [viewer.post(f"/share/{share['token']}/join") for _ in range(3)]
        assert all(response.status_code == 200 for response in admissions)
        viewer_ids = [response.json()["viewer_id"] for response in admissions]
        assert viewer.post(f"/share/{share['token']}/join").status_code == 429
        with viewer.websocket_connect(
            f"/share/{share['token']}/ws?viewer_id={viewer_ids[0]}"
        ) as first:
            with viewer.websocket_connect(
                f"/share/{share['token']}/ws?viewer_id={viewer_ids[1]}"
            ) as second:
                with viewer.websocket_connect(
                    f"/share/{share['token']}/ws?viewer_id={viewer_ids[2]}"
                ) as third:
                    assert first and second and third
        replacement = viewer.post(f"/share/{share['token']}/join")
        assert replacement.status_code == 200
        with viewer.websocket_connect(
            f"/share/{share['token']}/ws?viewer_id={replacement.json()['viewer_id']}"
        ) as replacement_socket:
            assert host.post(f"/share/stop/{share['token']}").status_code == 200
            assert replacement_socket.receive_json()["type"] == "end"


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
        joined = host.post(f"/share/{share['token']}/join")
        viewer_id = joined.json()["viewer_id"]
        response = host.get(
            f"/share/{share['token']}/viewer/{viewer_id}/media/not-registered"
        )
        assert response.status_code == 404
        assert host.post(f"/share/stop/{share['token']}").status_code == 200
