"""Observe progressive/silent/no-progress playback with offline recognition."""
import json
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import ROOT, open_scenario, request, snapshot, stop_if_active


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True, "Offline fixture required"
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome",
            headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            open_scenario(page, "silent", fixture["media"]["silent"])
            page.wait_for_function("document.querySelector('video').currentTime>.5", timeout=10000)
            silent = snapshot(page)
            assert not silent["text"] and not silent["history"], silent
            assert "语音" in silent["status"], silent
            assert request("/__fixture/state")["active"] == 1
            results.append({"case": "silent-progressive-start", "passed": True, "snapshot": silent})
            stop_if_active(page)

            open_scenario(page, "silence-speech", fixture["media"]["silence-speech"])
            page.wait_for_function("document.querySelector('video').currentTime>.5", timeout=10000)
            assert not snapshot(page)["text"]
            page.wait_for_function("""() => document.querySelector('video').currentTime>=4.1 &&
              document.querySelector('#current').innerText.includes('静音后')""", timeout=12000)
            speech = snapshot(page)
            assert speech["time"] < 5.8, speech
            assert "暂未识别到语音" not in speech["status"], speech
            results.append({"case": "silence-to-speech", "passed": True, "snapshot": speech})
            stop_if_active(page)

            begun = time.monotonic()
            open_scenario(page, "no-progress", fixture["media"]["no-progress"])
            page.wait_for_function("document.querySelector('#status').innerText.includes('15 秒')", timeout=20000)
            assert 14 <= time.monotonic() - begun < 20
            assert page.locator("#retryCaptions").is_visible()
            assert page.locator("#playOnly").is_visible()
            page.wait_for_timeout(300)
            stopped = request("/__fixture/state")
            assert stopped["active"] == 0, stopped
            page.locator("#playOnly").click()
            page.wait_for_function("document.querySelector('video').currentTime>.5", timeout=10000)
            page.wait_for_timeout(1000)
            after = request("/__fixture/state")
            assert after["started"] == stopped["started"]
            assert after["audio_seconds"] == stopped["audio_seconds"]
            results.append({"case": "no-progress-timeout-only-play", "passed": True,
                            "new_recognition_requests": 0, "new_audio_seconds": 0})
            assert not errors, errors
        finally:
            stop_if_active(page)
            page.locator("video").evaluate("video=>video.pause()")
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors}
            (directory / "preparation-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
