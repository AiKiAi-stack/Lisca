"""FasterWhisperEngine：本地 faster-whisper 引擎（whisper_streaming 后端）

GTX 1050 Ti（Pascal）适配：compute_type=int8_float32（whisper_online.py 已改）。
运行需 LD_LIBRARY_PATH 指向 venv 内 nvidia/cublas+cudnn（见 tools/run_subs.sh）。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "whisper_streaming"))

from whisper_online import FasterWhisperASR, OnlineASRProcessor  # noqa: E402

from .base import Engine, Subtitle  # noqa: E402


class FasterWhisperEngine(Engine):
    def __init__(self, language: str = "zh", model_dir: str = "models/faster-whisper-small",
                 min_chunk_size: float = 1.0):
        self.asr = FasterWhisperASR(lan=language, model_dir=model_dir)
        self.online = OnlineASRProcessor(self.asr)
        self._min_chunk_size = min_chunk_size

    def transcribe_chunk(self, audio) -> Subtitle:
        self.online.insert_audio_chunk(audio)
        beg, end, text = self.online.process_iter()
        return (beg, end, text, None)  # 本地引擎无字级时间戳

    def finish(self) -> Subtitle:
        beg, end, text = self.online.finish()
        return (beg, end, text, None)
