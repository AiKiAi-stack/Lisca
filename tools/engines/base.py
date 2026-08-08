"""ticket-04: 引擎抽象层 —— 引擎接口

所有 ASR 引擎实现同一接口：消费 16kHz mono float32 音频块，产出句子级字幕。
本地（FasterWhisperEngine）与 API（AliyunFunASREngine，ticket-05）可无缝切换。
"""
from abc import ABC, abstractmethod
from typing import Optional, Tuple

Subtitle = Tuple[Optional[float], Optional[float], str]  # (beg_sec, end_sec, text)


class Engine(ABC):
    """ASR 引擎接口"""

    @abstractmethod
    def transcribe_chunk(self, audio) -> Subtitle:
        """处理一个音频块（np.float32, 16kHz）。
        返回已确认的句子 (beg, end, text)；无确认内容时返回 (None, None, "")。"""

    def poll(self) -> Subtitle:
        """只取缓冲中已就绪的句子（不推入音频），无则返回 (None, None, "")。
        异步引擎（API 回调）用；同步引擎默认无积压。"""
        return (None, None, "")

    @abstractmethod
    def finish(self) -> Subtitle:
        """流结束时刷新剩余内容（最后一句）。"""

    def close(self) -> None:  # 默认空实现，资源型引擎可覆写
        pass
