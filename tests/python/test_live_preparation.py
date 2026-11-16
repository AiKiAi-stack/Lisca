"""Live preparation candidates through public cloud HTTP/WebSocket sessions."""

import pytest
from fastapi.testclient import TestClient

from lisca.cloud_translation import CloudConfigStore
from lisca.web import app as webui


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    monkeypatch.delenv("LISCA_LIVE_PREPARE_AHEAD_SEC", raising=False)
    with TestClient(webui.app) as session:
        assert session.put("/api/cloud", json={"api_key": "fixture-key"}).status_code == 200
        yield session


@pytest.mark.parametrize("value", ["nan", "inf", "-inf", "-0.1", "30.1", "invalid", ""])
def test_transcribe_session_rejects_invalid_live_preparation_candidate(client, monkeypatch, value):
    # Acceptance: invalid experiment settings fail before the client opens a WS.
    monkeypatch.setenv("LISCA_LIVE_PREPARE_AHEAD_SEC", value)

    response = client.post("/api/cloud/sessions", json={"url": "https://media.test/live.m3u8"})

    assert response.status_code == 400
    assert response.json()["detail"] == "LISCA_LIVE_PREPARE_AHEAD_SEC 必须是 0 到 30 之间的有限秒数"


@pytest.mark.parametrize("url,audio_seconds,value,expected", [
    ("https://media.test/live.m3u8", 1.0, "10", 10.0),
    ("https://media.test/live.m3u8", 0.2, "10", 10.0),
    ("https://media.test/live.m3u8", 1.0, None, 10.0),
    ("https://media.test/live.m3u8", 1.0, "0", 0.0),
    ("https://media.test/live.m3u8", 1.0, "30", 30.0),
    ("local", 1.0, "10", 0.0),
    ("local", 0.2, "10", 0.0),
    ("local", 1.0, None, 0.0),
    ("https://media.test/video.mp4", 1.0, "10", 0.0),
    ("https://media.test/video.mp4", 0.2, "10", 0.0),
    ("https://media.test/video.mp4", 1.0, None, 0.0),
])
def test_ready_scopes_the_session_preparation_candidate_to_original_live_hls(
        client, tmp_path, monkeypatch, url, audio_seconds, value, expected):
    # Acceptance: streaming and EOF readiness both tell the client how much
    # displayable material to prepare; readiness itself does not wait for it.
    if value is not None:
        monkeypatch.setenv("LISCA_LIVE_PREPARE_AHEAD_SEC", value)
    if url == "local":
        media = tmp_path / "video.mp4"
        media.touch()
        url = str(media)

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            yield b"\x00\x10" * int(16000 * audio_seconds)
        def close(self): pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (0.0, audio_seconds, "字幕准备好了。", None, True, 1)
        def poll(self): return (None, None, "", None)
        def finish(self): return self.poll()
        def close(self): pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    ticket = client.post("/api/cloud/sessions", json={"url": url})
    assert ticket.status_code == 200
    monkeypatch.setenv("LISCA_LIVE_PREPARE_AHEAD_SEC", "20")

    with client.websocket_connect("/ws?session=" + ticket.json()["session"]) as socket:
        events = []
        while True:
            event = socket.receive_json()
            events.append(event)
            if event["type"] == "end":
                break

    ready, = [event for event in events if event["type"] == "ready"]
    assert ready["prepare_ahead_sec"] == expected
    assert ready["original"] is True
    assert max(event["play_at"] for event in events if event["type"] == "buffer") < 10.0
    assert events[-1] == {"type": "end", "reason": "completed"}


def test_explicit_translation_session_ignores_the_live_preparation_candidate(client, monkeypatch):
    monkeypatch.setenv("LISCA_LIVE_PREPARE_AHEAD_SEC", "invalid")

    response = client.post("/api/cloud/sessions", json={
        "url": "https://media.test/live.m3u8", "mode": "translate", "source": "zh", "target": "en",
    })

    assert response.status_code == 200
    assert response.json()["mode"] == "translate"
