# encoding:utf-8
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Optional

from .logger import Loggers

LOGGER = Loggers().get_logger(__name__)

# 基础配置常量
DEFAULT_MAX_RETRY_COUNT = 5  # 最大重试次数
DEFAULT_MAX_RETRY_WAIT_TIME = 60.0  # 最大重试等待时间(秒)
DEFAULT_MAX_CONTAINER_DURATION = 1800  # 最大容器时长(秒)
DEFAULT_MAX_CONTAINER_BYTE_SIZE = 4 * 1024 * 1024 * 1024  # 最大容器字节大小
DEFAULT_TIMEOUT = 10.0  # 超时时间(秒)
DEFAULT_QUEUE_SIZE = 100  # 队列大小
DEFAULT_DIRECTORY = "results"  # 目录


class StreamType(Enum):
    """流类型定义"""

    VIDEO = auto()
    AUDIO = auto()
    BOTH = auto()


class StreamProtocol(Enum):
    """流协议定义"""

    RTSP = "rtsp"
    RTMP = "rtmp"
    UDP = "udp"
    TCP = "tcp"
    HTTP = "http"
    HLS = "hls"
    FILE = "file"

    @classmethod
    def from_url(cls, url: str) -> "StreamProtocol":
        """根据URL判断协议类型"""
        if url.startswith("rtsp://"):
            return cls.RTSP
        elif url.startswith("rtmp://"):
            return cls.RTMP
        elif url.startswith("tcp://"):
            return cls.TCP
        elif url.startswith("udp://"):
            return cls.UDP
        elif url.startswith(("http://", "https://")):
            if url.endswith(".m3u8"):
                return cls.HLS
            return cls.HTTP
        else:
            return cls.FILE

    def get_protocol_options(self) -> Dict[str, str]:
        """获取协议特定选项"""
        protocol_options = {"strict": "experimental"}
        protocol_options.update(
            {
                StreamProtocol.RTSP: {
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                    "rtsp_transport": "tcp",
                    "stimeout": "5000000",
                },
                StreamProtocol.RTMP: {
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                    "timeout": "10485760",
                },
                StreamProtocol.TCP: {
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                    "timeout": "5",
                },
                StreamProtocol.UDP: {
                    "fflags": "nobuffer",
                    "flags": "low_delay",
                    "buffer_size": "10485760",
                },
                StreamProtocol.HLS: {"timeout": "10485760"},
                StreamProtocol.HTTP: {"timeout": "10485760"},
            }.get(self, {})
        )

        return protocol_options


class StreamCodecType(Enum):
    """编解码器类型定义"""

    AUDIO = auto()
    VIDEO = auto()


class StreamCodecHwAccelType(Enum):
    """流硬件加速定义"""

    CUDA = auto()
    VAAPI = auto()
    QSV = auto()
    VULKAN = auto()
    VIDEOTOOLBOX = auto()


@dataclass
class StreamCodec:
    """流编解码器配置"""

    name: str
    encoder: str
    decoder: str
    format: str
    type: StreamCodecType
    hwaccel_type: Optional[StreamCodecHwAccelType] = None
    options: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        """初始化后处理"""
        if self.hwaccel_type is None:
            return

        hwaccel_codec = (
            self._hwaccel_codecs().get(self.hwaccel_type, {}).get(self.name, {})
        )
        for hwaccel_codec_key, hwaccel_codec_value in hwaccel_codec.items():
            setattr(self, hwaccel_codec_key, hwaccel_codec_value)

    def _hwaccel_codecs(self) -> Dict[StreamCodecHwAccelType, Any]:
        """硬件加速编解码器"""
        return {
            StreamCodecHwAccelType.CUDA: {
                "h264": {"encoder": "h264_nvenc", "decoder": "h264_cuvid"},
                "h265": {"encoder": "hevc_nvenc", "decoder": "hevc_cuvid"},
                "vp9": {"decoder": "vp9_cuvid"},
            },
            StreamCodecHwAccelType.VAAPI: {
                "h264": {"encoder": "h264_vaapi"},
                "h265": {"encoder": "hevc_vaapi"},
                "vp9": {"encoder": "vp9_vaapi"},
            },
            StreamCodecHwAccelType.QSV: {
                "h264": {"encoder": "h264_qsv", "decoder": "h264_qsv"},
                "h265": {"encoder": "hevc_qsv", "decoder": "hevc_qsv"},
                "vp9": {"encoder": "vp9_qsv", "decoder": "vp9_qsv"},
            },
            StreamCodecHwAccelType.VULKAN: {
                "h264": {"encoder": "h264_vulkan"},
                "h265": {"encoder": "hevc_vulkan"},
            },
            StreamCodecHwAccelType.VIDEOTOOLBOX: {
                "h264": {"encoder": "h264_videotoolbox"},
                "h265": {"encoder": "hevc_videotoolbox"},
            },
        }

    def get_decoder_options(self) -> Dict[str, Any]:
        """获取解码器选项"""
        decoder_options = {}
        if self.hwaccel_type:
            decoder_options["hwaccel"] = self.hwaccel_type.name.lower()
        if self.type is StreamCodecType.VIDEO:
            decoder_options["c:v"] = self.decoder
        if self.type is StreamCodecType.AUDIO:
            decoder_options["c:a"] = self.decoder
        decoder_options.update(self.options)
        return decoder_options

    def get_encoder_options(self) -> Dict[str, Any]:
        """获取编码器选项"""
        encoder_options = {}
        if self.hwaccel_type:
            encoder_options["hwaccel"] = self.hwaccel_type.name.lower()
        if self.type is StreamCodecType.VIDEO:
            encoder_options["c:v"] = self.encoder
        if self.type is StreamCodecType.AUDIO:
            encoder_options["c:a"] = self.encoder
        encoder_options.update(self.options)
        return encoder_options

    @classmethod
    def h264(cls) -> "StreamCodec":
        return cls(**asdict(StreamCodecPreset.H264.value))

    @classmethod
    def h265(cls) -> "StreamCodec":
        return cls(**asdict(StreamCodecPreset.H265.value))

    @classmethod
    def vp9(cls) -> "StreamCodec":
        return cls(**asdict(StreamCodecPreset.VP9.value))

    @classmethod
    def av1(cls) -> "StreamCodec":
        return cls(**asdict(StreamCodecPreset.AV1.value))

    @classmethod
    def mp3(cls) -> "StreamCodec":
        return cls(**asdict(StreamCodecPreset.MP3.value))

    @classmethod
    def aac(cls) -> "StreamCodec":
        return cls(**asdict(StreamCodecPreset.AAC.value))

    @classmethod
    def wav(cls) -> "StreamCodec":
        return cls(**asdict(StreamCodecPreset.WAV.value))

    @classmethod
    def flac(cls) -> "StreamCodec":
        return cls(**asdict(StreamCodecPreset.FLAC.value))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamCodec":
        """从字典创建实例"""
        data = deepcopy(data)
        data["type"] = StreamCodecType[data.pop("type").upper()]
        if data.get("hwaccel_type"):
            data["hwaccel_type"] = StreamCodecHwAccelType[
                data.pop("hwaccel_type").upper()
            ]
        return cls(**data)


class StreamCodecPreset(Enum):
    """流编解码器预设"""

    H264 = StreamCodec(
        name="h264",
        encoder="libx264",
        decoder="libx264",
        format="mp4",
        type=StreamCodecType.VIDEO,
        hwaccel_type=None,
        options={},
    )

    H265 = StreamCodec(
        name="h265",
        encoder="libx265",
        decoder="libx264",
        format="mp4",
        type=StreamCodecType.VIDEO,
        hwaccel_type=None,
        options={},
    )

    VP9 = StreamCodec(
        name="vp9",
        encoder="libvpx-vp9",
        decoder="libvpx-vp9",
        format="mkv",
        type=StreamCodecType.VIDEO,
        hwaccel_type=None,
        options={},
    )

    AV1 = StreamCodec(
        name="av1",
        encoder="libaom-av1",
        decoder="libaom-av1",
        format="mkv",
        type=StreamCodecType.VIDEO,
        hwaccel_type=None,
        options={},
    )

    MP3 = StreamCodec(
        name="mp3",
        encoder="libmp3lame",
        decoder="libmp3lame",
        format="mp3",
        type=StreamCodecType.AUDIO,
        hwaccel_type=None,
        options={},
    )

    AAC = StreamCodec(
        name="aac",
        encoder="aac",
        decoder="aac",
        format="m4a",
        type=StreamCodecType.AUDIO,
        hwaccel_type=None,
        options={},
    )

    WAV = StreamCodec(
        name="wav",
        encoder="pcm_s16le",
        decoder="pcm_s16le",
        format="wav",
        type=StreamCodecType.AUDIO,
        hwaccel_type=None,
        options={},
    )

    FLAC = StreamCodec(
        name="flac",
        encoder="flac",
        decoder="flac",
        format="flac",
        type=StreamCodecType.AUDIO,
        hwaccel_type=None,
        options={},
    )


class StreamQualityType(Enum):
    """流质量类型定义"""

    READ = auto()
    WRITE = auto()


@dataclass
class StreamQuality:
    """流质量配置"""

    video_bitrate: int
    video_width: int
    video_height: int
    video_pixel_format: str
    video_fps: int
    video_gop: int
    audio_bitrate: int
    audio_sample_format: str
    audio_sample_rate: int
    audio_layout: str
    frame_interval: int

    @classmethod
    def low(cls) -> "StreamQuality":
        return cls(**asdict(StreamQualityPreset.LOW.value))

    @classmethod
    def medium(cls) -> "StreamQuality":
        return cls(**asdict(StreamQualityPreset.MEDIUM.value))

    @classmethod
    def high(cls) -> "StreamQuality":
        return cls(**asdict(StreamQualityPreset.HIGH.value))

    @classmethod
    def null(cls) -> "StreamQuality":
        return cls(**asdict(StreamQualityPreset.NULL.value))

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamQuality":
        """从字典创建实例"""
        return cls(**data)


class StreamQualityPreset(Enum):
    """流质量预设"""

    LOW = StreamQuality(
        video_bitrate=2_000_000,  # 2Mbps
        video_width=1920,
        video_height=1080,
        video_pixel_format="yuv420p",
        video_fps=15,
        video_gop=60,
        audio_bitrate=128_000,  # 128kbps
        audio_sample_format="s16",
        audio_sample_rate=48000,
        audio_layout="stereo",
        frame_interval=0,
    )

    MEDIUM = StreamQuality(
        video_bitrate=4_000_000,  # 4Mbps
        video_width=1920,
        video_height=1080,
        video_pixel_format="yuv420p",
        video_fps=30,
        video_gop=60,
        audio_bitrate=256_000,  # 256kbps
        audio_sample_format="s16",
        audio_sample_rate=48000,
        audio_layout="stereo",
        frame_interval=0,
    )

    HIGH = StreamQuality(
        video_bitrate=8_000_000,  # 8Mbps
        video_width=1920,
        video_height=1080,
        video_pixel_format="yuv420p",
        video_fps=60,
        video_gop=60,
        audio_bitrate=320_000,  # 320kbps
        audio_sample_format="s16",
        audio_sample_rate=48000,
        audio_layout="stereo",
        frame_interval=0,
    )

    NULL = StreamQuality(
        video_bitrate=0,
        video_width=0,
        video_height=0,
        video_pixel_format="",
        video_fps=0,
        video_gop=0,
        audio_bitrate=0,
        audio_sample_format="",
        audio_sample_rate=0,
        audio_layout="",
        frame_interval=0,
    )


@dataclass
class StreamSettings:
    """流媒体设置"""

    # 读写设置
    url: str
    protocol: StreamProtocol = StreamProtocol.FILE
    type: StreamType = StreamType.BOTH
    codec: Dict[StreamCodecType, StreamCodec] = field(
        default_factory=lambda: {
            StreamCodecType.VIDEO: StreamCodec.h264(),
            StreamCodecType.AUDIO: StreamCodec.aac(),
        }
    )
    quality: Dict[StreamQualityType, StreamQuality] = field(
        default_factory=lambda: {
            StreamQualityType.READ: StreamQuality.null(),
            StreamQualityType.WRITE: StreamQuality.medium(),
        }
    )
    max_container_duration: int = DEFAULT_MAX_CONTAINER_DURATION
    max_container_byte_size: int = DEFAULT_MAX_CONTAINER_BYTE_SIZE
    directory: str = DEFAULT_DIRECTORY

    # 缓冲设置
    timeout: float = DEFAULT_TIMEOUT
    queue_size: int = DEFAULT_QUEUE_SIZE

    # 重试设置
    max_retry_count: int = DEFAULT_MAX_RETRY_COUNT
    max_retry_wait_time: float = DEFAULT_MAX_RETRY_WAIT_TIME

    def __post_init__(self) -> None:
        """初始化后处理"""
        self.protocol = StreamProtocol.from_url(self.url)

    def get_read_options(self) -> Dict[str, Any]:
        """获取读取选项"""
        read_options = {}
        read_options.update(self.protocol.get_protocol_options())
        if self.type in [StreamType.VIDEO, StreamType.BOTH]:
            read_options.update(self.codec[StreamCodecType.VIDEO].get_decoder_options())
        if self.type in [StreamType.AUDIO, StreamType.BOTH]:
            read_options.update(self.codec[StreamCodecType.AUDIO].get_decoder_options())

        return read_options

    def get_write_options(self) -> Dict[str, Any]:
        """获取写入选项"""
        write_options = {}
        if self.type in [StreamType.VIDEO, StreamType.BOTH]:
            write_options.update(
                self.codec[StreamCodecType.VIDEO].get_encoder_options()
            )
        if self.type in [StreamType.AUDIO, StreamType.BOTH]:
            write_options.update(
                self.codec[StreamCodecType.AUDIO].get_encoder_options()
            )

        return write_options

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StreamSettings":
        """从字典创建实例"""
        data = deepcopy(data)
        if data.get("quality"):
            quality = {}
            for quality_key, quality_value in data.pop("quality").items():
                quality_type = StreamQualityType[quality_key.upper()]
                if isinstance(quality_value, str):
                    quality[quality_type] = getattr(
                        StreamQuality, quality_value.lower()
                    )()
                else:
                    quality[quality_type] = StreamQuality.from_dict(quality_value)
            data["quality"] = quality
        if data.get("codec"):
            codec = {}
            for codec_key, codec_value in data.pop("codec").items():
                codec_type = StreamCodecType[codec_key.upper()]
                if isinstance(codec_value, str):
                    codec[codec_type] = getattr(StreamCodec, codec_value.lower())()
                else:
                    codec[codec_type] = StreamCodec.from_dict(codec_value)
            data["codec"] = codec
        if data.get("type"):
            data["type"] = StreamType[data.pop("type").upper()]
        if data.get("protocol"):
            data["protocol"] = StreamProtocol[data.pop("protocol").upper()]

        return cls(**data)
