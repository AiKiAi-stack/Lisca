"""Independent zero-ASR local-browser controls for the public acoustic CLI."""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import wave

import numpy as np
from playwright.sync_api import sync_playwright

from browser_acoustic_capture import (
    capture_visible_screenshot, install_audio_capture, save_audio_capture,
)


RATE = 48000
CASES = {"pause": (0.0, 0.0, 0), "lag": (0.0, 0.75, 1),
         "skip-50ms": (0.05, 0.0, 1), "replay-50ms": (-0.05, 0.0, 1)}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def prepare(root):
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError("prepare requires an empty evidence directory")
    rng = np.random.default_rng(70029)
    source = np.zeros(50 * RATE, dtype="<i2")
    items, spans = [], []
    for index in range(50):
        t = np.arange(round(0.6 * RATE)) / RATE
        notes = rng.integers(140, 5500, 12)
        frequencies = notes[np.minimum((t / 0.05).astype(int), 11)]
        phase = np.cumsum(frequencies) * (2 * np.pi / RATE)
        signal = (8500 * np.sin(phase) + 2100 * np.sin(phase * 1.7)
                  + rng.integers(-600, 601, len(t)))
        envelope = np.minimum(1, np.minimum(t * 200, (0.6 - t) * 200))
        start = index * RATE + RATE // 5
        source[start:start + len(t)] = np.rint(signal * envelope).astype("<i2")
        items.append({"reference": f"sentence {index + 1:02d}",
                      "asset_start_seconds": index, "asset_end_seconds": index + 1})
        spans.append({"item": index + 1, "spans": [{
            "start_seconds": index + 0.2, "end_seconds": index + 0.8,
            "start_uncertainty_seconds": 1 / RATE, "end_uncertainty_seconds": 1 / RATE}]})
    # wave.open in write mode returns a writer.
    # pylint: disable=no-member
    with wave.open(str(root / "source.wav"), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(source.tobytes())
    # pylint: enable=no-member
    source_hash = digest(root / "source.wav")
    write_json(root / "reference.json", {"schema_version": 1, "language": "en",
        "dataset": "independent-browser-pcm-calibration", "sha256": source_hash,
        "seconds": 50, "asset_sample_rate_hz": RATE, "items": items})
    write_json(root / "voice-spans.json", {"schema_version": 1, "source_sha256": source_hash,
        "source_condition": "synthetic_tone_fixture", "provenance": "independent integer source truth",
        "method": {"name": "seeded-source-construction", "parameters": "seed=70029; sample-exact"},
        "items": spans})
    write_json(root / "truth-control-manifest.json", {
        "schema_version": 1, "frozen_before_capture": True, "seed": 70029,
        "sample_rate_hz": RATE, "pause_target_seconds": 25.5, "pause_duration_seconds": 0.6,
        "caption_onset_lead_seconds": 0.08, "caption_end_tail_seconds": 0.08,
        "source_sha256": source_hash, "reference_sha256": digest(root / "reference.json"),
        "voice_spans_sha256": digest(root / "voice-spans.json"),
        "fixture_oracle_sha256": digest(__file__), "items": [
            {"item": i + 1, "reference": item["reference"],
             "segment": "before" if i < 25 else "crossing" if i == 25 else "after"}
            for i, item in enumerate(items)],
        "cases": {name: {"commanded_source_delta_seconds": delta,
                         "caption_lag_seconds": lag, "expected_cli_exit": exit_code}
                  for name, (delta, lag, exit_code) in CASES.items()},
        "oracle_policy": {"method": "integer_sample_equality_no_scorer_or_frame_metadata",
            "maximum_quantization_lsb": 1, "edge_exclusion_seconds_each": 0.008,
            "verified_trace_seconds_each": 0.16, "source_gap_bound_seconds_each": 0.016,
            "resolution_seconds": 0.05,
            "rule": "require unique complete traces immediately outside fixed edge bands; "
                    "unknown on mismatch; actual gap bounds independent of DOM command targets"},
        "qualification": "CLI gap interval must contain independent actual-gap bounds; "
                         "width <50ms; skip/replay bounds and certificates must exclude zero",
    })


def integer_oracle(source_path, capture_path):
    """Infer source addresses by integer equality, without scorer/action/clock data."""
    def read(path):
        with wave.open(str(path), "rb") as stream:
            if stream.getframerate() != RATE or stream.getnchannels() != 1:
                raise ValueError("oracle_requires_mono_48khz")
            return np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(np.int32)
    source, captured = read(source_path), read(capture_path)
    changes = np.diff(np.concatenate(([False], captured == 0, [False])).astype(np.int8))
    starts, ends = np.flatnonzero(changes == 1), np.flatnonzero(changes == -1)
    longest = int(np.argmax(ends - starts))
    begin, end = int(starts[longest]), int(ends[longest])
    if not 0.3 <= (end - begin) / RATE <= 0.9:
        return {"status": "unknown", "reason": "independent_silence_run_not_unique_pause"}
    edge, size = round(0.008 * RATE), round(0.16 * RATE)
    addresses = []
    for start in (begin - edge - size, end + edge):
        observed = captured[start:start + size]
        if len(observed) != size or np.max(np.abs(observed)) < 100:
            return {"status": "unknown", "reason": "independent_trace_not_voiced"}
        # Filter exact integer candidates first, then verify the entire fixed trace.
        candidates = np.flatnonzero(np.abs(source - observed[0]) <= 1)
        matches = [int(index) for index in candidates if index + size <= len(source)
                   and np.all(np.abs(source[index:index + size] - observed) <= 1)]
        if len(matches) != 1:
            return {"status": "unknown", "reason": "independent_integer_trace_not_unique",
                    "capture_trace_start_sample": start, "match_count": len(matches)}
        addresses.append({"capture_sample": start, "source_sample": matches[0],
                          "verified_sample_count": size})
    left, right = addresses
    gap = ((right["source_sample"] - right["capture_sample"])
           - (left["source_sample"] - left["capture_sample"]) + end - begin) / RATE
    return {"status": "qualified", "actual_gap_interval_seconds": [gap - 0.016, gap + 0.016],
            "actual_gap_point_seconds": gap, "independent_source_addresses": addresses,
            "silence_run_samples": [begin, end], "edge_exclusion_samples_each": edge,
            "commanded_delta_used_in_oracle": False, "scorer_or_frames_used_in_oracle": False}


PAGE = """<!doctype html><style>
body{margin:32px;font-family:Arial;background:#fff;color:#111}
#current{font-size:30px;width:500px;height:48px;margin-top:100px}
</style><button id=start>Start</button><input id=url>
<video id=player muted></video><audio id=audioEl src=/source.wav></audio><div id=current></div>
<script>
const media = document.getElementById('audioEl'), caption = document.getElementById('current');
window.fixture = {done:false,samples:[],controls:[]};
window.__realAcceptanceSampler = {started:performance.now()};
document.getElementById('start').onclick = async () => {
  window.__realAcceptanceSampler.started = performance.now();
  let pausedOnce = false;
  const sample = () => {
    const time = media.currentTime, shifted = time - CONFIG.lag;
    const index = Math.floor(shifted), phase = shifted - index;
    const text = index >= 0 && index < 50 && phase >= .12 && phase < .88
      ? 'sentence ' + String(index+1).padStart(2,'0') : '';
    caption.textContent = text; caption.style.display = text ? 'block' : 'none';
    fixture.samples.push({observed_after_ms:performance.now()-__realAcceptanceSampler.started,
      media_time:time,visible_text:text,visible:!!text,paused:media.paused,
      seeking:media.seeking,buffering:media.readyState<3});
    if (!pausedOnce && time >= 25.5) {
      pausedOnce = true; const before = media.currentTime; media.pause();
      fixture.controls.push({action:'pause',commanded_at_media_time:before});
      setTimeout(async () => {
        if (CONFIG.delta) {
          const target = media.currentTime + CONFIG.delta;
          const settled = new Promise(resolve => media.addEventListener('seeked', resolve,{once:true}));
          media.currentTime = target; await settled;
          fixture.controls.push({action:'seek',commanded_target:target,settled_media_time:media.currentTime});
        }
        await media.play();
      }, 600);
    }
    if (media.ended) { clearInterval(timer); fixture.done = true; }
  };
  const timer = setInterval(sample,40);
  await new Promise(resolve => setTimeout(resolve,100)); await media.play();
};
</script>"""


def run(root, name, ocr_executable, tessdata_dir):
    manifest = json.loads((root / "truth-control-manifest.json").read_text(encoding="utf-8"))
    for field, path in (("source_sha256", root / "source.wav"),
                        ("reference_sha256", root / "reference.json"),
                        ("voice_spans_sha256", root / "voice-spans.json"),
                        ("fixture_oracle_sha256", Path(__file__))):
        if digest(path) != manifest[field]:
            raise ValueError("frozen truth manifest changed: " + field)
    workdir = Path(__file__).resolve().parents[2]
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=workdir, text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=workdir, text=True).strip():
        raise ValueError("real browser controls require a clean committed candidate")
    directory = root / name
    directory.mkdir()
    delta, lag, _expected = CASES[name]
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):  # pylint: disable=invalid-name
            requests.append({"path": self.path, "range": self.headers.get("Range")})
            if self.path == "/":
                page = PAGE.replace("<script>", "<script>const CONFIG="
                                    + json.dumps({"delta": delta, "lag": lag}) + ";", 1)
                payload = page.encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            elif self.path == "/source.wav":
                payload = (root / "source.wav").read_bytes()
                begin, end = 0, len(payload) - 1
                byte_range = self.headers.get("Range")
                if byte_range:
                    first, last = byte_range.removeprefix("bytes=").split("-", 1)
                    begin, end = int(first or 0), int(last or end)
                self.send_response(206 if byte_range else 200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Accept-Ranges", "bytes")
                if byte_range:
                    self.send_header("Content-Range", f"bytes {begin}-{end}/{len(payload)}")
                self.send_header("Content-Length", str(end - begin + 1))
                self.end_headers()
                self.wfile.write(payload[begin:end + 1])
            else:
                self.send_error(404)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path="/usr/bin/google-chrome",
                headless=True, args=["--autoplay-policy=no-user-gesture-required", "--no-sandbox"])
            page = browser.new_page(viewport={"width": 800, "height": 400}, device_scale_factor=1,
                                    accept_downloads=True)
            origin = f"http://127.0.0.1:{server.server_port}"
            page.route("**/*", lambda route: route.continue_()
                       if route.request.url.startswith(origin + "/") else route.abort())
            page.goto(origin)
            installation = install_audio_capture(page)
            if installation.get("status") != "installed":
                raise ValueError("browser capture unavailable: " + json.dumps(installation))
            page.click("#start")
            screens, previous = [], ("", False)
            deadline = time.monotonic() + 65
            while time.monotonic() < deadline:
                state = page.evaluate("() => ({done:fixture.done,text:current.innerText,"
                                      "visible:!!current.innerText})")
                key = state["text"], state["visible"]
                if key != previous:
                    screen = capture_visible_screenshot(page, directory / "screens", directory, len(screens))
                    if screen:
                        screens.append(screen)
                        previous = key
                if state["done"]:
                    break
                page.wait_for_timeout(15)
            fixture = page.evaluate("() => fixture")
            audio = save_audio_capture(page, directory / "browser-playback.webm", directory)
            chrome_version = browser.version
            browser.close()
    finally:
        server.shutdown()
    samples = fixture["samples"]
    max_gap = max(b["observed_after_ms"] - a["observed_after_ms"] for a, b in zip(samples, samples[1:]))
    identity = {"recognition_mode": "none", "simulated_recognition": True,
        "session_post_start_count": 0, "completion_signal": "media_ended",
        "events": [{"type": "media_ended", "reason": "completed"}] if fixture["done"] else [],
        "recording_status": "completed" if fixture["done"] else "incomplete", "errors": []}
    write_json(directory / "recording.json", {**identity, "source_kind": "local_file",
        "input_sha256": manifest["source_sha256"], "input_sha256_after": digest(root / "source.wav"),
        "input_unchanged": digest(root / "source.wav") == manifest["source_sha256"]})
    write_json(directory / "browser-report.json", {**identity, "language": "en",
        "source_verified": True, "candidate_revision": revision, "candidate_worktree_dirty": False,
        "truth_manifest_sha256": digest(root / "truth-control-manifest.json"),
        "capture_tool_sha256": digest(workdir / "tests/acceptance/browser_acoustic_capture.py"),
        "scorer_sha256": digest(workdir / "tests/acceptance/score_acoustic_acceptance.py"),
        "fixture_oracle_sha256": digest(__file__), "chrome_version": chrome_version,
        "installation": installation, "controls": fixture["controls"], "requests": requests,
        "sampling": {"status": "valid" if max_gap <= 100 else "invalid",
                     "max_observed_gap_ms": max_gap, "sample_count": len(samples)},
        "timeline_samples": samples, "acoustic_capture": {
            "mode": "browser_web_audio_media_element", "audio": audio,
            "screenshots": screens, "physical_output_latency": "unknown"}})
    oracle = integer_oracle(root / "source.wav", directory / audio["pcm_capture"]["path"])
    write_json(directory / "independent-oracle.json", oracle)
    command = [sys.executable, str(workdir / "tests/acceptance/score_acoustic_acceptance.py"), "--calibration",
        "--source", str(root / "source.wav"), "--reference", str(root / "reference.json"),
        "--voice-spans", str(root / "voice-spans.json"), "--recording", str(directory / "recording.json"),
        "--report", str(directory / "browser-report.json"), "--language", "en",
        "--output", str(directory / "score.json"), "--ocr-executable", str(ocr_executable),
        "--tessdata-dir", str(tessdata_dir)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    (directory / "cli.stdout.txt").write_text(completed.stdout, encoding="utf-8")
    (directory / "cli.stderr.txt").write_text(completed.stderr, encoding="utf-8")
    result = json.loads((directory / "score.json").read_text(encoding="utf-8"))
    certificates = result.get("audio_alignment", {}).get("pause_certificates", [])
    interval = certificates[0].get("source_gap_interval_seconds") if len(certificates) == 1 else None
    truth = oracle.get("actual_gap_interval_seconds")
    covered = bool(interval and truth and interval[0] <= truth[0] <= truth[1] <= interval[1])
    resolved = bool(interval and interval[1] - interval[0] < 0.05
                    and (delta == 0 or interval[1] < 0 or interval[0] > 0)
                    and (delta == 0 or truth and (truth[1] < 0 or truth[0] > 0)))
    qualified = (oracle["status"] == "qualified" and covered and resolved
                 and completed.returncode == manifest["cases"][name]["expected_cli_exit"])
    write_json(directory / "execution.json", {"command": command, "exit_code": completed.returncode,
        "candidate_revision": revision, "oracle_status": oracle["status"],
        "expected_exit": manifest["cases"][name]["expected_cli_exit"],
        "independent_truth_covered": covered, "resolution_demonstrated": resolved,
        "capture_calibration_qualified": qualified, "release_acceptance_assessed": False})
    print(json.dumps({"case": name, "candidate": revision, "exit": completed.returncode,
                      "oracle": oracle, "qualified": qualified, "evidence": str(directory)}), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--ocr-executable", type=Path)
    parser.add_argument("--tessdata-dir", type=Path)
    args = parser.parse_args()
    if args.prepare_only:
        prepare(args.output_root.resolve())
    elif args.case and args.ocr_executable and args.tessdata_dir:
        run(args.output_root.resolve(), args.case, args.ocr_executable, args.tessdata_dir)
    else:
        parser.error("run requires --case, --ocr-executable and --tessdata-dir")


if __name__ == "__main__":
    main()
