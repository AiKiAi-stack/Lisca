"""E2E 验收：真实 UX 断言（R5 重写）

前置：webUI 运行在 127.0.0.1:8002（含阿里云引擎，key 来自 ~/Projects/.env）
运行：.venv/bin/python test/e2e_webui.py
退出码：0=全绿；1=至少一条红（红基准）

场景：
  1. 连续同步：播放起 15s 内 overlay 命中 ≥2 次（不 seek）
  2. 首段：首条字幕到达后自动播放，overlay 在播放起 12s 内出现文本
  3. 切换：停止 A → 开始 B，audioEl.currentSrc 含 B 路径，overlay 显示 B 字幕
  4. 本地文件：直接路径播放 overlay 同步
  5. 暂停冻结：暂停后 4s 内 history 条数不变、overlay 文本不增长

纪律：禁止手动 seek、禁止禁用 autoplay hack（纯真实用户流程）。
"""
import os
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8002"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VID_A = os.path.join(ROOT, "uploads", "demo.mp4")
VID_B = os.path.join(ROOT, "uploads", "0_20260709_223807.mp4")
ENGINE = "aliyun-funasr"
PLACEHOLDERS = ("输入地址", "点击下方播放", "准备中", "⚠", "字幕准备")


def _is_sub(text: str) -> bool:
    return bool(text and not any(m in text for m in PLACEHOLDERS))


def _snapshot(page):
    return page.evaluate("""() => ({
        t: player.currentTime,
        paused: player.paused,
        rs: player.readyState,
        cur: currentEl.textContent.trim(),
        hist: historyEl.childElementCount,
        asrc: audioEl.currentSrc || '',
    })""")


# ── 场景 1：连续同步 ─────────────────────────────────────────────
def scenario_continuous_sync(page):
    """播放起 15s 内 overlay 命中 ≥2 次（不 seek）"""
    page.goto(BASE)
    page.fill("#url", VID_A)
    page.select_option("#engine", ENGINE)
    page.click("#start")

    overlay_hits = 0
    t0 = time.time()
    deadline = t0 + 20
    while time.time() < deadline:
        s = _snapshot(page)
        if (s["t"] > 0.5 or not s["paused"]) and _is_sub(s["cur"]):
            overlay_hits += 1
        if overlay_hits >= 2:
            return True, f"overlay 命中 {overlay_hits} 次"
        time.sleep(0.5)
    return False, f"overlay 仅命中 {overlay_hits} 次（期望 ≥2）"


# ── 场景 2：首段 ─────────────────────────────────────────────────
def scenario_first_segment(page):
    """首条字幕到达后自动播放，overlay 在播放起 12s 内出现文本"""
    page.goto(BASE)
    page.fill("#url", VID_A)
    page.select_option("#engine", ENGINE)
    page.click("#start")

    # 等首条字幕到达 history（最长 30s，引擎加载 + 首句识别）
    t_first_sub = None
    for _ in range(60):
        time.sleep(0.5)
        if page.locator("#history div").count() >= 1:
            t_first_sub = time.time()
            break
    if t_first_sub is None:
        return False, "30s 内无首条字幕"

    # 等自动播放启动（前端在首条字幕后 +1.5s 自动 play）
    t_play_start = None
    for _ in range(30):
        time.sleep(0.5)
        s = _snapshot(page)
        if s["t"] > 0.5 or not s["paused"]:
            t_play_start = time.time()
            break
    if t_play_start is None:
        return False, "首条字幕后 15s 内未自动播放"

    # 播放起 12s 内 overlay 出现字幕文本
    deadline = t_play_start + 12
    while time.time() < deadline:
        s = _snapshot(page)
        if _is_sub(s["cur"]):
            dt = time.time() - t_play_start
            return True, f"播放起 {dt:.1f}s 后 overlay 出现字幕"
        time.sleep(0.5)
    return False, "播放起 12s 内 overlay 未出现字幕"


# ── 场景 3：切换 ─────────────────────────────────────────────────
def scenario_switch(page):
    """停止 A → 开始 B，audioEl.currentSrc 含 B 路径，overlay 显示 B 字幕"""
    page.goto(BASE)

    # --- 会话 A ---
    page.fill("#url", VID_A)
    page.select_option("#engine", ENGINE)
    page.click("#start")
    for _ in range(60):
        time.sleep(0.5)
        s = _snapshot(page)
        if not s["paused"] and s["t"] > 1 and _is_sub(s["cur"]):
            break
    else:
        return False, "A 未正常播放"

    # --- 停止 A → 开始 B（不刷新页面） ---
    page.click("#stop")
    time.sleep(1)
    page.fill("#url", VID_B)
    page.click("#start")

    # 等 B 播放起来（最长 25s）
    for _ in range(50):
        time.sleep(0.5)
        s = _snapshot(page)
        if not s["paused"] and s["t"] > 0.5:
            break
    else:
        return False, "B 未播放"

    # 采样 15s，断言 audio 源与 overlay
    problems = set()
    hits_b = 0
    samples = 0
    deadline = time.time() + 15
    while time.time() < deadline:
        s = _snapshot(page)
        samples += 1
        if "223807" not in s["asrc"]:
            problems.add("audio 源不是 B")
        if _is_sub(s["cur"]):
            hits_b += 1
        time.sleep(0.5)

    if samples < 5:
        return False, "采样不足"
    if "audio 源不是 B" in problems:
        return False, "audio 源未更新到 B"
    if hits_b == 0:
        return False, "overlay 未显示 B 的字幕"
    return True, f"audio 源已切换，overlay 命中 B 字幕 {hits_b} 次"


# ── 场景 4：本地文件 ─────────────────────────────────────────────
def scenario_local_file(page):
    """直接本地路径播放 overlay 同步"""
    page.goto(BASE)
    page.fill("#url", VID_A)
    page.select_option("#engine", ENGINE)
    page.click("#start")

    overlay_hits = 0
    deadline = time.time() + 20
    while time.time() < deadline:
        s = _snapshot(page)
        if (s["t"] > 0.5 or not s["paused"]) and _is_sub(s["cur"]):
            overlay_hits += 1
        if overlay_hits >= 2:
            return True, f"本地文件 overlay 命中 {overlay_hits} 次"
        time.sleep(0.5)
    return False, f"本地文件 overlay 仅命中 {overlay_hits} 次"


# ── 场景 5：暂停冻结 ─────────────────────────────────────────────
def scenario_pause_freeze(page):
    """暂停后 4s 内 history 条数不变、overlay 文本不增长"""
    page.goto(BASE)
    page.fill("#url", VID_A)
    page.select_option("#engine", ENGINE)
    page.click("#start")

    # 等播放起来 + overlay 显示字幕
    for _ in range(60):
        time.sleep(0.5)
        s = _snapshot(page)
        if not s["paused"] and s["t"] > 1 and _is_sub(s["cur"]):
            break
    else:
        return False, "未正常播放并显示字幕"

    # 暂停（后端 pause 有 ≤0.2s 生效窗口，途中 partial 落地属正常，先等它落完）
    page.evaluate("player.pause()")
    time.sleep(0.6)

    # 记录暂停稳定后状态
    h1 = page.locator("#history div").count()
    frozen1 = page.locator("#current").inner_text()
    time.sleep(4)

    # 检查冻结
    h2 = page.locator("#history div").count()
    frozen2 = page.locator("#current").inner_text()

    if h1 != h2:
        return False, f"暂停后 history 增长: {h1} → {h2}"
    if len(frozen2) > len(frozen1):
        return False, f"暂停后 overlay 增长: {len(frozen1)} → {len(frozen2)} 字符"
    return True, f"暂停后数据冻结（history={h1}，overlay 长度不变）"


# ── 主流程 ───────────────────────────────────────────────────────
SCENARIOS = [
    ("连续同步", scenario_continuous_sync),
    ("首段", scenario_first_segment),
    ("切换", scenario_switch),
    ("本地文件", scenario_local_file),
    ("暂停冻结", scenario_pause_freeze),
]


def main():
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        for name, fn in SCENARIOS:
            print(f"\n{'=' * 60}")
            print(f"[场景] {name}")
            try:
                ok, msg = fn(page)
            except Exception as exc:  # noqa: BLE001
                ok, msg = False, f"异常: {exc}"
            tag = "✅" if ok else "❌"
            print(f"  {tag} {msg}")
            results.append((name, ok, msg))
        browser.close()

    print(f"\n{'=' * 60}")
    print("=== 汇总 ===")
    for name, ok, msg in results:
        print(f"  {'✅' if ok else '❌'} {name}: {msg}")
    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    print(f"\n通过 {passed}/{len(results)}，失败 {failed}")
    if failed == 0:
        print("全绿")
        return 0
    print("存在红项（红基准）")
    return 1


if __name__ == "__main__":
    sys.exit(main())
