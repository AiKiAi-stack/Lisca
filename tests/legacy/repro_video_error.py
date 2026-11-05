"""复现回路：视频错误 full log + 失败即停会话（ticket-16）

断言：
  ① 日志含 video_error_full（code= / msg= / src= 完整信息）
  ② 会话停止：video_stop 或 ws_close 出现
  ③ 视频错误后不再有 sub 事件（音频处理/API 已停）

一条命令:  .venv/bin/python tests/legacy/repro_video_error.py
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 8129
BASE = f"http://127.0.0.1:{PORT}"
BAD_FILE = os.path.join(ROOT, "uploads", "does_not_exist_xyz.mp4")  # 不存在 → 代理 404/500


def wait_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


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
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--autoplay-policy=no-user-gesture-required"])
            page = browser.new_page()
            page.goto(BASE)
            page.fill("#url", BAD_FILE)
            page.select_option("#engine", "aliyun-funasr")
            page.click("#start")

            deadline = time.time() + 45
            txt = ""
            while time.time() < deadline:
                txt = page.evaluate("logBuffer.join('\\n')")
                if "video_error_full" in txt and ("video_stop" in txt or "ws_close" in txt):
                    break
                time.sleep(0.5)
            txt = page.evaluate("logBuffer.join('\\n')")
            browser.close()

            ok1 = "video_error_full" in txt and "code=" in txt and "src=" in txt
            ok2 = "video_stop" in txt or "ws_close" in txt
            ok3 = "sub:" not in txt  # 会话已停，不应再有字幕事件
            print(f"[①] 完整错误日志（video_error_full）: {'ok' if ok1 else 'RED'}")
            print(f"[②] 会话停止（video_stop/ws_close）: {'ok' if ok2 else 'RED'}")
            print(f"[③] 停止后无 sub 事件: {'ok' if ok3 else 'RED'}")
            for line in txt.splitlines():
                if "video_error_full" in line or "video_stop" in line or "ws_close" in line:
                    print(f"    {line[:120]}")
            if not (ok1 and ok2 and ok3):
                print("[RED] 视频错误处理不完整")
                return 1
            print("[GREEN] 视频错误 full log + 失败即停可用")
            return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    sys.exit(main())
