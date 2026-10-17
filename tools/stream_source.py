"""ticket-01: 拉流解码模块

从直接流地址（http(s) m3u8/flv/mp4 等）或本地文件解码音频，
输出 16kHz mono int16 PCM 数据块，供 ASR 引擎消费。
基于 PyAV（捆绑 ffmpeg 库），无需系统 ffmpeg。

用法:
    src = StreamSource("https://example.com/stream.m3u8")
    for chunk_bytes in src.iter_chunks(chunk_sec=0.1):
        audio_f32 = s16_to_f32(chunk_bytes)   # np.float32, 值域 [-1, 1]
"""
import urllib.request
from urllib.parse import urljoin, urlparse

import av
import numpy as np

SAMPLING_RATE = 16000

# 平台 CDN 防盗链：裸 Lavf UA 会被部分节点 403，统一带浏览器 UA
DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _hls_live_offset(url: str, headers: dict) -> float | None:
    """Return the start time of HLS's last live segment, or None for VOD/unknown."""
    for _ in range(3):
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=8) as response:
            playlist = response.read().decode("utf-8", "replace")
        if "#EXT-X-ENDLIST" in playlist:
            return None

        lines = [line.strip() for line in playlist.splitlines() if line.strip()]
        if any(line.startswith("#EXT-X-STREAM-INF:") for line in lines):
            for index, line in enumerate(lines):
                if line.startswith("#EXT-X-STREAM-INF:"):
                    variant = next((x for x in lines[index + 1:] if not x.startswith("#")), None)
                    if variant:
                        url = urljoin(url, variant)
                        break
            else:
                return None
            continue

        durations = []
        for line in lines:
            if line.startswith("#EXTINF:"):
                try:
                    durations.append(float(line.split(":", 1)[1].split(",", 1)[0]))
                except ValueError:
                    return None
        if len(durations) < 2:
            return None
        # live_start_index=-1 selects the final segment, matching hls.js's
        # liveSyncPosition (one target duration behind the live edge).
        return sum(durations[:-1])
    return None


def s16_to_f32(raw: bytes) -> np.ndarray:
    """int16 little-endian PCM bytes -> float32 array ([-1, 1])"""
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return arr / 32768.0


class StreamSource:
    """从文件或网络流解码音频，逐块产出 16kHz mono s16le PCM。"""

    def __init__(self, url_or_path: str, chunk_sec: float = 0.1,
                 http_headers: dict = None):
        self.url_or_path = url_or_path
        self.timeline_offset_sec = 0.0
        options = {}
        if url_or_path.startswith(("http://", "https://")):
            hdrs = {"User-Agent": DEFAULT_UA}
            hdrs.update(http_headers or {})
            # libavformat http 协议要求 headers 以 CRLF 结尾
            options["headers"] = "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
            if urlparse(url_or_path).path.lower().endswith(".m3u8"):
                try:
                    live_offset = _hls_live_offset(url_or_path, hdrs)
                except Exception:  # noqa: BLE001 - keep existing AVIO fallback on inaccessible manifests
                    live_offset = None
                if live_offset is not None:
                    options["live_start_index"] = "-1"
                    self.timeline_offset_sec = live_offset
        self.container = av.open(url_or_path, options=options)
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
            self.container.close()
            self._exhausted = True
        # 正常耗尽：吐出不足一块的尾部数据（调用方提前 break 时不会走到这里）
        if self._buf:
            yield self._buf
            self._buf = b""

    def close(self):
        self.container.close()
