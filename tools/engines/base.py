"""ticket-04: 引擎抽象层 —— 引擎接口

所有 ASR 引擎实现同一接口：消费 16kHz mono float32 音频块，产出句子级字幕。
本地（FasterWhisperEngine）与 API（AliyunFunASREngine，ticket-05）可无缝切换。
"""
from abc import ABC, abstractmethod
from typing import Optional, Tuple, Union

Subtitle = Union[
    Tuple[Optional[float], Optional[float], str, Optional[list]],
    Tuple[Optional[float], Optional[float], str, Optional[list], bool],
    Tuple[Optional[float], Optional[float], str, Optional[list], bool, Optional[int]],
]
# (beg_sec, end_sec, text, words?, is_final?, sentence_id?)；旧式四元组引擎默认视为 final。
# words = [(begin_sec, end_sec_or_None, text)]
# 字级时间戳，仅 API 引擎（aliyun fun-asr-realtime）提供，本地引擎为 None


class Engine(ABC):
    """ASR 引擎接口"""

    @abstractmethod
    def transcribe_chunk(self, audio) -> Subtitle:
        """处理一个音频块（np.float32, 16kHz）。
        返回已确认的句子 (beg, end, text, words?)；无确认内容时返回
        (None, None, "", None)。"""

    def poll(self) -> Subtitle:
        """只取缓冲中已就绪的句子（不推入音频），无则返回 (None, None, "", None)。
        异步引擎（API 回调）用；同步引擎默认无积压。"""
        return (None, None, "", None)

    @abstractmethod
    def finish(self) -> Subtitle:
        """流结束时刷新剩余内容（最后一句）。"""

    def close(self) -> None:  # 默认空实现，资源型引擎可覆写
        pass
