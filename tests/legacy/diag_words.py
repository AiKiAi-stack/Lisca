"""一次性诊断：观察句子 words 数据随 partial 到达的演化（累计/增量/漂移）"""
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 8131
VIDEO = os.path.join(ROOT, "uploads", "0_20260709_223807.mp4")

server = subprocess.Popen(
    [sys.executable, os.path.join(ROOT, "tools", "lisca.web.app.py"),
     "--host", "127.0.0.1", "--port", str(PORT)],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
try:
    for _ in range(80):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", PORT)) == 0:
                break
        time.sleep(0.3)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        page.goto(f"http://127.0.0.1:{PORT}")
        page.fill("#url", VIDEO)
        page.select_option("#engine", "aliyun-funasr")
        page.click("#start")
        seen = {}
        deadline = time.time() + 30
        while time.time() < deadline:
            time.sleep(0.5)
            st = page.evaluate(
                """() => subs.map(s => ({
                    beg: s.beg,
                    nw: s.words ? s.words.length : 0,
                    tail: s.words && s.words.length
                        ? s.words.slice(-3).map(w => [Math.round(w[0] * 10) / 10, w[2]])
                        : [],
                }))""")
            for s in st:
                key = round(s["beg"], 2)
                if key not in seen:
                    seen[key] = (s["nw"], s["tail"])
                elif s["nw"] != seen[key][0]:
                    print(f"句 beg={s['beg']:.2f}: words {seen[key][0]} -> {s['nw']} "
                          f"tail={seen[key][1]} -> {s['tail']}")
                    seen[key] = (s["nw"], s["tail"])
        print("=== 最终各句 words 数量 ===")
        for k in sorted(seen):
            print(f"beg={k:.2f}: nw={seen[k][0]} tail={seen[k][1]}")
        browser.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait()
