"""复现回路：固定窗口分块字幕（ticket-13 / ADR-0002）

断言：
  ① 字级 words 到达：会话中某句的字级时间戳数量 ≥ 3（分块由字级聚合）
  ② 窗口稳定：同一窗口（w0 相同）内 #current 文本不逐字增长（无流式输出）
  ③ 窗口切换：窗口边界处整体切换 ≥ 2 次（新窗口完整内容出现）
  ④ 空窗合理：无说话窗口为空白——最长连续空窗 ≤ 8s、非空占比 ≥ 50%

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
            page.select_option("#silence", "400")
            page.select_option("#winLen", "2")
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
                    const D = parseFloat(winLenEl.value) || 2;
                    const guard = 1.0;  // 与显示端窗口公式一致（ticket-15）
                    return {
                        cur: currentEl.textContent.trim(),
                        w0: Math.floor((player.currentTime - guard) / D) * D,
                        dw0: displayedBlock ? displayedBlock.w0 : null,
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

            # ② 窗口稳定：按显示端实际窗口（displayedBlock.w0，快照冻结）分组——
            # 同窗口内文本必须零变化（0 容忍）；w0 采样错位伪影用 dw0 消除
            violations = []
            for a, b in zip(samples, samples[1:]):
                if a["dw0"] is not None and a["dw0"] == b["dw0"] \
                        and not is_blank(a) and not is_blank(b) \
                        and a["cur"] != b["cur"]:
                    violations.append((a, b))
            ok2 = len(violations) == 0
            print(f"[②] 窗口内文本稳定（同窗口变化 {len(violations)} 次，需 0）: "
                  f"{'ok' if ok2 else 'RED'}")
            if violations:
                for a, b in violations[:3]:
                    print(f"     dw0={a['dw0']} ({a['cur'][:16]!r} → {b['cur'][:24]!r})")

            # ③ 窗口切换：显示端窗口变化（整体切换）≥ 2 次
            transitions = 0
            for a, b in zip(samples, samples[1:]):
                if a["dw0"] is not None and a["dw0"] != b["dw0"]:
                    transitions += 1
            ok3 = transitions >= 2
            print(f"[③] 窗口边界整体切换 {transitions} 次: {'ok' if ok3 else 'RED'}")

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
            ok4 = ratio >= 0.5 and longest <= 16  # 8s（4s 窗口 + 余量）
            print(f"[④] 字幕非空占比 {ratio:.0%}，最长连续空窗 {longest * 0.5:.1f}s: "
                  f"{'ok' if ok4 else 'RED'}")

            if not (ok1 and ok2 and ok3 and ok4):
                print("[RED] 分块字幕验收不完整")
                return 1
            print("[GREEN] 固定窗口分块字幕可用")
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
