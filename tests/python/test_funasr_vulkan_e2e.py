"""Exercise the local service, file-mode WebSocket, and video proxy together."""
import threading
import wave
from urllib.parse import urlencode

import numpy as np
from fastapi.testclient import TestClient


from lisca import funasr_gguf_server  # noqa: E402
from lisca.web import app as webui  # noqa: E402
import lisca.engines.funasr_gguf as engine_module  # noqa: E402


def test_local_file_transcription_websocket_and_video_proxy(monkeypatch, tmp_path):
    seen = {}

    def fake_transcribe(config, audio_path):
        with wave.open(audio_path, "rb") as audio:
            seen["wav"] = (audio.getnchannels(), audio.getframerate(), audio.getnframes())
        return "1\n00:00:00,000 --> 00:00:01,000\n端到端验证成功。"

    monkeypatch.setattr(funasr_gguf_server, "transcribe_file", fake_transcribe)
    config = funasr_gguf_server.ServerConfig(binary="unused", model="unused")
    service = funasr_gguf_server.create_server("127.0.0.1", 0, config)
    service_thread = threading.Thread(target=service.serve_forever, daemon=True)
    service_thread.start()

    class FakeStreamSource:
        def __init__(self, *_args, **_kwargs):
            pass

        def iter_chunks(self):
            yield np.full(16000, 1000, dtype="<i2").tobytes()

        def close(self):
            pass

    monkeypatch.setattr(webui, "StreamSource", FakeStreamSource)
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"local-video-proxy-fixture")

    try:
        original_init = engine_module.FunASRGGUFEngine.__init__

        def init_with_ephemeral_service(self, **kwargs):
            original_init(self, base_url=f"http://127.0.0.1:{service.server_port}", **kwargs)

        monkeypatch.setattr(engine_module.FunASRGGUFEngine, "__init__", init_with_ephemeral_service)
        with TestClient(webui.app) as client:
            with client.websocket_connect(
                "/ws?" + urlencode({"url": str(media), "engine": "funasr-gguf"})
            ) as websocket:
                events = []
                while True:
                    event = websocket.receive_json()
                    events.append(event)
                    if event["type"] == "end":
                        break

            video_response = client.get("/video", params={"url": str(media)})

        assert [event["type"] for event in events] == ["status", "subtitle", "ready", "end"]
        assert events[1]["text"] == "端到端验证成功。"
        assert events[1]["beg"] == 0.0 and events[1]["end"] == 1.0
        assert events.index(next(e for e in events if e["type"] == "subtitle")) < events.index(
            next(e for e in events if e["type"] == "ready")
        )
        assert video_response.status_code == 200
        assert video_response.content == media.read_bytes()
        assert seen["wav"] == (1, 16000, 16000)
    finally:
        service.shutdown()
        service.server_close()
        service_thread.join(timeout=5)
