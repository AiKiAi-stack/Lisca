"""Deterministic fake ASR engine for exercising streaming and subtitle plumbing.

This engine does not recognize speech. It emits timestamped sample captions so
the WebSocket, real-time audio pacing, player, and subtitle overlay can be
verified without a model or API credentials.
"""
from .base import Engine, Subtitle


class DemoEngine(Engine):
    def __init__(self, language: str = "zh", caption_interval: float = 1.0,
                 **_kwargs):
        del language
        if caption_interval <= 0:
            raise ValueError("caption_interval 必须大于 0")
        self.caption_interval = caption_interval
        self.elapsed = 0.0
        self.next_caption = caption_interval
        self.index = 0

    def transcribe_chunk(self, audio) -> Subtitle:
        self.elapsed += len(audio) / 16000.0
        if self.elapsed < self.next_caption:
            return (None, None, "", None)

        beg = self.next_caption - self.caption_interval
        end = self.next_caption
        self.index += 1
        self.next_caption += self.caption_interval
        text = f"演示字幕 {self.index:02d}（非识别结果）"
        return (beg, end, text, [(beg, end, text)])

    def finish(self) -> Subtitle:
        return (None, None, "", None)
