"""E2E：真实浏览器验证 webUI 字幕显示 + 暂停冻结

前置：webUI 运行在 127.0.0.1:8000（含阿里云引擎，key 来自 ~/Projects/.env）
运行：.venv/bin/python test/e2e_webui.py

验证：
① WS 收到 partial 字幕（history 有记录）
② 字幕快照显示：#current 出现文本（2s 定时器驱动，整块替换）
③ 暂停联动：暂停视频 → 后端停止解析 → 字幕冻结（4s 内不变）
"""
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8000"
DEMO = "/home/wbb/Projects/asr_project/uploads/demo.mp4"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        page.goto(BASE)
        page.fill("#url", DEMO)
        page.select_option("#engine", "aliyun-funasr")
        page.click("#start")
        print("[1] 已点开始，等待首条字幕（最长 60s）…")

        # 禁用自动播放（headless 视频会快播，与慢推字幕不同步）；进度手动控制
        page.evaluate("v => { v.play = () => Promise.resolve(); v.pause(); }",
                      page.locator("#player").element_handle())

        for i in range(60):
            time.sleep(1)
            if page.locator("#history div").count() >= 1:
                first = page.locator("#history div").first.inner_text()
                print(f"[2] 收到首条字幕: {first[:50]}")
                break
        else:
            print("[FAIL] 60s 内未收到字幕")
            page.screenshot(path="logs/e2e_fail.png")
            browser.close()
            sys.exit(1)

        # 手动固定进度到第一句区间内（t=1>0.5 避开"未播放"保护；句1 beg=0 end≈1 命中）
        page.evaluate("v => { v.currentTime = 1; v.pause(); }",
                      page.locator("#player").element_handle())
        time.sleep(2.5)
        cur = page.locator("#current").inner_text().strip()
        hist_count = page.locator("#history div").count()
        print(f"    history={hist_count}, #current='{cur[:40]}'")
        assert cur and "播放" not in cur and "输入地址" not in cur, f"应显示字幕, 实际: '{cur}'"
        print(f"[3] ✅ 字幕显示: '{cur[:50]}'")

        # 暂停联动：先真实播放（触发 play 事件→resume），再暂停（触发 pause 事件→pause 停后端）
        page.evaluate("v => { v.play = HTMLMediaElement.prototype.play; v.currentTime = 1; v.play(); }",
                      page.locator("#player").element_handle())
        page.wait_for_timeout(800)
        print("    播放中 paused:", page.evaluate("v => v.paused", page.locator("#player").element_handle()))
        page.evaluate("v => v.pause()", page.locator("#player").element_handle())
        print("    暂停后 paused:", page.evaluate("v => v.paused", page.locator("#player").element_handle()))
        time.sleep(1)
        h1 = page.locator("#history div").count()
        h1_content = page.locator("#history div").first.inner_text()[:40]
        frozen = page.locator("#current").inner_text()
        time.sleep(4)
        h2 = page.locator("#history div").count()
        h2_content = page.locator("#history div").first.inner_text()[:40]
        frozen2 = page.locator("#current").inner_text()
        print(f"    暂停后 history: {h1} -> {h2}")
        print(f"    history内容: '{h1_content}' -> '{h2_content}'")
        print(f"    暂停后 current: [{frozen[:30]}] -> [{frozen2[:30]}]")
        # 数据必须冻结（后端停止解析，无新 partial）；显示不得增长（用户 bug：暂停后字幕还在不断生成）
        assert h1 == h2 and h1_content == h2_content, "暂停后数据应冻结（后端停止解析）"
        assert len(frozen2) <= len(frozen), f"暂停后字幕不应继续生成: {len(frozen)} -> {len(frozen2)}"
        print(f"[4] ✅ 暂停后数据冻结，字幕不再生成")

        page.screenshot(path="logs/e2e_ok.png")
        browser.close()
        print("=== E2E 通过 ===")


if __name__ == "__main__":
    main()
