"""Healthy rolling HLS keeps playing when recognition never becomes ready/fails."""
import argparse
import json
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import ROOT, launch_browser, open_scenario, request, snapshot, stop_if_active


def main(recovery_only=False):
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True
    results, errors, observations = [], [], []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(error.stack))
        try:
            if not recovery_only:
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

            for mode in (("recovery-live",) if recovery_only else ("live-auth", "recovery-live")):
                before = request("/__fixture/state")["started"]
                open_scenario(page, mode, fixture["live_url"])
                samples = []
                observation = {"mode": mode, "samples": samples}
                observations.append(observation)
                deadline = time.monotonic() + 25
                begun = time.monotonic()
                def terminal():
                    return (page.locator("#retryCaptions").is_visible()
                            and (mode != "recovery-live" or "已用完" in snapshot(page)["status"]))
                while not terminal() and time.monotonic() < deadline:
                    item = snapshot(page)
                    item.update(page.locator("video").evaluate("video=>({ready_state:video.readyState})"))
                    item["wall_seconds"] = time.monotonic() - begun
                    samples.append(item)
                    page.wait_for_timeout(50)
                assert terminal(), samples[-1:]
                page.wait_for_function("document.querySelector('video').readyState>=3", timeout=15000)
                page.wait_for_timeout(500)
                first = snapshot(page)
                page.wait_for_timeout(3000)
                following = snapshot(page)
                observation["after_failure"] = [first, following]
                state = request("/__fixture/state")
                expected = 1 if mode == "live-auth" else 3
                assert state["started"] - before == expected, (mode, state["started"] - before)
                assert state["active"] == 0
                assert following["time"] - first["time"] >= 2.5, (mode, first, following)
                assert not following["paused"] and "部分缺失" in following["status"], following
                if mode == "recovery-live":
                    first_advance = next((index for index, item in enumerate(samples) if item["time"] > .3), None)
                    assert first_advance is not None, samples
                    advancing = samples[first_advance:]
                    assert len(advancing) >= 20, samples
                    backwards = sum(right["time"] < left["time"] - .15
                                    for left, right in zip(advancing, advancing[1:]))
                    assert backwards == 0, advancing
                    held = sum(right["wall_seconds"] - left["wall_seconds"]
                               for left, right in zip(samples, samples[1:])
                               if left["paused"] and left["ready_state"] >= 3)
                    assert held <= 2.15, (held, samples)
                results.append({"case": mode + "-media-continues", "passed": True,
                                "recognizers_started": expected, "snapshots": [first, following],
                                "recovery_samples": samples})
                stop_if_active(page)
            assert not errors, errors
        finally:
            stop_if_active(page)
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors,
                      "observations": observations}
            directory = ROOT / "logs" / "original-release-browser"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "live-failures-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--recovery-only", action="store_true")
    main(parser.parse_args().recovery_only)
