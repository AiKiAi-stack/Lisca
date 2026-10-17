"""复现回路：本地视频两条路径（无 API key / ASR 模型）

用户症状：本地视频"无法正常使用"。
本回路覆盖两条路径，各断言 overlay 同步命中：
  路径 A：直接输入本地文件路径 → 开始
  路径 B：上传按钮（set_input_files 触发 /upload → connect）

用法: python test/repro_local_video.py --video /path/to/video.mp4
也可将视频放到 uploads/ 后直接运行；若目录有多段视频会取前两段。
退出码: 0=全绿 1=复现症状(红) 2=环境错误
"""
import argparse
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8126
BASE = f"http://127.0.0.1:{PORT}"
PLACEHOLDER_MARKS = ("输入地址", "点击下方播放", "准备中", "⚠")
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".mov", ".webm", ".avi", ".wav", ".mp3", ".m4a", ".flac")


def wait_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def run_scenario(page, mode, video):
    """返回 (ok, 命中数, 详情)"""
    page.goto(BASE)
    page.select_option("#engine", "demo")
    if mode == "direct":
        page.fill("#url", video)
        page.click("#start")
    else:
        page.set_input_files("#file", video)
    hits = 0
    started = False
    err = ""
    deadline = time.time() + 60
    while time.time() < deadline:
        s = page.evaluate("""() => ({
            t: +player.currentTime.toFixed(1), paused: player.paused,
            cur: currentEl.textContent.trim(), hist: historyEl.childElementCount,
            err: currentEl.classList.contains('err'),
        })""")
        if s["err"]:
            err = s["cur"][:40]
            break
        if s["t"] > 0.5 or not s["paused"]:
            started = True
        if started and s["cur"] and not any(
                m in s["cur"] for m in PLACEHOLDER_MARKS):
            hits += 1
        if s["hist"] >= 2 and hits >= 4:
            break
        time.sleep(0.3)
    return (not err and started and hits >= 3), hits, err or ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", help="本地视频/音频文件；直接路径与上传路径都会验证")
    args = parser.parse_args()
    if args.video:
        videos = [os.path.abspath(args.video)]
    else:
        upload_dir = os.path.join(ROOT, "uploads")
        videos = sorted(
            os.path.join(upload_dir, name) for name in os.listdir(upload_dir)
            if name.lower().endswith(VIDEO_EXTENSIONS)
        )[:2] if os.path.isdir(upload_dir) else []
    videos = [path for path in videos if os.path.isfile(path)]
    if not videos:
        print("[ERR] 找不到媒体文件。请用 --video <本地文件> 指定，或放入 uploads/。")
        return 2
    scenarios = [("direct", videos[0]), ("upload", videos[1] if len(videos) > 1 else videos[0])]
    server = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "tools", "webui.py"),
         "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_port(PORT):
            print("[ERR] webUI 未起来")
            return 2
        from playwright.sync_api import sync_playwright
        results = {}
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--autoplay-policy=no-user-gesture-required"])
            for mode, video in scenarios:
                page = browser.new_page()
                ok, hits, err = run_scenario(page, mode, video)
                results[mode] = (ok, hits, err)
                page.close()
            browser.close()

        for mode, (ok, hits, err) in results.items():
            print(f"[{mode}] 本地路径: overlay 命中 {hits} 次"
                  f"{' ✅' if ok else f' ❌ {err}'}")
        if all(ok for ok, _, _ in results.values()):
            print("[GREEN] 本地媒体实时链路两路径均正常（字幕为演示内容，不代表识别效果）")
            return 0
        print("[RED] 本地视频存在异常")
        return 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    sys.exit(main())
