"""ticket-01: 拉流解码模块

从直接流地址（http(s) m3u8/flv/mp4 等）或本地文件解码音频，
输出 16kHz mono int16 PCM 数据块，供 ASR 引擎消费。
基于 PyAV（捆绑 ffmpeg 库），无需系统 ffmpeg。

用法:
    src = StreamSource("https://example.com/stream.m3u8")
    for chunk_bytes in src.iter_chunks(chunk_sec=0.1):
        audio_f32 = s16_to_f32(chunk_bytes)   # np.float32, 值域 [-1, 1]
"""
import av
import numpy as np

SAMPLING_RATE = 16000


def s16_to_f32(raw: bytes) -> np.ndarray:
    """int16 little-endian PCM bytes -> float32 array ([-1, 1])"""
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return arr / 32768.0


class StreamSource:
    """从文件或网络流解码音频，逐块产出 16kHz mono s16le PCM。"""

    def __init__(self, url_or_path: str, chunk_sec: float = 0.1):
        self.url_or_path = url_or_path
        self.container = av.open(url_or_path)
        if not self.container.streams.audio:
            self.container.close()
            raise ValueError(f"输入中没有音频流: {url_or_path}")
        self.stream = self.container.streams.audio[0]
        self.resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLING_RATE)
        self.chunk_samples = int(chunk_sec * SAMPLING_RATE)
        self._buf = b""
        self._exhausted = False

    def iter_chunks(self):
        """yield bytes: 16kHz mono int16 little-endian PCM 数据块"""
        if self._exhausted:
            return
        try:
            for frame in self.container.decode(self.stream):
                for rf in self.resampler.resample(frame):
                    self._buf += rf.to_ndarray()[0].tobytes()
                    while len(self._buf) >= self.chunk_samples * 2:
                        chunk = self._buf[: self.chunk_samples * 2]
                        self._buf = self._buf[self.chunk_samples * 2:]
                        yield chunk
        finally:
            # 剩余不足一块的尾部数据也吐出（保证不丢尾部语音）
            if self._buf:
                yield self._buf
                self._buf = b""
            self._exhausted = True
            self.container.close()

    def close(self):
        self.container.close()
