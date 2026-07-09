# encoding:utf-8
import os
import pytest
from streamparser.asr_config import ASRSettings, ASRProvider, SubtitleConfig, ReconnectConfig


class TestASRSettings:
    def test_from_dict_basic(self):
        data = {
            "provider": "dashscope",
            "api_key": "${DASHSCOPE_API_KEY}",
            "workspace_id": "ws-123",
            "region": "cn-beijing",
            "model": "fun-asr-realtime",
        }
        settings = ASRSettings.from_dict(data)
        assert settings.provider == ASRProvider.DASHSCOPE
        assert settings.api_key == "${DASHSCOPE_API_KEY}"
        assert settings.workspace_id == "ws-123"
        assert settings.model == "fun-asr-realtime"

    def test_from_dict_resolves_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-test-123")
        data = {"provider": "dashscope", "api_key": "${MY_KEY}"}
        settings = ASRSettings.from_dict(data)
        assert settings.api_key == "sk-test-123"

    def test_from_dict_defaults(self):
        data = {"provider": "dashscope", "api_key": "${DASHSCOPE_API_KEY}"}
        settings = ASRSettings.from_dict(data)
        assert settings.workspace_id == ""

    def test_websocket_url_construction(self):
        settings = ASRSettings(api_key="sk-test", workspace_id="ws-42", region="cn-beijing")
        assert "ws-42.cn-beijing.maas.aliyuncs.com" in settings.websocket_url

    def test_subtitle_config_defaults(self):
        config = SubtitleConfig()
        assert config.font_size == 32
        assert config.max_lines == 2
        assert config.position == "bottom"

    def test_reconnect_config_defaults(self):
        config = ReconnectConfig()
        assert config.max_retries == 10
        assert config.base_delay == 1.0
        assert config.max_delay == 30.0
