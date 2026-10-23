"""Serve the real WebUI on 8002 with a deterministic external ASR substitute.

Run only while the regular 8002 service is stopped. The real routes, decoder,
player, and caption code stay in use. This is simulated recognition evidence,
never a speech-accuracy or public-source acceptance result.
"""
import json
import math
from collections import deque
from fractions import Fraction
from pathlib import Path
import sys
import tempfile
import threading
import time

import av
from fastapi import Request
import numpy as np
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import webui  # noqa: E402
from cloud_translation import CloudConfigStore  # noqa: E402


def generate_media(path, seconds=8, silent=False):
    with av.open(str(path), "w") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
        video.codec_context.thread_count = 1
        video.options = {"preset": "ultrafast"}
        audio = output.add_stream("aac", rate=16000)
        audio.layout = "mono"
        for index in range(seconds * 10):
            pixels = np.full((180, 320, 3), (30, 45 + index % 100, 90), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame):
                output.mux(packet)
            signal = np.zeros(1600) if silent else .12 * np.sin(
                2 * math.pi * 440 * (np.arange(1600) + index * 1600) / 16000)
            frame = av.AudioFrame.from_ndarray(signal.astype(np.float32)[None, :],
                                              format="fltp", layout="mono")
            frame.sample_rate = 16000
            frame.pts, frame.time_base = index * 1600, Fraction(1, 16000)
            for packet in audio.encode(frame):
                output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode(None):
                output.mux(packet)


class RecognitionFixture:
    """A prescribed external recognition stream; it never recognizes audio."""

    def __init__(self, state):
        self.state = state
        with state["lock"]:
            self.mode = state["mode"]
            state["started"] += 1
            state["active"] += 1
        self.elapsed = 0.0
        self.closed = False
        self.pending = deque()
        self.steps = deque(self.script())

    def script(self):
        if self.mode == "silent":
            return []
        if self.mode == "withdrawn":
            return [(1.0, (0., 5., "This sentence was withdrawn.", None, True, 1)),
                    (1.2, (0., 5., "", None, True, 1)),
                    (1.4, (0., 5., "This sentence was withdrawn.", None, True, 1))]
        if self.mode == "long":
            text = "这是一段用于验证字幕在窄屏和放大字号后仍保持完整短句的原文内容，所有文字都应该在对应语音时间内显示，而不是被裁切或挤成很多行。"
            width = 10.8 / len(text)
            words = [(index * width, (index + 1) * width, char)
                     for index, char in enumerate(text)]
            return [(10.8, (0., 10.8, text, words, True, 1))]
        return [(1.8, (0., 1.8, "第一句完整字幕。", [(0., 1.8, "第一句完整字幕。")], True, 1)),
                (3.8, (2., 3.8, "Second complete sentence.",
                       [(2., 3.8, "Second complete sentence.")], True, 2)),
                (5.8, (4., 5.8, "最后一句原文字幕。", [(4., 5.8, "最后一句原文字幕。")], True, 3))]

    def transcribe_chunk(self, audio):
        self.elapsed += len(audio) / 16000
        with self.state["lock"]:
            self.state["audio_seconds"] += len(audio) / 16000
        while self.steps and self.steps[0][0] <= self.elapsed:
            self.pending.append(self.steps.popleft()[1])
        return self.poll()

    def poll(self):
        return self.pending.popleft() if self.pending else (None, None, "", None)

    def finish(self):
        return self.poll()

    def close(self):
        if not self.closed:
            self.closed = True
            with self.state["lock"]:
                self.state["active"] -= 1


def main():
    directory = ROOT / "logs" / "original-release-fixture"
    directory.mkdir(parents=True, exist_ok=True)
    for name, seconds, silent in [("normal", 8, False), ("long", 12, False), ("silent", 20, True)]:
        generate_media(directory / (name + ".mp4"), seconds, silent)
    state = {"mode": "normal", "started": 0, "active": 0, "audio_seconds": 0.,
             "lock": threading.Lock()}
    with tempfile.TemporaryDirectory(prefix="original-release-config-") as config:
        webui._CLOUD = CloudConfigStore(Path(config) / "cloud.bin")

        def create_recognizer(_name, **_kwargs):
            return RecognitionFixture(state)

        webui.create_engine = create_recognizer

        @webui.app.get("/__fixture/state")
        def fixture_state():
            with state["lock"]:
                snapshot = {key: value for key, value in state.items() if key != "lock"}
            return {**snapshot, "simulated_recognition": True,
                    "media": {name: str(directory / (name + ".mp4"))
                              for name in ("normal", "long", "silent")}}

        @webui.app.post("/__fixture/scenario")
        async def fixture_scenario(request: Request):
            mode = (await request.json())["mode"]
            if mode not in ("normal", "long", "silent", "withdrawn"):
                raise ValueError("Unknown offline scenario")
            with state["lock"]:
                state["mode"] = mode
            return {"mode": mode, "simulated_recognition": True}

        print(json.dumps({"fixture": "offline recognition substitute", "port": 8002,
                          "real_cloud_calls": False}), flush=True)
        uvicorn.run(webui.app, host="127.0.0.1", port=8002, log_level="warning")


if __name__ == "__main__":
    main()
