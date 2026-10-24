"""Healthy rolling HLS keeps playing when recognition never becomes ready/fails."""
import json
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import ROOT, launch_browser, open_scenario, request, snapshot, stop_if_active


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True
    results, errors = [], []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(error.stack))
        try:
            open_scenario(page, "live-no-caption", fixture["live_url"])
            page.wait_for_function("document.querySelector('video').readyState>=3", timeout=15000)
            begun = time.monotonic()
            page.wait_for_function("document.querySelector('video').currentTime>.3", timeout=3000)
            initial_wait = time.monotonic() - begun
            assert initial_wait <= 2.5, initial_wait
            page.wait_for_timeout(2000)
            first = snapshot(page)
            page.wait_for_timeout(2000)
            following = snapshot(page)
            assert following["time"] - first["time"] >= 1.7, (first, following)
            assert "部分缺失" in following["status"], following
            results.append({"case": "no-first-caption-bounded-startup", "passed": True,
                            "ready_media_to_play_seconds": round(initial_wait, 3),
                            "snapshots": [first, following]})
            stop_if_active(page)

            for mode in ("live-auth", "recovery-live"):
                before = request("/__fixture/state")["started"]
                open_scenario(page, mode, fixture["live_url"])
                page.locator("#retryCaptions").wait_for(state="visible", timeout=25000)
                page.wait_for_function("document.querySelector('video').readyState>=3", timeout=15000)
                page.wait_for_timeout(500)
                first = snapshot(page)
                page.wait_for_timeout(3000)
                following = snapshot(page)
                state = request("/__fixture/state")
                expected = 1 if mode == "live-auth" else 3
                assert state["started"] - before == expected, (mode, state["started"] - before)
                assert state["active"] == 0
                assert following["time"] - first["time"] >= 2.5, (mode, first, following)
                assert not following["paused"] and "部分缺失" in following["status"], following
                results.append({"case": mode + "-media-continues", "passed": True,
                                "recognizers_started": expected, "snapshots": [first, following]})
                stop_if_active(page)
            assert not errors, errors
        finally:
            stop_if_active(page)
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors}
            directory = ROOT / "logs" / "original-release-browser"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "live-failures-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
