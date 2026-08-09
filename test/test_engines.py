"""ticket-04: 引擎抽象层测试（mock 后端，无需 GPU/模型）"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from engines import create_engine  # noqa: E402


class FakeOnline:
    def __init__(self):
        self.chunks = []

    def insert_audio_chunk(self, audio):
        self.chunks.append(audio)

    def process_iter(self):
        return (0.0, 1.0, "测试字幕") if self.chunks else (None, None, "")

    def finish(self):
        return (1.0, 2.0, "结束")


class FakeASR:
    sep = " "


def test_create_engine_unknown_raises():
    with pytest.raises(ValueError, match="未知引擎"):
        create_engine("nonexistent")


def test_create_engine_aliyun_requires_key(monkeypatch):
    import engines.aliyun_funasr as af

    # 屏蔽真实 dashscope/streamparser import（无 key 路径不会触达）
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        create_engine("aliyun-funasr")


def test_faster_whisper_engine_delegates(monkeypatch):
    import engines.faster_whisper as fw

    fake_online = FakeOnline()
    monkeypatch.setattr(fw, "FasterWhisperASR", lambda **kw: FakeASR())
    monkeypatch.setattr(fw, "OnlineASRProcessor", lambda asr: fake_online)

    eng = create_engine("faster-whisper", language="zh", model_dir="/fake/model")
    assert eng.transcribe_chunk([0.1, 0.2]) == (0.0, 1.0, "测试字幕", None)
    assert eng.finish() == (1.0, 2.0, "结束", None)
    assert fake_online.chunks == [[0.1, 0.2]]


def test_engine_interface_abstract():
    from engines.base import Engine

    with pytest.raises(TypeError):
        Engine()  # 抽象类不可实例化
