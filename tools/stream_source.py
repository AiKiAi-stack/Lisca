"""ticket-01: 拉流解码模块

从直接流地址（http(s) m3u8/flv/mp4 等）或本地文件解码音频，
输出 16kHz mono int16 PCM 数据块，供 ASR 引擎消费。
基于 PyAV（捆绑 ffmpeg 库），无需系统 ffmpeg。

用法:
    src = StreamSource("https://example.com/stream.m3u8")
    for chunk_bytes in src.iter_chunks(chunk_sec=0.1):
        audio_f32 = s16_to_f32(chunk_bytes)   # np.float32, 值域 [-1, 1]
"""
import io
import logging
import os
import re
import ssl
import tempfile
import threading
import urllib.error
import urllib.request
from fractions import Fraction
from urllib.parse import urljoin, urlparse, urlsplit

import av
import numpy as np

SAMPLING_RATE = 16000
OPEN_TIMEOUT_SEC = 5.0
READ_TIMEOUT_SEC = 5.0
HLS_OPEN_RETRY_DELAY_SEC = 1.0
MAX_REDIRECTED_HLS_BYTES = 1024 * 1024
# Only repair small timestamp discontinuities. Larger jumps and clock resets
# are unsupported because the byte-stream interface cannot map them safely.
MAX_REPAIRED_PTS_GAP_SEC = 1.0
MAX_TOTAL_REPAIRED_PTS_GAP_SEC = 60.0


class AudioTimelineError(ValueError):
    """Raised when audio PTS discontinuities exceed supported repair limits."""


_HLS_CONTENT_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
}
_HLS_URI_ATTRIBUTE = re.compile(
    r'(?i)(\bURI\s*=\s*)(?:"([^"]*)"|\'([^\']*)\'|([^,\r\n]*))'
)

# 平台 CDN 防盗链：裸 Lavf UA 会被部分节点 403，统一带浏览器 UA
DEFAULT_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
_LOGGER = logging.getLogger(__name__)


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
        self._hls_open_recovery_used = False
        self._hls_open_retry_cancel = threading.Event()
        self._hls_open_lock = threading.Lock()
        self._http_headers = {}
        self._manifest_response = None
        parsed_url = urlparse(url_or_path)
        scheme = parsed_url.scheme.lower()
        is_remote = scheme in ("http", "https")
        is_https = scheme == "https"
        is_hls = is_remote and parsed_url.path.lower().endswith(".m3u8")
        self._is_hls = is_hls
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
                # Python io_open provides AVIOContexts without an FFmpeg HTTP
                # URLContext; HLS keepalive assumes that URLContext exists.
                options.update(live_start_index="-3", http_persistent="0")
                # Bound metadata probing so live HLS can yield before later segments arrive.
                options["analyzeduration"] = "100000"
            else:
                # libavformat http protocol requires CRLF-terminated headers.
                options["headers"] = "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
                if is_https:
                    options.update(ca_file=self._ca_file, tls_verify="1")
                if parsed_url.path.lower().endswith(".wav"):
                    # Limit stream-info read-ahead for known paced WAV sources.
                    options["analyzeduration"] = "100000"
        try:
            open_kwargs = {"timeout": (OPEN_TIMEOUT_SEC, READ_TIMEOUT_SEC)} if options else {}
            if is_hls:
                request = urllib.request.Request(url_or_path, headers=self._http_headers)
                response = self._open_hls_http(request, OPEN_TIMEOUT_SEC)
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
            self._pts_origin_sec = None
            self._pts_cursor_samples = 0
            self._inserted_pts_gap_samples = 0
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
                    yield from self._yield_pcm_chunks(self._append_resampled_frame(rf))
            # A normal EOF can leave a short delayed tail in the resampler.
            for rf in self.resampler.resample(None):
                yield from self._yield_pcm_chunks(self._append_resampled_frame(rf))
        finally:
            self.close()
        # 正常耗尽：吐出不足一块的尾部数据（调用方提前 break 时不会走到这里）
        if self._buf:
            yield from self._yield_pcm_chunks([self._buf])
            self._buf = b""

    def _yield_pcm_chunks(self, chunks):
        for chunk in chunks:
            if chunk:
                with self._hls_open_lock:
                    self._hls_open_recovery_used = False
            yield chunk

    def _append_resampled_frame(self, frame):
        frame_start = None
        if frame.pts is not None and frame.time_base is not None:
            frame_start = Fraction(frame.pts) * Fraction(frame.time_base)

        samples = frame.to_ndarray()[0]
        sample_rate = frame.sample_rate or SAMPLING_RATE
        if self._start_sec and frame_start is not None:
            skip = max(0, int(round((self._start_sec - float(frame_start)) * sample_rate)))
            if skip >= len(samples):
                return []
            if self.first_pts_sec is None:
                self.first_pts_sec = float(frame_start)
            samples = samples[skip:]
            frame_start += Fraction(skip, sample_rate)
            self._start_sec = 0
        elif self.first_pts_sec is None and frame_start is not None:
            self.first_pts_sec = float(frame_start)

        if not len(samples):
            return []

        max_gap_samples = int(MAX_REPAIRED_PTS_GAP_SEC * SAMPLING_RATE)
        if frame_start is not None:
            if self._pts_origin_sec is None:
                self._pts_origin_sec = frame_start
            pts_start_samples = int(
                round(float((frame_start - self._pts_origin_sec) * SAMPLING_RATE))
            )
            pts_delta_samples = pts_start_samples - self._pts_cursor_samples
            if pts_delta_samples > max_gap_samples:
                raise AudioTimelineError(
                    f"音频 PTS 正向间隙 {pts_delta_samples / SAMPLING_RATE:.3f}s "
                    f"超过自动补齐上限 {MAX_REPAIRED_PTS_GAP_SEC:.1f}s"
                )
            if pts_delta_samples < -max_gap_samples:
                raise AudioTimelineError(
                    f"音频 PTS 向后跳变 {-pts_delta_samples / SAMPLING_RATE:.3f}s "
                    f"超过可裁剪重叠上限 {MAX_REPAIRED_PTS_GAP_SEC:.1f}s"
                )
            if pts_delta_samples > 0:
                total_limit = int(MAX_TOTAL_REPAIRED_PTS_GAP_SEC * SAMPLING_RATE)
                if self._inserted_pts_gap_samples + pts_delta_samples > total_limit:
                    raise AudioTimelineError(
                        "音频 PTS 正向间隙修复累计超过 "
                        f"{MAX_TOTAL_REPAIRED_PTS_GAP_SEC:.1f}s 上限"
                    )
                self._buf += b"\x00" * (pts_delta_samples * 2)
                self._pts_cursor_samples += pts_delta_samples
                self._inserted_pts_gap_samples += pts_delta_samples
            elif pts_delta_samples < 0:
                samples = samples[min(-pts_delta_samples, len(samples)):]

        self._buf += samples.tobytes()
        if self._pts_origin_sec is not None:
            self._pts_cursor_samples += len(samples)

        chunks = []
        while len(self._buf) >= self.chunk_samples * 2:
            chunks.append(self._buf[: self.chunk_samples * 2])
            self._buf = self._buf[self.chunk_samples * 2:]
        return chunks

    def close(self):
        with self._hls_open_lock:
            if self._closed:
                return
            self._closed = True
            self._exhausted = True
            self._hls_open_retry_cancel.set()
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
        """Open child HTTP resources with the same verified CA trust."""
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
        if self._is_hls:
            response = self._open_hls_http(request, READ_TIMEOUT_SEC)
        else:
            response = urllib.request.urlopen(
                request, timeout=READ_TIMEOUT_SEC, context=self._tls_context
            )
        try:
            effective_url = response.geturl()
        except Exception:
            response.close()
            raise
        if effective_url == url or not _is_hls_manifest_candidate(response, url):
            return response

        try:
            prefix = response.read(16)
        except Exception:
            response.close()
            raise
        if not prefix.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"#EXTM3U"):
            return _PrefixedResponse(prefix, response, effective_url)

        try:
            body = _read_bounded_hls_manifest(response, prefix)
            body = _rewrite_hls_playlist(body, effective_url)
        finally:
            response.close()
        return _NamedResponse(io.BytesIO(body), effective_url)

    def _open_hls_http(self, request, timeout):
        """Retry one temporary HLS open failure until PCM advances the budget."""
        try:
            return self._urlopen_hls(request, timeout)
        except Exception as error:
            error_kind = _temporary_hls_open_error(error)
            if error_kind is None:
                raise

            with self._hls_open_lock:
                if self._hls_open_recovery_used:
                    raise
                self._hls_open_recovery_used = True
            _LOGGER.warning(
                "HLS open recovery attempt=1/1 delay=1s error=%s", error_kind
            )
            if self._hls_open_retry_cancel.wait(HLS_OPEN_RETRY_DELAY_SEC) or self._closed:
                raise
            return self._urlopen_hls(request, timeout)

    def _urlopen_hls(self, request, timeout):
        try:
            with self._hls_open_lock:
                if self._closed or self._hls_open_retry_cancel.is_set():
                    raise RuntimeError("stream source is closed")
                return urllib.request.urlopen(
                    request, timeout=timeout, context=self._tls_context
                )
        except urllib.error.HTTPError as error:
            error_body = getattr(error, "fp", None)
            if error_body is not None:
                try:
                    error_body.close()
                except Exception as close_error:
                    _LOGGER.debug(
                        "Failed to close HLS HTTP error body error=%s",
                        type(close_error).__name__,
                    )
            raise

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


class _PrefixedResponse:
    """Replay a short sniffed prefix before continuing the original response."""

    def __init__(self, prefix: bytes, response, url: str):
        self._prefix = io.BytesIO(prefix)
        self._response = response
        self.name = url

    def read(self, size=-1):
        if size is None or size < 0:
            return self._prefix.read() + self._response.read()
        prefix = self._prefix.read(size)
        if len(prefix) < size:
            prefix += self._response.read(size - len(prefix))
        return prefix

    def close(self):
        try:
            self._prefix.close()
        finally:
            self._response.close()

    def __getattr__(self, name):
        return getattr(self._response, name)


def _is_hls_manifest_candidate(response, url: str) -> bool:
    if urlparse(url).path.lower().endswith((".m3u8", ".m3u")):
        return True
    headers = getattr(response, "headers", None)
    if headers is None:
        return False
    return headers.get_content_type().lower() in _HLS_CONTENT_TYPES


def _temporary_hls_open_error(error):
    """Return a safe category for retryable failures before response bytes arrive."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 408:
            return "http_408"
        if 500 <= error.code <= 599:
            return "http_5xx"
        return None
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, ConnectionError):
        return "connection"
    if isinstance(error, urllib.error.URLError):
        if isinstance(error.reason, TimeoutError):
            return "timeout"
        if isinstance(error.reason, ConnectionError):
            return "connection"
    return None


def _read_bounded_hls_manifest(response, prefix: bytes) -> bytes:
    body = bytearray(prefix)
    headers = getattr(response, "headers", None)
    content_length = headers.get("Content-Length") if headers is not None else None
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            declared_length = 0
        if declared_length > MAX_REDIRECTED_HLS_BYTES:
            raise ValueError("redirected HLS manifest exceeds the size limit")

    while len(body) <= MAX_REDIRECTED_HLS_BYTES:
        chunk = response.read(min(64 * 1024, MAX_REDIRECTED_HLS_BYTES + 1 - len(body)))
        if not chunk:
            return bytes(body)
        body.extend(chunk)
    raise ValueError("redirected HLS manifest exceeds the size limit")


def _resolve_hls_uri(uri: str, base_url: str) -> str:
    if urlsplit(uri).scheme or uri.startswith("//"):
        return uri
    return urljoin(base_url, uri)


def _rewrite_hls_playlist(body: bytes, base_url: str) -> bytes:
    text = body.decode("utf-8")
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    if bom:
        text = text[1:]
    rewritten = []
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        ending = line[len(content):]
        if content.lstrip().startswith("#"):
            def replace_uri(match):
                uri = match.group(2) or match.group(3) or match.group(4) or ""
                quote = '"' if match.group(2) is not None else "'" if match.group(3) is not None else ""
                return match.group(1) + quote + _resolve_hls_uri(uri, base_url) + quote

            content = _HLS_URI_ATTRIBUTE.sub(replace_uri, content)
        else:
            stripped = content.strip()
            if stripped:
                leading = content[:len(content) - len(content.lstrip())]
                trailing = content[len(content.rstrip()):]
                content = leading + _resolve_hls_uri(stripped, base_url) + trailing
        rewritten.append(content + ending)
    return (bom + "".join(rewritten)).encode("utf-8")
