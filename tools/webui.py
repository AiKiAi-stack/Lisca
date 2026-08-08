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

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whisper_streaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, File, Request, UploadFile, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse  # noqa: E402
from starlette.background import BackgroundTask  # noqa: E402
import uvicorn  # noqa: E402

from engines import create_engine  # noqa: E402
from input_resolver import resolve_stream_url  # noqa: E402
from stream_source import StreamSource, s16_to_f32  # noqa: E402

app = FastAPI(title="实时字幕")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_static")
UPLOAD_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# yt-dlp 解析缓存（5 分钟）：播放器会发多个 range 请求，避免每次重复解析
_VIDEO_CACHE: dict = {"ts": 0.0, "url": "", "video": ""}


def _resolve_video_url(user_input: str) -> str:
    """解析视频流 URL（带防盗链 Referer 的平台，供 /video 代理使用）"""
    import subprocess
    import time

    now = time.time()
    if _VIDEO_CACHE["url"] == user_input and now - _VIDEO_CACHE["ts"] < 300:
        return _VIDEO_CACHE["video"]
    # bv* = 纯视频流（B站音视频分离，无合并 mp4；m4s 前端可播）
    for attempt in range(2):
        proc = subprocess.run(
            ["yt-dlp", "-g", "--no-warnings", "-f", "bv*", user_input],
            capture_output=True, text=True, timeout=120,
        )
        lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
        if proc.returncode == 0 and lines:
            video = lines[0]
            _VIDEO_CACHE.update(ts=now, url=user_input, video=video)
            return video
    raise ValueError(f"视频流解析失败: {proc.stderr.strip()[:300]}")


@app.get("/video")
async def video_proxy(url: str, request: Request):
    """代理视频流（补防盗链头），供前端 <video> 播放；本地文件直接返回"""
    if os.path.exists(url):
        return FileResponse(url)
    import httpx

    video_url = _resolve_video_url(url)
    headers = {"Referer": "https://www.bilibili.com", "User-Agent": UA}
    if "range" in request.headers:
        headers["Range"] = request.headers["range"]
    client = httpx.AsyncClient(timeout=None, follow_redirects=True)
    req = client.build_request("GET", video_url, headers=headers)
    upstream = await client.send(req, stream=True)
    pass_headers = {k: v for k, v in upstream.headers.items()
                    if k.lower() in ("content-type", "content-length", "accept-ranges", "content-range")}

    async def _cleanup():
        await upstream.aclose()
        await client.aclose()

    return StreamingResponse(
        upstream.aiter_raw(), status_code=upstream.status_code,
        headers=pass_headers, background=BackgroundTask(_cleanup),
    )


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


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
    if not url:
        await ws.send_json({"type": "error", "message": "缺少 url 参数"})
        await ws.close()
        return

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    stop = threading.Event()

    def put(item: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, item)

    def worker() -> None:
        try:
            real_url = resolve_stream_url(url)
            engine = create_engine(engine_name)
            put({"type": "status", "message": f"引擎就绪，开始拉流: {real_url[:60]}"})
            src = StreamSource(real_url)
            try:
                for chunk in src.iter_chunks():
                    if stop.is_set():
                        break
                    o = engine.transcribe_chunk(s16_to_f32(chunk))
                    if o[0] is not None:
                        put({"type": "subtitle", "beg": o[0], "end": o[1], "text": o[2]})
                o = engine.finish()
                if o[0] is not None:
                    put({"type": "subtitle", "beg": o[0], "end": o[1], "text": o[2]})
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
    ap = argparse.ArgumentParser(description="实时字幕 webUI")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()
    print(f"实时字幕 webUI: http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
