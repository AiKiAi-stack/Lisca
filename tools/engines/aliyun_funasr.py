"""AliyunFunASREngine：阿里云百炼 FunASR 实时识别（dashscope SDK）

复用 streamparser 的 ASRClient（DashScope WebSocket 封装）。
需要环境变量 DASHSCOPE_API_KEY + workspace_id/region 配置。

⚠️ 未实测：需有效 API key（ticket-05 待用户提供 key 后验证）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from .base import Engine, Subtitle  # noqa: E402

DEFAULT_REGION = "cn-beijing"
DEFAULT_MODEL = "fun-asr-realtime"


class AliyunFunASREngine(Engine):
    def __init__(self, language: str = "zh", model: str = DEFAULT_MODEL,
                 workspace_id: str = "", region: str = DEFAULT_REGION,
                 api_key: str = "", chunk_size: int = 3200):
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        if not self._api_key:
            raise RuntimeError(
                "未配置 DASHSCOPE_API_KEY（阿里云百炼）。开通后 export DASHSCOPE_API_KEY=sk-xxx "
                "并配置 workspace_id/region（ticket-05）"
            )
        if not workspace_id:
            raise RuntimeError("需要配置阿里云百炼 workspace_id（业务空间ID）")

        import dashscope
        dashscope.api_key = self._api_key
        dashscope.base_websocket_api_url = (
            f"wss://{workspace_id}.{region}.maas.aliyuncs.com/api-ws/v1/inference"
        )

        from streamparser.asr_client import ASRClient
        from streamparser.asr_config import ASRSettings

        settings = ASRSettings(
            api_key=self._api_key, workspace_id=workspace_id,
            region=region, model=model,
        )
        self.client = ASRClient(settings, chunk_size=chunk_size)
        self.client.start()

    def _drain(self) -> Subtitle:
        """取出缓冲中最新一条已确认字幕"""
        latest: Subtitle = (None, None, "")
        while self.client.subtitle_buffer:
            entry = self.client.subtitle_buffer.popleft()
            if entry.text:
                latest = (entry.start_time, entry.end_time, entry.text)
        return latest

    def transcribe_chunk(self, audio) -> Subtitle:
        import numpy as np
        data = (audio * 32768).astype(np.int16).tobytes()
        self.client.feed_audio(data)
        return self._drain()

    def finish(self) -> Subtitle:
        return self._drain()

    def close(self) -> None:
        self.client.stop()
