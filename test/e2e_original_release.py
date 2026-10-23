"""Observe the real player via public UI/HTTP/WS with offline recognition.

Requires original_release_fixture.py at the sole WebUI port, 8002. It refuses a
regular service, never installs credentials, and writes only simulated evidence.
"""
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8002"
ROOT = Path(__file__).resolve().parents[1]


def request(path, data=None):
    payload = None if data is None else json.dumps(data).encode()
    with urlopen(Request(BASE + path, data=payload,
                         headers={"Content-Type": "application/json"}), timeout=10) as response:
        return json.load(response)


def configure(page):
    page.goto(BASE, wait_until="domcontentloaded")
    page.locator("#settingsBtn").click()
    page.locator("#apiKey").fill("offline-fixture-key")
    page.locator("#saveCloud").click()
    page.locator("#cloudState").filter(has_text="已配置").wait_for()
    page.get_by_role("button", name="关闭设置", exact=True).click()


def open_scenario(page, mode, media):
    request("/__fixture/scenario", {"mode": mode})
    configure(page)
    page.locator("#url").fill(media)
    page.locator("#start").click()


def snapshot(page):
    return page.evaluate("""() => {
      const video = document.querySelector('video');
      const caption = document.querySelector('#current');
      const css = getComputedStyle(caption);
      return {time:video.currentTime, paused:video.paused,
        text:caption.innerText, status:document.querySelector('#status').innerText,
        history:document.querySelector('#history').innerText,
        lines:(caption.scrollHeight-parseFloat(css.paddingTop)-parseFloat(css.paddingBottom)) /
          (parseFloat(css.lineHeight) || parseFloat(css.fontSize)*1.3),
        fontSize:parseFloat(css.fontSize),
        clipped:caption.scrollWidth>caption.clientWidth+1};
    }""")


def wait_complete(page, timeout=20000):
    page.wait_for_function("""() => /已生成/.test(document.querySelector('#status').innerText)""",
                           timeout=timeout)


def stop_if_active(page):
    if page.locator("#stop").is_visible():
        page.locator("#stop").click()


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True, "Offline fixture required"
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome",
            headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            open_scenario(page, "normal", fixture["media"]["normal"])
            page.wait_for_function("""() => document.querySelector('video').currentTime>.3 &&
              document.querySelector('#current').innerText.includes('第一句')""", timeout=15000)
            first = snapshot(page)
            assert .0 <= first["time"] <= 1.8, first
            wait_complete(page)
            started = request("/__fixture/state")["started"]
            page.locator("video").evaluate("video=>video.pause()")
            page.locator("video").evaluate("video=>video.currentTime=2.4")
            page.wait_for_function("""() => document.querySelector('#current').innerText.includes('Second')""")
            before = snapshot(page)
            page.wait_for_timeout(1000)
            after = snapshot(page)
            assert abs(after["time"]-before["time"]) < .05, (before, after)
            assert request("/__fixture/state")["started"] == started
            results.append({"case": "completed-local-control", "passed": True,
                            "snapshot": after, "new_recognition_requests": 0})
            page.screenshot(path=str(directory / "completed-local.png"))
            stop_if_active(page)

            open_scenario(page, "withdrawn", fixture["media"]["normal"])
            page.wait_for_timeout(2000)
            removed = snapshot(page)
            withdrawn = "withdrawn" not in removed["text"] and "withdrawn" not in removed["history"]
            results.append({"case": "withdrawal-stale-final", "passed": withdrawn, "snapshot": removed})
            assert withdrawn, removed
            stop_if_active(page)

            page.set_viewport_size({"width": 320, "height": 740})
            open_scenario(page, "long", fixture["media"]["long"])
            wait_complete(page, 25000)
            page.locator("video").evaluate("video=>{video.pause();video.currentTime=3}")
            page.wait_for_timeout(300)
            narrow = snapshot(page)
            assert narrow["text"] and narrow["lines"] <= 2.1 and not narrow["clipped"], narrow
            results.append({"case": "narrow-long-caption", "passed": True, "snapshot": narrow})
            page.screenshot(path=str(directory / "narrow-caption.png"))
            page.locator("#settingsBtn").click()
            page.get_by_role("tab", name="字幕", exact=True).click()
            page.locator("#sizeRange").focus()
            page.locator("#sizeRange").press("End")
            page.get_by_role("button", name="关闭设置", exact=True).click()
            page.wait_for_timeout(300)
            large = snapshot(page)
            assert abs(large["fontSize"]-64) < .1, large
            assert large["text"] and large["lines"] <= 2.1 and not large["clipped"], large
            results.append({"case": "narrow-selected-large-font", "passed": True, "snapshot": large})
            page.locator("#fullscreenBtn").click()
            page.wait_for_function("document.fullscreenElement !== null")
            full = snapshot(page)
            assert full["text"] and abs(full["fontSize"]-64) < .1, full
            assert full["lines"] <= 2.1 and not full["clipped"], full
            results.append({"case": "fullscreen-selected-large-font", "passed": True, "snapshot": full})
            page.screenshot(path=str(directory / "fullscreen-caption.png"))
            page.locator("#exitFullscreenBtn").click()
            assert not errors, errors
        finally:
            stop_if_active(page)
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors}
            (directory / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
