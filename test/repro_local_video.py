"""复现回路：本地视频两条路径（R3）

用户症状：本地视频"无法正常使用"。
本回路覆盖两条路径，各断言 overlay 同步命中：
  路径 A：直接输入本地文件路径 → 开始
  路径 B：上传按钮（set_input_files 触发 /upload → connect）

一条命令:  .venv/bin/python test/repro_local_video.py
退出码: 0=全绿 1=复现症状(红) 2=环境错误
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8126
BASE = f"http://127.0.0.1:{PORT}"
VID_A = os.path.join(ROOT, "uploads", "demo.mp4")
VID_B = os.path.join(ROOT, "uploads", "0_20260709_223807.mp4")
PLACEHOLDER_MARKS = ("输入地址", "点击下方播放", "准备中", "⚠")


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
    if mode == "direct":
        page.fill("#url", video)
        page.select_option("#engine", "aliyun-funasr")
        page.click("#start")
    else:  # upload
        page.select_option("#engine", "aliyun-funasr")
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
            for mode, video in (("direct", VID_A), ("upload", VID_B)):
                page = browser.new_page()
                ok, hits, err = run_scenario(page, mode, video)
                results[mode] = (ok, hits, err)
                page.close()
            browser.close()

        for mode, (ok, hits, err) in results.items():
            print(f"[{mode}] 本地路径: overlay 命中 {hits} 次"
                  f"{' ✅' if ok else f' ❌ {err}'}")
        if all(ok for ok, _, _ in results.values()):
            print("[GREEN] 本地视频两路径均正常")
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
