"""复现回路：字级渲染与断句粒度（ticket-10 / ADR-0001）

断言：
  ① 字级 words 到达：会话中某句的字级时间戳数量 ≥ 3（API 引擎下发 words）
  ② 句内增量：同一句内 #current 文本多次增长（字级随播放进度逐字出现，
     而非整句一次性成块出现）
  ③ 空窗控制：播放采样窗口内字幕非空占比 ≥ 70%，最长连续空窗 ≤ 1s
     （句间定格上一句 → 无「瞬间消失→成段出现」）

一条命令:  .venv/bin/python test/repro_word_granularity.py
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8128
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

            page.fill("#url", VIDEO)
            page.select_option("#engine", "aliyun-funasr")
            # 断句灵敏度默认 400ms（控件默认即 400，此处显式选择）
            page.select_option("#silence", "400")
            page.click("#start")

            # 等播放起来且首条字幕已显示（排除视频开场静音/转写尚未覆盖的前导段）
            ready = False
            deadline = time.time() + 60
            while time.time() < deadline:
                cur = page.evaluate("currentEl.textContent.trim()")
                if (cur and not cur.startswith("⏳")
                        and page.evaluate("!player.paused && player.currentTime > 0.5")):
                    ready = True
                    break
                time.sleep(0.5)
            if not ready:
                print("[RED] 60s 内未进入「播放 + 字幕显示」状态")
                browser.close()
                return 1

            # 采样 15s（0.5s 一次）
            samples = []
            deadline = time.time() + 15
            while time.time() < deadline:
                samples.append(page.evaluate("""() => ({
                    cur: currentEl.textContent.trim(),
                    beg: subs.length ? subs[subs.length - 1].beg : null,
                    nw: subs.length && subs[subs.length - 1].words
                        ? subs[subs.length - 1].words.length : 0,
                })"""))
                time.sleep(0.5)
            browser.close()

            def is_sub(cur):
                return bool(cur) and not cur.startswith("⏳")

            # ① words 到达
            max_words = max((s["nw"] for s in samples), default=0)
            ok1 = max_words >= 3
            print(f"[①] 字级 words 到达（单句最多 {max_words} 字）: {'ok' if ok1 else 'RED'}")

            # ② 句内增量：同一 beg 下文本长度增长次数
            growths = 0
            prev_beg, prev_len = None, 0
            for s in samples:
                if s["beg"] is None or not is_sub(s["cur"]):
                    prev_beg = None
                    continue
                if s["beg"] == prev_beg and len(s["cur"]) > prev_len:
                    growths += 1
                prev_beg = s["beg"]
                prev_len = len(s["cur"])
            ok2 = growths >= 2
            print(f"[②] 同一句内文本增量增长 {growths} 次: {'ok' if ok2 else 'RED'}")

            # ③ 空窗控制：非空占比 + 最长连续空窗
            empties = []
            nonempty = 0
            longest = 0
            empty_run = 0
            for s in samples:
                if is_sub(s["cur"]):
                    nonempty += 1
                    empty_run = 0
                else:
                    empty_run += 1
                    if empty_run == 1:
                        empties.append(s)
                    longest = max(longest, empty_run)
            ratio = nonempty / len(samples) if samples else 0
            ok3 = ratio >= 0.7 and longest <= 2
            print(f"[③] 字幕非空占比 {ratio:.0%}，最长连续空窗 {longest * 0.5:.1f}s: "
                  f"{'ok' if ok3 else 'RED'}")
            if not ok3 and empties:
                print("     空窗样本（cur/beg/nw）:")
                for s in empties[:8]:
                    print(f"     cur={s['cur'][:20]!r} beg={s['beg']} nw={s['nw']}")

            if not (ok1 and ok2 and ok3):
                print("[RED] 字级渲染验收不完整")
                return 1
            print("[GREEN] 字级渲染与断句粒度可用")
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
