"""ticket-05: AliyunFunASREngine 测试（mock dashscope/streamparser，无需真实 key）"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))


def _patch_deps(monkeypatch, with_client=True):
    fake_dashscope = types.ModuleType("dashscope")
    monkeypatch.setitem(sys.modules, "dashscope", fake_dashscope)

    fake_streamparser = types.ModuleType("streamparser")
    monkeypatch.setitem(sys.modules, "streamparser", fake_streamparser)

    fake_client_mod = types.ModuleType("streamparser.asr_client")

    class FakeClient:
        instances = []

        def __init__(self, settings, chunk_size=3200, **kwargs):
            self.settings = settings
            self.kwargs = kwargs
            self.started = False
            FakeClient.instances.append(self)

        def start(self):
            self.started = True

        def stop(self):
            pass

    fake_client_mod.ASRClient = FakeClient
    monkeypatch.setitem(sys.modules, "streamparser.asr_client", fake_client_mod)

    fake_config_mod = types.ModuleType("streamparser.asr_config")

    class FakeSettings:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    fake_config_mod.ASRSettings = FakeSettings
    monkeypatch.setitem(sys.modules, "streamparser.asr_config", fake_config_mod)
    return FakeClient


def test_aliyun_without_key_raises(monkeypatch):
    from engines import create_engine

    _patch_deps(monkeypatch)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
        create_engine("aliyun-funasr", workspace_id="ws-1")


def test_aliyun_without_workspace_ok(monkeypatch):
    """新版 SDK 支持默认业务空间，workspace_id 可选"""
    from engines import create_engine

    FakeClient = _patch_deps(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    eng = create_engine("aliyun-funasr")
    assert FakeClient.instances and FakeClient.instances[-1].started
    eng.close()


def test_aliyun_success_constructs_client(monkeypatch):
    from engines import create_engine

    FakeClient = _patch_deps(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    eng = create_engine("aliyun-funasr", workspace_id="ws-1", region="cn-beijing")
    assert FakeClient.instances, "ASRClient 应被构造"
    client = FakeClient.instances[-1]
    assert client.started, "start() 应被调用"
    assert client.settings.api_key == "sk-test"
    assert client.settings.workspace_id == "ws-1"
    assert hasattr(eng, "transcribe_chunk") and hasattr(eng, "finish")
    eng.close()


def test_aliyun_passes_recognition_kwargs(monkeypatch):
    """断句灵敏度/心跳/语种应透传 ASRClient → Recognition（ticket-10）"""
    from engines import create_engine

    FakeClient = _patch_deps(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-test")
    eng = create_engine("aliyun-funasr", max_sentence_silence=800)
    client = FakeClient.instances[-1]
    assert client.kwargs["max_sentence_silence"] == 800
    assert client.kwargs["heartbeat"] is True
    assert client.kwargs["language_hints"] == ["zh"]
    eng.close()
