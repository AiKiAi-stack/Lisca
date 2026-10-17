import os
import sys
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import webui  # noqa: E402


def test_live_hls_subtitles_are_aligned_to_the_player_timeline(monkeypatch):
    class FakeStreamSource:
        timeline_offset_sec = 20.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for _ in range(5):
                yield b"\x00" * 3200

        def close(self):
            pass

    class FakeEngine:
        batch_mode = False

        def __init__(self, **_kwargs):
            self.sent = False

        def transcribe_chunk(self, _audio):
            if self.sent:
                return (None, None, "", None)
            self.sent = True
            return (0.0, 3.0, "test", [(0.0, 3.0, "test")])

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return (None, None, "", None)

    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    monkeypatch.setattr(webui, "create_engine", lambda *_a, **_k: FakeEngine())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with TestClient(webui.app) as client:
        with client.websocket_connect(
            "/ws?" + urlencode({"url": "http://stream.test/live.m3u8", "engine": "demo"})
        ) as websocket:
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    subtitle = next(event for event in events if event["type"] == "subtitle")
    ready = next(event for event in events if event["type"] == "ready")
    assert subtitle["beg"] == 20.0 and subtitle["end"] == 23.0
    assert subtitle["words"] == [[20.0, 23.0, "test"]]
    assert ready["start_at"] == 21.0


def test_silent_start_fallback_does_not_seek_ahead_of_asr_coverage(monkeypatch):
    class FakeStreamSource:
        timeline_offset_sec = 20.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for _ in range(70):
                yield b"\x00" * 3200

        def close(self):
            pass

    class SilentEngine:
        batch_mode = False

        def __init__(self, **_kwargs):
            pass

        def transcribe_chunk(self, _audio):
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return (None, None, "", None)

    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    monkeypatch.setattr(webui, "create_engine", lambda *_a, **_k: SilentEngine())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with TestClient(webui.app) as client:
        with client.websocket_connect(
            "/ws?" + urlencode({"url": "http://stream.test/live.m3u8", "engine": "demo"})
        ) as websocket:
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    ready = next(event for event in events if event["type"] == "ready")
    assert ready["start_at"] == 20.0
