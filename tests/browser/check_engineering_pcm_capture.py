"""Zero-ASR Chrome self-check on 8002 using only a replaced synthetic page."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "acceptance"))

from browser_pixel_capture import capture_pixel_observation, install_pixel_capture
from browser_pcm_capture import EngineeringPcmWriter, install_engineering_capture, save_engineering_capture
from e2e_original_release import BASE, launch_browser
from playwright.sync_api import sync_playwright


def check(output):
    output.mkdir(parents=True, exist_ok=False)
    result = {"scope": "synthetic_pcm_bridge_integrity_only", "paid_session_requests": 0,
              "physical_output_latency": "unknown", "output_clock_error_bound_ms": None,
              "source_revision": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "source_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                                for name in ("tests/acceptance/browser_pcm_capture.py",
                                             "tests/acceptance/browser_pcm_capture.js",
                                             "tests/acceptance/browser_acoustic_capture.py",
                                             "tests/acceptance/browser_pixel_capture.py",
                                             "tests/acceptance/run_real_browser_acceptance.py")}}
    writer = EngineeringPcmWriter(output / "synthetic.playback.f32le", output)
    saved = False
    try:
        with sync_playwright() as playwright:
            browser = launch_browser(playwright)
            result["browser_version"] = browser.version
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})

                def block_paid_request(route):
                    if route.request.method.upper() == "POST":
                        result["paid_session_requests"] += 1
                    route.abort()

                page.route("**/api/cloud/sessions**", block_paid_request)
                page.goto(BASE, wait_until="domcontentloaded")
                # Replace the actual app's #start element and its listeners before clicking.
                page.set_content('<input id="url" value="synthetic"><button id="start">Start</button>'
                                 '<div id="playerWrap" style="position:relative;width:800px;height:300px;background:#123">'
                                 '<audio id="audioEl"></audio>'
                                 '<div id="current" style="position:absolute;bottom:30px;left:50%;transform:translateX(-50%);'
                                 'width:max-content;max-width:90%;font:32px/40px sans-serif;padding:8px;'
                                 'color:white;background:black"></div></div>'
                                 '<style>#current:empty{display:none}</style>')
                page.evaluate("() => { window.__realAcceptanceSampler = {started: performance.now()}; }")
                installation = install_engineering_capture(page, writer, max_seconds=15)
                result["installation"] = installation
                assert installation["status"] == "installed"
                pixels = install_pixel_capture(page)
                result["pixel_capture"] = pixels

                def capture_pixels():
                    record = capture_pixel_observation(page, output / "pixels", output,
                                                       writer.pixel_count + 1, pixels)
                    record["context_id"] = writer.context_id
                    assert writer.receive(record)["status"] == "accepted"

                capture_pixels()
                rate = installation["sample_rate_hz"]
                frames = rate * 6 // 5
                signal = bytearray()
                for frame in range(frames):
                    level = 0 if frame < rate // 10 or frame >= frames - rate // 10 else (
                        1 if frame < rate * 3 // 10 else 1 + ((frame // 64) % 3))
                    signal.extend(struct.pack("<ff", .125 * level, -.25 * level))
                fmt = struct.pack("<HHIIHH", 3, 2, rate, rate * 8, 8, 32)
                riff = (b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt
                        + b"fact" + struct.pack("<II", 4, frames)
                        + b"data" + struct.pack("<I", len(signal)) + signal)
                source = b"RIFF" + struct.pack("<I", len(riff)) + riff
                (output / "synthetic-input.wav").write_bytes(source)
                result["input_sha256"] = hashlib.sha256(source).hexdigest()
                page.evaluate("""async encoded => {
                  const bytes = Uint8Array.from(atob(encoded), char => char.charCodeAt(0));
                  const media = document.getElementById('audioEl');
                  media.src = URL.createObjectURL(new Blob([bytes], {type: 'audio/wav'}));
                  await new Promise((resolve, reject) => {
                    media.addEventListener('canplaythrough', resolve, {once: true});
                    media.addEventListener('error', reject, {once: true});
                  });
                  document.getElementById('start').addEventListener('click', () => {
                    media.play();
                    const caption = document.getElementById('current');
                    setTimeout(() => {caption.textContent = 'First visible words';}, 100);
                    setTimeout(() => media.pause(), 350);
                    setTimeout(() => {caption.textContent = '';}, 450);
                    setTimeout(() => window.__engineeringCapture.context.suspend(), 500);
                    setTimeout(async () => {await window.__engineeringCapture.context.resume(); media.play();}, 650);
                    setTimeout(() => {caption.textContent = 'Second visible words';}, 800);
                    setTimeout(() => {caption.textContent = '';}, 1350);
                  });
                }""", base64.b64encode(source).decode())
                page.locator("#start").click()
                deadline = time.monotonic() + 2
                while time.monotonic() < deadline:
                    tick = time.monotonic()
                    capture_pixels()
                    page.wait_for_timeout(max(1, 100 - (time.monotonic() - tick) * 1000))
                assert page.evaluate("() => document.getElementById('audioEl').ended")
                capture_pixels()
                result["audio"] = save_engineering_capture(page, writer)
                saved = True
            finally:
                browser.close()

        audio = result["audio"]
        raw = (output / audio["path"]).read_bytes()
        ledger = [json.loads(line) for line in (output / audio["metadata_path"]).read_text().splitlines()]
        batches = [entry for entry in ledger if entry["kind"] == "pcm_batch"]
        assert audio["status"] == "captured", audio.get("integrity_errors")
        assert audio["channel_count"] == 2
        assert audio["byte_count"] == len(raw) == audio["frame_count"] * 8
        assert hashlib.sha256(raw).hexdigest() == audio["sha256"]
        expected_frame = audio["first_frame"]
        expected_offset = 0
        for sequence, batch in enumerate(batches):
            assert batch["sequence"] == sequence and batch["byte_offset"] == expected_offset
            for quantum in batch["quanta"]:
                assert quantum["current_frame"] == expected_frame
                expected_frame += quantum["frame_count"]
            piece = raw[expected_offset:expected_offset + batch["byte_count"]]
            assert hashlib.sha256(piece).hexdigest() == batch["sha256"]
            expected_offset += batch["byte_count"]
        assert expected_frame == audio["end_frame"] and expected_offset == len(raw)
        samples = list(struct.iter_unpack("<ff", raw))
        onset = next(index for index, pair in enumerate(samples) if pair != (0.0, 0.0))
        assert onset >= rate // 10
        assert samples[onset:onset + 1024] == [(0.125, -0.25)] * 1024
        assert all(pair in ((0.0, 0.0), (0.125, -0.25), (0.25, -0.5), (0.375, -0.75))
                   for pair in samples)
        assert audio["peak_queued_batches"] <= installation["max_queued_batches"]
        states = [entry for entry in ledger if entry["kind"] == "context_state"]
        assert [entry["state"] for entry in states].count("suspended") >= 2
        clocks = [entry for entry in ledger if entry["kind"] == "clock_sample" and entry["usable"]]
        assert len({entry["clock_segment"] for entry in clocks}) >= 2
        media_states = [entry for entry in ledger if entry["kind"] == "media_state"]
        assert any(entry["event"] == "pause" and entry["state"] == "running" for entry in media_states)
        observations = [entry for entry in ledger if entry["kind"] == "pixel_observation"]
        assert len(observations) >= 10 and result["paid_session_requests"] == 0
        assert {entry["visible_text"] for entry in observations if entry["dom_state_stable"]} == {
            "", "First visible words", "Second visible words"}
        for entry in observations:
            assert entry["roi_bounds"] == pixels["fixed_roi"]
            assert hashlib.sha256((output / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]
            assert entry["before_after_ms"] <= entry["observed_after_ms"] <= entry["after_after_ms"]
        result["pixel_capture"].update(status="captured", observation_count=len(observations),
                                      metadata_path=audio["metadata_path"], metadata_sha256=audio["metadata_sha256"])
        result.update(status="passed", onset_file_frame=onset,
                      onset_context_frame=audio["first_frame"] + onset,
                      exact_stereo_sample_pairs=True, usable_clock_sample_count=len(clocks),
                      context_states=[entry["state"] for entry in states])
    except Exception as error:
        result.update(status="failed", error_type=type(error).__name__, error=str(error), traceback=traceback.format_exc())
    finally:
        if not saved:
            result["audio"] = writer.finish({"status": "incomplete_self_check"})
        (output / "self-check.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.output.expanduser().resolve())
    print(json.dumps({"status": result["status"], "report": str(args.output / "self-check.json"),
                      "paid_session_requests": result["paid_session_requests"]}))
    raise SystemExit(0 if result["status"] == "passed" else 1)
