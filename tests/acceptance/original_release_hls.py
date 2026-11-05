"""Generate a small rolling HLS source for real-player checks, without ASR."""
import functools
import math
from fractions import Fraction
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import re
import threading
import time
from urllib.parse import urlsplit

import av
import numpy as np


def generate_hls(directory, seconds=60):
    with av.open(str(directory / "all.m3u8"), "w", format="hls", options={
        "hls_time": "2", "hls_list_size": "0", "hls_flags": "independent_segments",
        "hls_segment_filename": str(directory / "seg%02d.ts"),
    }) as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
        video.codec_context.gop_size = 20
        video.codec_context.max_b_frames = 0
        video.codec_context.thread_count = 1
        video.options = {"preset": "ultrafast", "tune": "zerolatency", "sc_threshold": "0"}
        audio = output.add_stream("aac", rate=16000)
        audio.layout = "mono"
        for index in range(seconds * 10):
            pixels = np.full((180, 320, 3), (40 + (index // 10 % 3) * 60, 50, 100), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame):
                output.mux(packet)
            signal = .12 * np.sin(2 * math.pi * 440 *
                                  (np.arange(1600) + index * 1600) / 16000)
            frame = av.AudioFrame.from_ndarray(signal.astype(np.float32)[None, :],
                                              format="fltp", layout="mono")
            frame.sample_rate = 16000
            frame.pts, frame.time_base = index * 1600, Fraction(1, 16000)
            for packet in audio.encode(frame):
                output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None):
                output.mux(packet)


def serve_hls(directory, state):
    entries = re.findall(r"(#EXTINF:[^\n]+)\n([^\n]+)",
                         (directory / "all.m3u8").read_text(encoding="utf-8"))

    class Handler(SimpleHTTPRequestHandler):
        def do_GET(self):
            if urlsplit(self.path).path != "/live.m3u8":
                return super().do_GET()
            with state["lock"]:
                elapsed = max(0, time.monotonic() - state["live_started"])
            end = min(len(entries), 3 + int(elapsed / 2))
            begin = max(0, end - 6)
            body = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
                    f"#EXT-X-MEDIA-SEQUENCE:{begin}\n#EXT-X-INDEPENDENT-SEGMENTS\n")
            body += "".join(f"{duration}\n{name}\n" for duration, name in entries[begin:end])
            if end == len(entries):
                body += "#EXT-X-ENDLIST\n"
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body.encode())

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Handler, directory=directory))
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
