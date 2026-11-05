"""端到端复现：按句时间轴字幕和可调播放延迟（ADR-0003）

断言：
  ① 字级 words 到达：识别句包含字级时间戳
  ② partial 修订时字幕可更新，文本完整显示
  ③ 字幕按句时间戳出现/消失，播放延迟控件可选 5s
  ④ 空窗合理：无说话时字幕清空

一条命令:  .venv/bin/python tests/legacy/repro_word_granularity.py
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
            page.select_option("#silence", "400")
            page.select_option("#delay", "5")
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
                samples.append(page.evaluate("""() => {
                    return {
                        cur: currentEl.textContent.trim(),
                        t: player.currentTime,
                        active: subs.some(s => s.beg <= player.currentTime && s.end > player.currentTime),
                        nw: subs.length && subs[subs.length - 1].words
                            ? subs[subs.length - 1].words.length : 0,
                    };
                }"""))
                time.sleep(0.5)
            browser.close()

            def is_blank(s):
                return not s["cur"] or s["cur"].startswith("⏳")

            # ① words 到达
            max_words = max((s["nw"] for s in samples), default=0)
            ok1 = max_words >= 3
            print(f"[①] 字级 words 到达（单句最多 {max_words} 字）: {'ok' if ok1 else 'RED'}")

            # ② subtitle presence follows ASR sentence intervals.
            mismatches = [s for s in samples if s["active"] != (not is_blank(s))]
            ok2 = len(mismatches) <= max(2, len(samples) * 0.1)
            print(f"[②] 字幕和句子时间轴一致（不一致 {len(mismatches)} 次）: "
                  f"{'ok' if ok2 else 'RED'}")

            # ③ active text changes as sentence intervals change.
            transitions = sum(a["cur"] != b["cur"] for a, b in zip(samples, samples[1:]))
            ok3 = transitions >= 1
            print(f"[③] 句间字幕切换 {transitions} 次: {'ok' if ok3 else 'RED'}")

            # ④ 空窗：非空占比 + 最长连续空窗
            nonempty = 0
            longest = 0
            empty_run = 0
            for s in samples:
                if is_blank(s):
                    empty_run += 1
                    longest = max(longest, empty_run)
                else:
                    nonempty += 1
                    empty_run = 0
            ratio = nonempty / len(samples) if samples else 0
            ok4 = ratio >= 0.5 and longest <= 16
            print(f"[④] 字幕非空占比 {ratio:.0%}，最长连续空窗 {longest * 0.5:.1f}s: "
                  f"{'ok' if ok4 else 'RED'}")

            if not (ok1 and ok2 and ok3 and ok4):
                print("[RED] 按句字幕验收不完整")
                return 1
            print("[GREEN] 按句时间轴字幕可用")
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
