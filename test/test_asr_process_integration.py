"""Public client and real SDK connected only to the loopback test service."""
import time
import threading

import dashscope
import pytest

from streamparser.asr_client import ASRClient, RecognitionFailure
from streamparser.asr_config import ASRSettings
from test_recognition_process import LocalService


@pytest.fixture(autouse=True)
def loopback_only(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.mark.parametrize("mode,ready", [
    ("handshake", "connected"), ("no_task_start", "started"), ("no_finish", "started"),
])
def test_public_client_cancel_reaps_actual_sdk_and_rejects_later_audio(monkeypatch, mode, ready):
    with LocalService(mode) as service:
        monkeypatch.setattr(dashscope, "base_websocket_api_url", service.url)
        client = ASRClient(ASRSettings(api_key="dummy-local-test-key"))
        try:
            client.start()
            assert getattr(service, ready).wait(8)
            client.feed_audio(b"\x01\x00" * 1600)
            started = time.monotonic()
            client.cancel()
            assert time.monotonic() - started < 5
            assert service.closed.wait(2)
            with pytest.raises(RuntimeError):
                client.feed_audio(b"\x00\x00" * 1600)
            assert list(client.subtitle_buffer) == []
            assert service.requests == 1
        finally:
            client.cancel()


def test_public_client_eof_keeps_ordered_audio_and_final_subtitles(monkeypatch):
    with LocalService("normal") as service:
        monkeypatch.setattr(dashscope, "base_websocket_api_url", service.url)
        client = ASRClient(ASRSettings(api_key="dummy-local-test-key"))
        try:
            client.start()
            pcm = b"\x01\x00" * 1600 + b"\x02\x00" * 123
            client.feed_audio(pcm)
            client.stop()
            assert b"".join(service.audio) == pcm
            assert [entry.text for entry in client.subtitle_buffer] == ["原文草稿", "原文尾部"]
            assert list(client.subtitle_buffer)[-1].is_final is True
            assert service.requests == 1
        finally:
            client.cancel()


def test_public_client_eof_deadline_reports_failure_and_closes_connection(monkeypatch):
    with LocalService("no_finish") as service:
        monkeypatch.setattr(dashscope, "base_websocket_api_url", service.url)
        monkeypatch.setattr("streamparser.asr_client.FINALIZE_TIMEOUT_SECONDS", 1.0)
        client = ASRClient(ASRSettings(api_key="dummy-local-test-key"))
        try:
            client.start()
            assert service.started.wait(8)
            client.feed_audio(b"\x01\x00" * 1600)
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                client.stop()
            assert time.monotonic() - started < 1.3
            assert service.closed.wait(2)
            assert service.requests == 1
        finally:
            client.cancel()


def test_public_client_preserves_real_sdk_permission_error_during_eof(monkeypatch):
    with LocalService("error_at_finish") as service:
        monkeypatch.setattr(dashscope, "base_websocket_api_url", service.url)
        client = ASRClient(ASRSettings(api_key="dummy-local-test-key"))
        try:
            client.start()
            client.feed_audio(b"\x01\x00" * 1600)
            with pytest.raises(RecognitionFailure) as caught:
                client.stop()
            assert caught.value.code == "Model.AccessDenied"
            assert caught.value.status_code == 44
            assert "private" not in str(caught.value)
            assert service.closed.wait(2)
            assert service.requests == 1
        finally:
            client.cancel()


def test_http_cancel_interrupts_webui_already_waiting_for_real_sdk_eof(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from tools import webui
    from cloud_translation import CloudConfigStore

    closed = threading.Event()

    class Source:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield b"\x01\x00" * 1600

        def close(self):
            closed.set()

    monkeypatch.setattr(webui, "_CLOUD", CloudConfigStore(tmp_path / "cloud.bin"))
    monkeypatch.setattr(webui, "_CLOUD_JOBS", {})
    monkeypatch.setattr(webui, "StreamSource", Source)
    with LocalService("no_finish") as service, TestClient(webui.app) as http:
        monkeypatch.setattr(dashscope, "base_websocket_api_url", service.url)
        http.put("/api/cloud", json={"api_key": "dummy-local-test-key"})
        ticket = http.post("/api/cloud/sessions", json={
            "mode": "transcribe", "url": "https://media.test/clip.mp4", "source": "auto",
        }).json()
        try:
            with http.websocket_connect("/ws?session=" + ticket["session"]) as ws:
                assert service.finished.wait(8), "actual SDK did not enter EOF wait"
                started = time.monotonic()
                assert http.delete("/api/cloud/sessions/" + ticket["session"]).status_code == 200
                assert service.closed.wait(5), "HTTP stop did not close the SDK connection"
                assert time.monotonic() - started < 5
                events = []
                while not events or events[-1]["type"] != "end":
                    events.append(ws.receive_json())
                assert events[-1] == {"type": "end", "reason": "cancelled"}
                assert not any(event["type"] == "error" for event in events)
                assert closed.wait(1)
                assert service.requests == 1
        finally:
            http.delete("/api/cloud")
