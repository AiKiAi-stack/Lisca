"""Serve the real WebUI on 8002 with a deterministic external ASR substitute.

Run only while the regular 8002 service is stopped. The real routes, decoder,
player, and caption code stay in use. This is simulated recognition evidence,
never a speech-accuracy or public-source acceptance result.
"""
import json
import math
import os
import re
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
from original_release_hls import generate_hls, serve_hls  # noqa: E402


class OfflineAuthenticationFailure(Exception):
    """Structured provider evidence for a test, without making an API call."""
    status_code = 401
    code = "InvalidApiKey"


def generate_media(path, seconds=8, silent=False, silent_seconds=0, with_audio=True):
    with av.open(str(path), "w") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
        video.codec_context.thread_count = 1
        video.options = {"preset": "ultrafast"}
        audio = output.add_stream("aac", rate=16000) if with_audio else None
        if audio:
            audio.layout = "mono"
        for index in range(seconds * 10):
            pixels = np.full((180, 320, 3), (30, 45 + index % 100, 90), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
            frame.pts, frame.time_base = index, Fraction(1, 10)
            for packet in video.encode(frame):
                output.mux(packet)
            if audio is None:
                continue
            signal = np.zeros(1600) if silent or index < silent_seconds * 10 else .12 * np.sin(
                2 * math.pi * 440 * (np.arange(1600) + index * 1600) / 16000)
            frame = av.AudioFrame.from_ndarray(signal.astype(np.float32)[None, :],
                                              format="fltp", layout="mono")
            frame.sample_rate = 16000
            frame.pts, frame.time_base = index * 1600, Fraction(1, 16000)
            for packet in audio.encode(frame):
                output.mux(packet)
        for stream in (video, audio):
            if stream is None:
                continue
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
            state["scenario_started"] += 1
            self.attempt = state["scenario_started"]
            self.observation = {"id": state["started"], "mode": self.mode,
                                "created_at": time.monotonic(), "closed": False,
                                "feed_observations": []}
            state["engines"].append(self.observation)
            state["engines"] = state["engines"][-32:]
        self.elapsed = 0.0
        self.closed = False
        self.pending = deque()
        self.steps = deque(self.script())

    def script(self):
        if self.mode in ("silent", "no-progress", "live-no-caption"):
            return []
        if self.mode == "share-history":
            historical = [(0.2, (index * 0.01, index * 0.01 + 0.005,
                                  f"Historical caption {index + 1}", None, True, index + 1))
                          for index in range(300)]
            return [*historical,
                    (0.2, (0., 60., "Caption after history snapshot", None, True, 301))]
        if self.mode == "controls":
            return [(beg + 1.8, (float(beg), beg + 1.8, f"Prepared sentence {beg // 2 + 1}.",
                      [(float(beg), beg + 1.8, f"Prepared sentence {beg // 2 + 1}.")],
                      True, beg // 2 + 1)) for beg in range(0, 40, 2)]
        if self.mode == "silence-speech":
            return [(5.8, (4., 5.8, "静音后的第一句原文。", None, True, 1)),
                    (7.8, (6., 7.8, "Speech continues after silence.", None, True, 2))]
        if self.mode in ("live-normal", "recovery-live", "recovery-coincident-live"):
            return [(beg + 1.5, (float(beg), beg + 1.5, f"Live sentence {beg // 2 + 1}.",
                      None, True, beg // 2 + 1)) for beg in range(0, 60, 2)]
        if self.mode == "live-gap":
            return [(1.5, (0., 1.5, "Live first sentence.", None, True, 1)),
                    (1.7, (1.5, None, "Pending live words", None, False, 2)),
                    (5., (1.5, 2.5, "Late sentence two.", None, True, 2)),
                    (5.1, (2.5, None, "Pending later words", None, False, 3)),
                    (7.5, (2.5, 3.5, "Late sentence three.", None, True, 3)),
                    (7.6, (3.5, None, "Pending later words again", None, False, 4)),
                    (9.5, (3.5, 4.5, "Late sentence four.", None, True, 4)),
                    (9.6, (4.5, None, "Pending last delayed words", None, False, 5)),
                    (11.5, (4.5, 5.5, "Late sentence five.", None, True, 5)),
                    *[(beg + .5, (float(beg), beg + 2., f"Recovered sentence {beg}.",
                                  None, True, beg)) for beg in range(12, 58, 2)]]
        if self.mode == "withdrawn":
            return [(1.0, (0., 5., "This sentence was withdrawn.", None, True, 1)),
                    (1.2, (0., 5., "", None, True, 1)),
                    (1.4, (0., 5., "This sentence was withdrawn.", None, True, 1))]
        if self.mode in ("long-en", "fast-en"):
            text = ("Clear original captions follow the speaker. Old lines never cover the next sentence."
                    if self.mode == "long-en" else
                    "During this rapid explanation the speaker keeps moving through several related ideas "
                    "without leaving a long pause between them. Each original caption should stay near its "
                    "spoken words and remain readable, while older lines finish before the next idea appears on the screen.")
            tokens = re.findall(r"\S+\s*", text)
            width = 10.8 / len(tokens)
            words = [(index * width, (index + 1) * width, token.strip())
                     for index, token in enumerate(tokens)]
            return [(10.8, (0., 10.8, text, words, True, 1))]
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
            observations = self.observation["feed_observations"]
            observations.append({"at": time.monotonic(), "through": self.elapsed})
            del observations[:-256]
        if self.mode in ("recovery-auth", "live-auth"):
            raise OfflineAuthenticationFailure("offline fixture authentication failure")
        if self.mode in ("recovery-network", "recovery-live") and self.elapsed >= 3:
            raise ConnectionError("offline fixture connection failure")
        if self.mode == "recovery-coincident-live" and self.attempt == 1 and self.elapsed >= .3:
            raise ConnectionError("offline fixture coincident connection failure")
        while self.steps and self.steps[0][0] <= self.elapsed:
            self.pending.append(self.steps.popleft()[1])
        return self.poll()

    def poll(self):
        if not self.pending:
            return (None, None, "", None)
        item = self.pending[0]
        sentence_id = item[5] if len(item) > 5 else None
        if (self.mode == "share-history" and sentence_id == 301
                and not self.state["share_history_future_gate"].is_set()):
            return (None, None, "", None)
        item = self.pending.popleft()
        if self.mode == "share-history" and sentence_id != 301:
            with self.state["lock"]:
                self.state["share_history_captions"] += 1
        return item

    def finish(self):
        return self.poll()

    def close(self):
        if not self.closed:
            self.closed = True
            with self.state["lock"]:
                self.state["active"] -= 1
                self.observation["closed"] = True
                self.observation["closed_at"] = time.monotonic()


def main():
    directory = Path(os.environ.get("STREAM_ASR_FIXTURE_DIR",
                                    str(ROOT / "logs" / "original-release-fixture")))
    directory.mkdir(parents=True, exist_ok=True)
    media = {"normal": (8, False, 0, True), "long": (12, False, 0, True),
             "silent": (20, True, 0, True), "controls": (40, False, 0, True),
             "no-progress": (40, False, 0, True),
             "silence-speech": (12, False, 4, True), "video-only": (3, False, 0, False)}
    for name, arguments in media.items():
        generate_media(directory / (name + ".mp4"), *arguments)
    (directory / "invalid.bin").write_bytes(b"This is not decodable media.")
    state = {"mode": "normal", "started": 0, "active": 0, "audio_seconds": 0.,
             "lock": threading.Lock(), "live_started": time.monotonic(), "engines": [],
             "scenario_started": 0, "share_history_captions": 0,
             "share_history_future_gate": threading.Event()}
    hls_directory = directory / "hls"
    hls_directory.mkdir(exist_ok=True)
    generate_hls(hls_directory)
    source = serve_hls(hls_directory, state)
    with tempfile.TemporaryDirectory(prefix="original-release-config-") as config:
        webui._CLOUD = CloudConfigStore(Path(config) / "cloud.bin")
        # Capability links in this same-host simulated browser check use the
        # local fixture origin; this is not a second-device LAN acceptance.
        webui._lan_ipv4_candidates = lambda: ["127.0.0.1"]

        def create_recognizer(_name, **_kwargs):
            return RecognitionFixture(state)

        webui.create_engine = create_recognizer

        @webui.app.get("/__fixture/state")
        def fixture_state():
            with state["lock"]:
                snapshot = json.loads(json.dumps({key: value for key, value in state.items()
                                                  if key not in ("lock", "share_history_future_gate")}))
            return {**snapshot, "simulated_recognition": True,
                    "process_id": os.getpid(),
                    "live_url": f"http://127.0.0.1:{source.server_port}/live.m3u8",
                    "media": {**{name: str(directory / (name + ".mp4")) for name in media},
                              "invalid": str(directory / "invalid.bin")}}

        @webui.app.post("/__fixture/release-share-history-future")
        async def release_share_history_future():
            state["share_history_future_gate"].set()
            return {"released": True}

        @webui.app.post("/__fixture/scenario")
        async def fixture_scenario(request: Request):
            mode = (await request.json())["mode"]
            if mode not in (*media, "withdrawn", "long-en", "fast-en", "live-normal", "live-gap",
                            "recovery-network", "recovery-auth", "recovery-live", "recovery-coincident-live",
                            "live-no-caption", "live-auth", "share-history"):
                raise ValueError("Unknown offline scenario")
            with state["lock"]:
                state["mode"] = mode
                state["scenario_started"] = 0
                state["share_history_captions"] = 0
                state["share_history_future_gate"].clear()
                if (mode.startswith("live-") or mode in
                        ("recovery-live", "recovery-coincident-live", "share-history")):
                    state["live_started"] = time.monotonic()
            return {"mode": mode, "simulated_recognition": True}

        print(json.dumps({"fixture": "offline recognition substitute", "port": 8002,
                          "real_cloud_calls": False}), flush=True)
        try:
            uvicorn.run(webui.app, host="127.0.0.1", port=8002, log_level="warning")
        finally:
            source.shutdown()
            source.server_close()


if __name__ == "__main__":
    main()
