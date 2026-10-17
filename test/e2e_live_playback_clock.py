"""Real A/V + rolling HLS + WebSocket + demo captions, against port 8002.

Generates media locally with PyAV. No API key or external stream is used.
"""
import functools
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from fractions import Fraction
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import av
import numpy as np

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\codex_tmp\playwright-browsers")
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002/"


def generate_hls(directory):
    output = av.open(str(directory / "all.m3u8"), "w", format="hls", options={
        "hls_time": "2", "hls_list_size": "0", "hls_flags": "independent_segments",
        "hls_segment_filename": str(directory / "seg%02d.ts"),
    })
    video = output.add_stream("libx264", rate=10)
    video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
    video.codec_context.gop_size = 20
    video.codec_context.max_b_frames = 0
    video.codec_context.thread_count = 1
    video.options = {"preset": "ultrafast", "tune": "zerolatency", "sc_threshold": "0"}
    audio = output.add_stream("aac", rate=16000)
    audio.layout = "mono"
    for i in range(260):
        pixels = np.full((180, 320, 3), (40 + (i // 10 % 3) * 60, 50, 100), dtype=np.uint8)
        pixels[80:100, :int((i % 20 + 1) * 16)] = 230
        frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        frame.pts, frame.time_base = i, Fraction(1, 10)
        for packet in video.encode(frame):
            output.mux(packet)
        signal = (0.1 * np.sin(2 * math.pi * 440 * (np.arange(1600) + i * 1600) / 16000)
                  if i % 10 == 0 else np.zeros(1600))
        frame = av.AudioFrame.from_ndarray(signal.astype(np.float32)[None, :], format="fltp", layout="mono")
        frame.sample_rate = 16000
        frame.pts, frame.time_base = i * 1600, Fraction(1, 16000)
        for packet in audio.encode(frame):
            output.mux(packet)
    for stream in (video, audio):
        for packet in stream.encode(None):
            output.mux(packet)
    output.close()


def main():
    (ROOT / "logs").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hls-clock-", dir=ROOT / "logs") as temporary:
        directory = Path(temporary)
        generate_hls(directory)
        manifest = (directory / "all.m3u8").read_text()
        entries = re.findall(r"(#EXTINF:[^\n]+)\n([^\n]+)", manifest)
        started = time.monotonic()

        class LiveHandler(SimpleHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/live.m3u8":
                    end = min(len(entries), 4 + int((time.monotonic() - started) / 2))
                    begin = max(0, end - 4)
                    body = (f"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
                            f"#EXT-X-MEDIA-SEQUENCE:{begin}\n#EXT-X-INDEPENDENT-SEGMENTS\n")
                    body += "".join(f"{duration}\n{name}\n" for duration, name in entries[begin:end])
                    if end == len(entries):
                        body += "#EXT-X-ENDLIST\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.end_headers()
                    self.wfile.write(body.encode())
                else:
                    super().do_GET()

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(LiveHandler, directory=directory))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True, args=["--autoplay-policy=no-user-gesture-required"])
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(BASE, wait_until="domcontentloaded")
                page.evaluate("""() => {
                    window.frameTimes = []; window.pauseEvents = 0;
                    player.addEventListener('pause', () => { window.pauseEvents++; });
                    function frame(now, metadata) {
                        window.frameTimes.push(metadata.mediaTime);
                        player.requestVideoFrameCallback(frame);
                    }
                    player.requestVideoFrameCallback(frame);
                }""")
                page.locator("#url").fill(f"http://127.0.0.1:{server.server_port}/live.m3u8")
                page.locator("#engine").select_option("demo")
                page.locator("#delay").select_option("5")
                page.locator("#start").click()
                samples = []
                screenshots = set()
                deadline = time.monotonic() + 35
                while time.monotonic() < deadline:
                    snapshot = page.evaluate("""() => ({t:player.currentTime, paused:player.paused,
                        caption:currentEl.textContent, history:historyEl.textContent,
                        log:logBuffer.join('|'),
                        frames:window.frameTimes.length, muted:player.muted})""")
                    samples.append(snapshot)
                    for checkpoint in (4, 8, 12):
                        if snapshot["t"] >= checkpoint and checkpoint not in screenshots:
                            page.screenshot(path=str(ROOT / "logs" / f"hls-clock-{checkpoint}.png"))
                            screenshots.add(checkpoint)
                    if snapshot["t"] >= 14:
                        break
                    page.wait_for_timeout(200)
                times = page.evaluate("window.frameTimes")
                clock_match = re.search(r"media_clock.*offset=([-.\d]+)s", samples[-1]["log"])
                offset = float(clock_match[1]) if clock_match else None
                checked = 0
                mismatches = []
                for sample in samples:
                    if offset is None or sample["paused"] or sample["t"] < offset + 0.2:
                        continue
                    elapsed = sample["t"] - offset
                    if 0.2 < elapsed % 1 < 0.8:
                        checked += 1
                        expected = f"{int(elapsed) + 1:02d}"
                        if expected not in sample["caption"]:
                            mismatches.append({"t":sample["t"], "expected":expected, "caption":sample["caption"]})
                result = {"last_time":samples[-1]["t"], "frames":len(times), "clock_offset":offset,
                          "checked_captions":checked, "mismatches":mismatches,
                          "backward_frames":sum(b < a - 0.15 for a, b in zip(times, times[1:])),
                          "pauses":page.evaluate("window.pauseEvents"), "errors":errors}
                print(json.dumps(result))
                if page.locator("#stop").is_visible():
                    page.locator("#stop").click()
                browser.close()
                assert result["last_time"] >= 14, samples[-1]
                assert result["frames"] > 80, result
                assert offset is not None, samples[-1]
                assert checked >= 15 and not mismatches, result
                assert result["backward_frames"] == 0, result
                assert result["pauses"] <= 1, result
                assert not errors, result
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
