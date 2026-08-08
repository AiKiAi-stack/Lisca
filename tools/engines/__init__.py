"""引擎工厂：按名称创建引擎（可配置切换）"""
from typing import Optional

from .base import Engine


def create_engine(name: str = "faster-whisper", **kwargs) -> Engine:
    """创建引擎实例。

    name:
        faster-whisper  本地 faster-whisper（默认，离线可用）
        aliyun-funasr   阿里云百炼 FunASR API（ticket-05，需 DASHSCOPE_API_KEY）
    """
    if name == "faster-whisper":
        from .faster_whisper import FasterWhisperEngine
        return FasterWhisperEngine(**kwargs)
    if name in ("aliyun-funasr", "aliyun", "api"):
        from .aliyun_funasr import AliyunFunASREngine
        return AliyunFunASREngine(**kwargs)
    raise ValueError(f"未知引擎: {name}（可用: faster-whisper / aliyun-funasr）")
