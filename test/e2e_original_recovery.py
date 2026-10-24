"""Observe bounded recovery and cancellation through real browser/API actions."""
import json
import re
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import launch_browser, ROOT, open_scenario, request, snapshot, stop_if_active


def wait_state(page, predicate, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = request("/__fixture/state")
        if predicate(state):
            return state
        page.wait_for_timeout(100)
    raise AssertionError("Fixture counters did not reach the expected public action outcome")


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    results, errors = [], []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(error.stack))
        try:
            before = request("/__fixture/state")["started"]
            open_scenario(page, "recovery-network", fixture["media"]["controls"])
            wait_state(page, lambda state: state["started"] == before + 3)
            exhausted = wait_state(page, lambda state: state["active"] == 0)
            page.wait_for_timeout(2500)
            assert request("/__fixture/state")["started"] == before + 3
            engines = exhausted["engines"][-3:]
            waits = [following["created_at"] - current["closed_at"]
                     for current, following in zip(engines, engines[1:])]
            assert waits[0] >= .85 and waits[1] >= 1.85, waits
            assert page.locator("#retryCaptions").is_visible()
            results.append({"case": "recognition-recovery-two-attempts", "passed": True,
                            "recognizers_started": 3, "observed_backoffs_seconds": waits,
                            "status": snapshot(page)["status"]})
            stop_if_active(page)

            for action in ("stop", "playOnly", "delete-config"):
                before = request("/__fixture/state")["started"]
                open_scenario(page, "recovery-network", fixture["media"]["controls"])
                page.wait_for_function("/恢复.*1\\/2/.test(document.querySelector('#status').innerText)", timeout=10000)
                if action == "delete-config":
                    page.locator("#settingsBtn").click()
                    page.locator("#clearCloud").click()
                    page.locator("#cloudState").filter(has_text="尚未配置").wait_for()
                    page.get_by_role("button", name="关闭设置", exact=True).click()
                else:
                    assert page.locator("#" + action).is_visible(), {"action": action, "snapshot": snapshot(page)}
                    page.locator("#" + action).click()
                stopped = wait_state(page, lambda state: state["active"] == 0)
                page.wait_for_timeout(3000)
                after = request("/__fixture/state")
                assert after["started"] == before + 1, (action, after["started"] - before)
                assert after["audio_seconds"] == stopped["audio_seconds"]
                if action == "playOnly":
                    assert not snapshot(page)["paused"]
                results.append({"case": "cancel-backoff-" + action, "passed": True,
                                "new_recognition_requests": 0, "new_audio_seconds": 0})
                stop_if_active(page)

            before = request("/__fixture/state")["started"]
            open_scenario(page, "recovery-auth", fixture["media"]["normal"])
            wait_state(page, lambda state: state["started"] == before + 1 and state["active"] == 0)
            page.wait_for_timeout(3000)
            assert request("/__fixture/state")["started"] == before + 1
            assert "认证" in snapshot(page)["status"]
            results.append({"case": "authentication-needs-user-action", "passed": True,
                            "automatic_retries": 0})
            stop_if_active(page)

            # A browser HLS failure uses the first shared attempt. A subsequent
            # ASR network failure may consume only the remaining second attempt.
            failed_fragments = []

            def fail_one_fragment(route):
                if not failed_fragments:
                    failed_fragments.append(time.monotonic())
                    route.fulfill(status=503, body="offline fixture source unavailable")
                else:
                    route.continue_()

            pattern = re.compile(r"/video\?url=.*seg00\.ts(?:&|$)")
            page.route(pattern, fail_one_fragment)
            before = request("/__fixture/state")["started"]
            open_scenario(page, "recovery-live", fixture["live_url"])
            wait_state(page, lambda state: state["started"] >= before + 2, timeout=20)
            wait_state(page, lambda state: state["active"] == 0, timeout=15)
            page.wait_for_timeout(3000)
            mixed = request("/__fixture/state")
            page.locator("#settingsBtn").click()
            page.get_by_role("tab", name="更多", exact=True).click()
            page.locator("#logBtn").click()
            diagnostics = page.locator("#logText").input_value()
            assert len(failed_fragments) == 1
            assert mixed["started"] == before + 2, mixed["started"] - before
            assert "fragLoadError" in diagnostics, diagnostics
            assert "recovery_wait: media 1/2" in diagnostics, diagnostics
            assert "recovery_wait: recognition 2/2" in diagnostics, diagnostics
            results.append({"case": "media-and-recognition-share-two-attempts", "passed": True,
                            "injected_media_failures": 1, "recognizers_started": 2,
                            "status": snapshot(page)["status"], "public_diagnostics": diagnostics})
            page.unroute(pattern, fail_one_fragment)
            assert not errors, errors
        finally:
            stop_if_active(page)
            page.locator("video").evaluate("video=>video.pause()")
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors}
            (directory / "recovery-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
