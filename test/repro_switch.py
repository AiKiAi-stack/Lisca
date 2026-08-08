"""复现回路：不刷新页面切换第二个视频后的状态错乱

用户症状：① 画面仍是第一个视频，声音/字幕是第二个；② overlay 不显示正确字幕。
流程：连接 A 播放起来后，直接输入 B 点「开始」（不刷新、不点停止），采样全状态。

一条命令:  .venv/bin/python test/repro_switch.py
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8124
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


def snap(page):
    return page.evaluate("""() => {
        const src = (el) => {
          const m = (el.currentSrc || '').match(/[?&]url=([^&]*)/);
          return m ? decodeURIComponent(m[1]).split('/').pop() : '';
        };
        return {
          t: +player.currentTime.toFixed(1), paused: player.paused,
          vsrc: src(player), asrc: src(audioEl),
          cur: currentEl.textContent.trim(), hist: historyEl.childElementCount,
          status: statusEl.textContent.trim(),
          rs: player.readyState,
        };
    }""")


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

            # --- 会话 A ---
            page.fill("#url", VID_A)
            page.select_option("#engine", "aliyun-funasr")
            page.click("#start")
            print("[A] 连接视频A，等播放+overlay命中…")
            ok_a = False
            for _ in range(60):
                time.sleep(0.5)
                s = snap(page)
                if not s["paused"] and s["t"] > 1 and s["cur"] and \
                        not any(m in s["cur"] for m in PLACEHOLDER_MARKS):
                    ok_a = True
                    break
            print(f"[A] ok={ok_a} {s}")
            if not ok_a:
                print("[ERR] A 未正常播放")
                return 2

            # --- 不刷新，按真实操作切 B：停止 → 输入 → 开始 ---
            netlog = []
            page.on("response", lambda r: netlog.append(
                (r.status, r.url.split("/")[-1][:40]))
                if "/video" in r.url or "/audio" in r.url else None)
            page.on("console", lambda m: print("  [console]", m.type, m.text[:100])
                    if m.type in ("error", "warning") else None)
            page.click("#stop")
            time.sleep(0.5)
            page.fill("#url", VID_B)
            page.click("#start")
            print("[B] 已停止A并二次点开始，采样 30s…")
            trace = []
            deadline = time.time() + 30
            while time.time() < deadline:
                trace.append((time.strftime("%H:%M:%S"), snap(page)))
                time.sleep(0.5)
            page.screenshot(path=os.path.join(ROOT, "logs", "repro_switch.png"))
            browser.close()

            print("\n(time, video_t, paused, vsrc, asrc, readyState, hist, status, overlay)")
            for wall, s in trace[::4]:
                print(f"  {wall} t={s['t']} paused={s['paused']} v={s['vsrc'][:24]} "
                      f"a={s['asrc'][:24]} rs={s['rs']} hist={s['hist']} "
                      f"st={s['status'][:18]} cur={s['cur'][:22]}")
            print("\n[net] /video /audio 响应:", netlog[:12])
            last = trace[-1][1]
            problems = []
            if "demo" in last["vsrc"]:
                problems.append("画面源仍是A(demo)")
            if "223807" not in last["asrc"]:
                problems.append("音频源不是B")
            if last["paused"]:
                problems.append("B 未播放(卡住)")
            hits_b = sum(1 for _, s in trace if "罗" in s["cur"])
            if hits_b == 0:
                problems.append("overlay 未显示B的字幕")
            else:
                print(f"overlay 显示B字幕采样数: {hits_b}")
            print("\n问题:", problems if problems else "无：B 画面/声音/字幕一致且 overlay 正常")
            return 0 if not problems else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    sys.exit(main())
