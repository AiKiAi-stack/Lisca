# encoding:utf-8
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum, auto
from fractions import Fraction
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any, Callable, Dict, List, Optional, Union

import av
from av.container import OutputContainer
from av.frame import Frame as AVFrame
from av.stream import Stream

from .config import StreamCodecType, StreamQualityType, StreamSettings, StreamType
from .frame import Frame, FrameType
from .logger import Loggers
from .utils import FormatterUtils

LOGGER = Loggers().get_logger(__name__)


class WriterState(Enum):
    IDLE = auto()
    WRITING = auto()
    PAUSED = auto()
    ERROR = auto()
    FINISHED = auto()


@dataclass
class WriterStatsFrame:
    """写入器帧统计"""

    count: int = 0
    video_count: int = 0
    audio_count: int = 0
    fetched_count: int = 0
    dropped_count: int = 0

    def update(self, frame: Union[Frame, AVFrame]) -> None:
        """更新"""
        if isinstance(frame, Frame):
            self.count += 1
            if frame.type == FrameType.VIDEO:
                self.video_count += 1
            elif frame.type == FrameType.AUDIO:
                self.audio_count += 1
        elif isinstance(frame, AVFrame):
            self.count += 1
            if isinstance(frame, av.VideoFrame):
                self.video_count += 1
            elif isinstance(frame, av.AudioFrame):
                self.audio_count += 1


@dataclass
class WriterStatsException:
    """写入器异常统计"""

    count: int = 0
    last_message: Optional[str] = None
    last_time: Optional[datetime] = None

    def update(self, exception: Exception) -> None:
        """更新"""
        self.count += 1
        self.last_message = str(exception)
        self.last_time = datetime.now()


@dataclass
class WriterStats:
    """写入器统计信息"""

    frame: WriterStatsFrame = field(default_factory=WriterStatsFrame)
    exception: WriterStatsException = field(default_factory=WriterStatsException)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @property
    def duration(self) -> float:
        """写入时长"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0


class WriterContainerTicker:
    """写入容器时钟"""

    def __init__(
        self,
        fps: float = 30.0,
        samples: int = 1024,
        sample_rate: int = 48000,
        time_base: Fraction = Fraction(1, 90000),
    ) -> None:
        self.fps: float = fps
        self.samples: int = samples
        self.sample_rate: int = sample_rate
        self.time_base: Fraction = time_base
        self.video_frame_duration: float = time_base * fps
        self.audio_frame_duration: Fraction = (time_base * sample_rate) / samples
        self.last_video_packet_dts: int = -(sys.maxsize + 1)
        self.last_video_packet_pts: int = -(sys.maxsize + 1)
        self.last_audio_packet_dts: int = -(sys.maxsize + 1)
        self.last_audio_packet_pts: int = -(sys.maxsize + 1)

    def generate_video_frame_timestamp(
        self, video_frame_count: int, video_frame_interval: int = 0
    ) -> tuple[int, int, Fraction]:
        """生成视频帧时间戳"""
        dts = int(
            video_frame_count * (1 + video_frame_interval) / self.video_frame_duration
        )
        pts = dts
        return dts, pts, self.time_base

    def generate_audio_frame_timestamp(
        self, audio_frame_count: int, audio_frame_interval: int = 0
    ) -> tuple[int, int, Fraction]:
        """生成音频帧时间戳"""
        dts = int(
            audio_frame_count * (1 + audio_frame_interval) / self.audio_frame_duration
        )
        pts = dts
        return dts, pts, self.time_base

    def ensure_video_packet_timestamp(self, packet: av.Packet) -> bool:
        """保护视频包时间戳"""
        if packet.dts > self.last_video_packet_dts:
            self.last_video_packet_dts = packet.dts
        else:
            return False

        if packet.pts is not None:
            if packet.pts > self.last_video_packet_pts:
                self.last_video_packet_pts = packet.pts
            else:
                return False

        return True

    def ensure_audio_packet_timestamp(self, packet: av.Packet) -> bool:
        """保护音频包时间戳"""
        if packet.dts > self.last_audio_packet_dts:
            self.last_audio_packet_dts = packet.dts
        else:
            return False

        if packet.pts is not None:
            if packet.pts > self.last_audio_packet_pts:
                self.last_audio_packet_pts = packet.pts
            else:
                return False

        return True

    def reset(self) -> None:
        """重置时间戳生成器变量"""
        self.last_video_packet_dts = -(sys.maxsize + 1)
        self.last_video_packet_pts = -(sys.maxsize + 1)
        self.last_audio_packet_dts = -(sys.maxsize + 1)
        self.last_audio_packet_pts = -(sys.maxsize + 1)


class WriterContainer:
    """写入器容器"""

    def __init__(self, filepath: str, settings: StreamSettings) -> None:
        self.filepath: str = filepath
        self.settings: StreamSettings = settings
        self.container: Optional[OutputContainer] = None
        self.video_stream: Optional[Stream] = None
        self.audio_stream: Optional[Stream] = None
        self.write_ticker: WriterContainerTicker = WriterContainerTicker(
            fps=settings.quality[StreamQualityType.WRITE].video_fps,
            sample_rate=settings.quality[StreamQualityType.WRITE].audio_sample_rate,
        )
        self.use_source_timestamps: bool = False
        self.frame_count: int = 0
        self.video_frame_count: int = 0
        self.audio_frame_count: int = 0
        self.byte_size: int = 0
        self.video_byte_size: int = 0
        self.audio_byte_size: int = 0
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        self._setup_container()

    def _setup_container(self) -> None:
        """设置容器"""
        self.container = av.open(
            file=self.filepath,
            mode="w",
            timeout=self.settings.timeout,
        )
        if self.settings.type in [StreamType.VIDEO, StreamType.BOTH]:
            self._setup_video_stream()
        if self.settings.type in [StreamType.AUDIO, StreamType.BOTH]:
            self._setup_audio_stream()

    def _setup_video_stream(self) -> None:
        """配置视频流"""
        if self.container:
            try:
                self.video_stream = self.container.add_stream(
                    codec_name=self.settings.codec[StreamCodecType.VIDEO].encoder,
                    rate=self.settings.quality[StreamQualityType.WRITE].video_fps,
                    width=self.settings.quality[StreamQualityType.WRITE].video_width,
                    height=self.settings.quality[StreamQualityType.WRITE].video_height,
                    pix_fmt=self.settings.quality[
                        StreamQualityType.WRITE
                    ].video_pixel_format,
                    gop_size=self.settings.quality[StreamQualityType.WRITE].video_gop,
                    bit_rate=self.settings.quality[
                        StreamQualityType.WRITE
                    ].video_bitrate,
                )
                self.video_stream.time_base = Fraction(1, 90000)
            except Exception as e:
                LOGGER.warning(f"设置视频编码器失败: {e}")
                raise

    def _setup_audio_stream(self) -> None:
        """配置音频流"""
        if self.container:
            try:
                self.audio_stream = self.container.add_stream(
                    codec_name=self.settings.codec[StreamCodecType.AUDIO].encoder,
                    rate=self.settings.quality[
                        StreamQualityType.WRITE
                    ].audio_sample_rate,
                    layout=self.settings.quality[StreamQualityType.WRITE].audio_layout,
                    bit_rate=self.settings.quality[
                        StreamQualityType.WRITE
                    ].audio_bitrate,
                )
                self.audio_stream.time_base = Fraction(1, 90000)
            except Exception as e:
                LOGGER.warning(f"设置音频编码器失败: {e}")
                raise

    def write_frame(self, frame: Frame) -> None:
        """写入帧"""
        if frame.type == FrameType.VIDEO:
            self._write_video_frame(frame)
        elif frame.type == FrameType.AUDIO:
            self._write_audio_frame(frame)
        else:
            raise NotImplementedError(f"不支持的帧类型: {frame.type}")

    def _write_video_frame(self, frame: Frame) -> None:
        """写入视频帧"""
        try:
            if isinstance(self.video_stream, av.VideoStream):
                in_frame = av.VideoFrame.from_ndarray(frame.data, format="rgb24")
                out_frame = in_frame.reformat(
                    width=self.settings.quality[StreamQualityType.WRITE].video_width,
                    height=self.settings.quality[StreamQualityType.WRITE].video_height,
                    format=self.settings.quality[
                        StreamQualityType.WRITE
                    ].video_pixel_format,
                )
                pts, dts, time_base = self.write_ticker.generate_video_frame_timestamp(
                    self.video_frame_count,
                    self.settings.quality[StreamQualityType.WRITE].frame_interval,
                )
                out_frame.dts = dts
                out_frame.pts = pts
                out_frame.time_base = time_base
                out_packets = self.video_stream.encode(out_frame)
                for out_packet in out_packets:
                    if self.container:
                        if self.write_ticker.ensure_video_packet_timestamp(out_packet):
                            self.container.mux(out_packet)
                            self.video_byte_size += out_packet.size
                            self.byte_size += out_packet.size
                self.video_frame_count += 1
                self.frame_count += 1

        except Exception as e:
            LOGGER.error(f"写入视频帧失败: {e}")
            raise

    def _write_audio_frame(self, frame: Frame) -> None:
        """写入音频帧"""
        try:
            if isinstance(self.audio_stream, av.AudioStream):
                in_frame = av.AudioFrame.from_ndarray(
                    frame.data,
                    format=frame.metadata.get("format", "s16"),
                    layout=frame.metadata.get("layout", "stereo"),
                )
                in_frame.rate = getattr(frame, "rate", 48000)
                in_frame.sample_rate = frame.metadata.get("sample_rate", 48000)
                resampler = av.AudioResampler(
                    format=self.settings.quality[
                        StreamQualityType.WRITE
                    ].audio_sample_format,
                    layout=self.settings.quality[StreamQualityType.WRITE].audio_layout,
                    rate=self.settings.quality[
                        StreamQualityType.WRITE
                    ].audio_sample_rate,
                )
                out_frames = resampler.resample(in_frame)
                for out_frame in out_frames:
                    pts, dts, time_base = (
                        self.write_ticker.generate_audio_frame_timestamp(
                            self.audio_frame_count,
                            self.settings.quality[
                                StreamQualityType.WRITE
                            ].frame_interval,
                        )
                    )
                    out_frame.dts = dts
                    out_frame.pts = pts
                    out_frame.time_base = time_base
                    out_packets = self.audio_stream.encode(out_frame)
                    for out_packet in out_packets:
                        if self.container:
                            if self.write_ticker.ensure_audio_packet_timestamp(
                                out_packet
                            ):
                                self.container.mux(out_packet)
                                self.audio_byte_size += out_packet.size
                                self.byte_size += out_packet.size
                    self.audio_frame_count += 1
                    self.frame_count += 1

        except Exception as e:
            LOGGER.error(f"写入音频帧失败: {e}")
            raise

    def close(self) -> None:
        """关闭"""
        if self.container:
            try:
                if isinstance(self.video_stream, av.VideoStream):
                    video_packets = self.video_stream.encode(None)
                    for video_packet in video_packets:
                        if self.write_ticker.ensure_video_packet_timestamp(
                            video_packet
                        ):
                            self.container.mux(video_packet)

                if isinstance(self.audio_stream, av.AudioStream):
                    audio_packets = self.audio_stream.encode(None)
                    for audio_packet in audio_packets:
                        if self.write_ticker.ensure_audio_packet_timestamp(
                            audio_packet
                        ):
                            self.container.mux(audio_packet)

                self.container.close()
                self.video_stream = None
                self.audio_stream = None
                self.container = None
                self.write_ticker.reset()
                self.end_time = datetime.now()

            except Exception as e:
                LOGGER.error(f"关闭写入器容器失败: {e}")
                raise

    @property
    def summary(self) -> Dict[str, Any]:
        """摘要信息"""
        return {
            "filepath": self.filepath,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "frame_count": self.frame_count,
            "video_frame_count": self.video_frame_count,
            "audio_frame_count": self.audio_frame_count,
            "byte_size": self.byte_size,
            "video_byte_size": self.video_byte_size,
            "audio_byte_size": self.audio_byte_size,
        }


class StreamWriter:
    """流媒体写入器"""

    def __init__(self, settings: StreamSettings) -> None:
        self.settings: StreamSettings = settings
        self.stats: WriterStats = WriterStats()
        self.state: WriterState = WriterState.IDLE

        self._container: Optional[WriterContainer] = None
        self._containers: List[WriterContainer] = []
        self._queue: Queue = Queue(maxsize=settings.queue_size)
        self._thread: Optional[Thread] = None
        self._lock: Lock = Lock()
        self._running = Event()
        self._on_container_rotate: Optional[Callable[[str], None]] = None

    def set_on_container_rotate(self, callback: Optional[Callable[[str], None]]) -> None:
        """Set callback invoked after container rotation with new filepath."""
        self._on_container_rotate = callback

    def start(self) -> bool:
        """启动流写入器"""
        with self._lock:
            if self.state != WriterState.IDLE:
                return False

            try:
                self._running.set()
                self.state = WriterState.WRITING
                self.stats.start_time = datetime.now()

                self._thread = Thread(target=self._write_frames, name="_write_frames")
                self._thread.start()

                LOGGER.info("流写入器已启动")
                return True

            except Exception as e:
                LOGGER.error(f"开始写入失败: {e}")
                self.stats.exception.update(e)
                self.state = WriterState.ERROR
                return False

    def stop(self) -> bool:
        """停止流写入器"""
        with self._lock:
            if self.state not in [WriterState.WRITING, WriterState.PAUSED]:
                return False

            try:
                self._running.clear()

                while not self._queue.empty():
                    try:
                        self._queue.get_nowait()
                    except Empty:
                        break

                if self._thread and self._thread.is_alive():
                    self._thread.join(timeout=5.0)

                if self._container:
                    self._container.close()
                    self._container = None
                self._containers.clear()

                self.stats.end_time = datetime.now()
                self.state = WriterState.FINISHED

                LOGGER.info(
                    f"流写入器已停止，统计信息: {FormatterUtils.format_json_string(self.get_stats())}"
                )
                return True

            except Exception as e:
                LOGGER.error(f"停止流写入器失败: {e}")
                self.stats.exception.update(e)
                self.state = WriterState.ERROR
                return False

    def pause(self) -> bool:
        """暂停写入"""
        with self._lock:
            if self.state != WriterState.WRITING:
                return False
            self.state = WriterState.PAUSED
            return True

    def resume(self) -> bool:
        """恢复写入"""
        with self._lock:
            if self.state != WriterState.PAUSED:
                return False
            self.state = WriterState.WRITING
            return True

    def do_rotate_container(self) -> bool:
        """进行轮换容器"""
        if self.state != WriterState.WRITING:
            return False
        try:
            with self._lock:
                self._rotate_container()
            return True
        except Exception as e:
            LOGGER.error(f"轮换写入器容器失败: {e}")
            return False

    def _create_container(self) -> WriterContainer:
        """创建容器"""
        output_dir = Path(self.settings.directory)
        output_dir.mkdir(parents=True, exist_ok=True)

        flag = len(self._containers)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if self.settings.type in [StreamType.VIDEO, StreamType.BOTH]:
            format = self.settings.codec[StreamCodecType.VIDEO].format
        elif self.settings.type in [StreamType.AUDIO, StreamType.BOTH]:
            format = self.settings.codec[StreamCodecType.AUDIO].format
        else:
            raise NotImplementedError(f"不支持的流类型: {self.settings.type}")

        filepath = str(output_dir / f"{flag}_{timestamp}.{format}")
        container = WriterContainer(
            filepath=filepath,
            settings=self.settings,
        )
        container.start_time = datetime.now()
        self._containers.append(container)

        return container

    def _whether_rotate_container(self) -> bool:
        """是否轮换容器"""
        if not self._container:
            return True

        if self.stats.duration >= self.settings.max_container_duration:
            return True

        if self._container.byte_size >= self.settings.max_container_byte_size:
            return True

        return False

    def _rotate_container(self) -> None:
        """轮换容器"""
        try:
            if self._container:
                self._container.close()
                LOGGER.info(f"已关闭写入器容器，摘要信息: {self._container.summary}")
            self._container = self._create_container()
            LOGGER.info(f"已创建写入器容器: {self._container.filepath}")
            if self._on_container_rotate:
                self._on_container_rotate(self._container.filepath)

        except Exception as e:
            LOGGER.error(f"轮换写入器容器失败: {e}")
            raise

    def _write_frames(self) -> None:
        """写入帧"""
        LOGGER.info("开始写入帧")
        while self._running.is_set():
            if self.state == WriterState.PAUSED:
                time.sleep(0.1)
                continue

            try:
                frame = self._queue.get(timeout=1.0)
                self.stats.frame.update(frame)
            except Empty:
                LOGGER.debug("帧缓冲区为空，未获取到帧")
                continue

            try:
                if self._whether_rotate_container():
                    self._rotate_container()
                if self._container:
                    self._container.write_frame(frame)
                    self.stats.frame.fetched_count += 1

            except Exception as e:
                LOGGER.error(f"写入帧失败: {e}")
                self.stats.exception.update(e)
                self.stats.frame.dropped_count += 1

        LOGGER.info("停止写入帧")

    def put_frame(self, frame: Frame) -> bool:
        """放入帧"""
        try:
            self._queue.put(frame, timeout=1.0)
            return True
        except Full:
            LOGGER.warning("帧缓冲区已满，丢弃当前帧")
            self.stats.frame.dropped_count += 1
            return False
        except Exception as e:
            LOGGER.error(f"放入帧失败: {e}")
            self.stats.exception.update(e)
            self.stats.frame.dropped_count += 1
            return False

    def get_stats(self) -> Dict[str, Any]:
        """获取统计值"""
        return {"content": asdict(self.stats), "duration": self.stats.duration}

    @property
    def queue_size(self) -> int:
        """队列大小"""
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running.is_set()
