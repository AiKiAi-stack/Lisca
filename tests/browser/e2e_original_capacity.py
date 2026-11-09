"""Open/seek/cancel actual 30-minute generated media with offline recognition.

This is a short behavioral/resource observation on a long input. It does not
claim 30 minutes of uninterrupted operation or real speech-quality acceptance.
"""
import hashlib
import json
from pathlib import Path
import time

import av
from playwright.sync_api import sync_playwright

from e2e_original_release import launch_browser, ROOT, open_scenario, request, snapshot, stop_if_active


def resources(pid, media):
    directory = Path("/proc") / str(pid)
    status = (directory / "status").read_text()
    values = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    media_handles = 0
    for descriptor in (directory / "fd").iterdir():
        try:
            media_handles += descriptor.resolve() == media
        except FileNotFoundError:
            pass
    return {"rss_kib": int(values["VmRSS"].split()[0]),
            "threads": int(values["Threads"]), "media_handles": media_handles}


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True
    media = Path(fixture["media"]["normal"]).with_name("capacity.mp4")
    with av.open(str(media)) as container:
        duration = container.duration / av.time_base
    assert 1800 <= duration < 1801
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    result = {"simulated_recognition": True, "real_accuracy_tested": False,
              "continuous_30_minute_run": False, "passed": False,
              "input_sha256": hashlib.sha256(media.read_bytes()).hexdigest(),
              "duration_seconds": duration}
    initial = resources(fixture["process_id"], media)
    samples = [initial]
    result.update({"initial_resources": initial, "resource_samples": samples})
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("dialog", lambda dialog: dialog.accept())
        try:
            begun = time.monotonic()
            open_scenario(page, "controls", str(media))
            page.wait_for_function("""() => document.querySelector('video').currentTime>.3 &&
                document.querySelector('#current').innerText.includes('Prepared sentence')""", timeout=15000)
            result["preparation_seconds"] = round(time.monotonic() - begun, 3)
            assert request("/__fixture/state")["active"] == 1
            for _ in range(20):
                samples.append(resources(fixture["process_id"], media))
                page.wait_for_timeout(100)
            state = request("/__fixture/state")
            engine = state["lisca.engines"][-1]
            lead = max(item["through"] - (item["at"] - engine["created_at"])
                       for item in engine["feed_observations"])
            assert lead <= 2.1, lead
            assert engine["feed_observations"][-1]["through"] < 15
            assert "已生成" not in snapshot(page)["status"]
            result["maximum_observed_feed_lead_seconds"] = round(lead, 4)

            page.locator("video").evaluate("video=>video.currentTime=1700")
            page.wait_for_function("document.querySelector('video').currentTime>1700.2", timeout=15000)
            assert request("/__fixture/state")["active"] == 1
            samples.append(resources(fixture["process_id"], media))
            stop_if_active(page)
            page.wait_for_timeout(500)
            cancelled = request("/__fixture/state")
            assert cancelled["active"] == 0, cancelled["active"]
            page.wait_for_timeout(1500)
            after = request("/__fixture/state")
            assert after["started"] == cancelled["started"]
            assert after["audio_seconds"] == cancelled["audio_seconds"]
            released = resources(fixture["process_id"], media)
            samples.append(released)
            peak = max(sample["rss_kib"] for sample in samples)
            result.update({"released_resources": released, "peak_rss_kib": peak})
            assert released["media_handles"] == 0, released
            # Global HTTP executor/native thread pools may retain warm workers.
            # Record their counts rather than equating retention with a session leak.
            assert peak - initial["rss_kib"] < 256 * 1024, (initial, peak)
            assert not errors, errors
            result.update({"passed": True, "seek_position": 1700,
                           "new_requests_after_stop": 0, "new_audio_after_stop": 0,
                           "initial_resources": initial, "peak_rss_kib": peak,
                           "released_resources": released, "resource_samples": samples})
        finally:
            stop_if_active(page)
            result.update({"at_unix_seconds": time.time(), "page_errors": errors})
            (directory / "capacity-report.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
