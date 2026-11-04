"""ticket-06: webUI 单页 MVP

单页：输入流地址/文件路径 → 引擎选择 → 大字号实时字幕（WebSocket 推送）。

用法:
    python webui.py [--host 0.0.0.0] [--port 8002]
    # SSH 远程访问: ssh -L 8002:localhost:8002 user@host 后浏览器开 localhost:8002

管理页面只允许主机本机访问；观众通过应用内生成的随机链接从局域网只读观看。
注意: 本地引擎需要 CUDA 运行库在 LD_LIBRARY_PATH（建议经 run_subs.sh 的环境启动）。
"""
import argparse
import asyncio
import hashlib
import ipaddress
import json
import logging
import math
import os
import re
import secrets
import socket
import shutil
import sys
import threading
import time
import warnings
from concurrent.futures import ThreadPoolExecutor
from collections import deque
from itertools import chain
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

# 代理 verify=False 的告警压掉（ticket-17）
warnings.filterwarnings("ignore", message=".*InsecureRequestWarning.*")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whisper_streaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse  # noqa: E402
from starlette.background import BackgroundTask  # noqa: E402
import uvicorn  # noqa: E402

from engines import create_engine  # noqa: E402
from caption_preview import StableCaptionPreview, TranslationClauses  # noqa: E402
from input_resolver import classify, resolve_stream_url  # noqa: E402
from platform_resolver import resolve_platform_url  # noqa: E402
from stream_source import AudioTimelineError, DEFAULT_UA, StreamSource, s16_to_f32  # noqa: E402
from live_share import LiveShare, MAX_VIEWERS  # noqa: E402
from cloud_translation import (CloudConfigStore, CloudError, QwenTranslator, RequestStop,
                               TranslationSession, validate_pair)  # noqa: E402
from provider_errors import classify_provider_failure  # noqa: E402
from streamparser.recognition_process import RecognitionCapacityError  # noqa: E402

app = FastAPI(title="实时字幕")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_static")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

UA = DEFAULT_UA

# 服务端后台日志（ticket-14）：环形缓冲，经 WS 推给前端日志面板
_SERVER_LOGS: deque = deque(maxlen=500)
_LOG_SINKS: list = []  # 每连接的 put() 回调，实时推送新日志行
_MAX_WS_EVENT_BACKLOG = 2048
_MAX_SESSION_CAPTIONS = 20_000
_MAX_SESSION_CAPTION_VARIANTS = 8
# Independent session-wide budget for retained transcript fingerprints used by
# withdrawal replay suppression; it is not a draft-revision count and never evicts.
_MAX_SESSION_CAPTION_RETENTION_FINGERPRINTS = 20_000
_MAX_PENDING_CAPTIONS = 2_048
_MAX_ACTIVE_RECOGNITION_WORKERS = 8
logger = logging.getLogger("webui")  # 会话生命周期 INFO 日志（用户可见）
_SHARES: dict[str, LiveShare] = {}
_SHARE_LOCK = threading.RLock()
_CLOUD = CloudConfigStore(os.path.join(os.path.dirname(UPLOAD_DIR), ".data", "cloud.bin"))
_CLOUD_JOBS: dict = {}
_CLOUD_LOCK = threading.RLock()
_ACTIVE_RECOGNITION_WORKERS = threading.BoundedSemaphore(_MAX_ACTIVE_RECOGNITION_WORKERS)


class SessionCapacityError(RuntimeError):
    """Recognition exceeded a bounded per-session caption index."""


def _session_monotonic() -> float:
    """Clock seam for bounded feed pacing and recognition-progress budgets."""
    return time.monotonic()


def _session_wait(stop: threading.Event, seconds: float) -> bool:
    """Wait interruptibly while keeping session pacing replaceable in tests."""
    return stop.wait(seconds)


def _redact_url(value: str) -> str:
    """Keep source identity useful in diagnostics while hiding credentials."""
    try:
        parts = urlsplit(value)
        if not parts.scheme or not parts.netloc:
            return value
        host = parts.netloc.rsplit("@", 1)[-1]
        query = urlencode([(key, "[已隐藏]") for key, _ in parse_qsl(
            parts.query, keep_blank_values=True
        )])
        return urlunsplit((parts.scheme, host, parts.path, query, ""))
    except ValueError:
        return "[已隐藏 URL]"


_URL_IN_TEXT = re.compile(r"(?:https?|rtsp|rtmp|rtp)://[^\s\"'<>]+", re.I)


def _redact_urls_in_text(value: str) -> str:
    return _URL_IN_TEXT.sub(lambda match: _redact_url(match.group(0)), value)


@app.get("/api/cloud")
def cloud_config():
    try:
        return _CLOUD.get().public(_CLOUD.path.exists())
    except CloudError as exc:
        raise HTTPException(400, str(exc)) from None


@app.put("/api/cloud")
async def update_cloud_config(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise CloudError("配置格式无效")
        return _CLOUD.update(data)
    except (ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, CloudError) else "无法保存云端配置"
        raise HTTPException(400, message) from None


@app.delete("/api/cloud")
def delete_cloud_config():
    with _CLOUD_LOCK:
        jobs = list(_CLOUD_JOBS.values())
        _CLOUD_JOBS.clear()
    for job in jobs:
        job["stop"].set()
    return _CLOUD.clear()


@app.post("/api/cloud/sessions")
async def create_cloud_session(request: Request):
    try:
        data = await request.json()
        if not isinstance(data, dict):
            raise CloudError("会话格式无效")
        mode = data.get("mode", "transcribe")
        source, target = data.get("source", "auto"), data.get("target")
        if mode == "translate":
            validate_pair(source, target)
        elif mode != "transcribe" or source not in ("auto", "zh", "en"):
            raise CloudError("请选择自动识别、中文或英文原文字幕")
        url = data.get("url", "")
        if not isinstance(url, str) or not url.strip() or len(url) > 8192:
            raise CloudError("请输入直播地址或选择本地视频")
        config = _CLOUD.get()
        if not config.configured:
            raise CloudError("请先配置云端 API Key")
        position = float(data.get("start_at", 0))
        if not math.isfinite(position) or position < 0:
            raise CloudError("播放位置无效")
        if position and not os.path.isfile(url):
            raise CloudError("首版仅支持本地视频跳转")
        with _CLOUD_LOCK:
            now = time.monotonic()
            for token, job in list(_CLOUD_JOBS.items()):
                if job["expires"] < now or job["stop"].is_set():
                    job["stop"].set()
                    _CLOUD_JOBS.pop(token, None)
            if len(_CLOUD_JOBS) >= 8:
                raise CloudError("待处理会话过多，请先停止当前会话")
            token = secrets.token_urlsafe(32)
            _CLOUD_JOBS[token] = {"url": url.strip(), "config": config,
                "source": source, "target": target, "mode": mode, "start_at": position,
                "stop": RequestStop(), "expires": now + 60, "active": False}
        return {"session": token, "mode": mode}
    except (ValueError, TypeError) as exc:
        message = str(exc) if isinstance(exc, CloudError) else "会话参数无效"
        raise HTTPException(400, message) from None


@app.delete("/api/cloud/sessions/{token}")
def stop_cloud_session(token: str):
    with _CLOUD_LOCK:
        job = _CLOUD_JOBS.pop(token, None)
    if job:
        job["stop"].set()
    return {"ok": True}


@app.post("/api/cloud/check")
def check_cloud_connection():
    config = _CLOUD.get()
    if not config.configured:
        raise HTTPException(400, "请先填写 Key")
    engine = None
    try:
        engine = create_engine("aliyun-funasr", api_key=config.api_key, language_hints=[])
        engine.poll()
        return {"ok": True, "message": "识别服务已发起连接；实际认证和语音识别将在播放时验证"}
    except Exception:
        raise HTTPException(400, "识别服务连接失败，请检查 Key、权限和网络") from None
    finally:
        if engine:
            engine.close()


def _is_local_host(host: str | None) -> bool:
    if host in ("localhost", "testclient"):
        return True
    try:
        address = ipaddress.ip_address(host or "")
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return address.is_loopback
    except ValueError:
        return False


def _is_lan_host(host: str | None) -> bool:
    if _is_local_host(host):
        return True
    try:
        address = ipaddress.ip_address(host or "")
        if getattr(address, "ipv4_mapped", None):
            address = address.ipv4_mapped
        return address.is_private or address.is_link_local
    except ValueError:
        return False


@app.middleware("http")
async def keep_admin_routes_local(request: Request, call_next):
    """Expose only capability-based viewer routes to other LAN devices."""
    path = request.url.path
    viewer_route = path in {"/hls.min.js", "/caption_presenter.js", "/playback_guard.js", "/player.css"} or path.startswith("/share/")
    client_host = request.client.host if request.client else None
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin \
            and urlparse(origin).netloc != request.url.netloc:
        return Response("请求来源不匹配", status_code=403)
    if viewer_route and path.startswith("/share/") and not _is_lan_host(client_host):
        return Response("分享链接仅允许局域网设备访问", status_code=403)
    if not viewer_route and not _is_local_host(client_host):
        return Response("管理页面仅允许在主机本机访问", status_code=403)
    return await call_next(request)


def _lan_ipv4_candidates() -> list[str]:
    """List this host's private IPv4 addresses so multi-adapter PCs can choose."""
    try:
        addresses = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        addresses = []
    candidates = []
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            continue
        if address.version == 4 and address.is_private and not address.is_loopback \
                and not address.is_link_local and value not in candidates:
            candidates.append(value)
    return candidates


@app.post("/share/start")
async def start_share(request: Request):
    if not _is_local_host(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="只有主机本机可以创建分享")
    data = await request.json()
    url = str(data.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=422, detail="缺少直播地址")
    with _SHARE_LOCK:
        if any(share.active for share in _SHARES.values()):
            raise HTTPException(status_code=409, detail="已有直播会话正在运行")
        share = LiveShare(url)
        share.video_id = share.register_media(url, "video")
        share.audio_id = share.register_media(url, "audio")
        _SHARES[share.token] = share
    addresses = _lan_ipv4_candidates() or ["127.0.0.1"]
    port = request.url.port or 8002
    urls = [f"http://{ip}:{port}/share/{share.token}" for ip in addresses]
    return {"token": share.token, "url": urls[0], "urls": urls,
            "max_viewers": MAX_VIEWERS}


@app.post("/share/stop/{token}")
async def stop_share(token: str, request: Request):
    if not _is_local_host(request.client.host if request.client else None):
        raise HTTPException(status_code=403, detail="只有主机本机可以停止直播")
    with _SHARE_LOCK:
        share = _SHARES.get(token)
        if not share:
            return {"ok": True}
        _SHARES.pop(token, None)
    await asyncio.to_thread(share.stop)
    return {"ok": True}


def _active_share(token: str) -> LiveShare:
    with _SHARE_LOCK:
        share = _SHARES.get(token)
    if not share or not share.active:
        raise HTTPException(status_code=404, detail="这场直播已结束或分享链接已失效")
    return share


@app.get("/share/{token}")
async def share_page(token: str):
    _active_share(token)
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


@app.get("/share/{token}/info")
async def share_info(token: str):
    share = _active_share(token)
    return {"is_hls": urlparse(share.url).path.lower().endswith(".m3u8"),
            "is_live": True}


@app.post("/share/{token}/join")
async def share_join(token: str, request: Request):
    share = _active_share(token)
    viewer_id = share.reserve_viewer(request.client.host if request.client else "")
    if viewer_id is None:
        raise HTTPException(status_code=429, detail="观众人数已满，最多允许 3 台设备同时观看")
    return {"viewer_id": viewer_id,
            "is_hls": urlparse(share.url).path.lower().endswith(".m3u8"),
            "video": f"/share/{token}/viewer/{viewer_id}/media/{share.video_id}",
            "audio": f"/share/{token}/viewer/{viewer_id}/media/{share.audio_id}"}


@app.get("/share/{token}/viewer/{viewer_id}/media/{media_id}")
async def share_media(token: str, viewer_id: str, media_id: str, request: Request):
    share = _active_share(token)
    client_host = request.client.host if request.client else ""
    if not share.has_viewer(viewer_id, client_host):
        raise HTTPException(status_code=404, detail="观众链接无效或已过期")
    entry = share.media_url(media_id)
    if not entry:
        raise HTTPException(status_code=404, detail="媒体链接无效")
    url, role = entry
    if not share.active:
        raise HTTPException(status_code=404, detail="直播已结束")
    if classify(url)[0] == "page":
        try:
            url = await (
                _resolve_audio_url(url) if role == "audio" else _resolve_video_url(url)
            )
        except Exception as exc:  # noqa: BLE001 - page resolver errors are user-visible
            logger.warning("共享直播平台页面解析失败: %s", _redact_urls_in_text(str(exc)))
            raise HTTPException(
                status_code=502,
                detail="平台页面解析失败：请检查链接、访问权限和解析器支持情况",
            ) from None
    if os.path.isfile(url):
        return FileResponse(url)
    try:
        return await _proxy_stream(request, url, share=share, viewer_id=viewer_id)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - keep pull errors distinct from page parsing
        logger.warning("共享直播媒体拉流失败: %s", _redact_urls_in_text(str(exc)))
        raise HTTPException(status_code=502, detail="媒体拉流失败：请检查来源、网络和访问权限") from None


class _ServerLogHandler(logging.Handler):
    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001 - 格式化失败不阻断日志
            return
        _SERVER_LOGS.append(line)
        for sink in list(_LOG_SINKS):
            sink({"type": "log", "line": line})

async def _resolve_video_url(user_input: str) -> str:
    return await resolve_platform_url(user_input, "bv*")


async def _resolve_audio_url(user_input: str) -> str:
    return await resolve_platform_url(user_input, "ba")


def _proxy_media_url(target: str, share: LiveShare | None = None,
                     viewer_id: str | None = None) -> str:
    """媒体 URL → 本地代理 URL（浏览器只与本地通信，代理统一补 UA/Referer）。

    分片/密钥/变体直连上游会被 UA/防盗链检查挡（ticket-19 实测 err=4），
    全部改走 /video?url= 代理；data: 等不可代理的保留原样。"""
    from urllib.parse import quote

    if share:
        media_id = share.register_media(target)
        if viewer_id:
            return f"/share/{share.token}/viewer/{viewer_id}/media/{media_id}"
        return f"/share/{share.token}/media/{media_id}"
    return "/video?url=" + quote(target, safe="")


def _rewrite_hls_playlist(text: str, stream_url: str,
                          share: LiveShare | None = None,
                          viewer_id: str | None = None) -> str:
    """HLS playlist 改写为本地代理 URL。

    普通行（ts 分片 / 变体子列表）与 # 行 URI 属性（EXT-X-KEY 密钥 /
    EXT-X-MEDIA / EXT-X-STREAM-INF 变体）统一 urljoin 后改写为 /video?url=，
    浏览器全部媒体流量走本地代理（ticket-19）；另清洗 ##EXT 双井号瑕疵行。"""
    import re

    URI_ATTR = re.compile(r'(URI=")([^"]*)(")')

    def _rewrite_attr(m):
        uri = m.group(2)
        if uri and not uri.startswith("data:"):
            return m.group(1) + _proxy_media_url(
                urljoin(stream_url, uri), share, viewer_id
            ) + m.group(3)
        return m.group(0)

    rewritten = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("##"):
            line = s[1:]  # 清洗双井号（实测源有 ##EXT-X-VERSION:3）
        elif s.startswith("#"):
            if "URI=" in s:
                line = URI_ATTR.sub(_rewrite_attr, s)
        elif s and not s.startswith("/video?url="):
            line = _proxy_media_url(urljoin(stream_url, s), share, viewer_id)
        rewritten.append(line)
    return "\n".join(rewritten)


def parse_m3u(text: str) -> list:
    """解析 m3u 频道列表：#EXTINF（频道名/group-title）→ 下一行 URL。

    返回 [{group, name, url}]，跳过无 URL 的条目（含 header 行）。"""
    import re

    # A single HLS media playlist is also an .m3u8 file, but its EXTINF lines
    # describe timed media segments rather than selectable TV channels.
    if re.search(r"(?m)^\s*#EXT-X-(?:TARGETDURATION|MEDIA-SEQUENCE|STREAM-INF|ENDLIST|PART|PROGRAM-DATE-TIME)\b", text, re.I):
        raise ValueError("这是单路 HLS 播放流，不是频道列表；请直接播放该地址，频道列表请使用 IPTV M3U 清单。")

    channels = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#EXTINF"):
            name = s.split(",", 1)[1].strip() if "," in s else ""
            m = re.search(r'group-title="([^"]*)"', s)
            channels.append({"group": m.group(1) if m else "", "name": name, "url": ""})
        elif channels and s and not s.startswith("#") and not channels[-1]["url"]:
            channels[-1]["url"] = s
    return [c for c in channels if c["url"]]


async def _proxy_stream(request: Request, stream_url: str,
                        share: LiveShare | None = None,
                        viewer_id: str | None = None):
    """转发流（补防盗链头），供前端 <video>/<audio> 播放。

    HLS playlist（m3u8）会被浏览器当作文本，相对 .ts/变体路径无法解析——
    把相对行改写为绝对 CDN URL 后再返回。"""
    import httpx

    headers = {"Referer": "https://www.bilibili.com", "User-Agent": UA}
    if "range" in request.headers:
        headers["Range"] = request.headers["range"]
    # verify=False（ticket-17）：B站 CDN 存在证书过期/异常，httpx 默认校验 TLS 会
    # 代理 500 → 视频黑屏。流媒体代理标准做法（yt-dlp/ffmpeg 均不校验这些 CDN）。
    client = httpx.AsyncClient(timeout=None, follow_redirects=True, verify=False)
    try:
        req = client.build_request("GET", stream_url, headers=headers)
        upstream = await client.send(req, stream=True)
    except Exception as exc:  # noqa: BLE001 - 代理层失败要完整入日志（ticket-16）
        logger.warning("代理请求失败 %s: %s", _redact_url(stream_url)[:120],
                       _redact_urls_in_text(str(exc)))
        await client.aclose()
        raise
    if upstream.status_code >= 400:
        logger.warning("代理上游非 2xx %s -> HTTP %s（%s）",
                       _redact_url(stream_url)[:120], upstream.status_code, request.url.path)
        await upstream.aclose()
        await client.aclose()
        raise HTTPException(status_code=502, detail="媒体拉流失败：上游拒绝或无法提供媒体")
    pass_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() in ("content-type", "content-length", "accept-ranges", "content-range")}
    content_type = upstream.headers.get("content-type", "").lower()

    is_hls = "mpegurl" in content_type or ".m3u8" in stream_url.lower()
    if is_hls:
        body = await upstream.aread()
        await upstream.aclose()
        await client.aclose()
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError:
            text = body.decode("latin-1")
        new_body = _rewrite_hls_playlist(text, stream_url, share, viewer_id)
        pass_headers["content-length"] = str(len(new_body.encode()))
        return Response(new_body, status_code=upstream.status_code,
                        headers=pass_headers)

    async def _cleanup():
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code,
        headers=pass_headers, background=BackgroundTask(_cleanup),
    )


@app.get("/video")
async def video_proxy(url: str, request: Request):
    """代理视频流（纯画面）；本地文件直接返回；m3u8 等直链不经 yt-dlp"""
    return await _proxy_media_route(url, request, _resolve_video_url)


@app.get("/audio")
async def audio_proxy(url: str, request: Request):
    """代理音频流（声音）；本地文件直接返回；m3u8 等直链不经 yt-dlp"""
    return await _proxy_media_route(url, request, _resolve_audio_url)


async def _proxy_media_route(url: str, request: Request, resolver):
    """Share page-resolution and upstream-error mapping for video/audio routes."""
    if os.path.exists(url):
        return FileResponse(url)
    kind, _ = classify(url)
    if kind == "page":
        try:
            url = await resolver(url)
        except Exception as exc:  # noqa: BLE001 - expose the failed stage, not resolver output
            logger.warning("平台页面解析失败: %s", _redact_urls_in_text(str(exc)))
            raise HTTPException(status_code=502, detail="平台页面解析失败：请检查链接、访问权限和解析器支持情况") from None
    try:
        return await _proxy_stream(request, url)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - proxy failures are distinct from parsing failures
        logger.warning("媒体拉流失败: %s", _redact_urls_in_text(str(exc)))
        raise HTTPException(status_code=502, detail="媒体拉流失败：请检查来源、网络和访问权限") from None


@app.get("/channels")
async def channels_proxy(url: str):
    """解析 m3u 频道列表（URL 或本地路径）→ [{group, name, url}]（ticket-19）。

    浏览器跨域不能直接读 m3u，统一走本地代理（补 UA/Referer）。"""
    import httpx

    try:
        if os.path.exists(url):
            with open(url, encoding="utf-8", errors="replace") as f:
                text = f.read()
        else:
            headers = {"Referer": "https://www.bilibili.com", "User-Agent": UA}
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, verify=False) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                text = resp.content.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 - distinguish fetch failures from malformed lists
        logger.warning("频道列表读取失败: %s", _redact_urls_in_text(str(exc)))
        raise HTTPException(
            status_code=502,
            detail="频道列表读取失败：请检查地址、网络和访问权限",
        ) from None
    try:
        return parse_m3u(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"频道列表解析失败：{exc}") from None


_PROBE_CACHE: dict = {}  # url -> (ts, {ok, latency_ms})，缓存 5 分钟


@app.get("/probe")
async def probe_stream(url: str):
    """频道连通性检测：拉播放列表（TTFB 计延迟）→ 分片前 1MB 测带宽。

    只测列表/首字节会误导（实测 TTFB 65ms 的源带宽仅 1MB/s 播放卡死）；
    分片下载速率才是"能不能流畅播"的关键（1080p IPTV 码率 ~1.1MB/s）。
    结果内存缓存 5 分钟，批量检测并发调用时命中秒回。"""
    import time

    now = time.time()
    hit = _PROBE_CACHE.get(url)
    if hit and now - hit[0] < 300:
        return hit[1]
    result = {"ok": False, "latency_ms": 0, "mbps": 0}
    headers = {"Referer": "https://www.bilibili.com", "User-Agent": UA}
    import httpx

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0),
                                     follow_redirects=True, verify=False) as client:
            t0 = time.time()
            async with client.stream("GET", url, headers=headers) as resp:
                head = b""
                async for chunk in resp.aiter_bytes():
                    head += chunk
                    if len(head) >= 8192:
                        break
                result["latency_ms"] = int((time.time() - t0) * 1000)
                text = head.decode("utf-8", "replace")
            if resp.status_code >= 400 or "#EXTM3U" not in text:
                return result  # 列表都拉不到 → 失效
            seg = next((ln.strip() for ln in text.splitlines()
                        if ln.strip() and not ln.strip().startswith("#")), None)
            if not seg:
                return result  # 无分片条目（变体列表等）→ 不可播
            seg_url = urljoin(url, seg)
            t1 = time.time()
            async with client.stream("GET", seg_url, headers=headers) as sresp:
                n = 0
                async for chunk in sresp.aiter_bytes():
                    n += len(chunk)
                    if n >= 1048576:  # 下 1MB 估算带宽
                        break
            dt = time.time() - t1
            result["mbps"] = round(n / dt / 1024 / 1024, 1) if dt > 0 else 0
            result["ok"] = sresp.status_code < 400 and n > 0
    except Exception as exc:  # noqa: BLE001 - 检测失败即频道失效
        logger.warning("probe 失败 %s: %s", _redact_url(url)[:120],
                       _redact_urls_in_text(str(exc)))
        result["ok"] = False
    _PROBE_CACHE[url] = (time.time(), dict(result))
    return result


@app.get("/")
def index():
    """返回单页（no-store 防浏览器缓存旧版 JS）"""
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


@app.get("/caption_presenter.js")
def caption_presenter_js():
    return FileResponse(os.path.join(STATIC_DIR, "caption_presenter.js"),
                        headers={"Cache-Control": "no-store"})


@app.get("/player.css")
def player_css():
    return FileResponse(os.path.join(STATIC_DIR, "player.css"),
                        headers={"Cache-Control": "no-store"})


@app.get("/playback_guard.js")
def playback_guard_js():
    return FileResponse(os.path.join(STATIC_DIR, "playback_guard.js"),
                        headers={"Cache-Control": "no-store"})


@app.get("/recovery_controller.js")
def recovery_controller_js():
    return FileResponse(os.path.join(STATIC_DIR, "recovery_controller.js"),
                        headers={"Cache-Control": "no-store"})


@app.get("/hls.min.js")
def hls_js():
    """hls.js（vendored 本地文件，ticket-19：Chrome 原生 HLS 不支持 TS 分片直播源）"""
    return FileResponse(os.path.join(STATIC_DIR, "hls.min.js"),
                        headers={"Cache-Control": "no-store"})


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """保存上传的音频/视频文件，返回可被 StreamSource 读取的绝对路径"""
    safe_name = os.path.basename(file.filename or "upload.bin")
    dst = os.path.join(UPLOAD_DIR, safe_name)
    with open(dst, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return {"path": os.path.abspath(dst), "name": safe_name}


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    if not _is_local_host(ws.client.host if ws.client else None):
        await ws.close(code=4403, reason="管理连接仅允许主机本机访问")
        return
    origin = ws.headers.get("origin")
    if origin and urlparse(origin).netloc != ws.url.netloc:
        await ws.close(code=4403, reason="请求来源不匹配")
        return
    await ws.accept()
    cloud_token = ws.query_params.get("session", "")
    cloud = None
    if cloud_token:
        with _CLOUD_LOCK:
            cloud = _CLOUD_JOBS.get(cloud_token)
            if cloud and not cloud["active"] and cloud["expires"] >= time.monotonic():
                cloud["active"] = True
                cloud["expires"] = float("inf")
            else:
                cloud = None
        if cloud is None:
            await ws.send_json({"type": "error", "message": "会话已过期或已使用，请重新开始"})
            await ws.close()
            return
    url = ws.query_params.get("url", "").strip()
    if cloud:
        url = cloud["url"]
    share_token = ws.query_params.get("share", "").strip()
    with _SHARE_LOCK:
        share = _SHARES.get(share_token) if share_token else None
    if share_token and (not share or not share.active or share.url != url):
        await ws.send_json({"type": "error", "message": "直播分享会话无效"})
        await ws.close()
        return
    engine_name = ws.query_params.get("engine", "faster-whisper").strip()
    if cloud:
        engine_name = "aliyun-funasr"
    original_mode = bool(cloud and cloud.get("mode") == "transcribe")
    silence = ws.query_params.get("silence", "400").strip()
    delay = ws.query_params.get("delay", "1").strip()
    try:
        delay_sec = max(0.1, min(float(delay), 30.0))
    except ValueError:
        delay_sec = 1.0
    if original_mode:
        delay_sec = 0.1
    if not url:
        await ws.send_json({"type": "error", "message": "缺少 url 参数"})
        await ws.close()
        return

    # Two reserved slots ensure overflow can be reported and followed by a
    # terminal event without evicting an already queued final caption.
    queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_WS_EVENT_BACKLOG + 2)
    overflowed = threading.Event()
    backlog_lock = threading.Lock()
    pending_event_count = 0
    loop = asyncio.get_running_loop()
    stop = share.stop_event if share else threading.Event()
    if cloud:
        if share:
            share.stop_event = cloud["stop"]
        stop = cloud["stop"]
    paused = threading.Event()  # 客户端暂停 → 后端停止推流解析，字幕冻结
    pause_lock = threading.Lock()
    pause_started_at = None
    paused_duration = 0.0

    def active_session_time() -> float:
        """Monotonic session time with the full user-pause interval excluded."""
        now = _session_monotonic()
        with pause_lock:
            excluded = paused_duration
            if pause_started_at is not None:
                excluded += max(0.0, now - pause_started_at)
        return now - excluded

    def put(item: dict) -> None:
        nonlocal pending_event_count
        def dispatch(queued_item: dict) -> None:
            queue.put_nowait(queued_item)
            if share:
                share.publish(queued_item)
        for name in ("message", "line"):
            if isinstance(item.get(name), str):
                item[name] = _redact_urls_in_text(item[name])
        if cloud:
            for name in ("message", "line"):
                if name in item:
                    for secret in (cloud["config"].api_key, cloud["config"].translation_key):
                        if secret:
                            item[name] = item[name].replace(secret, "[已隐藏]")
        with backlog_lock:
            if item.get("type") == "end":
                if overflowed.is_set():
                    item["reason"] = "error"
                pending_event_count += 1
                queued_item = item
            elif overflowed.is_set():
                return
            elif pending_event_count >= _MAX_WS_EVENT_BACKLOG:
                overflowed.set()
                stop.set()
                pending_event_count += 1
                queued_item = {
                    "type": "error",
                    "failure_category": "capacity",
                    "retryable": False,
                    "message": "字幕事件积压达到安全上限，结果不完整；请停止或重新开始识别",
                }
                logger.warning("会话事件队列达到安全上限，已停止识别")
            else:
                pending_event_count += 1
                queued_item = item
        try:
            loop.call_soon_threadsafe(dispatch, queued_item)
        except RuntimeError:
            with backlog_lock:
                pending_event_count -= 1
            stop.set()

    async def receive_loop() -> None:
        """接收客户端控制消息（pause/resume）"""
        nonlocal pause_started_at, paused_duration
        try:
            while True:
                msg = await ws.receive_text()
                if msg == "pause":
                    with pause_lock:
                        if pause_started_at is None:
                            pause_started_at = _session_monotonic()
                            paused.set()
                elif msg == "resume":
                    with pause_lock:
                        if pause_started_at is not None:
                            paused_duration += max(
                                0.0, _session_monotonic() - pause_started_at)
                            pause_started_at = None
                            paused.clear()
        except Exception:  # noqa: BLE001 - 连接关闭即结束
            pass
        finally:
            await asyncio.to_thread(stop.set)

    receiver = asyncio.create_task(receive_loop())

    def worker() -> None:
        import time

        timeline_offset = 0.0
        fed_sec = 0.0
        playback_ceiling = None
        pending_sentences = []
        caption_revisions = {}
        caption_final_texts = {}
        caption_final_variant_counts = {}
        caption_retained_texts = {}
        caption_draft_texts = {}
        withdrawn_caption_texts = {}
        retained_caption_fingerprint_count = 0
        translation = None
        translation_clauses = {}
        caption_seen = False
        terminal_reason = "error"
        normal_completion = False
        worker_failed = False
        provider_exception = None

        def call_provider(function, *args, **kwargs):
            nonlocal provider_exception
            try:
                return function(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - preserve provider fields, not its text
                if cloud:
                    provider_exception = exc
                raise
        last_progress_at = active_session_time()
        last_progress_through = timeline_offset

        def advance_progress(through) -> None:
            nonlocal last_progress_at, last_progress_through
            try:
                through = float(through)
            except (TypeError, ValueError):
                return
            if math.isfinite(through) and through > last_progress_through + 1e-6:
                last_progress_through = through
                last_progress_at = active_session_time()

        def fail_capacity() -> None:
            nonlocal phase
            phase = "capacity"
            raise SessionCapacityError("caption storage capacity exceeded")

        def retain_caption_variant(revision_key, fingerprint) -> None:
            nonlocal retained_caption_fingerprint_count
            if not fingerprint:
                return
            retained = (caption_retained_texts.get(revision_key, set())
                        | withdrawn_caption_texts.get(revision_key, set()))
            if fingerprint in retained:
                return
            if (retained_caption_fingerprint_count
                    >= _MAX_SESSION_CAPTION_RETENTION_FINGERPRINTS):
                fail_capacity()
            caption_retained_texts.setdefault(revision_key, set()).add(fingerprint)
            retained_caption_fingerprint_count += 1

        def retain_final_caption_variant(revision_key, fingerprint) -> None:
            if not fingerprint:
                return
            final_texts = caption_final_texts.setdefault(revision_key, set())
            if fingerprint not in final_texts:
                final_count = caption_final_variant_counts.get(revision_key, 0)
                if final_count >= _MAX_SESSION_CAPTION_VARIANTS:
                    fail_capacity()
                caption_final_variant_counts[revision_key] = final_count + 1
                final_texts.add(fingerprint)
            retain_caption_variant(revision_key, fingerprint)

        def emit(o) -> None:
            """推送整句累计文本与 ASR final 状态；旧式四元组引擎默认为 final。"""
            nonlocal caption_seen
            beg, end, text, words = o[:4]
            is_final = bool(o[4]) if len(o) > 4 else True
            sentence_id = o[5] if len(o) > 5 else None
            revision = None
            fingerprint = None
            if original_mode and sentence_id is not None:
                revision_key = str(sentence_id)
                normalized = " ".join(str(text or "").split())
                fingerprint = (hashlib.sha256(normalized.encode("utf-8")).digest()
                               if normalized else None)
                # ASR callbacks do not carry provider revisions. After an explicit
                # withdrawal, do not turn a retransmitted copy of that exact body
                # into a newer client revision; genuinely changed text can recover.
                if fingerprint and fingerprint in withdrawn_caption_texts.get(revision_key, set()):
                    return
                current_draft = None
                if sentence_id is not None:
                    if (revision_key not in caption_revisions
                            and len(caption_revisions) >= _MAX_SESSION_CAPTIONS):
                        fail_capacity()
                    current_draft = caption_draft_texts.get(revision_key)
                if is_final:
                    if current_draft:
                        retain_caption_variant(revision_key, current_draft)
                    if fingerprint:
                        retain_final_caption_variant(revision_key, fingerprint)
                if sentence_id is not None:
                    revision = caption_revisions.get(revision_key, -1) + 1
                    caption_revisions[revision_key] = revision
                    if is_final:
                        caption_draft_texts.pop(revision_key, None)
                    elif fingerprint:
                        # Coalesce uncommitted drafts; displayed stable previews and
                        # final bodies have their own retained-fingerprint paths.
                        caption_draft_texts[revision_key] = fingerprint
            if text or ((translation or original_mode) and is_final and sentence_id is not None):
                if translation:
                    key = sentence_id if sentence_id is not None else round(beg, 1)
                    if (key not in translation_clauses
                            and len(translation_clauses) >= _MAX_SESSION_CAPTIONS):
                        fail_capacity()
                    collector = translation_clauses.setdefault(key, TranslationClauses(beg))
                    for clause_beg, clause_end, clause_text, sequence in collector.observe(
                            text, words, is_final, end):
                        if not clause_text:
                            translation.withdraw(f"{key}:{sequence}")
                            continue
                        translation.submit({"type": "subtitle", "beg": clause_beg + timeline_offset,
                            "end": clause_end + timeline_offset if clause_end is not None else None,
                            "text": clause_text, "is_final": True,
                            "sentence_id": f"{key}:{sequence}"})
                    if is_final:
                        translation_clauses.pop(key, None)
                    return
                if original_mode and not text and is_final:
                    revision_key = str(sentence_id)
                    previous_texts = caption_retained_texts.pop(revision_key, set())
                    caption_final_texts.pop(revision_key, None)
                    if previous_texts:
                        withdrawn_caption_texts.setdefault(
                            revision_key, set()).update(previous_texts)
                    pending_sentences[:] = [
                        item for item in pending_sentences if item["id"] != sentence_id]
                    put({"type": "caption_remove", "sentence_id": sentence_id,
                         "revision": revision})
                    return
                # Use the API sentence id when available. Nearby sentence starts
                # can be less than a second apart and must not share one hold.
                match = next((i for i, item in enumerate(pending_sentences)
                              if sentence_id is not None and item["id"] == sentence_id), None)
                if match is None and sentence_id is None:
                    nearest = min(((abs(item["beg"] - beg), i)
                                   for i, item in enumerate(pending_sentences)
                                   if item["id"] is None), default=(float("inf"), None))
                    match = nearest[1] if nearest[0] < 0.75 else None
                if is_final:
                    caption_seen = True
                    if match is not None:
                        pending_sentences.pop(match)
                elif match is None:
                    if len(pending_sentences) >= _MAX_PENDING_CAPTIONS:
                        fail_capacity()
                    preview = StableCaptionPreview(beg)
                    preview.observe(text, words)
                    pending_sentences.append({"id": sentence_id, "beg": beg,
                                              "preview": preview})
                else:
                    item = pending_sentences[match]
                    item["beg"] = min(item["beg"], beg)
                    for clause_beg, clause_end, clause_text in item["preview"].observe(text, words):
                        clause_normalized = " ".join(str(clause_text or "").split())
                        clause_fingerprint = (
                            hashlib.sha256(clause_normalized.encode("utf-8")).digest()
                            if clause_normalized else None)
                        if (original_mode and sentence_id is not None
                                and clause_fingerprint):
                            revision_key = str(sentence_id)
                            if clause_fingerprint in withdrawn_caption_texts.get(
                                    revision_key, set()):
                                continue
                            # Retain the source body and displayed clause for the
                            # session-wide withdrawal budget, independent of drafts.
                            retain_caption_variant(revision_key, fingerprint)
                            retain_caption_variant(revision_key, clause_fingerprint)
                        caption_seen = True
                        put({"type": "preview", "beg": clause_beg + timeline_offset,
                             "end": clause_end + timeline_offset, "text": clause_text,
                             "words": [(word_beg + timeline_offset, word_end + timeline_offset, token)
                                       for word_beg, word_end, token in words or []
                                       if isinstance(word_beg, (int, float))
                                       and isinstance(word_end, (int, float))
                                       and word_beg >= clause_beg and word_end <= clause_end],
                             "sentence_id": sentence_id})
                shifted_words = None
                if words:
                    shifted_words = [
                        (word_beg + timeline_offset,
                         word_end + timeline_offset if word_end is not None else None,
                         word_text)
                        for word_beg, word_end, word_text in words
                    ]
                message = {"type": "subtitle", "beg": beg + timeline_offset,
                     "end": end + timeline_offset if end is not None else None,
                     "text": text, "words": shifted_words, "is_final": is_final,
                     "sentence_id": sentence_id}
                if revision is not None:
                    message["revision"] = revision
                put(message)
                if original_mode and str(text or "").strip():
                    progress_end = end
                    if progress_end is None and words:
                        progress_end = max((word[1] for word in words
                                            if len(word) > 1 and word[1] is not None),
                                           default=None)
                    if progress_end is not None:
                        advance_progress(float(progress_end) + timeline_offset)

        # 服务端日志（ticket-14）：连接即推环形缓冲快照，之后实时推送
        for line in list(_SERVER_LOGS)[-200:]:
            put({"type": "log", "line": line})
        _LOG_SINKS.append(put)
        logger.info("会话开始 url=%s engine=%s silence=%s",
                    _redact_url(url)[:120], engine_name, silence)

        engine = None
        phase = "input_validation"

        def check_no_progress() -> None:
            nonlocal phase
            # Live HTTP HLS has a 2s client-side degrade path and must keep
            # accepting audio so a late caption can recover coverage. Keep the
            # 15s preparation budget for local files and non-live media URLs.
            if (original_mode and (prefetch_local or not is_live_hls)
                    and active_session_time() - last_progress_at >= 15.0):
                phase = "no_progress"
                raise TimeoutError("recognition made no progress for 15 seconds")

        try:
            kind, target = classify(url)
            phase = "platform_resolution" if kind == "page" else "media_open"
            real_url = resolve_stream_url(url)
            if stop.is_set():
                return
            http_headers = None
            if kind == "page":
                p = urlparse(target)
                http_headers = {"Referer": f"{p.scheme}://{p.netloc}"}
            engine_kwargs = {}
            if engine_name in ("aliyun-funasr", "aliyun", "api"):
                engine_kwargs["stop_event"] = stop
                # 断句灵敏度仅 API 引擎支持（本地引擎无该参数）
                try:
                    engine_kwargs["max_sentence_silence"] = int(silence)
                except ValueError:
                    engine_kwargs["max_sentence_silence"] = 400
                if cloud:
                    engine_kwargs.update(api_key=cloud["config"].api_key,
                                         language_hints=[] if cloud["source"] == "auto" else [cloud["source"]])
            if urlparse(real_url).path.lower().endswith(".m3u8"):
                put({"type": "status", "message": f"正在检查直播源: {_redact_url(real_url)[:100]}"})
            source_kwargs = {"http_headers": http_headers}
            if cloud and cloud["start_at"]:
                source_kwargs["start_sec"] = cloud["start_at"]
            phase = "media_open"
            src = StreamSource(real_url, **source_kwargs)
            timeline_offset = getattr(src, "timeline_offset_sec", 0.0)
            source_reader = None
            try:
                parsed_real_url = urlparse(real_url)
                is_hls = parsed_real_url.path.lower().endswith(".m3u8")
                is_live_hls = (parsed_real_url.scheme.lower() in {"http", "https"}
                               and is_hls)
                if hasattr(src, "container"):
                    video_streams = src.container.streams.video
                    if is_hls and not video_streams:
                        raise ValueError("直播源没有视频轨，已停止识别以避免黑屏时继续调用 API")
                    if video_streams and video_streams[0].codec_context.name == "mpeg2video":
                        raise ValueError("媒体使用 MPEG-2 视频编码，浏览器无法播放；已停止识别。请换用浏览器支持的来源")
                if stop.is_set():
                    return

                phase = "media_preflight"
                chunks = iter(src.iter_chunks())
                try:
                    first_chunk = next(chunks)
                except StopIteration:
                    phase = "media_empty"
                    raise ValueError("输入没有可解码的音频内容") from None
                if not first_chunk:
                    phase = "media_empty"
                    raise ValueError("输入没有可解码的音频内容")
                if stop.is_set():
                    return
                phase = "recognition"
                engine = call_provider(create_engine, engine_name, **engine_kwargs)
                last_progress_at = active_session_time()
                last_progress_through = timeline_offset
                if cloud and not original_mode:
                    translation = TranslationSession(QwenTranslator(cloud["config"],
                        cloud["source"], cloud["target"]), put, stop)
                batch_mode = getattr(engine, "batch_mode", False)
                prefetch_local = original_mode and os.path.isfile(real_url)
                if batch_mode and not os.path.isfile(real_url):
                    raise ValueError("FunASR Vulkan（文件模式）只支持本地媒体文件；直播流请使用实时引擎")
                put({"type": "status", "message": f"引擎就绪，开始拉流: {_redact_url(real_url)[:100]}"})
                logger.info("引擎就绪 %s，拉流 %s", engine_name, _redact_url(real_url)[:120])
                # 音频持续送入 ASR；视频元素同时承载 HLS 音轨，并保持在已送入音频的
                # delay 秒之后播放。这样延迟覆盖整个画面和声音，而非只延迟字幕。
                ready_sent = False
                if cloud and not original_mode:
                    put({"type": "ready", "delay": 0, "start_at": timeline_offset,
                         "progressive": True})
                    ready_sent = True
                clock_sent = False
                silence_run = 0.0
                feed_clock = active_session_time()
                playback_ceiling = timeline_offset

                def publish_buffer():
                    nonlocal playback_ceiling, ready_sent
                    if not batch_mode and (not cloud or original_mode):
                        # Keep the configured live delay as a floor. If ASR has an
                        # unfinished sentence, hold before it; when final arrives,
                        # resume at real time without jumping over buffered A/V.
                        feed_target = timeline_offset + max(0.0, fed_sec - delay_sec)
                        pending_target = min(
                            (timeline_offset + item["preview"].emitted_end
                             for item in pending_sentences),
                            default=float("inf"),
                        )
                        playback_ceiling = max(playback_ceiling, min(feed_target, pending_target))
                        put({"type": "buffer", "play_at": playback_ceiling,
                             "local_silence_from": timeline_offset + fed_sec - silence_run
                             if original_mode and silence_run >= 1.5 else None,
                             "local_silence_through": timeline_offset + fed_sec
                             if original_mode and silence_run >= 1.5 else None})
                        if not ready_sent and (not original_mode or caption_seen or silence_run >= 1.5) and fed_sec >= delay_sec \
                                and playback_ceiling >= timeline_offset + 0.25:
                            put({"type": "ready", "delay": delay_sec,
                                 "start_at": timeline_offset, "original": original_mode,
                                 "buffered": True, "silent": not caption_seen})
                            ready_sent = True

                def decoded_chunks():
                    nonlocal phase
                    exhausted = object()
                    while True:
                        phase = "media_decode"
                        chunk = next(chunks, exhausted)
                        if chunk is exhausted:
                            return
                        yield chunk

                def polled_chunks():
                    nonlocal phase
                    yield first_chunk
                    exhausted = object()
                    while not stop.is_set():
                        # At most one read is pending; SDK calls stay on this worker.
                        pending = source_reader.submit(next, chunks, exhausted)
                        while not pending.done() and not stop.is_set():
                            phase = "recognition"
                            result = call_provider(engine.poll)
                            if result[0] is not None:
                                emit(result)
                                publish_buffer()
                            check_no_progress()
                            stop.wait(0.02)
                        if stop.is_set():
                            return
                        phase = "media_decode"
                        chunk = pending.result()
                        if chunk is exhausted:
                            return
                        yield chunk

                if original_mode and is_live_hls:
                    source_reader = ThreadPoolExecutor(max_workers=1, thread_name_prefix="audio-source")
                    streaming_chunks = polled_chunks()
                else:
                    streaming_chunks = chain((first_chunk,), decoded_chunks())
                for chunk in streaming_chunks:
                    phase = "recognition"
                    if stop.is_set():
                        break
                    check_no_progress()
                    if not clock_sent and urlparse(real_url).path.lower().endswith('.m3u8'):
                        first_pts = getattr(src, 'first_pts_sec', None)
                        if first_pts is not None:
                            timeline_offset = 0.0
                            last_progress_through = timeline_offset
                            playback_ceiling = 0.0
                            put({'type': 'timeline', 'audio_pts': first_pts})
                        clock_sent = True
                    # 客户端暂停：停止推流解析（字幕冻结，省 API 调用）
                    while paused.is_set():
                        if stop.is_set():
                            break
                        _session_wait(stop, 0.2)
                    if stop.is_set():
                        break
                    # Pace by the actual PCM duration (including a short tail).
                    chunk_sec = len(chunk) / (16000 * 2)
                    if not batch_mode:
                        # Keep local files at most two seconds ahead so playback
                        # can start before EOF. Bound buffered HLS too: an unpaced
                        # segment burst can overflow the ASR input queue at startup.
                        # One cumulative deadline lets delayed reads catch up without
                        # turning source or recognizer time into permanent feed lag.
                        # Stable live phrases need headroom while staying below
                        # the existing ten-second recognizer input queue.
                        lead_sec = 6.0 if original_mode and is_live_hls else 2.0 if prefetch_local else 0.0
                        wait = fed_sec + chunk_sec - lead_sec - (active_session_time() - feed_clock)
                        if wait > 0:
                            if cloud and _session_wait(stop, wait):
                                break
                            if not cloud:
                                time.sleep(wait)
                    check_no_progress()
                    audio = s16_to_f32(chunk)
                    silence_run = silence_run + chunk_sec if len(audio) and max(abs(audio)) < 0.005 else 0.0
                    o = call_provider(engine.transcribe_chunk, audio)
                    if not batch_mode:
                        fed_sec += chunk_sec
                    if o[0] is not None:
                        emit(o)
                    # 异步引擎（API）积压的句子逐条取出（增量，含 partial）
                    while not stop.is_set():
                        o2 = call_provider(engine.poll)
                        if o2[0] is None:
                            break
                        emit(o2)
                    if original_mode and silence_run >= 1.5:
                        advance_progress(timeline_offset + fed_sec)
                    check_no_progress()
                    publish_buffer()
                if stop.is_set():
                    return
                phase = "recognition"
                finalize = getattr(engine, "finalize", None)
                if callable(finalize):
                    # SDK shutdown waits for callbacks; deliver them while it waits.
                    with ThreadPoolExecutor(max_workers=1) as finalizer:
                        completion = finalizer.submit(call_provider, finalize)
                        while not completion.done() and not stop.is_set():
                            result = call_provider(engine.poll)
                            if result[0] is not None:
                                emit(result)
                                publish_buffer()
                            else:
                                _session_wait(stop, 0.02)
                        completion.result()
                if stop.is_set():
                    return
                o = call_provider(engine.finish)
                if stop.is_set():
                    return
                if o[0] is not None:
                    emit(o)
                    publish_buffer()
                # finish 后仍可能有剩余句子（异步引擎），poll 取尽
                while True:
                    o2 = call_provider(engine.poll)
                    if o2[0] is None:
                        break
                    emit(o2)
                    publish_buffer()
                if translation:
                    translation.finish()
                normal_completion = True
                # Batch engines finish the whole file before the player starts.
                # Send ready only after subtitles have reached the browser.
                if not ready_sent:
                    put({"type": "ready", "delay": delay_sec, "start_at": timeline_offset,
                         "original": original_mode, "buffered": not bool(translation),
                         "prepared": prefetch_local})
                    ready_sent = True
            finally:
                if source_reader is None:
                    src.close()
                else:
                    try:
                        interrupt = getattr(src, "interrupt", None)
                        if callable(interrupt):
                            interrupt()
                    finally:
                        # PyAV decode and container.close must never run concurrently.
                        closing = source_reader.submit(src.close)
                        try:
                            closing.result()
                        finally:
                            source_reader.shutdown(wait=True)
        except Exception as exc:  # noqa: BLE001 - 用户可见错误信息
            if stop.is_set():
                return
            worker_failed = True
            terminal_reason = "error"
            local_capacity_error = isinstance(exc, RecognitionCapacityError)
            if local_capacity_error:
                phase = "capacity"
            audio_timeline_error = isinstance(exc, AudioTimelineError)
            if audio_timeline_error:
                stop.set()
            provider_details = (classify_provider_failure(exc)
                                if provider_exception is exc and not local_capacity_error else None)
            if audio_timeline_error:
                message = "音频时间轴存在过大间隙或跳变，已停止识别；可更换来源或仅播放"
            elif phase == "input_validation":
                message = "输入地址无法识别：请提供有效 URL 或本机已存在的文件路径"
            elif phase == "platform_resolution":
                message = "平台页面解析失败：请检查链接、访问权限和解析器支持情况"
            elif phase == "media_open":
                detail = str(exc)
                if "没有音频流" in detail:
                    message = "输入中没有音频轨，未启动识别"
                elif "MPEG-2" in detail:
                    message = detail
                elif "没有视频轨" in detail:
                    message = detail
                else:
                    message = "无法打开媒体流：请检查来源、网络和访问权限；未启动识别"
            elif phase == "media_preflight":
                message = "输入音频解码失败，未启动识别"
            elif phase == "media_empty":
                message = "输入没有可解码的音频内容，未启动识别"
            elif phase == "media_decode":
                message = "媒体拉流或音频解码失败，已停止后续识别"
            elif phase == "no_progress":
                message = "识别连续 15 秒没有字幕或静音处理进展，已停止新音频处理；可重试、仅播放或停止"
            elif phase == "capacity":
                message = ("识别达到安全容量上限，结果不完整；已停止识别，请重新开始"
                           if local_capacity_error else
                           "字幕记录达到安全容量上限，结果不完整；已停止识别，请停止或重新开始")
            elif provider_details is not None:
                message = provider_details.pop("message")
                logger.warning("provider failure category=%s status=%s code=%s",
                               provider_details["failure_category"],
                               provider_details["provider_status"],
                               provider_details["provider_code"])
            elif cloud and not isinstance(exc, CloudError):
                message = "语音识别服务异常：请检查 Key、模型权限和网络后重试"
            else:
                message = _redact_urls_in_text(str(exc))[:300]
            logger.warning("worker 异常: %s", message)
            error_event = {"type": "error", "message": message}
            if provider_details is not None:
                error_event.update(provider_details)
            if audio_timeline_error:
                error_event.update(failure_category="media_timeline", retryable=False)
            elif phase == "no_progress":
                error_event.update(failure_category="no_progress", retryable=False)
            elif phase == "capacity":
                error_event.update(failure_category="capacity", retryable=False)
            elif phase in ("media_open", "media_preflight", "media_decode"):
                reason = exc.reason if isinstance(exc, URLError) else exc
                retryable = isinstance(reason, (TimeoutError, ConnectionError, socket.gaierror))
                if isinstance(exc, HTTPError):
                    retryable = exc.code in (408, 429) or 500 <= exc.code <= 599
                error_event.update(failure_category="media_network" if retryable else "media_decode",
                                   retryable=retryable)
            put(error_event)
        finally:
            try:
                if translation:
                    try:
                        translation.close()
                    except Exception:  # noqa: BLE001 - release worker slot after cleanup failure
                        logger.warning("翻译器关闭失败")
                if engine is not None and callable(getattr(engine, "close", None)):
                    try:
                        engine.close()
                    except Exception:  # noqa: BLE001 - 断开连接时仍需结束会话
                        worker_failed = True
                        put({"type": "error", "failure_category": "recognition_stop",
                             "retryable": False, "message": "识别任务未能正常关闭，请停止后重试"})
                        logger.warning("引擎关闭失败")
                if put in _LOG_SINKS:
                    _LOG_SINKS.remove(put)
                logger.info("会话结束")
                if worker_failed:
                    terminal_reason = "error"
                elif overflowed.is_set():
                    terminal_reason = "error"
                elif stop.is_set():
                    terminal_reason = "cancelled"
                elif normal_completion:
                    terminal_reason = "completed"
                put({"type": "end", "reason": terminal_reason})
            finally:
                # Keep the slot until the actual worker has released its source,
                # ASR and event sink, even if the owner WebSocket already closed.
                _ACTIVE_RECOGNITION_WORKERS.release()

    if not _ACTIVE_RECOGNITION_WORKERS.acquire(blocking=False):
        message = "后台识别任务达到安全上限，已有任务仍在关闭；请稍后重试"
        logger.warning("后台识别任务达到安全上限，拒绝新任务")
        if put in _LOG_SINKS:
            _LOG_SINKS.remove(put)
        put({"type": "error", "failure_category": "capacity", "retryable": False,
             "message": message})
        put({"type": "end", "reason": "error"})
    else:
        try:
            threading.Thread(target=worker, daemon=True).start()
        except RuntimeError:
            _ACTIVE_RECOGNITION_WORKERS.release()
            if put in _LOG_SINKS:
                _LOG_SINKS.remove(put)
            put({"type": "error", "failure_category": "capacity", "retryable": False,
                 "message": "无法创建后台识别任务，请稍后重试"})
            put({"type": "end", "reason": "error"})

    try:
        while True:
            item = await queue.get()
            with backlog_lock:
                pending_event_count -= 1
            if item["type"] == "end" and share:
                # Invalidate media access before acknowledging the end to the host.
                share.active = False
                with _SHARE_LOCK:
                    _SHARES.pop(share.token, None)
            await ws.send_json(item)
            if item["type"] == "end":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await asyncio.to_thread(stop.set)
        receiver.cancel()
        await asyncio.gather(receiver, return_exceptions=True)
        if cloud_token:
            with _CLOUD_LOCK:
                _CLOUD_JOBS.pop(cloud_token, None)
        if share:
            await asyncio.to_thread(share.stop)
            with _SHARE_LOCK:
                _SHARES.pop(share.token, None)


@app.websocket("/share/{token}/ws")
async def share_ws_endpoint(ws: WebSocket, token: str):
    if not _is_lan_host(ws.client.host if ws.client else None):
        await ws.close(code=4403, reason="分享链接仅允许局域网设备访问")
        return
    try:
        share = _active_share(token)
    except HTTPException:
        await ws.close(code=4404, reason="分享链接已失效")
        return
    viewer_id = ws.query_params.get("viewer_id", "")
    client_host = ws.client.host if ws.client else ""
    await ws.accept()
    joined = share.subscribe(viewer_id, client_host)
    if joined is None:
        await ws.send_json({"type": "error", "message": "观众链接无效或已绑定到其他设备"})
        await ws.close(code=4403, reason="观众凭证无效")
        return
    queue, snapshot = joined

    async def wait_for_disconnect():
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                return

    disconnect_task = asyncio.create_task(wait_for_disconnect())
    event_task = asyncio.create_task(queue.get())
    try:
        for event in snapshot:
            if disconnect_task.done():
                break
            await ws.send_json(event)
        while True:
            done, _pending = await asyncio.wait(
                (disconnect_task, event_task), timeout=15,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if disconnect_task in done:
                disconnect_task.result()
                break
            if event_task not in done:
                await ws.send_json({"type": "heartbeat"})
                continue
            event = event_task.result()
            event_task = asyncio.create_task(queue.get())
            # Status and logs can contain source URLs; guests only need playback data.
            if event.get("type") in ("subtitle", "caption_remove", "preview", "timeline", "ready",
                                      "buffer", "error", "end"):
                if event.get("type") == "error":
                    category = event.get("failure_category")
                    if category in ("capacity", "share_history_truncated"):
                        await ws.send_json({"type": "error", "failure_category": category,
                                            "retryable": False,
                                            "message": event.get("message", "分享记录不完整")})
                    else:
                        await ws.send_json({"type": "error", "message": "主播端直播处理发生错误"})
                else:
                    await ws.send_json(event)
            if event.get("type") == "end":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        for task in (disconnect_task, event_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(disconnect_task, event_task, return_exceptions=True)
        share.unsubscribe(viewer_id)


def main():
    # Directly launching the venv Python does not activate Scripts on Windows.
    os.environ["PATH"] = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
    # 服务端日志捕获（ticket-14）：webui INFO + 库 WARNING 写入环形缓冲
    handler = _ServerLogHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    # 噪音源压到 WARNING：uvicorn 访问日志 / HTTP 客户端 / dashscope SDK
    for noisy in ("uvicorn.access", "uvicorn.error", "httpx", "dashscope"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # 从用户 ~/Projects/.env 读取 DashScope key（如存在且未设置），不硬编码
    if not os.environ.get("DASHSCOPE_API_KEY"):
        env_path = os.path.expanduser("~/Projects/.env")
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    if line.startswith("dashscopetoken="):
                        os.environ["DASHSCOPE_API_KEY"] = line.strip().split("=", 1)[1].strip()
                        break

    ap = argparse.ArgumentParser(description="实时字幕 webUI")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8002)
    args = ap.parse_args()
    print(f"实时字幕 webUI: http://{args.host}:{args.port}")
    # log_config=None：禁用 uvicorn 默认日志配置，保留我们挂的环形缓冲 handler
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                log_config=None)


if __name__ == "__main__":
    main()
