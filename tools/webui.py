"""ticket-06: webUI 单页 MVP

单页：输入流地址/文件路径 → 引擎选择 → 大字号实时字幕（WebSocket 推送）。

用法:
    python webui.py [--host 127.0.0.1] [--port 8002]
    # SSH 远程访问: ssh -L 8002:localhost:8002 user@host 后浏览器开 localhost:8002

注意: 本地引擎需要 CUDA 运行库在 LD_LIBRARY_PATH（建议经 run_subs.sh 的环境启动）。
"""
import argparse
import asyncio
import json
import logging
import os
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
from stream_source import DEFAULT_UA, StreamSource, s16_to_f32  # noqa: E402

app = FastAPI(title="实时字幕")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_static")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

UA = DEFAULT_UA

# 服务端后台日志（ticket-14）：环形缓冲，经 WS 推给前端日志面板
_SERVER_LOGS: deque = deque(maxlen=500)
_LOG_SINKS: list = []  # 每连接的 put() 回调，实时推送新日志行
logger = logging.getLogger("webui")  # 会话生命周期 INFO 日志（用户可见）


class _ServerLogHandler(logging.Handler):
    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:  # noqa: BLE001 - 格式化失败不阻断日志
            return
        _SERVER_LOGS.append(line)
        for sink in list(_LOG_SINKS):
            sink({"type": "log", "line": line})

# yt-dlp 解析缓存（5 分钟）：播放器会发多个 range 请求，避免每次重复解析
_CACHE: dict = {"ts": 0.0, "url": "", "video": "", "audio": ""}


def _resolve_stream_url(user_input: str, fmt: str, key: str) -> str:
    """解析流 URL（视频 bv* / 音频 ba），带 5 分钟缓存"""
    import subprocess
    import time

    now = time.time()
    if _CACHE["url"] == user_input and now - _CACHE["ts"] < 300:
        if _CACHE.get(key):
            return _CACHE[key]
    # URL 变更时清空旧解析结果，防止切换链接后残留上一视频的 video/audio URL
    if _CACHE["url"] != user_input:
        _CACHE.update(video="", audio="")
    for attempt in range(2):
        proc = subprocess.run(
            ["yt-dlp", "-g", "--no-warnings", "-f", fmt, user_input],
            capture_output=True, text=True, timeout=120,
        )
        lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
        if proc.returncode == 0 and lines:
            _CACHE.update(ts=now, url=user_input)
            _CACHE[key] = lines[0]
            return lines[0]
    raise ValueError(f"流解析失败({fmt}): {proc.stderr.strip()[:200]}")


def _resolve_video_url(user_input: str) -> str:
    return _resolve_stream_url(user_input, "bv*", "video")


def _resolve_audio_url(user_input: str) -> str:
    return _resolve_stream_url(user_input, "ba", "audio")


def _proxy_media_url(target: str) -> str:
    """媒体 URL → 本地代理 URL（浏览器只与本地通信，代理统一补 UA/Referer）。

    分片/密钥/变体直连上游会被 UA/防盗链检查挡（ticket-19 实测 err=4），
    全部改走 /video?url= 代理；data: 等不可代理的保留原样。"""
    from urllib.parse import quote

    return "/video?url=" + quote(target, safe="")


def _rewrite_hls_playlist(text: str, stream_url: str) -> str:
    """HLS playlist 改写为本地代理 URL。

    普通行（ts 分片 / 变体子列表）与 # 行 URI 属性（EXT-X-KEY 密钥 /
    EXT-X-MEDIA / EXT-X-STREAM-INF 变体）统一 urljoin 后改写为 /video?url=，
    浏览器全部媒体流量走本地代理（ticket-19）；另清洗 ##EXT 双井号瑕疵行。"""
    import re

    URI_ATTR = re.compile(r'(URI=")([^"]*)(")')

    def _rewrite_attr(m):
        uri = m.group(2)
        if uri and not uri.startswith("data:"):
            return m.group(1) + _proxy_media_url(urljoin(stream_url, uri)) + m.group(3)
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
            line = _proxy_media_url(urljoin(stream_url, s))
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


async def _proxy_stream(request: Request, stream_url: str):
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
        new_body = _rewrite_hls_playlist(text, stream_url)
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
        url = _resolve_video_url(url)
    return await _proxy_stream(request, url)


@app.get("/audio")
async def audio_proxy(url: str, request: Request):
    """代理音频流（声音）；本地文件直接返回；m3u8 等直链不经 yt-dlp"""
    if os.path.exists(url):
        return FileResponse(url)
    kind, _ = classify(url)
    if kind == "page":
        url = _resolve_audio_url(url)
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
    await ws.accept()
    url = ws.query_params.get("url", "").strip()
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
    stop = threading.Event()
    paused = threading.Event()  # 客户端暂停 → 后端停止推流解析，字幕冻结

    def put(item: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

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
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8002)
    args = ap.parse_args()
    print(f"实时字幕 webUI: http://{args.host}:{args.port}")
    # log_config=None：禁用 uvicorn 默认日志配置，保留我们挂的环形缓冲 handler
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                log_config=None)


if __name__ == "__main__":
    main()
