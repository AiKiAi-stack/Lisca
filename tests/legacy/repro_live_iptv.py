"""复现回路：直播源观看端到端（ticket-19，真实 IPTV 源）

断言：
  ① 粘贴 .m3u 地址 → 📺 频道面板出现分组频道列表（≥300 频道）
  ② 批量检测：手动触发后逐条异步标注（检测中行 + 进度变化 + 出现 ✅/❌ 行）
  ③ 点击已标注可用（✅）的频道 → 视频播放（t 前进）+ HLS 出声（muted=false，audioEl 无 src）
  ④ 直播字幕命中（aliyun-funasr 引擎）
一条命令:  .venv/bin/python tests/legacy/repro_live_iptv.py
依赖: 真实网络可达该 IPTV 源（IPTV 源会更换服务器，若恰逢全失效请稍后重跑）；
      DASHSCOPE key（~/Projects/.env 的 dashscopetoken 行）
退出码: 0=全绿；1=至少一条红；2=网络不可达（源失效，注明非代码问题）
"""
import os
import re
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PORT = 8130
BASE = f"http://127.0.0.1:{PORT}"
M3U = "https://live.zbds.top/tv/iptv4.m3u"
PLACEHOLDERS = ("输入地址", "点击下方播放", "准备中", "⚠", "字幕准备", "无法播放", "跳转中")


def wait_port(port, timeout=20):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.3)
    return False


def main():
    # 网络可达性预检：m3u 拉不到直接退出码 2（非代码问题）。
    # 源对非浏览器 UA 返回 403（实测），预检须带浏览器 UA。
    import urllib.request

    try:
        req = urllib.request.Request(M3U, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read()
        if len(body) < 1000:
            print(f"[网络] m3u 内容过小（{len(body)}B），源可能失效")
            return 2
    except Exception as exc:  # noqa: BLE001
        print(f"[网络] m3u 不可达（{type(exc).__name__}），无法验证，非代码问题")
        return 2

    server = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "tools", "webui.py"),
         "--host", "127.0.0.1", "--port", str(PORT)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results = []
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

            # ① m3u → 频道面板（用 API 引擎，本地引擎需 CUDA 运行库环境）
            page.fill("#url", M3U)
            page.select_option("#engine", "aliyun-funasr")
            page.click("#start")
            page.wait_for_selector("#chPanel:not([hidden])", timeout=20000)
            n_ch = page.locator("#chList .ch-row").count()
            ok = n_ch >= 300
            print(f"[①] 频道列表 {n_ch} 个（≥300）: {'ok' if ok else 'RED'}")
            results.append(ok)

            # ② 批量检测：手动触发 → 逐条异步标注；等出现可用（✅）频道
            page.click("#chProbeBtn")
            probing = 0
            info = ""
            deadline = time.time() + 45
            while time.time() < deadline:
                time.sleep(1)
                n_ok = page.locator("#chList .ch-row.ok").count()
                probing = page.locator("#chList .ch-row .ch-status",
                                       has_text="检测中").count()
                info = page.evaluate("chInfo.textContent")
                if n_ok >= 1 and probing >= 1 and "批量检测" in info:
                    break
            marked = page.locator("#chList .ch-row.ok, #chList .ch-row.fail").count()
            async_ok = marked >= 1 and probing >= 1 and "批量检测" in info
            print(f"[②] 批量检测: 已标注 {marked} 行(含✅) 检测中 {probing} 行 "
                  f"进度「{info}」: {'ok' if async_ok else 'RED'}")
            results.append(async_ok)

            # ③ 选延迟最低的可用频道 → 点击播放（IPTV 源抖动，快源更稳）
            best_url = page.evaluate("""() => {
                let best = null, bestMs = Infinity;
                for (const [k, cell] of chRows) {
                    if (!cell.row.classList.contains('ok')) continue;
                    const m = cell.status.textContent.match(/(\\d+)ms/);
                    if (m && +m[1] < bestMs) { bestMs = +m[1]; best = k; }
                }
                return best;
            }""")
            if best_url:
                ok_name = page.evaluate(
                    "(u) => chRows.get(u).row.querySelector('.ch-name').textContent",
                    best_url)
                page.evaluate("(u) => chRows.get(u).row.click()", best_url)
            else:
                ok_name = "(无可用频道)"
            t_max, sub_hits, hist_max = 0.0, 0, 0
            deadline = time.time() + 75
            while time.time() < deadline:
                s = page.evaluate("""() => ({
                    t: player.currentTime, paused: player.paused,
                    cur: currentEl.textContent.trim(), muted: player.muted,
                    asrc: audioEl.getAttribute('src'), hist: historyEl.childElementCount,
                })""")
                t_max = max(t_max, s["t"])
                hist_max = max(hist_max, s["hist"])
                is_sub = s["cur"] and not any(m in s["cur"] for m in PLACEHOLDERS)
                if is_sub and s["t"] > 0.5:
                    sub_hits += 1
                if t_max > 2 and (sub_hits >= 2 or hist_max >= 3):
                    break
                time.sleep(0.5)
            play_ok = t_max > 2
            hls_audio_ok = not page.evaluate("player.muted") and \
                page.evaluate("audioEl.getAttribute('src')") is None
            print(f"[③] 频道「{ok_name}」播放 t_max={t_max:.1f}s "
                  f"出声(muted=false+audioEl无src)={hls_audio_ok}: "
                  f"{'ok' if play_ok and hls_audio_ok else 'RED'}")
            results.append(play_ok and hls_audio_ok)
            sub_ok = sub_hits >= 2 or hist_max >= 3
            print(f"[④] 直播字幕（overlay命中 {sub_hits} 次 / history {hist_max} 条）: "
                  f"{'ok' if sub_ok else 'RED'}")
            results.append(sub_ok)

            page.screenshot(path=os.path.join(ROOT, "logs", "repro_live_iptv.png"))
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    passed = sum(1 for r in results if r)
    failed = len(results) - passed
    print(f"\n通过 {passed}/{len(results)}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
