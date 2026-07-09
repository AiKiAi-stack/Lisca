from typing import Optional

from streamparser.asr_client import ASRClient
from streamparser.asr_config import ASRSettings
from streamparser.audio_processor import AudioProcessor
from streamparser.srt_writer import SRTWriter
from streamparser.subtitle import SubtitleOverlay


class ASRSubtitlePipeline:
    def __init__(self, settings: ASRSettings) -> None:
        self._settings = settings
        self._asr: Optional[ASRClient] = None
        self._audio_processor: Optional[AudioProcessor] = None
        self._srt_writer: Optional[SRTWriter] = None
        self._subtitle_overlay: Optional[SubtitleOverlay] = None

    def start(self, video_filename: str) -> None:
        self._asr = ASRClient(self._settings)
        self._asr.start()
        self._audio_processor = AudioProcessor()
        self._srt_writer = SRTWriter()
        self._srt_writer.open(video_filename)
        self._subtitle_overlay = SubtitleOverlay()

    def stop(self) -> None:
        if self._asr is not None:
            self._asr.stop()
        if self._srt_writer is not None:
            self._srt_writer.close()

    def feed_audio_frame(self, frame: bytes) -> None:
        if self._asr is not None:
            self._asr.feed_audio(frame)

    def rotate(self, video_filename: str) -> None:
        if self._srt_writer is not None:
            self._srt_writer.rotate(video_filename)

    @property
    def subtitle_overlay(self) -> SubtitleOverlay:
        assert self._subtitle_overlay is not None
        return self._subtitle_overlay
