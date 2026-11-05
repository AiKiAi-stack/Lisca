"""Observe the real player via public UI/HTTP/WS with offline recognition.

Requires original_release_fixture.py at the sole WebUI port, 8002. It refuses a
regular service, never installs credentials, and writes only simulated evidence.
"""
import json
import os
import re
from pathlib import Path
import shutil
import time
from urllib.request import Request, urlopen

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8002"
ROOT = Path(__file__).resolve().parents[2]


def launch_browser(playwright):
    executable = (os.environ.get("STREAM_ASR_BROWSER") or shutil.which("google-chrome")
                  or shutil.which("chromium") or shutil.which("chromium-browser"))
    options = {"executable_path": executable} if executable else {}
    return playwright.chromium.launch(headless=True,
        args=["--autoplay-policy=no-user-gesture-required"], **options)


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
    for close in page.locator("dialog[open] [data-close]").all():
        close.click()
    if page.locator("#stop").is_visible():
        page.locator("#stop").click()


def observe_caption_pages(page):
    changes = []
    deadline = time.monotonic() + 16
    while time.monotonic() < deadline:
        item = snapshot(page)
        if not changes or item["text"] != changes[-1]["text"]:
            changes.append({"time": item["time"], "text": item["text"]})
        assert item["lines"] <= 2.1 and not item["clipped"], item
        if item["time"] >= 11.3:
            break
        page.wait_for_timeout(40)
    assert changes and item["time"] >= 11.3, changes
    artificial_flashes = [left for left, right in zip(changes, changes[1:])
                          if left["text"] and right["time"] - left["time"] < .55]
    assert not artificial_flashes, artificial_flashes
    return changes


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True, "Offline fixture required"
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            open_scenario(page, "normal", fixture["media"]["normal"])
            page.wait_for_function("""() => document.querySelector('video').currentTime>.3 &&
              document.querySelector('#current').innerText.includes('第一句')""", timeout=15000)
            first = snapshot(page)
            assert .0 <= first["time"] <= 1.8, first
            timed = [first]
            deadline = time.monotonic() + 15
            while timed[-1]["time"] < 6.4 and time.monotonic() < deadline:
                page.wait_for_timeout(40)
                timed.append(snapshot(page))
            expected = [(0., 1.8, "第一句完整字幕。"),
                        (2., 3.8, "Second complete sentence."),
                        (4., 5.8, "最后一句原文字幕。")]
            for beg, end, text in expected:
                visible = [item for item in timed if item["text"] == text]
                assert len(visible) >= 10, (text, visible)
                assert all(beg - .15 <= item["time"] <= end + .15 for item in visible), visible
            gap = [item for item in timed if 6 <= item["time"] <= 6.4]
            assert gap and all(not item["text"] for item in gap), gap
            assert timed[-1]["time"] >= 6.4, timed[-1]
            results.append({"case": "zh-en-visible-time-and-missing-interval", "passed": True,
                            "media_samples": timed})
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
            page.locator("video").evaluate("video=>{video.currentTime=0;video.play()}")
            changes = observe_caption_pages(page)
            results.append({"case": "long-caption-pages-without-artificial-flashing", "passed": True,
                            "visible_changes": changes, "sampling_tolerance_seconds": .05})
            page.locator("video").evaluate("video=>{video.pause();video.currentTime=3}")
            page.wait_for_timeout(200)
            page.locator("#fullscreenBtn").click()
            page.wait_for_function("document.fullscreenElement !== null")
            full = snapshot(page)
            assert full["text"] and abs(full["fontSize"]-64) < .1, full
            assert full["lines"] <= 2.1 and not full["clipped"], full
            results.append({"case": "fullscreen-selected-large-font", "passed": True, "snapshot": full})
            page.screenshot(path=str(directory / "fullscreen-caption.png"))
            page.locator("#exitFullscreenBtn").click()
            stop_if_active(page)
            open_scenario(page, "long-en", fixture["media"]["long"])
            wait_complete(page, 25000)
            page.locator("video").evaluate("video=>{video.currentTime=0;video.play()}")
            english = observe_caption_pages(page)
            expected_english = "Clear original captions follow the speaker. Old lines never cover the next sentence."
            visible_english = " ".join(item["text"] for item in english if item["text"])
            normalize = lambda text: re.sub(r"[^a-z0-9]", "", text.lower())
            assert normalize(visible_english) == normalize(expected_english), english
            results.append({"case": "english-long-caption-readable-and-complete", "passed": True,
                            "visible_changes": english, "sampling_tolerance_seconds": .05})
            page.locator("video").evaluate("video=>{video.pause();video.currentTime=6.8}")
            page.wait_for_timeout(200)
            assert snapshot(page)["text"], snapshot(page)
            page.screenshot(path=str(directory / "english-narrow-caption.png"))
            stop_if_active(page)
            page.set_viewport_size({"width": 1280, "height": 800})
            page.locator("#settingsBtn").click()
            page.get_by_role("tab", name="字幕", exact=True).click()
            page.locator("#styleReset").click()
            page.get_by_role("button", name="关闭设置", exact=True).click()
            open_scenario(page, "fast-en", fixture["media"]["long"])
            wait_complete(page, 25000)
            page.locator("video").evaluate("video=>{video.currentTime=0;video.play()}")
            fast = observe_caption_pages(page)
            expected_fast = ("During this rapid explanation the speaker keeps moving through several related ideas "
                             "without leaving a long pause between them. Each original caption should stay near its "
                             "spoken words and remain readable, while older lines finish before the next idea appears on the screen.")
            assert normalize(" ".join(item["text"] for item in fast)) == normalize(expected_fast), fast
            results.append({"case": "fast-english-timed-pages-without-backlog", "passed": True,
                            "prescribed_words_per_second": len(expected_fast.split()) / 10.8,
                            "visible_changes": fast, "sampling_tolerance_seconds": .05})
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
