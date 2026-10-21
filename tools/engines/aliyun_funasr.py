"""AliyunFunASREngine：阿里云百炼 FunASR 实时识别（dashscope SDK）

复用 streamparser 的 ASRClient（DashScope WebSocket 封装）。
API key 从参数或环境变量 DASHSCOPE_API_KEY 读取（建议经 .env / 环境注入，勿硬编码）。
workspace_id 可选：新版 dashscope SDK（>=1.20）支持默认业务空间。
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
                 api_key: str = "", chunk_size: int = 3200, model_dir: str = "",
                 max_sentence_silence: int = 400, heartbeat: bool = True,
                 language_hints=("zh",), multi_threshold_mode_enabled: bool = True):
        # model_dir 仅为兼容通用 CLI 传参（本地引擎使用），API 引擎忽略
        del model_dir
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        if not self._api_key:
            raise RuntimeError(
                "未配置 DASHSCOPE_API_KEY（阿里云百炼）。"
                "export DASHSCOPE_API_KEY=sk-xxx 后重试（ticket-05）"
            )

        import dashscope
        dashscope.api_key = self._api_key
        if workspace_id:
            # 显式指定业务空间（多空间账号）
            dashscope.base_websocket_api_url = (
                f"wss://{workspace_id}.{region}.maas.aliyuncs.com/api-ws/v1/inference"
            )

        from streamparser.asr_client import ASRClient
        from streamparser.asr_config import ASRSettings

        settings = ASRSettings(
            api_key=self._api_key, workspace_id=workspace_id,
            region=region, model=model,
        )
        # 断句灵敏度（VAD 静音阈值 ms）+ 多阈值模式防止连续讲话切成超长段
        # + 心跳防长静音断连 + 固定中文语种（fun-asr-realtime 仅支持 1 个语种值）
        recognition_kwargs = {}
        if max_sentence_silence:
            recognition_kwargs["max_sentence_silence"] = max_sentence_silence
        recognition_kwargs["multi_threshold_mode_enabled"] = bool(multi_threshold_mode_enabled)
        if heartbeat:
            recognition_kwargs["heartbeat"] = True
        if language_hints:
            recognition_kwargs["language_hints"] = list(language_hints)
        self.client = ASRClient(settings, chunk_size=chunk_size,
                                **recognition_kwargs)
        self.client.start()

    def _drain(self) -> Subtitle:
        """FIFO 取出一条待显示句子（partial 实时文本，前端定时快照成块显示）"""
        self.client.raise_if_error()
        while self.client.subtitle_buffer:
            entry = self.client.subtitle_buffer.popleft()
            if entry.text:
                beg = entry.start_time
                end = entry.end_time if (entry.end_time and entry.end_time > beg) else None
                return (beg, end, entry.text, entry.words, entry.is_final,
                        getattr(entry, "sentence_id", None))
        return (None, None, "", None, False)

    def transcribe_chunk(self, audio) -> Subtitle:
        import numpy as np
        data = (audio * 32768).astype(np.int16).tobytes()
        self.client.feed_audio(data)
        return self._drain()

    def poll(self) -> Subtitle:
        """只取积压句子（不推音频）：快推场景下把所有已确认句子逐条取出"""
        return self._drain()

    def finish(self) -> Subtitle:
        """流结束：停止 WS 等服务端处理完所有已推音频（句子进 buffer），
        返回第一条；剩余句子由调用方 finish 后循环 poll() 取尽。"""
        if not getattr(self, "_stopped", False):
            self.client.stop()
            self._stopped = True
        self.client.raise_if_error()
        return self._drain()

    def close(self) -> None:
        if not getattr(self, "_stopped", False):
            cancel = getattr(self.client, "cancel", None)
            if callable(cancel):
                cancel()
            else:
                self.client.stop()
            self._stopped = True
