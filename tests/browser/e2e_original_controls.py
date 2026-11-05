"""Observe local pause/seek ownership through the real player and HTTP API."""
import json
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import (
    ROOT, launch_browser, open_scenario, request, snapshot, stop_if_active, wait_complete,
)


def hold(page, seconds):
    page.locator("video").evaluate("video=>video.pause()")
    page.wait_for_timeout(300)  # Allow the in-flight pause control to arrive.
    before = snapshot(page)
    counter = request("/__fixture/state")
    page.wait_for_timeout(seconds * 1000)
    after = snapshot(page)
    following = request("/__fixture/state")
    assert abs(after["time"] - before["time"]) < .05, (before, after)
    assert following["started"] == counter["started"]
    assert following["audio_seconds"] - counter["audio_seconds"] < .15
    return after


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True, "Offline fixture required"
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    results, dialogs, sessions = [], [], []
    decision = {"accept": False}
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))

        def handle_dialog(dialog):
            dialogs.append(dialog.message)
            if decision["accept"]:
                dialog.accept()
            else:
                dialog.dismiss()

        def handle_request(outgoing):
            if outgoing.method == "POST" and outgoing.url.endswith("/api/cloud/sessions"):
                data = outgoing.post_data_json
                sessions.append({"start_at": data.get("start_at"), "mode": data.get("mode")})

        page.on("dialog", handle_dialog)
        page.on("request", handle_request)
        try:
            open_scenario(page, "normal", fixture["media"]["normal"])
            wait_complete(page)
            count = request("/__fixture/state")["started"]
            for seconds in (3, 30):
                held = hold(page, seconds)
                page.locator("video").evaluate("video=>{video.currentTime=2.4;video.play()}")
                page.wait_for_function("document.querySelector('video').currentTime>2.6")
                assert request("/__fixture/state")["started"] == count
                results.append({"case": f"completed-pause-{seconds}s", "passed": True,
                                "snapshot": held, "new_recognition_requests": 0})
            page.locator("video").evaluate("video=>{video.currentTime=video.duration-.1;video.play()}")
            page.wait_for_function("document.querySelector('video').ended")
            assert not snapshot(page)["text"]
            page.locator("video").evaluate("video=>video.currentTime=.5")
            page.wait_for_function("document.querySelector('#current').innerText.includes('第一句')")
            assert request("/__fixture/state")["started"] == count
            results.append({"case": "eof-clear-and-cached-revisit", "passed": True})

            open_scenario(page, "controls", fixture["media"]["controls"])
            page.wait_for_function("""() => document.querySelector('video').currentTime>.3 &&
                document.querySelector('#current').innerText.includes('Prepared sentence 1.')""", timeout=15000)
            hold(page, 3)
            count = request("/__fixture/state")["started"]
            page.locator("video").evaluate("video=>video.currentTime=1.2")
            page.wait_for_timeout(800)
            assert not dialogs, dialogs
            assert request("/__fixture/state")["started"] == count
            assert "Prepared sentence 1." in snapshot(page)["text"]
            results.append({"case": "unfinished-prepared-seek", "passed": True,
                            "new_recognition_requests": 0})

            before = snapshot(page)
            page.locator("video").evaluate("video=>{video.currentTime=20;video.currentTime=21;video.currentTime=22}")
            page.wait_for_timeout(1200)
            dismissed = snapshot(page)
            assert len(dialogs) == 1, dialogs
            assert request("/__fixture/state")["started"] == count
            assert abs(dismissed["time"] - before["time"]) < .2, (before, dismissed)
            results.append({"case": "unfinished-outside-seek-dismiss", "passed": True,
                            "confirmation_count": 1, "new_recognition_requests": 0})

            decision["accept"] = True
            page.locator("video").evaluate("video=>video.currentTime=20")
            page.wait_for_function("document.querySelector('video').currentTime>=20.2", timeout=15000)
            following = request("/__fixture/state")
            assert following["started"] == count + 1, following
            assert following["active"] == 1, following
            assert len(dialogs) == 2, dialogs
            assert sessions[-1]["start_at"] == 20, sessions
            results.append({"case": "unfinished-outside-seek-accept", "passed": True,
                            "restart_position": sessions[-1]["start_at"], "active_recognizers": 1})

            long_pause = hold(page, 30)
            before_resume = request("/__fixture/state")["started"]
            page.locator("video").evaluate("video=>video.play()")
            page.wait_for_function("time=>document.querySelector('video').currentTime>time+.25",
                                   arg=long_pause["time"], timeout=15000)
            resumed = request("/__fixture/state")
            assert resumed["started"] == before_resume + 1, resumed
            assert abs(sessions[-1]["start_at"] - long_pause["time"]) < .2, sessions
            assert resumed["active"] == 1, resumed
            results.append({"case": "unfinished-long-pause-resume-position", "passed": True,
                            "pause_seconds": 30, "restart_position": sessions[-1]["start_at"]})

            request("/__fixture/scenario", {"mode": "normal"})
            before_switch = request("/__fixture/state")["started"]
            page.locator("#file").set_input_files(fixture["media"]["normal"])
            page.wait_for_function("""() => document.querySelector('video').currentTime>.3 &&
                document.querySelector('#current').innerText.includes('第一句')""", timeout=15000)
            switched = snapshot(page)
            assert "Prepared sentence" not in switched["history"], switched
            assert request("/__fixture/state")["started"] == before_switch + 1
            assert request("/__fixture/state")["active"] == 1
            results.append({"case": "upload-switch-isolates-previous-session", "passed": True,
                            "snapshot": switched})
            assert not errors, errors
        finally:
            stop_if_active(page)
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results,
                      "sessions": sessions, "page_errors": errors}
            (directory / "controls-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
