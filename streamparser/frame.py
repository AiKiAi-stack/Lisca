# encoding:utf-8
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import av
import cv2
import librosa
import numpy as np
from av.frame import Frame as AVFrame

from .logger import Loggers

LOGGER = Loggers().get_logger(__name__)


class FrameType(Enum):
    """帧类型定义"""

    VIDEO = auto()  # 视频帧
    AUDIO = auto()  # 音频帧


class FrameError(Exception):
    """帧处理异常"""

    def __init__(self, message: str, type: FrameType) -> None:
        self.type = type
        self.timestamp = datetime.now()
        super().__init__(f"[{type.name}] {message}")


@dataclass
class FrameMetrics:
    """帧指标"""

    processing_time: Optional[float] = None
    quality: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Frame:
    """帧"""

    data: np.ndarray
    id: int
    time: float
    type: FrameType
    rate: float
    metadata: Dict[str, Any] = field(default_factory=dict)
    metrics: FrameMetrics = field(default_factory=FrameMetrics)

    def __post_init__(self) -> None:
        """初始化后处理"""
        try:
            self._validate()
            self._calculate_metrics()
        except Exception as e:
            LOGGER.error(f"帧初始化错误: {e}")
            raise FrameError(str(e), self.type) from e

    def _validate(self) -> None:
        """验证帧属性"""
        if not isinstance(self.data, np.ndarray):
            raise ValueError(f"帧数据类型必须是数组: {np.ndarray}")
        if self.type == FrameType.VIDEO:
            self._validate_video()
        elif self.type == FrameType.AUDIO:
            self._validate_audio()
        else:
            raise NotImplementedError(f"不支持的帧类型: {self.type}")

    def _validate_video(self) -> None:
        """验证视频帧"""
        if len(self.data.shape) not in (2, 3):
            raise ValueError(f"视频帧维度必须是: {(2, 3)}")
        if np.isnan(self.data).any() or np.isinf(self.data).any():
            raise ValueError("视频帧数据包含无效值")

    def _validate_audio(self) -> None:
        """验证音频帧"""
        if len(self.data.shape) not in (1, 2):
            raise ValueError(f"音频帧维度必须是: {(1, 2)}")
        if np.isnan(self.data).any() or np.isinf(self.data).any():
            raise ValueError("音频帧数据包含无效值")

    def _calculate_metrics(self) -> None:
        """计算帧指标"""
        try:
            start_time = time.time()
            if not self.metadata.get("key_frame", 1):
                return
            if self.type == FrameType.VIDEO:
                self._calculate_video_metrics()
            elif self.type == FrameType.AUDIO:
                self._calculate_audio_metrics()
            else:
                raise NotImplementedError(f"不支持的帧类型: {self.type}")
            self.metrics.processing_time = time.time() - start_time
        except Exception as e:
            LOGGER.warning(f"计算质量指标失败: {e}")

    def _calculate_video_metrics(self) -> None:
        """计算视频帧指标"""
        image = (
            cv2.cvtColor(self.data, cv2.COLOR_RGB2GRAY)
            if len(self.data.shape) == 3
            else self.data
        ).astype(np.uint8)
        self.metrics.quality["brightness"] = float(np.mean(image))
        self.metrics.quality["contrast"] = float(np.std(image))
        self.metrics.quality["blur"] = float(cv2.Laplacian(image, ddepth=6).var())
        self.metrics.quality["noise_level"] = float(
            np.mean(np.abs(image - cv2.fastNlMeansDenoising(image).astype(np.uint8)))
        )

    def _calculate_audio_metrics(self) -> None:
        """计算音频帧指标"""
        sample = self.data
        if len(sample.shape) == 2:
            sample = np.mean(sample, axis=0)
        self.metrics.quality["rms_volume"] = float(np.sqrt(np.mean(np.square(sample))))
        self.metrics.quality["peak_amplitude"] = float(np.max(np.abs(sample)))
        self.metrics.quality["zero_crossing_rate"] = float(
            np.sum(np.abs(np.diff(np.signbit(sample)))) / len(sample)
        )
        if len(sample) >= 1024:
            spectral_centroid = librosa.feature.spectral_centroid(
                y=sample, sr=self.metadata.get("sample_rate", 48000), n_fft=1024
            )
            self.metrics.quality["spectrum_centroid"] = float(
                np.mean(spectral_centroid)
            )

    def save(self, directory: str) -> Optional[str]:
        """保存帧的数据和元数据"""
        try:
            save_path = Path(directory)
            save_path.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.fromtimestamp(time.time()).strftime("%Y%m%d_%H%M%S_%f")
            if self.type == FrameType.VIDEO:
                frame_path = self._save_video(save_path, timestamp)
            elif self.type == FrameType.AUDIO:
                frame_path = self._save_audio(save_path, timestamp)
            else:
                raise NotImplementedError(f"不支持的帧类型: {self.type}")
            self._save_metadata(save_path, timestamp)
            return str(frame_path)
        except Exception as e:
            LOGGER.error(f"保存帧失败: {e}")

    def _save_video(self, directory: Path, timestamp: str) -> Path:
        """保存视频帧"""
        frame_path = directory / f"frame_{timestamp}_video.npy"
        np.save(str(frame_path), self.data)
        return frame_path

    def _save_audio(self, directory: Path, timestamp: str) -> Path:
        """保存音频帧"""
        frame_path = directory / f"frame_{timestamp}_audio.npy"
        np.save(str(frame_path), self.data)
        return frame_path

    def _save_metadata(self, directory: Path, timestamp: str) -> None:
        """保存帧元数据"""
        meta_path = directory / f"frame_{timestamp}.json"
        frame_info = {
            "id": self.id,
            "time": self.time,
            "type": self.type.name,
            "rate": self.rate,
            "metadata": self.metadata,
            "metrics": asdict(self.metrics),
        }
        meta_path.write_text(
            json.dumps(frame_info, indent=4, ensure_ascii=False), encoding="utf-8"
        )


class FrameProcessor:
    """帧处理器"""

    def __init__(self) -> None:
        self._filters: Dict[str, Callable[[Frame], Frame]] = {}

    def register_filter(
        self, filter_id: str, filter_fn: Callable[[Frame], Frame]
    ) -> None:
        """注册帧过滤器"""
        if filter_id in self._filters:
            LOGGER.warning(f"覆盖已注册过滤器: {filter_id}")
        self._filters[filter_id] = filter_fn
        LOGGER.info(f"已注册过滤器: {filter_id}")

    def remove_filter(self, filter_id: str) -> None:
        """移除帧过滤器"""
        if filter_id in self._filters:
            del self._filters[filter_id]
            LOGGER.info(f"移移已注册过滤器: {filter_id}")

    def process_frame(self, frame: AVFrame, id: int = -1) -> Frame:
        """处理帧数据"""
        if isinstance(frame, av.VideoFrame):
            processed_frame = self._process_video_frame(frame, id)
        elif isinstance(frame, av.AudioFrame):
            processed_frame = self._process_audio_frame(frame, id)
        else:
            raise NotImplementedError(f"不支持的帧类型: {type(frame)}")

        # 应用过滤器链
        for filter_id, filter_fn in self._filters.items():
            try:
                processed_frame = filter_fn(processed_frame)
            except Exception as e:
                LOGGER.error(f"应用过滤器: {filter_id} 时发生错误: {e}")
                raise FrameError(str(e), processed_frame.type) from e
        return processed_frame

    @staticmethod
    def _process_video_frame(frame: av.VideoFrame, id: int = -1) -> Frame:
        """处理视频帧"""
        try:
            image = frame.to_ndarray(format="rgb24")
            return Frame(
                data=image,
                id=id,
                time=frame.time if frame.time is not None else time.time(),
                type=FrameType.VIDEO,
                rate=float(getattr(frame, "rate", 0)),
                metadata={
                    "pts": frame.pts,
                    "dts": frame.dts,
                    "time_base": frame.time_base,
                    "width": frame.width,
                    "height": frame.height,
                    "colorspace": frame.colorspace,
                    "color_range": frame.color_range,
                    "format": frame.format.name,
                    "key_frame": frame.key_frame,
                },
            )

        except Exception as e:
            LOGGER.error(f"视频帧处理错误: {e}")
            raise

    @staticmethod
    def _process_audio_frame(frame: av.AudioFrame, id: int = -1) -> Frame:
        """处理音频帧"""
        try:
            sample = frame.to_ndarray()
            return Frame(
                data=sample,
                id=id,
                time=frame.time if frame.time is not None else time.time(),
                type=FrameType.AUDIO,
                rate=float(getattr(frame, "rate", 0)),
                metadata={
                    "pts": frame.pts,
                    "dts": frame.dts,
                    "time_base": frame.time_base,
                    "sample_rate": frame.sample_rate,
                    "samples": frame.samples,
                    "format": frame.format.name,
                    "layout": frame.layout.name,
                },
            )
        except Exception as e:
            LOGGER.error(f"音频帧处理错误: {e}")
            raise
