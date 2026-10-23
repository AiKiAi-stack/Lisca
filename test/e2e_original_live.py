"""Measure real rolling-HLS playback with prescribed, explicitly simulated ASR."""
import argparse
import json
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import BASE, ROOT, configure, request, snapshot, stop_if_active


def main(mode, seconds):
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True and fixture.get("live_url")
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    samples, messages, errors = [], [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome",
            headless=True, args=["--autoplay-policy=no-user-gesture-required"])
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        def observe_socket(socket):
            def received(payload):
                event = json.loads(payload)
                if event.get("type") in ("subtitle", "caption_remove", "timeline", "ready", "error", "end"):
                    messages.append({"observed_at": time.monotonic(), **event})
            socket.on("framereceived", received)

        page.on("websocket", observe_socket)
        result = {"mode": mode, "passed": False, "simulated_recognition": True,
                  "real_accuracy_tested": False}
        try:
            configure(page)
            request("/__fixture/scenario", {"mode": mode})
            page.locator("video").evaluate("""video => {
                const observation = window.__testMediaObservation = {pauses:[], waiting:[], frames:[]};
                for (const name of ['pause','waiting']) video.addEventListener(name, () => {
                    observation[name==='pause'?'pauses':'waiting'].push({at:performance.now(),
                        time:video.currentTime,status:document.querySelector('#status').innerText});
                });
                function frame(_now, metadata) {
                    observation.frames.push(metadata.mediaTime);
                    video.requestVideoFrameCallback(frame);
                }
                video.requestVideoFrameCallback(frame);
            }""")
            page.locator("#url").fill(fixture["live_url"])
            page.locator("#start").click()
            page.wait_for_function("document.querySelector('video').currentTime>.3", timeout=15000)
            begun = time.monotonic()
            while time.monotonic() - begun < seconds:
                sample = snapshot(page)
                sample.update(page.locator("video").evaluate("video=>({ready_state:video.readyState,ended:video.ended})"))
                sample["wall_seconds"] = time.monotonic() - begun
                samples.append(sample)
                page.wait_for_timeout(50)
            observed = page.evaluate("window.__testMediaObservation")
            application_wait = sum(
                following["wall_seconds"] - current["wall_seconds"]
                for current, following in zip(samples, samples[1:])
                if current["paused"] and current["ready_state"] >= 3
                and "等待" in current["status"] and "字幕" in current["status"])
            frames = observed["frames"]
            result.update({"application_wait_seconds": round(application_wait, 3),
                           "media_advance_seconds": round(samples[-1]["time"]-samples[0]["time"], 3),
                           "source_wait_events": len(observed["waiting"]),
                           "pause_events": observed["pauses"],
                           "backward_frames": sum(b < a - .15 for a, b in zip(frames, frames[1:])),
                           "frame_count": len(frames), "page_errors": errors})
            assert not errors and result["backward_frames"] == 0, result
            assert result["frame_count"] > 60, result
            if mode == "live-gap":
                assert any("部分缺失" in sample["status"] for sample in samples), result
                assert application_wait <= 2.15, result
                assert result["media_advance_seconds"] >= seconds - 3, result
                for sample in samples:
                    assert not (sample["time"] > 6 and "Late sentence" in sample["text"]), sample
            else:
                visible = [sample for sample in samples if sample["text"].startswith("Live sentence")]
                assert len(visible) >= 20, result
            result["passed"] = True
            page.screenshot(path=str(directory / (mode + ".png")))
        finally:
            stop_if_active(page)
            result.update({"at_unix_seconds": time.time(), "samples": samples,
                           "recognition_messages": messages, "page_errors": errors})
            (directory / (mode + "-report.json")).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({key:value for key,value in result.items()
                              if key not in ("samples", "recognition_messages")}, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("live-normal", "live-gap"), default="live-gap")
    parser.add_argument("--seconds", type=float, default=20)
    args = parser.parse_args()
    main(args.mode, args.seconds)
