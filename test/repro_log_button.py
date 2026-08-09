"""复现回路：日志反馈按钮（R4）

断言：
  ① #logBtn 存在且可见
  ② 点击后 #logPanel 显示
  ③ 开始会话后 #logText 含 connect/status 事件
  ④ 收到字幕后有 sub 事件
一条命令:  .venv/bin/python test/repro_log_button.py
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8127
BASE = f"http://127.0.0.1:{PORT}"
VIDEO = os.path.join(ROOT, "uploads", "0_20260709_223807.mp4")


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

            # ① 按钮存在且可见
            vis = page.is_visible("#logBtn")
            print(f"[①] #logBtn 可见: {'ok' if vis else 'RED'}")
            if not vis:
                return 1

            # ② 面板可开合
            page.click("#logBtn")
            panel_visible = page.is_visible("#logPanel")
            print(f"[②] 点击后 #logPanel 显示: {'ok' if panel_visible else 'RED'}")

            # ③④ 会话事件记录
            page.fill("#url", VIDEO)
            page.select_option("#engine", "aliyun-funasr")
            page.click("#start")
            deadline = time.time() + 60
            got_sub = False
            while time.time() < deadline:
                txt = page.evaluate("logBuffer.join('\\n')")
                if "sub:" in txt:
                    got_sub = True
                if got_sub and "connect" in txt and "status" in txt:
                    break
                time.sleep(0.5)
            txt = page.evaluate("logBuffer.join('\\n')")
            ok3 = "connect" in txt and "status" in txt
            ok4 = "sub:" in txt
            print(f"[③] 日志含 connect/status: {'ok' if ok3 else 'RED'}")
            print(f"[④] 日志含 sub 事件: {'ok' if ok4 else 'RED'}")
            print(f"    日志长度: {len(txt)} 字符；样例:\n" +
                  "\n".join(f"      {l}" for l in txt.splitlines()[:8]))
            browser.close()

            if not (vis and panel_visible and ok3 and ok4):
                print("[RED] 日志按钮功能不完整")
                return 1
            print("[GREEN] 日志按钮可用")
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
