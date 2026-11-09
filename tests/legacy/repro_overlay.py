"""复现回路：字幕只出现在视频下方（history），不叠加在视频上

用户症状：webUI 播放时，字幕仅显示在下方 history 面板，视频画面上没有叠加字幕。
本脚本走真实用户流程（不手动 seek / 不禁用自动播放），断言 overlay(#current) 显示过字幕。

一条命令:  .venv/bin/python tests/legacy/repro_overlay.py
退出码: 0=overlay 正常显示字幕; 1=症状复现(有 history 但 overlay 没字幕); 2=其他错误
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 8123
BASE = f"http://127.0.0.1:{PORT}"
DEMO = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "uploads", "demo.mp4")

PLACEHOLDER_MARKS = ("输入地址", "点击下方播放", "⚠")


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
        [sys.executable, os.path.join(ROOT, "tools", "lisca.web.app.py"),
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

            # --- Part A: 响应必须禁缓存 + 页面必须是 overlay 布局 ---
            resp = page.goto(BASE)
            cc = (resp.headers.get("cache-control") or "").lower()
            print(f"[A1] Cache-Control: '{cc}' -> {'ok' if 'no-store' in cc else 'FAIL: 无 no-store，浏览器可能缓存旧页'}")
            html = page.content()
            overlay_css = "#current" in html and "position: absolute" in html
            print(f"[A2] overlay 布局(#current absolute): {'ok' if overlay_css else 'FAIL: 页面是旧布局'}")

            # --- Part B: 真实流程，断言 overlay 显示字幕 ---
            page.fill("#url", DEMO)
            page.select_option("#engine", "aliyun-funasr")
            page.click("#start")
            early = len(sys.argv) > 2 and sys.argv[2] == "earlyplay"
            if early:
                # 模拟用户：加载期间看到"点击播放"提示，提前手动点播放
                time.sleep(0.5)
                page.evaluate("player.play()")
                print("[B] earlyplay: 已提前手动点播放")
            print("[B] 已点开始，采样 overlay/history/播放进度（最长 90s）…")

            trace, overlay_hits, hist_max = [], 0, 0
            hits_after_hist3 = 0
            started_playing = False
            deadline = time.time() + 90
            while time.time() < deadline:
                snap = page.evaluate("""() => ({
                    t: player.currentTime, paused: player.paused,
                    rs: player.readyState, cur: currentEl.textContent.trim(),
                    hist: historyEl.childElementCount,
                    err: currentEl.classList.contains('err'),
                })""")
                wall = time.strftime("%H:%M:%S")
                cur = snap["cur"]
                is_sub = (cur and not snap["err"]
                          and not any(m in cur for m in PLACEHOLDER_MARKS))
                if snap["t"] > 0.5 or not snap["paused"]:
                    started_playing = True
                if started_playing and is_sub:
                    overlay_hits += 1
                    if hist_max >= 3:
                        hits_after_hist3 += 1
                hist_max = max(hist_max, snap["hist"])
                trace.append((wall, round(snap["t"], 1), snap["paused"],
                              snap["rs"], snap["hist"], cur[:30]))
                # 持续同步判绿：history≥3 句后 overlay 仍命中≥4 次（连续语音场景）
                if hist_max >= 3 and hits_after_hist3 >= 4:
                    break
                # 引擎报错 → 提前结束
                if snap["err"]:
                    print(f"[ERR] 引擎/前端报错: {cur[:80]}")
                    break
                time.sleep(0.5)

            page.screenshot(path=os.path.join(ROOT, "logs", "repro_overlay.png"))
            browser.close()

            print("\n采样轨迹 (time, video_t, paused, readyState, hist, overlay文本):")
            for row in trace[::4]:
                print("   ", row)
            print(f"\n结果: started_playing={started_playing}, history条数={hist_max}, "
                  f"overlay命中={overlay_hits}, hist≥3后命中={hits_after_hist3}")

            if hist_max >= 3 and hits_after_hist3 == 0:
                print("[RED] 症状复现：字幕持续进下方 history，但 overlay(#current) 不再显示")
                return 1
            if hist_max >= 3 and hits_after_hist3 >= 4:
                print("[GREEN] overlay 与 history 持续同步显示")
                return 0
            print("[ERR] 字幕不足（引擎/网络/静默段），无法判定")
            return 2
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    sys.exit(main())
