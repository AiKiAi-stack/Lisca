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
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "whisper_streaming"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
import uvicorn  # noqa: E402

from engines import create_engine  # noqa: E402
from input_resolver import resolve_stream_url  # noqa: E402
from stream_source import StreamSource, s16_to_f32  # noqa: E402

app = FastAPI(title="实时字幕")

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webui_static")


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(STATIC_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


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
