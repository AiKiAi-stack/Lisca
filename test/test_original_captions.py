"""Original-language captions through the public cloud HTTP/WS seam."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import webui
from cloud_translation import CloudConfigStore


@pytest.mark.parametrize("text,request_data,hints", [
    ("别太装。我们还是很期待的。", {}, []),
    ("Please keep the original English words.", {}, []),
    ("别太装。", {"source": "zh", "target": "en"}, ["zh"]),
    ("Keep these English words.", {"source": "en", "target": "zh"}, ["en"]),
])
def test_default_cloud_session_keeps_original_speech_without_translation(tmp_path, monkeypatch, text, request_data, hints):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    parameters = []

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass
        def iter_chunks(self):
            yield b"\x00" * 3200
        def close(self):
            pass

    class Engine:
        def transcribe_chunk(self, _audio):
            return (0.0, 1.0, text, None, True, 1)
        def poll(self):
            return (None, None, "", None)
        def finish(self):
            return self.poll()
        def close(self):
            pass

    def create(_name, **kwargs):
        parameters.append(kwargs)
        return Engine()

    def forbidden_translator(*_args, **_kwargs):
        pytest.fail("original-language captions must never invoke a translator")

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", create)
    monkeypatch.setattr(webui, "QwenTranslator", forbidden_translator)
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        ticket = client.post("/api/cloud/sessions", json={"url": "https://media.test/video.mp4", **request_data})
        assert ticket.status_code == 200, ticket.text
        assert ticket.json()["mode"] == "transcribe"
        with client.websocket_connect("/ws?session=" + ticket.json()["session"]) as socket:
            events = []
            while True:
                event = socket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break
    captions = [event for event in events if event["type"] == "subtitle"]
    assert captions and "".join(cue["text"] for cue in captions) == text
    assert all(cue["is_final"] for cue in captions)
    assert not any(cue.get("translated") for cue in captions)
    assert parameters[0]["language_hints"] == hints
    assert parameters[0]["api_key"] == "test-private-key"


def test_connection_check_does_not_start_a_translation_request(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    calls = []
    class Engine:
        def poll(self):
            return (None, None, "", None)
        def close(self):
            calls.append("closed")
    def create(_name, **kwargs):
        assert kwargs["language_hints"] == []
        calls.append("asr")
        return Engine()
    def forbidden(*_args, **_kwargs):
        pytest.fail("connection check must not call translation")
    monkeypatch.setattr(webui, "create_engine", create)
    monkeypatch.setattr(webui, "QwenTranslator", forbidden)
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        assert client.post("/api/cloud/check").status_code == 200
    assert calls == ["asr", "closed"]


def test_original_language_selection_is_validated_before_any_cloud_request(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "test-private-key"})
        response = client.post("/api/cloud/sessions", json={"url": "https://media.test/file.mp4",
            "mode": "transcribe", "source": "unsupported"})
        assert response.status_code == 400
        assert client.get("/api/cloud").json()["default_mode"] == "transcribe"
