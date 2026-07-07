# encoding:utf-8
import json
import os
import time
from copy import deepcopy
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Optional

import av
import cv2
import numpy as np
from av.audio.stream import AudioStream
from av.frame import Frame as AVFrame
from av.video.stream import VideoStream

from .config import StreamSettings, StreamType
from .frame import Frame, FrameProcessor, FrameType
from .logger import Loggers

LOGGER = Loggers().get_logger(__name__)


class FileUtils:
    """文件操作工具类"""

    @staticmethod
    def check_directory_exist(directory: str) -> bool:
        """检查目录存在"""
        path = Path(directory)
        return path.exists() and path.is_dir()

    @staticmethod
    def ensure_directory(directory: str) -> Path:
        """确保目录存在"""
        path = Path(directory)
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def create_directory(base_dir: str, prefix: str = "") -> Path:
        """创建目录文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dir_name = f"{prefix}_{timestamp}" if prefix else timestamp
        path = Path(base_dir) / dir_name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def get_directory_size(directory: str) -> int:
        """获取目录大小(字节)"""
        return sum(
            f.stat().st_size for f in Path(directory).glob("**/*") if f.is_file()
        )

    @staticmethod
    def get_directory_available_space(directory: str) -> int:
        """获取目录可用空间(字节)"""
        statvfs = os.statvfs(directory)
        return statvfs.f_bavail * statvfs.f_frsize

    @staticmethod
    def clean_oldfiles(
        directory: str, max_age_days: int = 7, exclude_patterns: Optional[list] = None
    ) -> None:
        """清理旧文件

        Args:
            directory: 目录路径
            max_age_days: 最大保留天数
            exclude_patterns: 排除的文件模式列表
        """
        try:
            cutoff_date = datetime.now() - timedelta(days=max_age_days)
            exclude_patterns = exclude_patterns or []

            for path in Path(directory).glob("**/*"):
                if path.is_file():
                    # 检查是否在排除列表中
                    if any(path.match(pattern) for pattern in exclude_patterns):
                        continue

                    if path.stat().st_mtime < cutoff_date.timestamp():
                        path.unlink()
                        LOGGER.debug(f"已删除旧文件: {path}")

        except Exception as e:
            LOGGER.error(f"清理旧文件失败: {e}")


class FormatterUtils:
    """格式化工具类"""

    @staticmethod
    def format_json_string(
        objects: Any, indent: int = 4, ensure_ascii: bool = False
    ) -> str:
        """格式化为JSON字符串

        Args:
            objects: 要转换的对象
            indent: JSON缩进空格数
            ensure_ascii: 是否确保ASCII输出

        Returns:
            str: 格式化的JSON字符串
        """
        try:
            converted_objects = FormatterUtils._convert_to_serializable(
                deepcopy(objects)
            )
            return json.dumps(
                converted_objects, ensure_ascii=ensure_ascii, indent=indent
            )
        except Exception as e:
            return f"Error converting to JSON: {str(e)}"

    @staticmethod
    def _convert_to_serializable(objects: Any) -> Any:
        """转换对象为可序列化的基本类型

        支持的类型:
        - 基本类型(str, int, float, bool, None)
        - 容器类型(list, tuple, dict, set)
        - 数组类型(numpy)
        - 日期时间类型
        - 枚举类型
        - 数据类
        - 路径对象
        - 自定义对象(通过__dict__)
        """
        # 处理None
        if objects is None:
            return None

        # 处理基本类型
        if isinstance(objects, (str, int, float, bool)):
            return objects

        # 处理数组类型
        if isinstance(objects, np.ndarray):
            return f"array({objects}, dtype={objects.dtype})"

        # 处理日期时间
        if isinstance(objects, datetime):
            return objects.isoformat()

        # 处理枚举
        if isinstance(objects, Enum):
            return {objects.name: objects.value}

        # 处理路径
        if isinstance(objects, Path):
            return str(objects)

        # 处理数据类
        if is_dataclass(objects):
            if isinstance(objects, type):
                return [objects_field.name for objects_field in fields(objects)]
            return FormatterUtils._convert_to_serializable(asdict(objects))

        # 处理字典
        if isinstance(objects, dict):
            return {
                key: FormatterUtils._convert_to_serializable(value)
                for key, value in objects.items()
            }

        # 处理列表，元组和集合
        if isinstance(objects, (list, tuple, set)):
            return [FormatterUtils._convert_to_serializable(item) for item in objects]

        # 处理其他对象
        try:
            return FormatterUtils._convert_to_serializable(objects.__dict__)
        except AttributeError:
            return str(objects)


class ConversionUtils:
    """格式转换工具类"""

    @staticmethod
    def normalize_audio_volume(frame: Frame, target_db: float = -20.0) -> Frame:
        """
        标准化音频音量

        Args:
            frame: 音频帧
            target_db: 目标分贝值

        Returns:
            Frame: 处理后的帧
        """
        if frame.type != FrameType.AUDIO:
            return frame

        try:
            current_db = 20 * np.log10(np.sqrt(np.mean(frame.data**2)))
            gain = 10 ** ((target_db - current_db) / 20)
            frame.data = frame.data * gain
            return frame
        except Exception as e:
            LOGGER.error(f"音量标准化失败: {e}")
            return frame

    @staticmethod
    def int_to_float_audio(
        frame: Frame, dtype: type[np.floating] = np.float32
    ) -> Frame:
        """
        将整型音频数据转换为浮点型

        Args:
            frame: 音频帧
            dtype: 目标数据类型 (np.float32 或 np.float64)

        Returns:
            Frame: 处理后的帧
        """
        if frame.type != FrameType.AUDIO:
            return frame

        try:
            # 获取原始数据类型的信息
            original_dtype = frame.data.dtype
            if np.issubdtype(original_dtype, np.floating):
                return frame

            # 根据不同的整型范围进行归一化
            if original_dtype == np.int16:
                normalized = frame.data.astype(dtype) / 32768.0
            elif original_dtype == np.int32:
                normalized = frame.data.astype(dtype) / 2147483648.0
            elif original_dtype == np.uint8:
                normalized = (frame.data.astype(dtype) - 128) / 128.0
            else:
                LOGGER.error(f"不支持的音频数据类型: {original_dtype}")
                return frame

            frame.data = normalized
            return frame
        except Exception as e:
            LOGGER.error(f"音频格式转换失败: {e}")
            return frame

    @staticmethod
    def float_to_int_audio(frame: Frame, dtype: type[np.integer] = np.int16) -> Frame:
        """
        将浮点型音频数据转换为整型

        Args:
            frame: 音频帧
            dtype: 目标数据类型 (np.int16, np.int32, 或 np.uint8)

        Returns:
            Frame: 处理后的帧
        """
        if frame.type != FrameType.AUDIO:
            return frame

        try:
            if not np.issubdtype(frame.data.dtype, np.floating):
                return frame

            # 确保数据在[-1, 1]范围内
            normalized = np.clip(frame.data, -1.0, 1.0)

            # 根据目标类型进行转换
            if dtype == np.int16:
                frame.data = (normalized * 32767).astype(np.int16)
            elif dtype == np.int32:
                frame.data = (normalized * 2147483647).astype(np.int32)
            elif dtype == np.uint8:
                frame.data = ((normalized + 1.0) * 127.5).astype(np.uint8)
            else:
                LOGGER.error(f"不支持的目标数据类型: {dtype}")
                return frame

            return frame
        except Exception as e:
            LOGGER.error(f"音频格式转换失败: {e}")
            return frame

    @staticmethod
    def apply_audio_effects(frame: Frame, effects: Dict[str, Any]) -> Frame:
        """
        应用音频效果

        Args:
            frame: 音频帧
            effects: 效果参数字典，可包含：
                    - fade_in: 淡入时间(秒)
                    - fade_out: 淡出时间(秒)
                    - speed: 速度调整倍数
                    - reverse: 是否反转

        Returns:
            Frame: 处理后的帧
        """
        if frame.type != FrameType.AUDIO:
            return frame

        try:
            # 确保数据为浮点型
            if not np.issubdtype(frame.data.dtype, np.floating):
                frame = ConversionUtils.int_to_float_audio(frame)

            # 应用淡入效果
            if effects.get("fade_in"):
                fade_samples = int(
                    effects["fade_in"] * frame.metadata.get("sample_rate", 48000)
                )
                if fade_samples > 0:
                    fade_curve = np.linspace(0, 1, fade_samples)
                    frame.data[:fade_samples] *= fade_curve

            # 应用淡出效果
            if effects.get("fade_out"):
                fade_samples = int(
                    effects["fade_out"] * frame.metadata.get("sample_rate", 48000)
                )
                if fade_samples > 0:
                    fade_curve = np.linspace(1, 0, fade_samples)
                    frame.data[-fade_samples:] *= fade_curve

            # 速度调整
            if effects.get("speed", 1.0) != 1.0:
                from scipy import signal

                speed = float(effects["speed"])
                frame.data = (
                    signal.resample(frame.data, int(len(frame.data) / speed), t=None)[0]
                    if isinstance(frame.data, np.ndarray)
                    else frame.data
                )

            # 音频反转
            if effects.get("reverse", False):
                frame.data = np.flip(frame.data)

            return frame
        except Exception as e:
            LOGGER.error(f"音频效果处理失败: {e}")
            return frame

    @staticmethod
    def scale_video_frame(
        frame: Frame,
        target_width: int,
        target_height: int,
        maintain_aspect: bool = True,
    ) -> Frame:
        """
        缩放视频帧

        Args:
            frame: 视频帧
            target_width: 目标宽度
            target_height: 目标高度
            maintain_aspect: 是否保持宽高比

        Returns:
            Frame: 处理后的帧
        """
        if frame.type != FrameType.VIDEO:
            return frame

        try:
            current_height, current_width = frame.data.shape[:2]

            if maintain_aspect:
                # 计算缩放比例
                scale = min(
                    target_width / current_width, target_height / current_height
                )
                new_width = int(current_width * scale)
                new_height = int(current_height * scale)

                # 缩放
                resized = cv2.resize(
                    frame.data, (new_width, new_height), interpolation=cv2.INTER_AREA
                )

                # 创建画布并居中放置图像
                canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
                y_offset = (target_height - new_height) // 2
                x_offset = (target_width - new_width) // 2
                canvas[
                    y_offset : y_offset + new_height, x_offset : x_offset + new_width
                ] = resized

                frame.data = canvas
            else:
                frame.data = cv2.resize(
                    frame.data,
                    (target_width, target_height),
                    interpolation=cv2.INTER_AREA,
                )

            return frame

        except Exception as e:
            LOGGER.error(f"视频帧缩放失败: {e}")
            return frame

    @staticmethod
    def rotate_video_frame(frame: Frame, angle: float, expand: bool = True) -> Frame:
        """
        旋转视频帧

        Args:
            frame: 视频帧
            angle: 旋转角度，正值表示逆时针旋转
            expand: 是否扩展画布以容纳完整旋转后的图像

        Returns:
            Frame: 处理后的帧
        """
        if frame.type != FrameType.VIDEO:
            return frame

        try:
            height, width = frame.data.shape[:2]
            center = (width / 2, height / 2)

            # 计算旋转矩阵
            rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)

            if expand:
                # 计算旋转后的图像尺寸
                cos = np.abs(rotation_matrix[0, 0])
                sin = np.abs(rotation_matrix[0, 1])
                new_width = int((height * sin) + (width * cos))
                new_height = int((height * cos) + (width * sin))

                # 调整旋转矩阵以考虑新尺寸
                rotation_matrix[0, 2] += (new_width / 2) - center[0]
                rotation_matrix[1, 2] += (new_height / 2) - center[1]
            else:
                new_width = width
                new_height = height

            # 执行旋转
            frame.data = cv2.warpAffine(
                frame.data,
                rotation_matrix,
                (new_width, new_height),
                flags=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(0, 0, 0),
            )
            return frame

        except Exception as e:
            LOGGER.error(f"视频帧旋转失败: {e}")
            return frame


@dataclass
class AnalyzerStats:
    """流分析器统计信息"""

    duration: float = 0.0
    info: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    state: bool = False


class StreamAnalyzer:
    """流分析器"""

    @staticmethod
    def analyze_stream_available(
        settings: StreamSettings,
        attempts: int = 3,
    ) -> AnalyzerStats:
        """分析流可用性"""
        details = AnalyzerStats()
        details.info = {
            "container": {},
            "bandwidth": 0.0,
            "retry_count": 0,
        }
        for attempt in range(attempts):
            start_time = time.time()
            try:
                with av.open(
                    settings.url,
                    options=settings.get_read_options(),
                    timeout=settings.timeout,
                ) as container:
                    # 检查流类型是否匹配
                    if not (
                        any(
                            isinstance(stream, VideoStream)
                            for stream in container.streams
                        )
                        or any(
                            isinstance(stream, AudioStream)
                            for stream in container.streams
                        )
                    ):
                        raise ValueError("未找到视频或音频流")

                    # 尝试读取一些数据以验证流的可访问性
                    bytes_received = 0
                    for packet in container.demux():
                        bytes_received += packet.size
                        if bytes_received > 1024 * 1024:
                            break

                    # 设置流连接信息
                    details.duration = time.time() - start_time
                    details.info["container"] = {
                        "format": container.format.name,
                        "metadata": {**container.metadata},
                    }
                    details.info["container"]["streams"] = []
                    for stream in container.streams:
                        if isinstance(stream, VideoStream):
                            video_stream = {
                                "index": stream.index,
                                "type": stream.type,
                                "codec_name": stream.codec_context.name,
                                "codec_type": stream.codec_context.type,
                                "time_base": (
                                    float(stream.time_base)
                                    if stream.time_base
                                    else None
                                ),
                                "start_time": stream.start_time,
                                "duration": getattr(stream, "duration", None),
                                "bit_rate": getattr(stream, "bit_rate", None),
                                "width": stream.width,
                                "height": stream.height,
                                "format": stream.pix_fmt,
                                "average_frame_rate": (
                                    float(stream.average_rate)
                                    if stream.average_rate
                                    else None
                                ),
                                "real_frame_rate": (
                                    float(stream.base_rate)
                                    if stream.base_rate
                                    else None
                                ),
                                "frames": stream.frames,
                                "sample_aspect_ratio": (
                                    str(stream.sample_aspect_ratio)
                                    if stream.sample_aspect_ratio
                                    else None
                                ),
                                "display_aspect_ratio": (
                                    str(stream.display_aspect_ratio)
                                    if stream.display_aspect_ratio
                                    else None
                                ),
                                "color_range": stream.color_range,
                                "color_space": getattr(stream, "color_space", None),
                                "color_transfer": stream.color_trc,
                                "color_primaries": stream.color_primaries,
                                "metadata": stream.metadata,
                            }
                            details.info["container"]["streams"].append(video_stream)
                        elif isinstance(stream, AudioStream):
                            audio_stream = {
                                "index": stream.index,
                                "type": stream.type,
                                "codec_name": stream.codec_context.name,
                                "codec_type": stream.codec_context.type,
                                "time_base": (
                                    float(stream.time_base)
                                    if stream.time_base
                                    else None
                                ),
                                "start_time": stream.start_time,
                                "duration": getattr(stream, "duration", None),
                                "bit_rate": getattr(stream, "bit_rate", None),
                                "sample_rate": stream.sample_rate,
                                "channels": stream.channels,
                                "channel_layout": (
                                    stream.layout.name if stream.layout else None
                                ),
                                "samples": getattr(stream, "duration", None),
                                "frame_size": stream.frame_size,
                                "bits_per_sample": getattr(
                                    stream, "bits_per_sample", None
                                ),
                                "metadata": stream.metadata,
                            }
                            details.info["container"]["streams"].append(audio_stream)
                    details.info["bandwidth"] = bytes_received / details.duration
                    details.error = None
                    details.state = True

                    return details

            except Exception as e:
                details.duration = time.time() - start_time
                details.info.update({"retry_count": attempt + 1})
                details.error = str(e)
                details.state = False
                LOGGER.error(
                    f"流连接失败由于{str(e)}，等待{attempt}秒尝试第{attempt + 1}次"
                )
                time.sleep(attempt)

        return details

    @staticmethod
    def analyze_stream_healthy(
        settings: StreamSettings, duration: int = 5
    ) -> AnalyzerStats:
        """分析流的健康状况"""
        details = AnalyzerStats()
        details.info = {
            "frame_metrics": {
                "processing_time": {"video": [], "audio": []},
                "quality": {
                    "video": {
                        "brightness": [],  # 亮度
                        "contrast": [],  # 对比度
                        "blur": [],  # 模糊度
                        "noise_level": [],  # 噪声水平
                    },
                    "audio": {
                        "rms_volume": [],  # 均方根音量
                        "peak_amplitude": [],  # 峰值振幅
                        "zero_crossing_rate": [],  # 过零率
                        "spectrum_centroid": [],  # 频谱质心
                    },
                },
            },
            "frame_count": 0,
            "issues": [],
        }

        start_time = time.time()
        try:
            frame_processor = FrameProcessor()
            frame_count = 0
            with av.open(
                settings.url,
                options=settings.get_read_options(),
                timeout=settings.timeout,
            ) as container:
                # 获取目标流
                streams = []
                if settings.type in [StreamType.VIDEO, StreamType.BOTH]:
                    video_stream = next(
                        (
                            stream
                            for stream in container.streams
                            if isinstance(stream, VideoStream)
                        ),
                        None,
                    )
                    if video_stream:
                        streams.append(video_stream)

                if settings.type in [StreamType.AUDIO, StreamType.BOTH]:
                    audio_stream = next(
                        (
                            stream
                            for stream in container.streams
                            if isinstance(stream, AudioStream)
                        ),
                        None,
                    )
                    if audio_stream:
                        streams.append(audio_stream)

                # 采集帧进行分析
                for frame in container.decode(*streams):
                    last_time = time.time()
                    if last_time - start_time > duration:
                        details.duration = last_time - start_time
                        break

                    if isinstance(frame, AVFrame):
                        processed_frame = frame_processor.process_frame(
                            frame, frame_count
                        )
                        # 收集处理时间
                        if processed_frame.metrics.processing_time:
                            details.info["frame_metrics"]["processing_time"][
                                processed_frame.type.name.lower()
                            ].append(processed_frame.metrics.processing_time)

                        # 收集质量指标
                        frame_quality = processed_frame.metrics.quality
                        for key, value in frame_quality.items():
                            details.info["frame_metrics"]["quality"][
                                processed_frame.type.name.lower()
                            ][key].append(value)

                        frame_count += 1

                # 计算平均值
                frame_metrics = details.info["frame_metrics"]
                for frame_type in ["video", "audio"]:
                    frame_metrics["processing_time"][frame_type] = float(
                        np.mean(frame_metrics["processing_time"][frame_type])
                    )
                    frame_metrics["quality"][frame_type] = {
                        key: float(np.mean(values))
                        for key, values in frame_metrics["quality"][frame_type].items()
                        if values
                    }

                # 检查健康状态
                PROCESSING_TIME_THRESHOLD = 100
                FRAME_QUALITY_THRESHOLDS = {
                    "video": {
                        "brightness": (40, 220),  # 亮度范围
                        "contrast": (30, None),  # 最小对比度
                        "blur": (None, 100),  # 最大模糊度
                        "noise_level": (None, 100),  # 最大噪声水平
                    },
                    "audio": {
                        "rms_volume": (0.01, None),  # 最小音量
                        "peak_amplitude": (0.01, None),  # 最小峰值振幅
                        "zero_crossing_rate": (0.1, 0.9),  # 过零率范围
                        "spectrum_centroid": (100, 10_000),  # 频谱质心范围
                    },
                }

                issues = []
                for frame_type in ["video", "audio"]:
                    processing_time = frame_metrics["processing_time"][frame_type]
                    if processing_time > PROCESSING_TIME_THRESHOLD:
                        issues.append(
                            f"{frame_type}帧的平均处理时间过高: {processing_time:.2f}ms"
                        )
                    for key, value in frame_metrics["quality"][frame_type].items():
                        min_val, max_val = FRAME_QUALITY_THRESHOLDS[frame_type].get(
                            key, (None, None)
                        )
                        if min_val is not None and value < min_val:
                            issues.append(f"{frame_type}帧的{key}过低: {value:.2f}")
                        if max_val is not None and value > max_val:
                            issues.append(f"{frame_type}帧的{key}过高: {value:.2f}")

                details.info["frame_metrics"] = frame_metrics
                details.info["frame_count"] = frame_count
                details.info["issues"] = issues
                if len(issues) > 3:
                    details.state = False
                else:
                    details.state = True

        except Exception as e:
            details.duration = last_time - start_time
            details.info["frame_count"] = frame_count
            details.error = str(e)
            details.state = False
            LOGGER.error(f"分析流健康失败: {e}")

        return details
