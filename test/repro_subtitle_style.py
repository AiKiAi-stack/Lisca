"""复现回路：字幕样式实时调整控件（ticket-18）

断言：
  ① #styleBtn 存在且可见
  ①b 初始 #stylePanel 隐藏（hidden 属性生效，同日志面板坑）
  ② 点击展开 → 再点收起
  ③ 字号滑块 42 → #current fontSize=42px + 数值显示同步（input 实时生效）
  ④ 位置滑块 20 → #current bottom=20% + 数值显示同步
  ⑤ 字体选宋体 → #current fontFamily 含 "Noto Serif CJK SC"
  ⑥ localStorage subtitleStyle 保存 {"size":42,"pos":20,"font":"songti"}
  ⑦ 刷新页面 → 样式恢复（字号/位置/字体均回显）
  ⑧ 重置默认 → fontSize=30px / bottom=12% / fontFamily 空 / localStorage 更新
一条命令:  .venv/bin/python test/repro_subtitle_style.py
"""
import json
import os
import socket
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PORT = 8128
BASE = f"http://127.0.0.1:{PORT}"


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

            # ① 按钮存在且可见
            vis = page.is_visible("#styleBtn")
            print(f"[①] #styleBtn 可见: {'ok' if vis else 'RED'}")
            results.append(vis)

            # ①b 初始面板隐藏：hidden 属性必须生效
            init_hidden = not page.is_visible("#stylePanel")
            print(f"[①b] 初始 #stylePanel 隐藏: {'ok' if init_hidden else 'RED'}")
            results.append(init_hidden)

            # ② 展开 → 收起
            page.click("#styleBtn")
            shown = page.is_visible("#stylePanel")
            page.click("#styleBtn")
            hidden_again = not page.is_visible("#stylePanel")
            toggle_ok = shown and hidden_again
            print(f"[②] 展开后可见={shown} 再点后隐藏={hidden_again}: "
                  f"{'ok' if toggle_ok else 'RED'}")
            results.append(toggle_ok)

            # ③ 字号滑块 → 实时生效 + 数值显示同步
            page.evaluate("sizeRange.value = '42'; "
                          "sizeRange.dispatchEvent(new Event('input', {bubbles: true}))")
            fs = page.evaluate("currentEl.style.fontSize")
            sv = page.evaluate("sizeVal.textContent")
            size_ok = fs == "42px" and sv == "42px"
            print(f"[③] 字号=42 → fontSize={fs} 显示={sv}: {'ok' if size_ok else 'RED'}")
            results.append(size_ok)

            # ④ 位置滑块 → 实时生效 + 数值显示同步
            page.evaluate("posRange.value = '20'; "
                          "posRange.dispatchEvent(new Event('input', {bubbles: true}))")
            bt = page.evaluate("currentEl.style.bottom")
            pv = page.evaluate("posVal.textContent")
            pos_ok = bt == "20%" and pv == "20%"
            print(f"[④] 位置=20 → bottom={bt} 显示={pv}: {'ok' if pos_ok else 'RED'}")
            results.append(pos_ok)

            # ⑤ 字体选宋体（面板当前收起，先展开再操作）
            page.click("#styleBtn")
            page.select_option("#fontSel", "songti")
            ff = page.evaluate("currentEl.style.fontFamily")
            font_ok = "Noto Serif CJK SC" in (ff or "")
            print(f"[⑤] 字体=宋体 → fontFamily={ff}: {'ok' if font_ok else 'RED'}")
            results.append(font_ok)

            # ⑥ localStorage 持久化
            stored = json.loads(page.evaluate("localStorage.getItem('subtitleStyle')") or "{}")
            ls_ok = stored == {"size": 42, "pos": 20, "font": "songti"}
            print(f"[⑥] localStorage subtitleStyle={stored}: {'ok' if ls_ok else 'RED'}")
            results.append(ls_ok)

            # ⑦ 刷新恢复
            page.reload()
            fs2 = page.evaluate("currentEl.style.fontSize")
            bt2 = page.evaluate("currentEl.style.bottom")
            ff2 = page.evaluate("currentEl.style.fontFamily")
            reload_ok = (fs2 == "42px" and bt2 == "20%" and "Noto Serif CJK SC" in (ff2 or ""))
            print(f"[⑦] 刷新后 fontSize={fs2} bottom={bt2} fontFamily={ff2}: "
                  f"{'ok' if reload_ok else 'RED'}")
            results.append(reload_ok)

            # ⑧ 重置默认（刷新后面板重新收起，先展开）
            page.click("#styleBtn")
            page.click("#styleReset")
            fs3 = page.evaluate("currentEl.style.fontSize")
            bt3 = page.evaluate("currentEl.style.bottom")
            ff3 = page.evaluate("currentEl.style.fontFamily")
            stored3 = json.loads(page.evaluate("localStorage.getItem('subtitleStyle')") or "{}")
            reset_ok = (fs3 == "30px" and bt3 == "12%" and not ff3
                        and stored3 == {"size": 30, "pos": 12, "font": "default"})
            print(f"[⑧] 重置后 fontSize={fs3} bottom={bt3} fontFamily={ff3!r} "
                  f"localStorage={stored3}: {'ok' if reset_ok else 'RED'}")
            results.append(reset_ok)

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
