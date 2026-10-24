"""Delay real session responses to exercise cancellation and ownership races."""
import json
import time

from playwright.sync_api import Error, sync_playwright

from e2e_original_release import BASE, ROOT, configure, request, snapshot, stop_if_active


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    results, errors = [], []
    pending = {}
    control = {"hold_next": True}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome",
            headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def hold_response(route):
            if control["hold_next"]:
                control["hold_next"] = False
                pending["response"] = route.fetch()
                pending["route"] = route
            else:
                route.continue_()

        def wait_pending():
            deadline = time.monotonic() + 10
            while "route" not in pending and time.monotonic() < deadline:
                page.wait_for_timeout(50)
            assert "route" in pending, "Session response was not intercepted"

        def release():
            route, response = pending.pop("route"), pending.pop("response")
            route.fulfill(response=response)

        page.route("**/api/cloud/sessions", hold_response)
        try:
            configure(page)
            request("/__fixture/scenario", {"mode": "controls"})
            count = request("/__fixture/state")["started"]
            page.locator("#url").fill(fixture["media"]["normal"])
            page.locator("#start").click()
            wait_pending()
            # Both requests use real server responses; only their browser
            # delivery order changes, as can happen on a slow network.
            page.locator("#file").set_input_files(fixture["media"]["long"])
            page.wait_for_function("""() => document.querySelector('video').currentTime>.3 &&
                document.querySelector('#current').innerText.includes('Prepared sentence')""", timeout=15000)
            release()
            page.wait_for_timeout(1000)
            state = request("/__fixture/state")
            assert state["started"] == count + 1 and state["active"] == 1
            duration = page.locator("video").evaluate("video=>video.duration")
            assert 12 <= duration < 12.2, duration
            results.append({"case": "late-created-session-cannot-replace-new-file", "passed": True,
                            "recognizers_started": 1, "active_recognizers": 1,
                            "snapshot": snapshot(page)})
            stop_if_active(page)
            page.wait_for_timeout(300)

            configure(page)
            control["hold_next"] = True
            count = request("/__fixture/state")["started"]
            page.locator("#url").fill(fixture["media"]["normal"])
            page.locator("#start").click()
            wait_pending()
            assert page.locator("#stop").is_visible(), "Stop must be available while a session request is pending"
            page.locator("#stop").click()
            release()
            page.wait_for_timeout(1500)
            state = request("/__fixture/state")
            assert state["started"] == count and state["active"] == 0
            assert "停止" in snapshot(page)["status"]
            results.append({"case": "stop-before-session-response", "passed": True,
                            "new_recognition_requests": 0})
            assert not errors, errors
        finally:
            if "route" in pending:
                try:
                    release()
                except Error:
                    pass
            stop_if_active(page)
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors}
            (directory / "lifecycle-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
