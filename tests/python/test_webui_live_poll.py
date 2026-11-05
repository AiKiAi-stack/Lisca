"""Deliver ready ASR callbacks while a live source is waiting for input."""
import threading

from fastapi.testclient import TestClient

import webui
from cloud_translation import CloudConfigStore


def test_live_caption_does_not_wait_for_the_next_source_read(tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    reading = threading.Event()
    release = threading.Event()
    read_finished = threading.Event()
    source_closed = threading.Event()
    engine_closed = threading.Event()

    class Source:
        def __init__(self, *_args, **_kwargs): pass
        def iter_chunks(self):
            yield b"\x00\x10" * 16000
            reading.set()
            try:
                release.wait(5)
            finally:
                read_finished.set()
        def interrupt(self):
            release.set()
        def close(self):
            assert read_finished.is_set(), "source close overlapped a source read"
            source_closed.set()

    class Engine:
        def __init__(self):
            self.owner = threading.get_ident()
            self.delivered = False
        def transcribe_chunk(self, _audio):
            assert threading.get_ident() == self.owner
            return (0.0, 1.0, "字幕不能等下一段音频。", None, False, 1)
        def poll(self):
            assert threading.get_ident() == self.owner
            if reading.is_set() and not self.delivered:
                self.delivered = True
                return (0.0, 1.0, "字幕不能等下一段音频。", None, True, 1)
            return (None, None, "", None)
        def finish(self): return self.poll()
        def close(self):
            assert threading.get_ident() == self.owner
            assert source_closed.is_set() and read_finished.is_set()
            engine_closed.set()

    monkeypatch.setattr(webui, "StreamSource", Source)
    monkeypatch.setattr(webui, "create_engine", lambda *_args, **_kwargs: Engine())
    # Bound a regression failure without unblocking a passing source read.
    watchdog = threading.Timer(2, release.set)
    watchdog.start()
    try:
        with TestClient(webui.app) as client:
            client.put("/api/cloud", json={"api_key": "test-private-key"})
            ticket = client.post("/api/cloud/sessions", json={
                "url": "https://media.test/live.m3u8",
            }).json()
            with client.websocket_connect("/ws?session=" + ticket["session"]) as socket:
                assert reading.wait(2)
                events = []
                while True:
                    event = socket.receive_json()
                    events.append(event)
                    if event["type"] == "subtitle" and event["is_final"]:
                        assert not release.is_set(), "caption waited for the blocked source read"
                        assert event["text"] == "字幕不能等下一段音频。"
                    if event["type"] == "ready":
                        break
                    assert event["type"] != "end", event
                assert not release.is_set(), "playback readiness waited for the blocked source read"
                assert any(event["type"] == "subtitle" and event["is_final"] for event in events)
                assert any(event["type"] == "buffer" and event["play_at"] >= 0.9 for event in events)
                assert client.delete("/api/cloud/sessions/" + ticket["session"]).json() == {"ok": True}
                while True:
                    event = socket.receive_json()
                    if event["type"] == "end":
                        break
                assert event == {"type": "end", "reason": "cancelled"}
        assert source_closed.is_set() and read_finished.is_set() and engine_closed.is_set()
    finally:
        watchdog.cancel()
        release.set()
