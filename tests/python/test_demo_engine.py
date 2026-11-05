"""Demo ASR engine emits deterministic, timestamped captions without credentials."""


from engines import create_engine  # noqa: E402


def test_demo_engine_emits_timestamped_captions_without_api_key(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    engine = create_engine("demo", caption_interval=0.2)

    assert engine.transcribe_chunk([0.0] * 1600)[2] == ""
    beg, end, text, words = engine.transcribe_chunk([0.0] * 1600)

    assert (beg, end) == (0.0, 0.2)
    assert text == "演示字幕 01（非识别结果）"
    assert words == [(0.0, 0.2, text)]
    assert engine.finish() == (None, None, "", None)
