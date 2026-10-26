"""Cloud configuration and translation through the local HTTP/WS boundary."""
import os
import sys
import threading
import types

import httpx
import pytest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from cloud_translation import CloudConfig, CloudConfigStore, CloudError, QwenTranslator, RequestStop  # noqa: E402










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


