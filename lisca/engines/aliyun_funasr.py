"""AliyunFunASREngine：阿里云百炼 FunASR 实时识别（dashscope SDK）

复用 lisca.asr 的 ASRClient（DashScope WebSocket 封装）。
API key 从参数或环境变量 DASHSCOPE_API_KEY 读取（建议经 .env / 环境注入，勿硬编码）。
workspace_id 可选：新版 dashscope SDK（>=1.20）支持默认业务空间。
"""
import os
import threading


from .base import Engine, Subtitle  # noqa: E402

DEFAULT_REGION = "cn-beijing"
DEFAULT_MODEL = "fun-asr-realtime"


class AliyunFunASREngine(Engine):
    def __init__(self, language: str = "zh", model: str = DEFAULT_MODEL,
                 workspace_id: str = "", region: str = DEFAULT_REGION,
                 api_key: str = "", chunk_size: int = 3200, model_dir: str = "",
                 max_sentence_silence: int = 400, heartbeat: bool = True,
                 language_hints=("zh",), multi_threshold_mode_enabled: bool = True,
                 stop_event=None):
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

        from lisca.asr.asr_client import ASRClient
        from lisca.asr.asr_config import ASRSettings

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
        self._monitor_done = threading.Event()
        self._stop_monitor = None
        self._stop_error = None
        if stop_event is not None:
            self._stop_monitor = threading.Thread(
                target=self._watch_stop, args=(stop_event,), daemon=True)
            try:
                self._stop_monitor.start()
            except RuntimeError:
                self.client.cancel()
                raise

    def _watch_stop(self, stop_event):
        # Cancellation must interrupt finish(), including its server-complete wait.
        while not self._monitor_done.wait(0.05):
            if stop_event.is_set():
                try:
                    self.client.cancel()
                except Exception as exc:
                    self._stop_error = exc
                return

    def _close_monitor(self):
        self._monitor_done.set()
        if self._stop_monitor is not None:
            self._stop_monitor.join(timeout=5.0)
            if self._stop_monitor.is_alive():
                raise TimeoutError("Recognition cancellation monitor did not exit")
        if self._stop_error is not None:
            raise self._stop_error

    def _drain(self) -> Subtitle:
        """FIFO 取出一条待显示句子（partial 实时文本，前端定时快照成块显示）"""
        self.client.raise_if_error()
        while self.client.subtitle_buffer:
            entry = self.client.subtitle_buffer.popleft()
            if entry.text or (entry.is_final and getattr(entry, "sentence_id", None) is not None):
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

    def finalize(self) -> None:
        """Close audio input without consuming results, so callers can keep polling."""
        if not getattr(self, "_stopped", False):
            self.client.stop()
            self._stopped = True
        self._close_monitor()
        self.client.raise_if_error()

    def finish(self) -> Subtitle:
        """Finalize input and return the first remaining result."""
        self.finalize()
        return self._drain()

    def close(self) -> None:
        try:
            if not getattr(self, "_stopped", False):
                cancel = getattr(self.client, "cancel", None)
                if callable(cancel):
                    cancel()
                else:
                    self.client.stop()
                self._stopped = True
        finally:
            self._close_monitor()
