import time
import types
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient


import webui  # noqa: E402


def test_unsupported_hls_video_is_rejected_before_api_starts(monkeypatch):
    closed = []
    started = []

    class Mpeg2Source:
        def __init__(self, *_args, **_kwargs):
            self.container = types.SimpleNamespace(streams=types.SimpleNamespace(video=[
                types.SimpleNamespace(codec_context=types.SimpleNamespace(name="mpeg2video"))]))

        def close(self):
            closed.append(True)

        def iter_chunks(self):
            raise AssertionError("unsupported video must not be transcribed")

    monkeypatch.setattr(webui, "StreamSource", Mpeg2Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_a, **_k: started.append(True))
    with TestClient(webui.app) as client:
        with client.websocket_connect("/ws?" + urlencode({
            "url": "http://stream.test/live.m3u8", "engine": "aliyun-funasr",
        })) as websocket:
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    assert any(event["type"] == "error" and "MPEG-2" in event["message"]
               for event in events)
    assert closed and not started


@pytest.mark.parametrize(("delay", "expected_start"), [("0.1", 20.0), ("1", 20.0), ("5", 20.0)])
def test_live_hls_continuously_advances_audio_video_delay_buffer(monkeypatch, delay, expected_start):
    class FakeStreamSource:
        timeline_offset_sec = 20.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for _ in range(150):
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
            "/ws?" + urlencode({"url": "http://stream.test/live.m3u8", "engine": "demo", "delay": delay})
        ) as websocket:
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    subtitle = next(event for event in events if event["type"] == "subtitle")
    ready = next(event for event in events if event["type"] == "ready")
    buffer_events = [event for event in events if event["type"] == "buffer"]
    assert subtitle["beg"] == 20.0 and subtitle["end"] == 23.0
    assert subtitle["words"] == [[20.0, 23.0, "test"]]
    assert subtitle["is_final"] is True
    assert ready["start_at"] == pytest.approx(expected_start)
    assert ready["delay"] == float(delay)
    assert len(buffer_events) == 150
    assert buffer_events[-1]["play_at"] == pytest.approx(35.0 - float(delay))


def test_silent_start_waits_until_configured_audio_video_delay_is_buffered(monkeypatch):
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
    assert ready["start_at"] == pytest.approx(20.0)


def test_playback_holds_sentence_start_until_asr_finalizes(monkeypatch):
    class FakeStreamSource:
        timeline_offset_sec = 20.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for _ in range(50):
                yield b"\x00" * 3200

        def close(self):
            pass

    class RevisingEngine:
        batch_mode = False

        def __init__(self, **_kwargs):
            self.calls = 0

        def transcribe_chunk(self, _audio):
            self.calls += 1
            if self.calls == 1:
                return (0.3, None, "partial words still being revised", None, False)
            if self.calls == 30:
                return (0.3, 4.0, "The complete finalized sentence.", None, True)
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return (None, None, "", None)

    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    monkeypatch.setattr(webui, "create_engine", lambda *_a, **_k: RevisingEngine())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with TestClient(webui.app) as client:
        with client.websocket_connect(
            "/ws?" + urlencode({
                "url": "http://stream.test/live.m3u8", "engine": "demo", "delay": "0.1",
            })
        ) as websocket:
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    partial = next(event for event in events
                   if event["type"] == "subtitle" and event["is_final"] is False)
    final = next(event for event in events
                 if event["type"] == "subtitle" and event["is_final"] is True)
    buffers = [event["play_at"] for event in events if event["type"] == "buffer"]
    assert partial["beg"] == final["beg"] == pytest.approx(20.3)
    # Once final arrives, release the full available buffer. The actual player
    # still advances at normal speed through that buffer without seeking.
    assert max(buffers[3:28]) == pytest.approx(20.3)
    assert buffers[29] == pytest.approx(22.9)
    assert buffers[-1] == pytest.approx(24.9)
    ready_index = next(i for i, event in enumerate(events) if event["type"] == "ready")
    assert ready_index < events.index(final)
    assert max(buffers[3:28]) == pytest.approx(20.3)


def test_repeated_long_partial_releases_a_clause_before_final(monkeypatch):
    class FakeStreamSource:
        timeline_offset_sec = 20.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for _ in range(35):
                yield b"\x00" * 3200

        def close(self):
            pass

    class LongPartialEngine:
        batch_mode = False

        def __init__(self):
            self.calls = 0

        def transcribe_chunk(self, _audio):
            self.calls += 1
            words = [(0.3 + i * 0.1, 0.4 + i * 0.1, char)
                     for i, char in enumerate("今天我们一起看比赛")]
            if self.calls == 4:
                return (0.3, None, "今天我们一起看比赛，", words, False, 7)
            if self.calls == 7:
                return (0.3, None, "今天我们一起看比赛，后面还有一百多个词", words, False, 7)
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return (None, None, "", None)

    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    monkeypatch.setattr(webui, "create_engine", lambda *_a, **_k: LongPartialEngine())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with TestClient(webui.app) as client:
        with client.websocket_connect("/ws?" + urlencode({
            "url": "http://stream.test/live.m3u8", "engine": "demo", "delay": "0.1",
        })) as websocket:
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    assert any(event["type"] == "preview" for event in events), events
    preview = next(event for event in events if event["type"] == "preview")
    assert preview["text"] == "今天我们一起看比赛，"
    assert preview["beg"] == pytest.approx(20.3)
    assert preview["end"] > 21.0
    ready = next(event for event in events if event["type"] == "ready")
    assert events.index(ready) < len(events) - 2


def test_nearby_api_sentences_keep_separate_playback_holds(monkeypatch):
    class FakeStreamSource:
        timeline_offset_sec = 0.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for _ in range(20):
                yield b"\x00" * 3200

        def close(self):
            pass

    class CloseSentencesEngine:
        batch_mode = False

        def __init__(self):
            self.calls = 0

        def transcribe_chunk(self, _audio):
            self.calls += 1
            if self.calls == 4:
                return (0.3, None, "第一句", None, False, 1)
            if self.calls == 5:
                return (0.8, None, "第二句", None, False, 2)
            if self.calls == 6:
                return (0.3, 0.7, "第一句。", None, True, 1)
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return (None, None, "", None)

    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    monkeypatch.setattr(webui, "create_engine", lambda *_a, **_k: CloseSentencesEngine())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    with TestClient(webui.app) as client:
        with client.websocket_connect("/ws?" + urlencode({
            "url": "http://stream.test/live.m3u8", "engine": "demo", "delay": "0.1",
        })) as websocket:
            buffers = []
            while True:
                event = websocket.receive_json()
                if event["type"] == "buffer":
                    buffers.append(event["play_at"])
                if event["type"] == "end":
                    break

    assert buffers[4] == pytest.approx(0.3)
    assert buffers[5] == pytest.approx(0.5)
    assert buffers[-1] == pytest.approx(0.8)


def test_late_final_without_prior_partial_never_rewinds_playback(monkeypatch):
    class FakeStreamSource:
        timeline_offset_sec = 20.0

        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            for _ in range(35):
                yield b"\x00" * 3200

        def close(self):
            pass

    class LateFinalEngine:
        batch_mode = False

        def __init__(self, **_kwargs):
            self.calls = 0

        def transcribe_chunk(self, _audio):
            self.calls += 1
            if self.calls == 30:
                return (0.2, 2.0, "Late complete caption.", None, True)
            return (None, None, "", None)

        def poll(self):
            return (None, None, "", None)

        def finish(self):
            return (None, None, "", None)

    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    monkeypatch.setattr(webui, "create_engine", lambda *_a, **_k: LateFinalEngine())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    with TestClient(webui.app) as client:
        with client.websocket_connect(
            "/ws?" + urlencode({
                "url": "http://stream.test/live.m3u8", "engine": "demo", "delay": "0.1",
            })
        ) as websocket:
            events = []
            while True:
                event = websocket.receive_json()
                events.append(event)
                if event["type"] == "end":
                    break

    buffers = [event["play_at"] for event in events if event["type"] == "buffer"]
    assert buffers[28] == pytest.approx(22.8)
    assert all(right >= left for left, right in zip(buffers, buffers[1:])), buffers
    assert buffers[-1] == pytest.approx(23.4)
