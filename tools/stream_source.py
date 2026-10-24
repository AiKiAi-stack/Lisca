"""ticket-01: 拉流解码模块

从直接流地址（http(s) m3u8/flv/mp4 等）或本地文件解码音频，
输出 16kHz mono int16 PCM 数据块，供 ASR 引擎消费。
基于 PyAV（捆绑 ffmpeg 库），无需系统 ffmpeg。

用法:
    src = StreamSource("https://example.com/stream.m3u8")
    for chunk_bytes in src.iter_chunks(chunk_sec=0.1):
        audio_f32 = s16_to_f32(chunk_bytes)   # np.float32, 值域 [-1, 1]
"""
import os
import ssl
import tempfile
import urllib.request
from urllib.parse import urlparse

import av
import numpy as np

SAMPLING_RATE = 16000
OPEN_TIMEOUT_SEC = 5.0
READ_TIMEOUT_SEC = 5.0

# 平台 CDN 防盗链：裸 Lavf UA 会被部分节点 403，统一带浏览器 UA
DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def s16_to_f32(raw: bytes) -> np.ndarray:
    """int16 little-endian PCM bytes -> float32 array ([-1, 1])"""
    arr = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    return arr / 32768.0


class StreamSource:
    """从文件或网络流解码音频，逐块产出 16kHz mono s16le PCM。"""

    def __init__(self, url_or_path: str, chunk_sec: float = 0.1,
                 http_headers: dict = None, start_sec: float = 0):
        self.url_or_path = url_or_path
        self.timeline_offset_sec = start_sec
        self._start_sec = start_sec
        self.first_pts_sec = None
        options = {}
        self._tls_context = None
        self._ca_file = None
        self._temporary_ca_file = None
        self._closed = False
        self._http_headers = {}
        self._manifest_response = None
        parsed_url = urlparse(url_or_path)
        scheme = parsed_url.scheme.lower()
        is_remote = scheme in ("http", "https")
        is_https = scheme == "https"
        is_hls = is_remote and parsed_url.path.lower().endswith(".m3u8")
        if is_remote:
            hdrs = {"User-Agent": DEFAULT_UA}
            hdrs.update(http_headers or {})
            self._http_headers = hdrs
            if is_https:
                self._tls_context, self._ca_file, self._temporary_ca_file = _https_trust()
            if is_hls:
                # Decode a few segments behind the edge for a stable startup.
                # Alignment uses the decoded PTS, never the duration of a
                # separately fetched sliding playlist.
                options["live_start_index"] = "-3"
            else:
                # libavformat http protocol requires CRLF-terminated headers.
                options["headers"] = "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
                if is_https:
                    options.update(ca_file=self._ca_file, tls_verify="1")
        try:
            open_kwargs = {"timeout": (OPEN_TIMEOUT_SEC, READ_TIMEOUT_SEC)} if options else {}
            if is_hls:
                request = urllib.request.Request(url_or_path, headers=self._http_headers)
                response = urllib.request.urlopen(
                    request, timeout=OPEN_TIMEOUT_SEC, context=self._tls_context
                )
                self._manifest_response = response
                named_response = _NamedResponse(response, response.geturl())
                open_kwargs.update(format="hls", io_open=self._open_nested_http)
                self.container = av.open(named_response, options=options, **open_kwargs)
            else:
                if self._tls_context is not None:
                    open_kwargs["io_open"] = self._open_nested_http
                self.container = av.open(url_or_path, options=options, **open_kwargs)
        except Exception:
            try:
                self._close_manifest_response()
            finally:
                self._remove_temporary_ca_file()
            raise
        try:
            if not self.container.streams.audio:
                raise ValueError(f"输入中没有音频流: {url_or_path}")
            self.stream = self.container.streams.audio[0]
            if start_sec:
                self.container.seek(int(start_sec * av.time_base))
            self.resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLING_RATE)
            self.chunk_samples = int(chunk_sec * SAMPLING_RATE)
            self._buf = b""
            self._exhausted = False
        except Exception:
            self.close()
            raise

    def iter_chunks(self):
        """yield bytes: 16kHz mono int16 little-endian PCM 数据块"""
        if self._exhausted:
            return
        try:
            for frame in self.container.decode(self.stream):
                for rf in self.resampler.resample(frame):
                    frame_start = float(rf.pts * rf.time_base) if rf.pts is not None else None
                    samples = rf.to_ndarray()[0]
                    if self._start_sec and frame_start is not None:
                        skip = max(0, int(round((self._start_sec - frame_start) * SAMPLING_RATE)))
                        if skip >= len(samples):
                            continue
                        samples = samples[skip:]
                        self._start_sec = 0
                    if self.first_pts_sec is None and rf.pts is not None and rf.time_base is not None:
                        self.first_pts_sec = float(rf.pts * rf.time_base)
                    self._buf += samples.tobytes()
                    while len(self._buf) >= self.chunk_samples * 2:
                        chunk = self._buf[: self.chunk_samples * 2]
                        self._buf = self._buf[self.chunk_samples * 2:]
                        yield chunk
        finally:
            self.close()
        # 正常耗尽：吐出不足一块的尾部数据（调用方提前 break 时不会走到这里）
        if self._buf:
            yield self._buf
            self._buf = b""

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._exhausted = True
        try:
            self.container.close()
        finally:
            try:
                self._close_manifest_response()
            finally:
                self._remove_temporary_ca_file()

    def _close_manifest_response(self):
        response, self._manifest_response = self._manifest_response, None
        if response is not None:
            response.close()

    def _open_nested_http(self, url: str, _flags: int, options: dict):
        """Open HLS child playlists/segments with the same verified CA trust."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"HLS 子资源只支持 HTTP(S): {parsed.scheme or 'unknown'}")
        headers = self._http_headers.copy()
        raw_headers = options.get("headers", "")
        if isinstance(raw_headers, bytes):
            raw_headers = raw_headers.decode("latin-1")
        for line in str(raw_headers).splitlines():
            name, separator, value = line.partition(":")
            if separator and name.strip():
                headers[name.strip()] = value.strip()
        request = urllib.request.Request(url, headers=headers)
        return urllib.request.urlopen(
            request, timeout=READ_TIMEOUT_SEC, context=self._tls_context
        )

    def _remove_temporary_ca_file(self):
        if self._temporary_ca_file is not None:
            try:
                os.unlink(self._temporary_ca_file)
            except FileNotFoundError:
                pass
            self._temporary_ca_file = None


def _https_trust():
    """Return a Python TLS context and a PEM CA file for PyAV's FFmpeg build."""
    paths = ssl.get_default_verify_paths()
    configured = next((os.environ.get(name) for name in (
        "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", paths.openssl_cafile_env or "SSL_CERT_FILE"
    ) if os.environ.get(name)), None)
    if configured:
        if not os.path.isfile(configured):
            raise FileNotFoundError(f"配置的 CA 文件不存在: {configured}")
        context = ssl.create_default_context(cafile=configured)
        return context, configured, None

    candidates = (
        paths.cafile,
        paths.openssl_cafile,
        "/etc/ssl/certs/ca-certificates.crt",
        "/etc/pki/tls/certs/ca-bundle.crt",
        "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
        "/etc/ssl/cert.pem",
    )
    ca_file = next((path for path in candidates if path and os.path.isfile(path)), None)
    if ca_file:
        return ssl.create_default_context(cafile=ca_file), ca_file, None

    context = ssl.create_default_context()
    certificates = context.get_ca_certs(binary_form=True)
    if not certificates:
        raise ssl.SSLError("系统没有可用于 HTTPS 媒体的受信任 CA")
    bundle = tempfile.NamedTemporaryFile(
        mode="w", encoding="ascii", prefix="stream-source-ca-", suffix=".pem", delete=False
    )
    try:
        with bundle:
            for certificate in certificates:
                bundle.write(ssl.DER_cert_to_PEM_cert(certificate))
        return context, bundle.name, bundle.name
    except Exception:
        os.unlink(bundle.name)
        raise


class _NamedResponse:
    """File-like HLS manifest response whose name preserves relative URL base."""

    def __init__(self, response, url: str):
        self._response = response
        self.name = url

    def read(self, size=-1):
        return self._response.read(size)

    def close(self):
        self._response.close()
