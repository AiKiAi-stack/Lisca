# encoding:utf-8
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum, auto
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any, Callable, Dict, Generator, List, Optional, Union

import av
import av.error
from av.container import InputContainer
from av.frame import Frame as AVFrame
from av.stream import Stream

from .config import StreamProtocol, StreamQualityType, StreamSettings, StreamType
from .frame import Frame, FrameProcessor, FrameType
from .logger import Loggers
from .utils import FormatterUtils

LOGGER = Loggers().get_logger(__name__)


class ReaderState(Enum):
    """读取器状态"""

    IDLE = auto()
    READING = auto()
    PAUSED = auto()
    ERROR = auto()
    FINISHED = auto()


@dataclass
class ReaderStatsFrame:
    """读取器帧统计"""

    count: int = 0
    video_count: int = 0
    audio_count: int = 0
    fetched_count: int = 0
    dropped_count: int = 0
    skipped_count: int = 0

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
class ReaderStatsException:
    """读取器异常统计"""

    count: int = 0
    last_message: Optional[str] = None
    last_time: Optional[datetime] = None
    retry_count: int = 0
    retry_wait_time: float = 1.0

    def update(self, exception: Exception) -> None:
        """更新"""
        self.count += 1
        self.last_message = str(exception)
        self.last_time = datetime.now()

    def setup_retry(self) -> None:
        """设置重试"""
        self.retry_count += 1
        self.retry_wait_time *= 2.0

    def reset_retry(self) -> None:
        """重置重试"""
        self.retry_count = 0
        self.retry_wait_time = 1.0


@dataclass
class ReaderStats:
    """读取器统计"""

    frame: ReaderStatsFrame = field(default_factory=ReaderStatsFrame)
    exception: ReaderStatsException = field(default_factory=ReaderStatsException)
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

    @property
    def duration(self) -> float:
        """读取时长"""
        if self.start_time and self.end_time:
            return (self.end_time - self.start_time).total_seconds()
        return 0.0


class StreamReader:
    """流媒体读取器"""

    def __init__(self, settings: StreamSettings) -> None:
        self.settings: StreamSettings = settings
        self.stats: ReaderStats = ReaderStats()
        self.state: ReaderState = ReaderState.IDLE

        self._processor: FrameProcessor = FrameProcessor()
        self._container: Optional[InputContainer] = None
        self._streams: List[Stream] = []
        self._queue: Queue = Queue(maxsize=settings.queue_size)
        self._thread: Optional[Thread] = None
        self._lock: Lock = Lock()
        self._running: Event = Event()

    def start(self) -> bool:
        """启动流读取器"""
        with self._lock:
            if self.state != ReaderState.IDLE:
                return False

            try:
                self._running.set()
                self.state = ReaderState.READING
                self.stats.start_time = datetime.now()

                self._thread = Thread(target=self._read_frames, name="_read_frames")
                self._thread.start()

                LOGGER.info("流读取器已启动")
                return True

            except Exception as e:
                LOGGER.error(f"启动流读取器失败: {e}")
                self.stats.exception.update(e)
                self.state = ReaderState.ERROR
                return False

    def stop(self) -> bool:
        """停止流读取器"""
        with self._lock:
            if self.state not in [ReaderState.READING, ReaderState.PAUSED]:
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

                self._streams.clear()
                if self._container:
                    self._container.close()
                    self._container = None

                self.stats.end_time = datetime.now()
                self.state = ReaderState.FINISHED

                LOGGER.info(
                    f"流读取器已停止，统计信息: {FormatterUtils.format_json_string(self.get_stats())}"
                )
                return True

            except Exception as e:
                LOGGER.error(f"停止流读取器失败: {e}")
                self.stats.exception.update(e)
                self.state = ReaderState.ERROR
                return False

    def pause(self) -> bool:
        """暂停读取"""
        with self._lock:
            if self.state != ReaderState.READING:
                return False
            self.state = ReaderState.PAUSED
            LOGGER.info("流读取器已暂停")
            return True

    def resume(self) -> bool:
        """恢复读取"""
        with self._lock:
            if self.state != ReaderState.PAUSED:
                return False
            self.state = ReaderState.READING
            LOGGER.info("流读取器已恢复")
            return True

    @contextmanager
    def _open_stream(self) -> Generator:
        """打开流"""
        self._container = None
        try:
            self._container = av.open(
                file=self.settings.url,
                mode="r",
                options=self.settings.get_read_options(),
                timeout=self.settings.timeout,
            )
            self._init_streams()
            yield self._container
        except av.error.FFmpegError as e:
            LOGGER.error(f"打开流失败: {e}")
            raise
        finally:
            if self._container:
                try:
                    self._container.close()
                    self._container = None
                except Exception as e:
                    LOGGER.error(f"关闭容器失败: {e}")
                    raise

    def _init_streams(self) -> None:
        """初始化流"""
        try:
            self._streams.clear()
            if self._container:
                if self.settings.type in [StreamType.VIDEO, StreamType.BOTH]:
                    if getattr(self._container.streams, "video", None):
                        video_stream = self._container.streams.video[0]
                        video_stream.thread_type = "AUTO"
                        self._streams.append(video_stream)

                if self.settings.type in [StreamType.AUDIO, StreamType.BOTH]:
                    if getattr(self._container.streams, "audio", None):
                        audio_stream = self._container.streams.audio[0]
                        self._streams.append(audio_stream)

            if not self._streams:
                raise ValueError(f"未找到支持的流: {self.settings.type}")

        except Exception as e:
            LOGGER.error(f"初始化流失败: {e}")
            raise

    def _read_frames(self) -> None:
        """读取帧"""
        while self._running.is_set():
            try:
                if self.state == ReaderState.PAUSED:
                    time.sleep(0.1)
                    continue

                with self._open_stream() as container:
                    LOGGER.info(f"成功打开流: {self.settings.url}")

                    for frame in container.decode(*self._streams):
                        self.stats.frame.update(frame)

                        if not self._running.is_set():
                            self.stats.frame.dropped_count += 1
                            break

                        if self.state == ReaderState.PAUSED:
                            self.stats.frame.dropped_count += 1
                            continue

                        if (
                            self.stats.frame.video_count
                            % (
                                self.settings.quality[
                                    StreamQualityType.READ
                                ].frame_interval
                                + 1
                            )
                            != 0
                        ) or (
                            self.stats.frame.audio_count
                            % (
                                self.settings.quality[
                                    StreamQualityType.READ
                                ].frame_interval
                                + 1
                            )
                            != 0
                        ):
                            self.stats.frame.skipped_count += 1
                            continue

                        self.stats.exception.reset_retry()
                        try:
                            processed_frame = self._processor.process_frame(
                                frame, self.stats.frame.count
                            )
                            self._queue.put(processed_frame, timeout=1.0)
                            self.stats.frame.fetched_count += 1

                        except Full:
                            LOGGER.warning("帧缓冲区已满，丢弃当前帧")
                            self.stats.frame.dropped_count += 1

                    else:
                        if self.settings.protocol == StreamProtocol.FILE:
                            LOGGER.info("读取帧已完成")
                            break

            except Exception as e:
                LOGGER.error(f"读取帧失败: {e}")
                self.stats.frame.dropped_count += 1
                self.stats.exception.update(e)
                self.stats.exception.setup_retry()
                if not self._handle_retry():
                    self.state = ReaderState.ERROR
                    break
        else:
            LOGGER.info("读取帧已停止")

    def _handle_retry(self) -> bool:
        """处理重试"""
        if self._running.is_set():
            if self.stats.exception.retry_count > self.settings.max_retry_count:
                LOGGER.error(f"达最大重试次数: {self.settings.max_retry_count}")
                return False

            if self.stats.exception.retry_wait_time > self.settings.max_retry_wait_time:
                LOGGER.error(f"达最大重试等待时间: {self.settings.max_retry_wait_time}")
                return False

            LOGGER.info(
                f"等待{self.stats.exception.retry_wait_time:.3f}秒后进行第{self.stats.exception.retry_count}次重试"
            )

            time.sleep(self.stats.exception.retry_wait_time)
            return True

        return False

    def get_frame(self, timeout: float = 1.0) -> Optional[Frame]:
        """获取帧"""
        try:
            return self._queue.get(timeout=timeout)
        except Empty:
            LOGGER.warning("帧缓冲区为空，未获取到帧")
            return None

    def get_stats(self) -> Dict[str, Any]:
        """获取统计值"""
        return {"content": asdict(self.stats), "duration": self.stats.duration}

    def register_frame_filter(
        self, filter_id: str, filter_fn: Callable[[Frame], Frame]
    ) -> None:
        """注册帧过滤器"""
        self._processor.register_filter(filter_id, filter_fn)

    def remove_frame_filter(self, filter_id: str) -> None:
        """移除帧过滤器"""
        self._processor.remove_filter(filter_id)

    @property
    def queue_size(self) -> int:
        """当前队列大小"""
        return self._queue.qsize()

    @property
    def is_running(self) -> bool:
        """是否正在运行"""
        return self._running.is_set()
