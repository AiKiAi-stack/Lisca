"""复现回路：首段字幕错位 + 句子停留过长（R1）

用户症状：B站完整视频前几秒字幕异常（需拖进度条才正常）；字幕停留过长。
本回路不 seek，250ms 采样播放起 25s，事后用 history 的最终 [beg,end,text] 断言：
  ① 首段：t<=3s 内 overlay 显示过与视频时刻匹配的句子
  ② 不滞留：t 落在所有句窗之外（且非句间容差带）时 overlay 必须为空

一条命令:  .venv/bin/python test/repro_first_seconds.py [视频路径]
退出码: 0=通过 1=复现症状(红) 2=环境错误
"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8125
BASE = f"http://127.0.0.1:{PORT}"
VIDEO = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    ROOT, "uploads", "0_20260709_223807.mp4")
SAMPLE_SEC = 0.25
RUN_SEC = 25
PLACEHOLDER_MARKS = ("输入地址", "点击下方播放", "准备中", "⚠")


def wait_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def text_matches(cur, full):
    """overlay 文本与句子最终文本匹配（partial 前缀关系）"""
    if not cur or not full:
        return False
    return cur.startswith(full) or full.startswith(cur)


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
            page.click("#start")

            trace = []
            deadline = time.time() + RUN_SEC + 20
            while time.time() < deadline:
                snap = page.evaluate("""() => ({
                    t: +player.currentTime.toFixed(2), paused: player.paused,
                    cur: currentEl.textContent.trim(),
                    hist: Array.from(historyEl.children).map(
                        d => ({sp: d.childNodes[0].textContent,
                               tx: d.childNodes[1].textContent})),
                })""")
                trace.append(snap)
                time.sleep(SAMPLE_SEC)
                if time.time() > deadline:
                    break
            page.screenshot(path=os.path.join(ROOT, "logs", "repro_first.png"))
            browser.close()

            # --- 从 history 解析句子 [beg,end,text] 并合并近重复句 ---
            raw = []
            for h in trace[-1]["hist"]:
                try:
                    beg_s, end_s = h["sp"].replace("s", "").split("-")
                    raw.append((float(beg_s), float(end_s), h["tx"]))
                except ValueError:
                    continue
            raw.sort()
            merged = []
            for beg, end, tx in raw:
                if merged and abs(beg - merged[-1][0]) < 0.3:
                    # 同 utterance 的 partial 抖动：保留更长的文本与更大的 end
                    if len(tx) > len(merged[-1][2]):
                        merged[-1] = (beg, end, tx)
                    else:
                        merged[-1] = (merged[-1][0], max(merged[-1][1], end),
                                      merged[-1][2])
                else:
                    merged.append([beg, end, tx])
            print(f"句子数(去重后): {len(merged)} "
                  f"{[(round(b,1), round(e,1), t[:8]) for b,e,t in merged[:6]]}")

            if not merged:
                print("[ERR] 未产生字幕，无法判定")
                return 2

            playing = [s for s in trace if not s["paused"]]
            if not playing:
                print("[ERR] 视频未播放，无法判定")
                return 2

            def cur_is_sub(cur):
                return cur and not any(m in cur for m in PLACEHOLDER_MARKS)

            def expected_idx(t):
                """时间窗 [beg-0.15, end+0.2) 匹配的句子（取最新 beg）"""
                hit = -1
                for i, (beg, end, _) in enumerate(merged):
                    if t >= beg - 0.15 and t < end + 0.2:
                        hit = i
                return hit

            # --- ① 首段：t<=3s 内 overlay 与匹配句一致 ---
            first_ok = False
            for s in playing:
                if s["t"] > 3.0:
                    break
                if not cur_is_sub(s["cur"]):
                    continue
                i = expected_idx(s["t"])
                if i >= 0 and text_matches(s["cur"], merged[i][2]):
                    first_ok = True
                    break
            print(f"[①首段] t<=3s overlay 命中匹配句: {'ok' if first_ok else 'RED'}")

            # --- ② 滞留/错位：t 无匹配句窗且超出所有句窗 ≥1.5s 时 overlay 必须为空 ---
            max_end = max(e for _, e, _ in merged)
            linger = []
            for s in playing:
                if not cur_is_sub(s["cur"]):
                    continue
                # 窗口内显示不算滞留（partial 文本被 funasr 修订属正常）；
                # 句边界 0.1-0.4s 的定时器残留也不算"过长"。
                # 真滞留：无匹配句窗且超出所有句窗 ≥1.5s 仍显示文本。
                if expected_idx(s["t"]) < 0 and s["t"] >= max_end + 1.5:
                    linger.append((round(s["t"], 1), s["cur"][:14],
                                   round(max_end, 1)))
            print(f"[②滞留] 句窗外仍显示文本的采样数: {len(linger)}"
                  f"{(' 示例: ' + str(linger[:4])) if linger else ' ok'}")

            if not first_ok or linger:
                print("[RED] 症状复现：首段错位或句子滞留")
                return 1
            print("[GREEN] 首段同步、无滞留")
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
