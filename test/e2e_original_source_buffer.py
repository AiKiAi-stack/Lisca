"""Distinguish actual browser media buffering from a caption wait, offline."""
import json
import re
import time

from playwright.sync_api import Error, sync_playwright

from e2e_original_release import launch_browser, ROOT, configure, open_scenario, request, snapshot, stop_if_active


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True and fixture.get("live_url")
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    pending, errors = [], []
    result = {"simulated_recognition": True, "real_accuracy_tested": False, "passed": False}
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            configure(page)
            # Hold only the browser's fourth fragment. The real decoder/ASR
            # side uses its independent source connection and keeps progressing.
            page.route(re.compile(r"/video\?url=.*seg03\.ts(?:&|$)"), lambda route: pending.append(route))
            request("/__fixture/scenario", {"mode": "live-normal"})
            page.locator("#url").fill(fixture["live_url"])
            page.locator("#start").click()
            page.wait_for_function("document.querySelector('video').currentTime>.3", timeout=15000)
            count = request("/__fixture/state")["started"]
            page.wait_for_function("""() => {const video=document.querySelector('video');
                return video.currentTime>5 && video.readyState<3 && !video.paused;}""", timeout=15000)
            assert len(pending) == 1
            buffering = snapshot(page)
            page.wait_for_timeout(1000)
            following = snapshot(page)
            indicator = page.locator("#sourceBufferStatus")
            following["source_buffer_status"] = indicator.inner_text() if indicator.count() else None
            assert abs(following["time"] - buffering["time"]) < .15, (buffering, following)
            result["buffering_snapshot"] = following
            assert following["source_buffer_status"] and "缓冲" in following["source_buffer_status"], dict(result)
            assert indicator.is_visible()
            assert request("/__fixture/state")["started"] == count
            pending.pop().continue_()
            page.wait_for_function("document.querySelector('video').currentTime>7", timeout=10000)
            assert request("/__fixture/state")["started"] == count
            assert not errors, errors
            result.update({"passed": True, "new_recognition_requests": 0,
                           "resumed_snapshot": snapshot(page)})
            stop_if_active(page)
            open_scenario(page, "live-normal", fixture["live_url"])
            deadline = time.monotonic() + 12
            while not pending and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert len(pending) == 1
            old_media = pending.pop()
            before_switch = request("/__fixture/state")["started"]
            request("/__fixture/scenario", {"mode": "controls"})
            page.locator("#file").set_input_files(fixture["media"]["normal"])
            page.wait_for_function("""() => document.querySelector('video').currentTime>.3 &&
                document.querySelector('#current').innerText.includes('Prepared sentence')""", timeout=15000)
            old_cancelled = False
            try:
                old_media.continue_()
            except Error:
                old_cancelled = True
            page.wait_for_timeout(1000)
            switched = snapshot(page)
            state = request("/__fixture/state")
            assert state["started"] == before_switch + 1 and state["active"] == 1
            assert "Prepared sentence" in switched["text"] and "Live sentence" not in switched["history"], switched
            assert page.locator("video").evaluate("video=>video.duration") < 8.2
            assert not page.locator("#current").evaluate("element=>element.classList.contains('err')")
            result.update({"stale_hls_response_does_not_replace_new_file": True,
                           "old_media_request_cancelled": old_cancelled,
                           "switch_recognizers_started": 1, "switched_snapshot": switched})
            assert not errors, errors
        finally:
            for route in pending:
                try:
                    route.abort()
                except Error:
                    pass  # A stopped media loader may already have cancelled it.
            stop_if_active(page)
            result.update({"at_unix_seconds": time.time(), "page_errors": errors})
            (directory / "source-buffer-report.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
