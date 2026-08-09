"""复现回路：m3u8 直播/点播源端到端（R6）

默认 VOD: mux x36xhzz（Big Buck Bunny，英文音轨，funasr 会输出文本）
可选 LIVE: --live 用 akamai 测试直播流（网络不可达时退出码 2 并注明）

断言：overlay 显示字幕 ≥3 次、history 条数 ≥3、视频实际在播（t 前进）
一条命令:  .venv/bin/python test/repro_m3u8.py [--live]
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8129
BASE = f"http://127.0.0.1:{PORT}"
VOD = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"
LIVE_CANDIDATES = [
    "https://cph-p2p-msl.akamaized.net/hls/live/2000341/test/master.m3u8",
    "https://moctobpltc-i.akamaihd.net/hls/live/571329/eight/playlist.m3u8",
]
PLACEHOLDER_MARKS = ("输入地址", "点击下方播放", "准备中", "⚠")


def wait_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def main():
    live = "--live" in sys.argv
    stream = VOD
    if live:
        import urllib.request
        for cand in LIVE_CANDIDATES:
            try:
                with urllib.request.urlopen(cand, timeout=12) as r:
                    body = r.read().decode("utf-8", "replace")
                # 变体/分片 URL 需可达，否则视为测试流失效
                for line in body.splitlines():
                    s = line.strip()
                    if s and not s.startswith("#") and not s.startswith("http"):
                        from urllib.parse import urljoin
                        probe = urljoin(cand, s)
                        with urllib.request.urlopen(probe, timeout=12):
                            pass
                        stream = cand
                        print(f"[LIVE] 使用测试流: {cand[:60]}")
                        break
                if stream != VOD:
                    break
            except Exception as e:  # noqa: BLE001
                print(f"[LIVE] 候选不可用: {cand[:55]} ({type(e).__name__})")
        if stream == VOD:
            print("[LIVE] 公网直播测试流均不可用（akamai 等已失效）。"
                  "代理/播放机制已由 VOD 回路覆盖，请用真实直播源人工验证。")
            return 2
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
            page.fill("#url", stream)
            page.select_option("#engine", "aliyun-funasr")
            page.click("#start")

            hits, hist_max, t_max, started = 0, 0, 0.0, False
            err = ""
            deadline = time.time() + 100
            while time.time() < deadline:
                s = page.evaluate("""() => ({
                    t: +player.currentTime.toFixed(1), paused: player.paused,
                    cur: currentEl.textContent.trim(),
                    hist: historyEl.childElementCount,
                    err: currentEl.classList.contains('err'),
                    vsrc: (player.currentSrc || '').slice(0, 60),
                })""")
                if s["err"]:
                    err = s["cur"][:50]
                    break
                t_max = max(t_max, s["t"])
                hist_max = max(hist_max, s["hist"])
                if s["t"] > 0.5 or not s["paused"]:
                    started = True
                if started and s["cur"] and not any(
                        m in s["cur"] for m in PLACEHOLDER_MARKS):
                    hits += 1
                if hist_max >= 3 and hits >= 4:
                    break
                time.sleep(0.5)
            page.screenshot(path=os.path.join(ROOT, "logs", "repro_m3u8.png"))
            browser.close()

        mode = "LIVE" if live else "VOD"
        print(f"[{mode}] src={stream[:55]}")
        print(f"    视频在播: t_max={t_max}s started={started}"
              f" 历史={hist_max} overlay命中={hits} {err}")
        if err:
            print(f"[ERR] 引擎/前端报错: {err}")
            return 2
        if hist_max >= 3 and hits >= 4 and started and t_max > 3:
            print("[GREEN] m3u8 端到端正常（视频播放 + 字幕同步）")
            return 0
        print("[RED] m3u8 播放或字幕未同步")
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
