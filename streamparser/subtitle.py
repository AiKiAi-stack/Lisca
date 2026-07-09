import os

import numpy as np
from dataclasses import dataclass
from PIL import Image, ImageDraw, ImageFont
from typing import Optional

from streamparser.frame import Frame, FrameType


@dataclass
class SubtitleEntry:
    index: int
    text: str
    start_time: float
    end_time: Optional[float]
    is_final: bool

    def _format_time(self, seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int(round((seconds - int(seconds)) * 1000))
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    def to_srt(self) -> str:
        if self.end_time is None:
            return ""
        start = self._format_time(self.start_time)
        end = self._format_time(self.end_time)
        return f"{self.index}\n{start} --> {end}\n{self.text}\n"


class SubtitleOverlay:
    """FrameProcessor-compatible filter that renders CJK text onto video frames."""

    def __init__(self, font_path: str = "", font_size: int = 32) -> None:
        self.font_size = font_size
        self._entry: Optional[SubtitleEntry] = None
        self._font = self._load_font(font_path)

    def _load_font(self, font_path: str):
        if font_path and os.path.exists(font_path):
            return ImageFont.truetype(font_path, self.font_size)
        candidates = [
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return ImageFont.truetype(candidate, self.font_size)
        return ImageFont.load_default()

    def update_entry(self, entry: SubtitleEntry) -> None:
        self._entry = entry

    def __call__(self, frame: Frame) -> Frame:
        if frame.type != FrameType.VIDEO or self._entry is None:
            return frame
        self._render(frame)
        return frame

    def _render(self, frame: Frame) -> None:
        assert self._entry is not None
        img = Image.fromarray(frame.data, mode="RGB")
        draw = ImageDraw.Draw(img)
        text = self._entry.text

        bbox = draw.textbbox((0, 0), text, font=self._font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        x = (img.width - text_w) // 2
        y = img.height - text_h - 40

        stroke_width = 2
        for dx in range(-stroke_width, stroke_width + 1):
            for dy in range(-stroke_width, stroke_width + 1):
                if dx != 0 or dy != 0:
                    draw.text((x + dx, y + dy), text, font=self._font, fill=(0, 0, 0))

        draw.text((x, y), text, font=self._font, fill=(255, 255, 255))

        frame.data = np.array(img, dtype=np.uint8)
