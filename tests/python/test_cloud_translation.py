"""Cloud configuration and translation through the local HTTP/WS boundary."""
import os
import threading
import types

import httpx
import pytest

from fastapi.testclient import TestClient

import webui  # noqa: E402
from cloud_translation import CloudConfig, CloudConfigStore, CloudError, QwenTranslator, RequestStop  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})


def test_cloud_configuration_is_redacted_and_language_pairs_are_explicit():
    with TestClient(webui.app) as client:
        response = client.put("/api/cloud", json={"api_key": "sk-private-test"})
        assert response.status_code == 200
        assert response.json()["configured"] is True
        assert "sk-private-test" not in response.text
        config = client.get("/api/cloud").json()
        assert config["language_pairs"] == [["zh", "en"], ["en", "zh"]]
        assert "sk-private-test" not in str(config)
        assert client.delete("/api/cloud").status_code == 200
        assert client.get("/api/cloud").json()["configured"] is False


def test_cloud_session_validates_pair_before_any_media_or_api_request():
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "sk-private-test"})
        rejected = client.post("/api/cloud/sessions", json={"mode": "translate",
            "url": "http://media.test/live.m3u8", "source": "fr", "target": "zh"})
        assert rejected.status_code == 400
        valid = client.post("/api/cloud/sessions", json={"mode": "translate",
            "url": "http://media.test/live.m3u8", "source": "en", "target": "zh"})
        assert valid.status_code == 200
        assert "sk-private-test" not in valid.text
        assert valid.json()["session"]


def test_cloud_ws_emits_complete_stable_clause_before_sentence_final(monkeypatch):
    translated = threading.Event()
    parameters = []

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x00" * 3200
            yield b"\x00" * 3200
            assert translated.wait(3), "stable clause translation must arrive before audio ends"
            yield b"\x00" * 3200

        def close(self):
            pass

    class Engine:
        calls = 0

        def transcribe_chunk(self, _audio):
            self.calls += 1
            return (0.0, 0.3 if self.calls > 2 else None,
                    "Hello world. Next." if self.calls > 2 else "Hello world.",
                    [(0.0, 0.1, "Hello"), (0.1, 0.2, "world"), (0.2, 0.3, "Next")],
                    self.calls > 2, 7)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return self.poll()

        def close(self):
            pass

    class Translator:
        def __init__(self, _config, source, target):
            assert (source, target) == ("en", "zh")

        def translate(self, text, cancel):
            yield "你"
            yield "你好世界" if text == "Hello world." else "下一句"
            translated.set()

        def close(self):
            pass

    def engine_factory(_name, **kwargs):
        parameters.append(kwargs)
        return Engine()

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", engine_factory)
    monkeypatch.setattr(webui, "QwenTranslator", Translator)
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "sk-private-test"})
        ticket = client.post("/api/cloud/sessions", json={"mode": "translate",
            "url": "https://media.test/video.mp4", "source": "en", "target": "zh"}).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break
    cues = [event for event in events if event["type"] == "subtitle"]
    assert [event["text"] for event in cues] == ["你好世界", "下一句"]
    assert all(event["is_final"] is True for event in cues)
    assert cues[0]["source_text"] == "Hello world."
    assert cues[-1]["source_text"] == "Next."
    assert parameters[0]["language_hints"] == ["en"]
    assert parameters[0]["api_key"] == "sk-private-test"
    assert all("sk-private-test" not in str(event) for event in events)
    assert not any(event["type"] == "buffer" for event in events)


def test_translator_sends_explicit_language_pair_and_accumulates_flash_deltas():
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, text='data: {"choices":[{"delta":{"content":"Hello"}}]}\n\n'
                              'data: {"choices":[{"delta":{"content":" world"},"finish_reason":"stop"}]}\n\n'
                              'data: [DONE]\n\n')

    translator = QwenTranslator(CloudConfig(api_key="private"), "zh", "en",
                                transport=httpx.MockTransport(provider))
    try:
        assert list(translator.translate("你好世界", threading.Event())) == ["Hello", "Hello world"]
        import json
        body = json.loads(requests[0].content)
        assert body["translation_options"] == {"source_lang": "Chinese", "target_lang": "English"}
        assert requests[0].headers["authorization"] == "Bearer private"
        assert "private" not in str(requests[0].url)
    finally:
        translator.close()


@pytest.mark.parametrize("status", [401, 429, 503])
def test_provider_error_is_actionable_without_echoing_secrets(status):
    translator = QwenTranslator(CloudConfig(api_key="private"), "en", "zh",
        transport=httpx.MockTransport(lambda _request: httpx.Response(status, text="private")))
    try:
        with pytest.raises(CloudError) as caught:
            list(translator.translate("Hello", threading.Event()))
        assert "private" not in str(caught.value)
    finally:
        translator.close()


def test_truncated_translation_is_not_a_successful_final():
    translator = QwenTranslator(CloudConfig(api_key="private"), "en", "zh",
        transport=httpx.MockTransport(lambda _request: httpx.Response(200,
            text='data: {"choices":[{"delta":{"content":"你"}}]}\n\n')))
    try:
        with pytest.raises(CloudError, match="中断"):
            list(translator.translate("Hello", threading.Event()))
    finally:
        translator.close()


def test_cross_origin_config_and_websocket_cannot_start_billed_work():
    with TestClient(webui.app) as client:
        assert client.put("/api/cloud", json={"api_key": "secret"},
            headers={"Origin": "https://unrelated.example"}).status_code == 403
        from starlette.websockets import WebSocketDisconnect
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws?engine=aliyun-funasr&url=x",
                    headers={"Origin": "https://unrelated.example"}):
                pass


@pytest.mark.skipif(os.name != "nt", reason="Windows user-bound DPAPI")
def test_saved_key_is_encrypted_and_can_be_deleted(tmp_path):
    path = tmp_path / "cloud.bin"
    store = CloudConfigStore(path)
    store.update({"api_key": "private-dpapi-key", "save": True})
    assert b"private-dpapi-key" not in path.read_bytes()
    reloaded = CloudConfigStore(path)
    assert reloaded.get().api_key == "private-dpapi-key"
    reloaded.clear()
    assert not path.exists() and not reloaded.get().configured


def test_stop_invalidates_unstarted_ticket_without_starting_apis():
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "private"})
        ticket = client.post("/api/cloud/sessions", json={"mode": "translate",
            "url": "https://media.test/video.mp4", "source": "zh", "target": "en"}).json()
        client.delete("/api/cloud/sessions/" + ticket["session"])
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            assert ws.receive_json()["type"] == "error"


def test_stop_during_request_start_prevents_later_requests():
    entered, release = threading.Event(), threading.Event()
    requests, results = [], []

    def provider(request):
        requests.append(request)
        entered.set()
        assert release.wait(2)
        return httpx.Response(200, text='data: [DONE]\n\n')

    scope = RequestStop()
    translator = QwenTranslator(CloudConfig(api_key="private"), "en", "zh",
                                transport=httpx.MockTransport(provider))
    worker = threading.Thread(target=lambda: results.extend(translator.translate("Hello", scope)))
    worker.start()
    assert entered.wait(2)
    stopper = threading.Thread(target=scope.set)
    stopper.start()
    assert scope.wait(2)
    release.set()
    worker.join(2)
    stopper.join(2)
    assert not worker.is_alive() and not stopper.is_alive()
    assert list(translator.translate("Another sentence", scope)) == []
    assert not results and len(requests) == 1
    translator.close()


def test_complete_source_correction_can_replace_an_already_translated_clause():
    from cloud_translation import TranslationSession
    first = threading.Event()
    cues = []

    class Translator:
        def translate(self, text, _stop):
            yield "draft"
            yield "译文:" + text

        def close(self):
            pass

    def emit(cue):
        cues.append(cue)
        first.set()

    session = TranslationSession(Translator(), emit, threading.Event())
    original = {"type": "subtitle", "sentence_id": "1:0", "beg": 0, "end": 2,
                "is_final": True, "text": "I love cats."}
    session.submit(original)
    assert first.wait(2)
    session.submit({**original, "text": "I really love cats."})
    session.finish()
    assert [cue["text"] for cue in cues] == ["译文:I love cats.", "译文:I really love cats."]
    assert cues[1]["revision"] > cues[0]["revision"]
    assert all(cue["is_final"] for cue in cues)


def test_withdrawal_prevents_an_inflight_translation_from_returning_after_removal():
    from cloud_translation import TranslationSession
    started, release = threading.Event(), threading.Event()
    cues = []

    class Translator:
        def translate(self, _text, _stop):
            started.set()
            assert release.wait(2)
            yield "已被删除的旧译文"

        def close(self):
            pass

    session = TranslationSession(Translator(), cues.append, threading.Event())
    session.submit({"type": "subtitle", "sentence_id": "1:0", "beg": 0, "end": 2,
                    "is_final": True, "text": "Deleted hypothesis."})
    assert started.wait(2)
    session.withdraw("1:0")
    release.set()
    session.finish()
    assert len(cues) == 1 and cues[0]["type"] == "caption_remove"


def test_empty_final_asr_revision_withdraws_a_previously_released_translation(monkeypatch):
    class Source:
        def __init__(self, *_args, **_kwargs):
            pass
        def iter_chunks(self):
            for _ in range(3):
                yield b"\x00" * 3200
        def close(self):
            pass

    class Engine:
        calls = 0
        def transcribe_chunk(self, _audio):
            self.calls += 1
            return (0.0, 0.3 if self.calls == 3 else None,
                    "" if self.calls == 3 else "I love cats.", None, self.calls == 3, 7)
        def poll(self):
            return (None, None, "", None)
        def finish(self):
            return self.poll()
        def close(self):
            pass

    class Translator:
        def __init__(self, *_args):
            pass
        def translate(self, _text, _stop):
            yield "我喜欢猫。"
        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kw: Engine())
    monkeypatch.setattr(webui, "QwenTranslator", Translator)
    with TestClient(webui.app) as client:
        client.put("/api/cloud", json={"api_key": "sk-private-test"})
        ticket = client.post("/api/cloud/sessions", json={"mode": "translate","url": "https://media.test/video.mp4",
            "source": "en", "target": "zh"}).json()
        with client.websocket_connect("/ws?session=" + ticket["session"]) as ws:
            events = []
            while True:
                event = ws.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break
    removed = [i for i, event in enumerate(events) if event["type"] == "caption_remove"]
    assert len(removed) == 1
    assert events[removed[0]]["sentence_id"] == "7:0"
    assert not any(event["type"] == "subtitle" for event in events[removed[0] + 1:])
