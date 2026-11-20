"""Exercise the complete app's unloaded-media Start flow with browser-only routes.

Only static app resources reach the existing 8002 service. All API, media and
WebSocket traffic is simulated before networking; no credentials are read.
"""
import argparse
from contextlib import nullcontext
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import time
from urllib.parse import urlsplit

import av
import numpy as np
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests" / "acceptance"))
import run_real_browser_acceptance as collector
from browser_pixel_capture import PIXEL_STATE
from e2e_original_release import BASE, launch_browser
from original_release_hls import generate_hls


STATIC = {"/": "index.html", **{f"/{name}": name for name in (
    "ui-components.css", "player.css", "i18n.js", "hls.min.js", "caption_presenter.js",
    "playback_guard.js", "recovery_controller.js", "source_library.js", "icons.svg", "favicon.svg")}}


def make_media(directory):
    directory.mkdir()
    rate, seconds = 48000, 3
    signal = b"".join(struct.pack("<f", .125 if rate // 5 <= frame < rate * 14 // 5 else 0)
                      for frame in range(rate * seconds))
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    riff = b"WAVEfmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(signal)) + signal
    mono = directory / "mono.wav"
    mono.write_bytes(b"RIFF" + struct.pack("<I", len(riff)) + riff)
    stereo = directory / "stereo.mp4"
    with av.open(str(stereo), "w") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
        video.codec_context.thread_count = 1
        video.options = {"preset": "ultrafast"}
        audio = output.add_stream("aac", rate=rate)
        audio.layout = "stereo"
        for index in range(seconds * 10):
            frame = av.VideoFrame.from_ndarray(np.full((180, 320, 3), (25, 70, 90), np.uint8), format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame):
                output.mux(packet)
            t = (np.arange(rate // 10) + index * rate // 10) / rate
            samples = np.array([.12 * np.sin(2 * np.pi * 440 * t) + .025 * np.sin(2 * np.pi * 937 * t),
                                .08 * np.sin(2 * np.pi * 660 * t) + .02 * np.sin(2 * np.pi * 1351 * t)], np.float32)
            frame = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
            frame.sample_rate = rate
            frame.pts, frame.time_base = index * rate // 10, Fraction(1, rate)
            for packet in audio.encode(frame):
                output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None):
                output.mux(packet)
    hls = directory / "hls"
    hls.mkdir()
    generate_hls(hls, seconds=4)
    return mono, stereo, hls


def pcm_checks(directory, audio, case, media):
    result = {"ledger_integrity": False, "all_destination_channels": False, "speaker_mix_integrity": False}
    if not audio.get("path") or not audio.get("metadata_path"):
        return result
    raw = (directory / audio["path"]).read_bytes()
    records = [json.loads(line) for line in (directory / audio["metadata_path"]).read_text().splitlines()]
    batches = [entry for entry in records if entry["kind"] == "pcm_batch"]
    offset, frame = 0, audio.get("first_frame")
    valid = bool(batches) and hashlib.sha256(raw).hexdigest() == audio.get("sha256")
    for sequence, batch in enumerate(batches):
        valid &= batch["sequence"] == sequence and batch["byte_offset"] == offset
        piece = raw[offset:offset + batch["byte_count"]]
        valid &= hashlib.sha256(piece).hexdigest() == batch["sha256"]
        for quantum in batch["quanta"]:
            valid &= quantum["current_frame"] == frame
            frame = quantum["current_frame"] + quantum["frame_count"]
        offset += batch["byte_count"]
    result["ledger_integrity"] = bool(valid and frame == audio.get("end_frame") and offset == len(raw))
    result["all_destination_channels"] = audio.get("channel_count") == audio.get("destination_channel_count") == 2
    if not raw or audio.get("channel_count") != 2:
        return result
    samples = np.frombuffer(raw, dtype="<f4").reshape(-1, 2)
    if case == "mono":
        result["speaker_mix_integrity"] = bool(np.array_equal(samples[:, 0], samples[:, 1])
            and np.isin(samples, [0, .125]).all() and (samples[:, 0] == .125).sum() >= 48000)
        result["speaker_mix_method"] = "exact_float32_mono_duplicated_at_unity_gain"
    elif case == "hls":
        result["speaker_mix_integrity"] = bool(np.array_equal(samples[:, 0], samples[:, 1])
                                               and np.max(np.abs(samples)) > .01)
        result["speaker_mix_method"] = "decoded_mono_equal_in_both_destination_channels"
    else:
        with av.open(str(media)) as source:
            reference = np.concatenate([frame.to_ndarray() for frame in source.decode(audio=0)], axis=1).T
        signal = np.flatnonzero(np.max(np.abs(samples), axis=1) > 1e-4)
        if not len(signal):
            return result
        window = samples[signal[0] + 2048:signal[0] + 10240]
        if len(window) != 8192:
            return result
        fft_size = 1 << (len(reference) + len(window) - 1).bit_length()
        correlations = [np.fft.irfft(np.fft.rfft(reference[:, channel], fft_size)
                                    * np.conj(np.fft.rfft(window[:, channel], fft_size)), fft_size)
                        [:len(reference) - len(window) + 1] for channel in range(2)]
        lag = int(np.argmax(sum(correlations)))
        expected = reference[lag:lag + len(window)]
        metrics = []
        for channel in range(2):
            actual, target = window[:, channel], expected[:, channel]
            dot = float(np.dot(actual, target))
            gain = dot / float(np.dot(target, target))
            similarity = dot / float(np.linalg.norm(actual) * np.linalg.norm(target))
            metrics.append({"gain": gain, "cosine_similarity": similarity})
        result["speaker_mix_metrics"] = metrics
        result["speaker_mix_method"] = "both_aac_channels_compared_with_independent_pyav_decode"
        result["speaker_mix_integrity"] = all(.98 <= item["gain"] <= 1.02 and item["cosine_similarity"] > .999
                                               for item in metrics)
    return result


class RoutedPage:
    def __init__(self, page, case, media, hls, directory, evidence):
        self.page, self.case, self.directory, self.evidence = page, case, directory, evidence
        self.socket = None
        self.connected_at = None
        self.initial_sent = self.end_sent = False
        page.route("**/*", self.route_request)
        page.route_web_socket("**", self.websocket)
        page.on("response", self.response)
        self.media, self.hls = media, hls

    def __getattr__(self, name):
        return getattr(self.page, name)

    def route(self, pattern, handler):
        def observe(route):
            class Boundary:
                request = route.request

                def continue_(self):
                    route.fallback()

            handler(Boundary())

        self.page.route(pattern, observe)

    def route_request(self, route):
        request = route.request
        parts = urlsplit(request.url)
        entry = {"method": request.method, "path": parts.path}
        if request.method == "GET" and parts.netloc == "127.0.0.1:8002" and parts.path in STATIC:
            entry["action"] = "forward_static"
            route.continue_()
        elif parts.path == "/api/cloud" and request.method == "GET":
            entry["action"] = "fulfill_mock_config"
            route.fulfill(json={"configured": True, "saved": False, "persistent_storage": False})
        elif parts.path == "/api/cloud/sessions" and request.method == "POST":
            entry["action"] = "fulfill_mock_session"
            self.evidence["intercepted_session_posts"] += 1
            route.fulfill(json={"session": "OFFLINE_STARTUP_FIXTURE"})
        elif parts.path.startswith("/api/cloud/sessions/") and request.method == "DELETE":
            entry["action"] = "fulfill_mock_stop"
            route.fulfill(json={"ok": True})
        elif parts.path == "/share/start":
            entry["action"] = "fulfill_mock_share"
            route.fulfill(json={"token": "OFFLINE_SHARE", "url": BASE + "/share/OFFLINE_SHARE"})
        elif parts.path.startswith("/share/stop/"):
            entry["action"] = "fulfill_mock_stop"
            route.fulfill(json={"ok": True})
        elif parts.path == "/channels":
            entry["action"] = "fulfill_direct_hls_check"
            route.fulfill(status=400, json={"detail": "单路 HLS"})
        elif parts.path in ("/video", "/audio"):
            entry["action"] = "fulfill_fixture_media"
            if self.case == "hls":
                playlist = (self.hls / "all.m3u8").read_text()
                playlist = "\n".join("/__engineering_fixture__/hls/" + line if line.endswith(".ts") else line
                                     for line in playlist.splitlines()) + "\n"
                route.fulfill(body=playlist, content_type="application/vnd.apple.mpegurl")
            else:
                route.fulfill(path=str(self.media), content_type="audio/wav" if self.case == "mono" else "video/mp4")
        elif parts.path.startswith("/__engineering_fixture__/hls/"):
            entry["action"] = "fulfill_fixture_segment"
            route.fulfill(path=str(self.hls / Path(parts.path).name), content_type="video/mp2t")
        else:
            entry["action"] = "abort_unexpected"
            self.evidence["unexpected_requests"].append(entry)
            route.abort()
        self.evidence["routing"].append(entry)

    def response(self, response):
        parts = urlsplit(response.url)
        if parts.path in STATIC:
            raw = response.body()
            local = (ROOT / "lisca" / "web" / "static" / STATIC[parts.path]).read_bytes()
            self.evidence["app_resources"][parts.path] = {
                "sha256": hashlib.sha256(raw).hexdigest(), "matches_candidate": raw == local,
                "matches_a0e511e": raw == subprocess.check_output(
                    ["git", "show", f"a0e511e:lisca/web/static/{STATIC[parts.path]}"], cwd=ROOT),
            }

    def websocket(self, socket):
        self.socket = socket
        self.connected_at = time.monotonic()
        self.evidence["intercepted_websockets"] += 1
        socket.on_message(lambda _message: None)
        # Never call connect_to_server: this browser socket has no backend peer.

    def evaluate(self, script, *args, **kwargs):
        value = self.page.evaluate(script, *args, **kwargs)
        if script == PIXEL_STATE:
            with (self.directory / "geometry-trace.jsonl").open("a") as stream:
                stream.write(json.dumps(value) + "\n")
        if script == collector.SAMPLER_START:
            self.evidence["initial_media_state"] = self.page.evaluate("""() => ['player','audioEl'].map(id => {
              const media=document.getElementById(id);
              return {id, has_src:media.hasAttribute('src'), current_src:media.currentSrc,
                ready_state:media.readyState};
            })""")
        return value

    def wait_for_timeout(self, milliseconds):
        if self.socket and not self.initial_sent:
            self.initial_sent = True
            for message in (
                {"type": "subtitle", "sentence_id": 1, "revision": 1, "beg": .15, "end": 2.5,
                 "text": "Fixture visible words", "is_final": True,
                 "words": [[.15, .8, "Fixture"], [.8, 1.6, "visible"], [1.6, 2.5, "words"]]},
                {"type": "buffer", "play_at": 4},
                {"type": "ready", "original": True, "buffered": True, "start_at": 0, "delay": 0},
            ):
                self.socket.send(json.dumps(message))
        if self.case != "hls" and self.initial_sent and not self.end_sent and time.monotonic() - self.connected_at > .3:
            self.end_sent = True
            self.socket.send(json.dumps({"type": "end", "reason": "completed"}))
        self.page.wait_for_timeout(milliseconds)


def check(output, fixtures=None):
    output.mkdir(parents=True, exist_ok=False)
    if fixtures:
        mono, stereo, hls = fixtures / "mono.wav", fixtures / "stereo.mp4", fixtures / "hls"
    else:
        fixtures = output / "fixtures"
        mono, stereo, hls = make_media(fixtures)
    results = []
    with sync_playwright() as playwright:
        for case, media in (("mono", mono), ("stereo", stereo), ("hls", None)):
            directory = output / case
            directory.mkdir()
            evidence = {"case": case, "backend_session_posts_forwarded": 0,
                        "backend_websockets_connected": 0, "intercepted_session_posts": 0,
                        "intercepted_websockets": 0, "routing": [], "unexpected_requests": [], "app_resources": {}}
            browser = launch_browser(playwright)
            evidence["browser_version"] = browser.version

            class BrowserProxy:
                def new_page(self, **kwargs):
                    page = browser.new_page(service_workers="block", **kwargs)
                    return RoutedPage(page, case, media, hls, directory, evidence)

                def close(self):
                    browser.close()

            original_launch = collector.launch_browser
            original_playwright = collector.sync_playwright
            collector.launch_browser = lambda _playwright: BrowserProxy()
            collector.sync_playwright = lambda: nullcontext(playwright)
            try:
                args = collector.parse_args([
                    "--media", str(media) if media else "https://offline.invalid/fixture.m3u8",
                    "--label", case, "--output", str(directory), "--capture-engineering-sync",
                    "--timeout", "10", "--seconds", "3",
                ])
                evidence["collector_result"] = collector.collect(args)
            finally:
                collector.launch_browser = original_launch
                collector.sync_playwright = original_playwright
                browser.close()
            report = json.loads((directory / f"{case}.report.json").read_text())
            capture = report.get("engineering_sync", {})
            audio = capture.get("audio") or {}
            evidence["errors"] = report.get("errors")
            evidence["audio"] = audio
            evidence["pixel_capture"] = capture.get("pixel_capture")
            evidence["pcm_checks"] = pcm_checks(directory, audio, case, media)
            evidence["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                "tests/acceptance/browser_pcm_capture.js", "tests/acceptance/browser_pcm_capture.py",
                "tests/acceptance/browser_pixel_capture.py", "tests/acceptance/run_real_browser_acceptance.py",
                "tests/browser/check_engineering_app_startup.py")}
            initial = evidence.get("initial_media_state", [])
            evidence["checks"] = {
                "unloaded_before_real_start": len(initial) == 2 and all(not item["has_src"] and not item["current_src"] for item in initial),
                "mock_session_only": evidence["intercepted_session_posts"] == evidence["intercepted_websockets"] == 1,
                "static_app_matches_candidate": {"/", "/player.css", "/caption_presenter.js", "/playback_guard.js", "/recovery_controller.js", "/source_library.js"} <= evidence["app_resources"].keys() and all(item["matches_candidate"] and item["matches_a0e511e"] for item in evidence["app_resources"].values()),
                "no_unexpected_network": not evidence["unexpected_requests"],
                "continued_pcm": audio.get("status") == "captured" and not audio.get("integrity_errors") and audio.get("signal_observed") is True and audio.get("frame_count", 0) >= 48000,
                "continued_pixels": capture.get("pixel_capture", {}).get("status") == "captured" and capture.get("pixel_capture", {}).get("observation_count", 0) >= 10,
                "pcm_ledger_integrity": evidence["pcm_checks"]["ledger_integrity"],
                "all_destination_channels": evidence["pcm_checks"]["all_destination_channels"],
                "speaker_mix_integrity": evidence["pcm_checks"]["speaker_mix_integrity"],
            }
            evidence["status"] = "passed" if all(evidence["checks"].values()) else "failed"
            (directory / "startup-check.json").write_text(json.dumps(evidence, indent=2) + "\n")
            results.append({"case": case, "status": evidence["status"], "checks": evidence["checks"], "errors": evidence["errors"]})
    result = {"scope": "complete_app_startup_with_simulated_recognition", "backend_asr_posts": 0,
              "candidate_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "app_revision_matching_served_bytes": subprocess.check_output(["git", "rev-parse", "a0e511e"], cwd=ROOT, text=True).strip(),
              "fixture_sha256": {path.relative_to(fixtures).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                                 for path in fixtures.rglob("*") if path.is_file()},
              "status": "passed" if all(item["status"] == "passed" for item in results) else "failed", "cases": results}
    (output / "startup-matrix.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixtures", type=Path, help="Reuse immutable fixture inputs from a previous run")
    args = parser.parse_args()
    result = check(args.output.expanduser().resolve(), args.fixtures.expanduser().resolve() if args.fixtures else None)
    print(json.dumps(result))
    raise SystemExit(0 if result["status"] == "passed" else 1)
