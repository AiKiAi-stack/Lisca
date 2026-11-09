# encoding:utf-8
import pytest
from lisca.asr.asr_config import ASRSettings


class TestASRSettings:
    def test_api_key_env_expansion(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-test-123")
        settings = ASRSettings(api_key="${MY_KEY}")
        assert settings.api_key == "sk-test-123"

    def test_api_key_kept_when_not_env_ref(self):
        settings = ASRSettings(api_key="sk-test")
        assert settings.api_key == "sk-test"

    def test_websocket_url_construction(self):
        settings = ASRSettings(api_key="sk-test", workspace_id="ws-42", region="cn-beijing")
        assert "ws-42.cn-beijing.maas.aliyuncs.com" in settings.websocket_url

    def test_missing_env_var_keeps_placeholder(self, monkeypatch):
        monkeypatch.delenv("NO_SUCH_KEY", raising=False)
        settings = ASRSettings(api_key="${NO_SUCH_KEY}")
        assert settings.api_key == "${NO_SUCH_KEY}"
