from dataclasses import dataclass
from typing import Optional


@dataclass
class SubtitleEntry:
    index: int
    text: str
    start_time: float
    end_time: Optional[float]
    is_final: bool
    words: Optional[list] = None  # [(begin_sec, end_sec_or_None, text)] 字级时间戳（aliyun 引擎提供）

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
