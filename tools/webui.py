"""ticket-06: webUI 单页 MVP

单页：输入流地址/文件路径 → 引擎选择 → 大字号实时字幕（WebSocket 推送）。

用法:
    python webui.py [--host 127.0.0.1] [--port 8000]
    # SSH 远程访问: ssh -L 8000:localhost:8000 user@host 后浏览器开 localhost:8000

注意: 本地引擎需要 CUDA 运行库在 LD_LIBRARY_PATH（建议经 run_subs.sh 的环境启动）。
"""
import argparse
import asyncio
import json
import os
import shutil
import sys
import threading
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whisper_streaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, File, Request, Response, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse  # noqa: E402
from starlette.background import BackgroundTask  # noqa: E402
import uvicorn  # noqa: E402

from engines import create_engine  # noqa: E402
from input_resolver import classify, resolve_stream_url  # noqa: E402
from stream_source import DEFAULT_UA, StreamSource, s16_to_f32  # noqa: E402

app = FastAPI(title="实时字幕")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_static")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

UA = DEFAULT_UA

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


async def _proxy_stream(request: Request, stream_url: str):
    """转发流（补防盗链头），供前端 <video>/<audio> 播放。

    HLS playlist（m3u8）会被浏览器当作文本，相对 .ts/变体路径无法解析——
    把相对行改写为绝对 CDN URL 后再返回。"""
    import httpx

    headers = {"Referer": "https://www.bilibili.com", "User-Agent": UA}
    if "range" in request.headers:
        headers["Range"] = request.headers["range"]
    client = httpx.AsyncClient(timeout=None, follow_redirects=True)
    req = client.build_request("GET", stream_url, headers=headers)
    upstream = await client.send(req, stream=True)
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
        rewritten = []
        for line in text.splitlines():
            s = line.strip()
            if s and not s.startswith("#") and not s.startswith(("http://", "https://")):
                line = urljoin(stream_url, s)
            rewritten.append(line)
        new_body = "\n".join(rewritten)
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


@app.get("/")
def index():
    """返回单页（no-store 防浏览器缓存旧版 JS）"""
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers={"Cache-Control": "no-store"})


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

        def emit(o) -> None:
            """推送当前句 partial（整句累计文本 + 可选字级 words，前端字级快照显示）"""
            beg, end, text, words = o
            if text:
                put({"type": "subtitle", "beg": beg, "end": end, "text": text,
                     "words": words})

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
            engine = create_engine(engine_name, **engine_kwargs)
            put({"type": "status", "message": f"引擎就绪，开始拉流: {real_url[:60]}"})
            src = StreamSource(real_url, http_headers=http_headers)
            chunk_sec = 0.1  # 拉流分块（秒）：帧粒度基准（0.1s = 3 帧 @30fps）
            try:
                last = time.monotonic()
                for chunk in src.iter_chunks():
                    if stop.is_set():
                        break
                    # 客户端暂停：停止推流解析（字幕冻结，省 API 调用）
                    while paused.is_set():
                        if stop.is_set():
                            break
                        time.sleep(0.2)
                    if stop.is_set():
                        break
                    # 按实时速率推流：每 chunk 对应 chunk_sec 秒音频
                    wait = chunk_sec - (time.monotonic() - last)
                    if wait > 0:
                        time.sleep(wait)
                    last = time.monotonic()
                    o = engine.transcribe_chunk(s16_to_f32(chunk))
                    if o[0] is not None:
                        emit(o)
                    # 异步引擎（API）积压的句子逐条取出（增量，含 partial）
                    while True:
                        o2 = engine.poll()
                        if o2[0] is None:
                            break
                        emit(o2)
                o = engine.finish()
                if o[0] is not None:
                    emit(o)
                # finish 后仍可能有剩余句子（异步引擎），poll 取尽
                while True:
                    o2 = engine.poll()
                    if o2[0] is None:
                        break
                    emit(o2)
            finally:
                src.close()
        except Exception as exc:  # noqa: BLE001 - 用户可见错误信息
            put({"type": "error", "message": str(exc)[:300]})
        finally:
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
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    print(f"实时字幕 webUI: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
