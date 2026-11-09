"""Manual browser smoke on 8002 with a synthetic video and explicit fake providers.

Not imported by pytest or production. No external API requests or real keys.
"""
from pathlib import Path
import sys

import av
import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from lisca.web import app as webui  # noqa: E402
from lisca.cloud_translation import CloudConfigStore  # noqa: E402


def make_video(path):
    with av.open(str(path), "w") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 320, 180, "yuv420p"
        audio = output.add_stream("aac", rate=16000)
        audio.layout = "mono"
        for index in range(120):
            image = np.zeros((180, 320, 3), dtype=np.uint8)
            image[:, :, 2] = 60
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            for packet in video.encode(frame):
                output.mux(packet)
            sound = av.AudioFrame.from_ndarray(np.zeros((1, 1600), dtype=np.float32),
                                               format="flt", layout="mono")
            sound.sample_rate, sound.pts = 16000, index * 1600
            for packet in audio.encode(sound):
                output.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode():
                output.mux(packet)


class FakeEngine:
    def __init__(self, language):
        self.chunks, self.language = 0, language

    def transcribe_chunk(self, _audio):
        self.chunks += 1
        if self.chunks % 4:
            return self.poll()
        sentence = (self.chunks - 1) // 30
        final = self.chunks % 30 >= 28
        text = "Hello, welcome to the video." if self.language == "en" else "你好，欢迎观看视频。"
        return (sentence * 3.0, sentence * 3.0 + 3 if final else None, text, None, final, sentence)

    def poll(self):
        return (None, None, "", None)

    def finish(self):
        return self.poll()

    def close(self):
        pass


class FakeTranslator:
    def __init__(self, _config, _source, target):
        self.target = target

    def translate(self, _text, stop):
        text = "你好，欢迎观看视频。" if self.target == "zh" else "Hello, welcome to the video."
        for end in (3, len(text)):
            if stop.wait(0.05):
                return
            yield text[:end]

    def close(self):
        pass


if __name__ == "__main__":
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    video_path = logs / "cloud-smoke.mp4"
    make_video(video_path)
    webui._CLOUD = CloudConfigStore(logs / "smoke-config.bin")
    webui._CLOUD.update({"api_key": "test-only-no-cloud"})
    webui.create_engine = lambda _name, **kw: FakeEngine((kw.get("language_hints") or ["zh"])[0])
    webui.QwenTranslator = FakeTranslator
    original_index = webui.index
    # Make the test double conspicuous in screenshots.
    @webui.app.middleware("http")
    async def smoke_banner(request, call_next):
        if request.url.path == "/":
            response = original_index()
            body = response.body.decode().replace("<h1>Lisca</h1>",
                '<h1 data-i18n-skip>TEST FIXTURE · Lisca（未调用真实 API / no real API）</h1>')
            return webui.HTMLResponse(body, headers={"Cache-Control": "no-store"})
        return await call_next(request)

    print("SMOKE VIDEO:", video_path, flush=True)
    webui.uvicorn.run(webui.app, host="127.0.0.1", port=8002, log_level="warning")
