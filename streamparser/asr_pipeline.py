from typing import Optional

import av
import numpy as np

from streamparser.asr_client import ASRClient
from streamparser.asr_config import ASRSettings
from streamparser.audio_processor import AudioProcessor
from streamparser.frame import Frame
from streamparser.srt_writer import SRTWriter
from streamparser.subtitle import SubtitleOverlay


class ASRSubtitlePipeline:
    def __init__(self, settings: ASRSettings) -> None:
        self._settings = settings
        self._asr: Optional[ASRClient] = None
        self._audio_processor: Optional[AudioProcessor] = None
        self._srt_writer: Optional[SRTWriter] = None
        self._subtitle_overlay: Optional[SubtitleOverlay] = None

    def start(self, video_filename: str = "") -> None:
        self._asr = ASRClient(self._settings)
        self._asr.start()
        self._audio_processor = AudioProcessor()
        self._subtitle_overlay = SubtitleOverlay()
        if video_filename:
            self._srt_writer = SRTWriter()
            self._srt_writer.open(video_filename)

    def stop(self) -> None:
        if self._asr is not None:
            self._asr.stop()
        if self._srt_writer is not None:
            self._srt_writer.close()

    def feed_audio_frame(self, frame: Frame) -> None:
        if self._asr is None or self._audio_processor is None:
            return
        metadata = frame.metadata
        fmt = metadata.get("format", "s16")
        if isinstance(fmt, av.AudioFormat):
            fmt = fmt.name
        layout = metadata.get("layout", "stereo")
        if isinstance(layout, av.AudioLayout):
            layout = layout.name
        sample_rate = metadata.get("sample_rate", 48000)
        av_frame = av.AudioFrame.from_ndarray(
            frame.data,
            format=fmt,
            layout=layout,
        )
        av_frame.rate = sample_rate
        av_frame.sample_rate = sample_rate
        pcm = self._audio_processor.process(av_frame)
        if isinstance(pcm, np.ndarray):
            pcm = pcm.tobytes()
        self._asr.feed_audio(pcm)

    def poll_subtitles(self) -> None:
        if self._asr is None or self._subtitle_overlay is None:
            return
        while self._asr.subtitle_buffer:
            entry = self._asr.subtitle_buffer.popleft()
            self._subtitle_overlay.update_entry(entry)
            if self._srt_writer and entry.is_final:
                self._srt_writer.write_entry(entry)

    def rotate(self, video_filename: str) -> None:
        if self._srt_writer is None:
            self._srt_writer = SRTWriter()
        self._srt_writer.rotate(video_filename)

    @property
    def subtitle_overlay(self) -> SubtitleOverlay:
        assert self._subtitle_overlay is not None
        return self._subtitle_overlay
