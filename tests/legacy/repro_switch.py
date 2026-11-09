"""复现回路：不刷新页面切换第二个视频后的状态错乱

用户症状：① 画面仍是第一个视频，声音/字幕是第二个；② overlay 不显示正确字幕。
流程：连接 A 播放起来后，直接输入 B 点「开始」（不刷新、不点停止），采样全状态。

一条命令:  .venv/bin/python tests/legacy/repro_switch.py
B站双链接: .venv/bin/python tests/legacy/repro_switch.py --bili BV1xx BV2yy
"""
import argparse
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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


def run_local_mode():
    """本地双文件切换（原有回路）"""
    url_a, url_b = VID_A, VID_B
    marker_a, marker_b = "demo", "223807"
    content_marker = "罗"  # B 视频字幕特征字
    return _run_switch(url_a, url_b, marker_a, marker_b, content_marker,
                       wait_playing=True)


def run_bili_mode(bv1: str, bv2: str):
    """B站双链接切换（R2 扩展回路）"""
    url_a = f"https://www.bilibili.com/video/{bv1}"
    url_b = f"https://www.bilibili.com/video/{bv2}"
    return _run_switch(url_a, url_b, bv1, bv2, None,
                       wait_playing=False)


def _run_switch(url_a, url_b, marker_a, marker_b, content_marker, wait_playing):
    """通用切换回路：连接 A → 停止 → 连接 B → 断言 audioEl.src 含 marker_b"""
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
            page.goto(BASE)

            # --- 会话 A ---
            page.fill("#url", url_a)
            page.select_option("#engine", "aliyun-funasr")
            page.click("#start")
            print(f"[A] 连接: {url_a[:60]}")
            ok_a = False
            s = {}
            for _ in range(90):
                time.sleep(0.5)
                s = snap(page)
                if wait_playing:
                    if not s["paused"] and s["t"] > 1 and s["cur"] and \
                            not any(m in s["cur"] for m in PLACEHOLDER_MARKS):
                        ok_a = True
                        break
                else:
                    # B站模式：等视频有内容（readyState>=2 或 time>0）即可
                    if s["rs"] >= 2 or s["t"] > 0:
                        ok_a = True
                        break
            print(f"[A] ok={ok_a} {s}")
            if not ok_a:
                print("[ERR] A 未加载")
                return 2

            # 记录 A 的 audio src
            asrc_a = page.evaluate("audioEl.currentSrc || audioEl.src")
            print(f"[A] audioEl src: {asrc_a[:80]}")

            # --- 不刷新，按真实操作切 B：停止 → 输入 → 开始 ---
            netlog = []
            page.on("response", lambda r: netlog.append(
                (r.status, r.url.split("/")[-1][:40]))
                if "/video" in r.url or "/audio" in r.url else None)
            page.on("console", lambda m: print("  [console]", m.type, m.text[:100])
                    if m.type in ("error", "warning") else None)
            page.click("#stop")
            time.sleep(0.5)
            page.fill("#url", url_b)
            page.click("#start")
            print(f"[B] 已停止A并二次点开始: {url_b[:60]}")

            # 等 B 的 audioEl.src 被赋值（前端 connect() 同步赋值）
            time.sleep(2)
            asrc_b_early = page.evaluate("audioEl.currentSrc || audioEl.src")
            print(f"[B] audioEl src (切换后): {asrc_b_early[:80]}")

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
            # 核心断言：audioEl.currentSrc 的 url 参数必须含 marker_b（第二输入）
            if marker_b not in last["asrc"]:
                problems.append(f"音频源不含{marker_b}（实际: {last['asrc'][:60]}）")
            if wait_playing:
                if marker_a in last["vsrc"]:
                    problems.append(f"画面源仍是A({marker_a})")
                if last["paused"]:
                    problems.append("B 未播放(卡住)")
                if content_marker:
                    hits_b = sum(1 for _, s in trace if content_marker in s["cur"])
                    if hits_b == 0:
                        problems.append("overlay 未显示B的字幕")
                    else:
                        print(f"overlay 显示B字幕采样数: {hits_b}")
            # 额外断言：audio src 确实变了（不是 A 的残留）
            if marker_a in asrc_b_early and marker_b not in asrc_b_early:
                problems.append(f"audioEl.src 仍指向A（{asrc_b_early[:60]}）")

            print("\n问题:", problems if problems else "无：B 画面/声音/字幕一致且 overlay 正常")
            return 0 if not problems else 1
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


def main():
    parser = argparse.ArgumentParser(description="切换回路复现")
    parser.add_argument("--bili", nargs=2, metavar=("BV1", "BV2"),
                        help="B站双链接模式（传入两个 BV 号）")
    args = parser.parse_args()
    if args.bili:
        return run_bili_mode(args.bili[0], args.bili[1])
    return run_local_mode()


if __name__ == "__main__":
    sys.exit(main())
