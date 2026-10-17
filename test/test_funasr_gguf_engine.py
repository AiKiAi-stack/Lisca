"""FunASR GGUF client engine behavior without a running model service."""
import json
import os
import sys
import io
import wave

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from engines.funasr_gguf import FunASRGGUFEngine, _parse_srt, _wav_bytes  # noqa: E402


def test_parse_srt_preserves_sentence_timestamps():
    result = _parse_srt("1\n00:00:00,250 --> 00:00:01,500\n你好。\n", 2.0)
    assert result == [(0.25, 1.5, "你好。", None)]


def test_plain_transcript_spans_the_recording():
    assert _parse_srt("recognized text", 2.5) == [(0.0, 2.5, "recognized text", None)]


def test_wav_bytes_contains_mono_16khz_pcm():
    wav = _wav_bytes([np.array([0.0, 0.5], dtype=np.float32)])
    with wave.open(io.BytesIO(wav), "rb") as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 16000
        assert audio.getnframes() == 2


def test_engine_buffers_until_finish_then_polls_segments(monkeypatch):
    engine = FunASRGGUFEngine(base_url="http://127.0.0.1:8765")
    engine.transcribe_chunk(np.ones(16000, dtype=np.float32) * 0.25)
    assert not engine._pending
    assert engine._duration == 1.0
    monkeypatch.setattr(engine, "_transcribe", lambda: [
        (0.0, 0.5, "第一句", None), (0.5, 1.0, "第二句", None),
    ])
    assert engine.finish() == (0.0, 0.5, "第一句", None)
    assert engine.poll() == (0.5, 1.0, "第二句", None)
    assert engine.finish() == (None, None, "", None)


def test_engine_posts_wav_and_reads_openai_style_text(monkeypatch):
    engine = FunASRGGUFEngine()
    engine.transcribe_chunk(np.zeros(16000, dtype=np.float32))
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def read(self): return json.dumps({"text": "1\n00:00:00,000 --> 00:00:01,000\n测试"}).encode()
    def fake_urlopen(request, timeout):
        assert request.full_url.endswith("/v1/audio/transcriptions")
        assert b"RIFF" in request.data
        assert timeout == 600.0
        return Response()
    monkeypatch.setattr("engines.funasr_gguf.urllib.request.urlopen", fake_urlopen)
    assert engine.finish() == (0.0, 1.0, "测试", None)
