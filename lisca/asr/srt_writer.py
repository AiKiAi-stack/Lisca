import os
from pathlib import Path
from typing import TextIO

from lisca.asr.subtitle import SubtitleEntry
from lisca.asr.logger import Loggers


class SRTWriter:
    def __init__(self, base_dir: str = "results"):
        self.base_dir = base_dir
        self._current_file: str | None = None
        self._file: TextIO | None = None
        self._entry_index = 0
        self.logger = Loggers().get_logger(__name__)

    def open(self, video_filename: str) -> None:
        os.makedirs(self.base_dir, exist_ok=True)
        stem = Path(video_filename).stem
        self._current_file = os.path.join(self.base_dir, f"{stem}.srt")
        self._file = open(self._current_file, "w", encoding="utf-8")
        self._entry_index = 0

    def write_entry(self, entry: SubtitleEntry) -> None:
        if not entry.is_final:
            self.logger.debug(
                "Skipping non-final entry (current index: %d)", self._entry_index
            )
            return
        assert self._file is not None, "write_entry called without open()"
        self._entry_index += 1
        entry.index = self._entry_index
        self._file.write(entry.to_srt())
        self._file.flush()

    def rotate(self, video_filename: str) -> None:
        self.close()
        self.open(video_filename)

    def close(self) -> None:
        if self._file and not self._file.closed:
            self._file.close()
        self._file = None
        self._current_file = None
        self._entry_index = 0
