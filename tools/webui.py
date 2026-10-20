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
import ipaddress
import json
import logging
import os
import socket
import shutil
import sys
import threading
import warnings
from collections import deque
from urllib.parse import urljoin, urlparse

# 代理 verify=False 的告警压掉（ticket-17）
warnings.filterwarnings("ignore", message=".*InsecureRequestWarning.*")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whisper_streaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, File, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse  # noqa: E402
from starlette.background import BackgroundTask  # noqa: E402
import uvicorn  # noqa: E402

from engines import create_engine  # noqa: E402
from caption_preview import StableCaptionPreview  # noqa: E402
from input_resolver import classify, resolve_stream_url  # noqa: E402
from platform_resolver import resolve_platform_url  # noqa: E402
from stream_source import DEFAULT_UA, StreamSource, s16_to_f32  # noqa: E402
from live_share import LiveShare, MAX_VIEWERS  # noqa: E402

app = FastAPI(title="实时字幕")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_static")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

UA = DEFAULT_UA

# 服务端后台日志（ticket-14）：环形缓冲，经 WS 推给前端日志面板
_SERVER_LOGS: deque = deque(maxlen=500)
_LOG_SINKS: list = []  # 每连接的 put() 回调，实时推送新日志行
logger = logging.getLogger("webui")  # 会话生命周期 INFO 日志（用户可见）
_SHARES: dict[str, LiveShare] = {}
_SHARE_LOCK = threading.RLock()


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
    viewer_route = path == "/hls.min.js" or path.startswith("/share/")
    client_host = request.client.host if request.client else None
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
    addresses = _lan_ipv4_candidates()
    if not addresses:
        with _SHARE_LOCK:
            _SHARES.pop(share.token, None)
        raise HTTPException(status_code=500, detail="无法确定本机局域网 IPv4 地址")
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
        share.stop()
        _SHARES.pop(token, None)
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
            raise HTTPException(status_code=502, detail=f"直播源解析失败: {str(exc)[:180]}") from exc
    if os.path.isfile(url):
        return FileResponse(url)
    return await _proxy_stream(request, url, share=share, viewer_id=viewer_id)


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
        logger.warning("代理请求失败 %s: %s", stream_url[:80], exc)
        await client.aclose()
        raise
    if upstream.status_code >= 400:
        logger.warning("代理上游非 2xx %s -> HTTP %s（%s）",
                       stream_url[:80], upstream.status_code, request.url.path)
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
    if os.path.exists(url):
        return FileResponse(url)
    kind, _ = classify(url)
    if kind == "page":
        url = await _resolve_video_url(url)
    return await _proxy_stream(request, url)


@app.get("/audio")
async def audio_proxy(url: str, request: Request):
    """代理音频流（声音）；本地文件直接返回；m3u8 等直链不经 yt-dlp"""
    if os.path.exists(url):
        return FileResponse(url)
    kind, _ = classify(url)
    if kind == "page":
        url = await _resolve_audio_url(url)
    return await _proxy_stream(request, url)


@app.get("/channels")
async def channels_proxy(url: str):
    """解析 m3u 频道列表（URL 或本地路径）→ [{group, name, url}]（ticket-19）。

    浏览器跨域不能直接读 m3u，统一走本地代理（补 UA/Referer）。"""
    import httpx

    if os.path.exists(url):
        with open(url, encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        headers = {"Referer": "https://www.bilibili.com", "User-Agent": UA}
        async with httpx.AsyncClient(timeout=20, follow_redirects=True, verify=False) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            text = resp.content.decode("utf-8", "replace")
    try:
        return parse_m3u(text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
        logger.warning("probe 失败 %s: %s", url[:60], exc)
        result["ok"] = False
    _PROBE_CACHE[url] = (time.time(), dict(result))
    return result


@app.get("/")
def index():
    """返回单页（no-store 防浏览器缓存旧版 JS）"""
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


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
    await ws.accept()
    url = ws.query_params.get("url", "").strip()
    share_token = ws.query_params.get("share", "").strip()
    with _SHARE_LOCK:
        share = _SHARES.get(share_token) if share_token else None
    if share_token and (not share or not share.active or share.url != url):
        await ws.send_json({"type": "error", "message": "直播分享会话无效"})
        await ws.close()
        return
    engine_name = ws.query_params.get("engine", "faster-whisper").strip()
    silence = ws.query_params.get("silence", "400").strip()
    delay = ws.query_params.get("delay", "1").strip()
    try:
        delay_sec = max(0.1, min(float(delay), 30.0))
    except ValueError:
        delay_sec = 1.0
    if not url:
        await ws.send_json({"type": "error", "message": "缺少 url 参数"})
        await ws.close()
        return

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop = share.stop_event if share else threading.Event()
    paused = threading.Event()  # 客户端暂停 → 后端停止推流解析，字幕冻结

    def put(item: dict) -> None:
        def dispatch() -> None:
            queue.put_nowait(item)
            if share:
                share.publish(item)
        loop.call_soon_threadsafe(dispatch)

    async def receive_loop() -> None:
        """接收客户端控制消息（pause/resume）"""
        try:
            while True:
                msg = await ws.receive_text()
                if msg == "pause":
                    paused.set()
                elif msg == "resume":
                    paused.clear()
        except Exception:  # noqa: BLE001 - 连接关闭即结束
            pass

    asyncio.create_task(receive_loop())

    def worker() -> None:
        import time

        timeline_offset = 0.0
        fed_sec = 0.0
        playback_ceiling = None
        pending_sentences = []

        def emit(o) -> None:
            """推送整句累计文本与 ASR final 状态；旧式四元组引擎默认为 final。"""
            beg, end, text, words = o[:4]
            is_final = bool(o[4]) if len(o) > 4 else True
            sentence_id = o[5] if len(o) > 5 else None
            if text:
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
                    if match is not None:
                        pending_sentences.pop(match)
                elif match is None:
                    preview = StableCaptionPreview(beg)
                    preview.observe(text, words)
                    pending_sentences.append({"id": sentence_id, "beg": beg,
                                              "preview": preview})
                else:
                    item = pending_sentences[match]
                    item["beg"] = min(item["beg"], beg)
                    for clause_beg, clause_end, clause_text in item["preview"].observe(text, words):
                        put({"type": "preview", "beg": clause_beg + timeline_offset,
                             "end": clause_end + timeline_offset, "text": clause_text,
                             "sentence_id": sentence_id})
                shifted_words = None
                if words:
                    shifted_words = [
                        (word_beg + timeline_offset,
                         word_end + timeline_offset if word_end is not None else None,
                         word_text)
                        for word_beg, word_end, word_text in words
                    ]
                put({"type": "subtitle", "beg": beg + timeline_offset,
                     "end": end + timeline_offset if end is not None else None,
                     "text": text, "words": shifted_words, "is_final": is_final,
                     "sentence_id": sentence_id})

        # 服务端日志（ticket-14）：连接即推环形缓冲快照，之后实时推送
        for line in list(_SERVER_LOGS)[-200:]:
            put({"type": "log", "line": line})
        _LOG_SINKS.append(put)
        logger.info("会话开始 url=%s engine=%s silence=%s",
                    url[:80], engine_name, silence)

        engine = None
        try:
            real_url = resolve_stream_url(url)
            kind, target = classify(url)
            http_headers = None
            if kind == "page":
                p = urlparse(target)
                http_headers = {"Referer": f"{p.scheme}://{p.netloc}"}
            engine_kwargs = {}
            if engine_name in ("aliyun-funasr", "aliyun", "api"):
                # 断句灵敏度仅 API 引擎支持（本地引擎无该参数）
                try:
                    engine_kwargs["max_sentence_silence"] = int(silence)
                except ValueError:
                    engine_kwargs["max_sentence_silence"] = 400
            if urlparse(real_url).path.lower().endswith(".m3u8"):
                put({"type": "status", "message": f"正在检查直播源: {real_url[:60]}"})
            src = StreamSource(real_url, http_headers=http_headers)
            timeline_offset = getattr(src, "timeline_offset_sec", 0.0)
            try:
                if urlparse(real_url).path.lower().endswith(".m3u8") and hasattr(src, "container"):
                    video_streams = src.container.streams.video
                    if not video_streams:
                        raise ValueError("直播源没有视频轨，已停止识别以避免黑屏时继续调用 API")
                    codec = video_streams[0].codec_context.name
                    if codec == "mpeg2video":
                        raise ValueError("直播源使用 MPEG-2 视频编码，浏览器无法播放；已停止识别。请换 H.264 频道")
                engine = create_engine(engine_name, **engine_kwargs)
                batch_mode = getattr(engine, "batch_mode", False)
                if batch_mode and not os.path.isfile(real_url):
                    raise ValueError("FunASR Vulkan（文件模式）只支持本地媒体文件；直播流请使用实时引擎")
                put({"type": "status", "message": f"引擎就绪，开始拉流: {real_url[:60]}"})
                logger.info("引擎就绪 %s，拉流 %s", engine_name, real_url[:80])
                # 音频持续送入 ASR；视频元素同时承载 HLS 音轨，并保持在已送入音频的
                # delay 秒之后播放。这样延迟覆盖整个画面和声音，而非只延迟字幕。
                ready_sent = False
                clock_sent = False
                last = time.monotonic()
                playback_ceiling = timeline_offset
                for chunk in src.iter_chunks():
                    if stop.is_set():
                        break
                    if not clock_sent and urlparse(real_url).path.lower().endswith('.m3u8'):
                        first_pts = getattr(src, 'first_pts_sec', None)
                        if first_pts is not None:
                            timeline_offset = 0.0
                            playback_ceiling = 0.0
                            put({'type': 'timeline', 'audio_pts': first_pts})
                        clock_sent = True
                    # 客户端暂停：停止推流解析（字幕冻结，省 API 调用）
                    while paused.is_set():
                        if stop.is_set():
                            break
                        time.sleep(0.2)
                    if stop.is_set():
                        break
                    # Pace by the actual PCM duration (including a short tail).
                    chunk_sec = len(chunk) / (16000 * 2)
                    if not batch_mode:
                        wait = chunk_sec - (time.monotonic() - last)
                        if wait > 0:
                            time.sleep(wait)
                        last = time.monotonic()
                    o = engine.transcribe_chunk(s16_to_f32(chunk))
                    if not batch_mode:
                        fed_sec += chunk_sec
                    if o[0] is not None:
                        emit(o)
                    # 异步引擎（API）积压的句子逐条取出（增量，含 partial）
                    while True:
                        o2 = engine.poll()
                        if o2[0] is None:
                            break
                        emit(o2)
                    if not batch_mode:
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
                        put({"type": "buffer", "play_at": playback_ceiling})
                        if not ready_sent and fed_sec >= delay_sec \
                                and playback_ceiling >= timeline_offset + 0.25:
                            put({"type": "ready", "delay": delay_sec,
                                 "start_at": timeline_offset})
                            ready_sent = True
                o = engine.finish()
                if o[0] is not None:
                    emit(o)
                # finish 后仍可能有剩余句子（异步引擎），poll 取尽
                while True:
                    o2 = engine.poll()
                    if o2[0] is None:
                        break
                    emit(o2)
                # Batch engines finish the whole file before the player starts.
                # Send ready only after subtitles have reached the browser.
                if not ready_sent:
                    put({"type": "ready", "delay": delay_sec, "start_at": timeline_offset})
                    ready_sent = True
            finally:
                src.close()
        except Exception as exc:  # noqa: BLE001 - 用户可见错误信息
            logger.warning("worker 异常: %s", exc)
            put({"type": "error", "message": str(exc)[:300]})
        finally:
            if engine is not None and callable(getattr(engine, "close", None)):
                try:
                    engine.close()
                except Exception as exc:  # noqa: BLE001 - 断开连接时仍需结束会话
                    logger.warning("引擎关闭失败: %s", exc)
            if put in _LOG_SINKS:
                _LOG_SINKS.remove(put)
            logger.info("会话结束")
            put({"type": "end"})

    threading.Thread(target=worker, daemon=True).start()

    try:
        while True:
            item = await queue.get()
            await ws.send_json(item)
            if item["type"] == "end":
                break
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        stop.set()
        if share:
            share.stop()
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
            if event.get("type") in ("subtitle", "preview", "timeline", "ready",
                                      "buffer", "error", "end"):
                if event.get("type") == "error":
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
