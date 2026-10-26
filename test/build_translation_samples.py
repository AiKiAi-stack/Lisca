"""Wrap Windows-generated fixed WAV samples in a browser-playable H.264 video."""
import hashlib
import json
from pathlib import Path
import wave

import av
import numpy as np


DIRECTORY = Path(__file__).resolve().parents[1] / "logs" / "translation-acceptance"
REFERENCES = {
    "zh": "欢迎使用实时翻译。今天是星期三。下一班火车将在下午三点到站。请不要关闭窗口。",
    "en": "Welcome to live translation. Today is Wednesday. The next train will arrive at three in the afternoon. Please do not close the window.",
}


def build(language):
    path = DIRECTORY / f"{language}.wav"
    with wave.open(str(path)) as source:
        duration = source.getnframes() / source.getframerate()
    target = DIRECTORY / f"{language}.mp4"
    with av.open(str(target), "w") as output:
        video = output.add_stream("libx264", rate=10)
        video.width, video.height, video.pix_fmt = 640, 360, "yuv420p"
        audio = output.add_stream("aac", rate=16000)
        audio.layout = "mono"
        for index in range(int(duration * 10) + 1):
            image = np.full((360, 640, 3), (24, 40, 64), dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="rgb24")
            frame.pts = index
            for packet in video.encode(frame):
                output.mux(packet)
        for packet in video.encode():
            output.mux(packet)
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=16000)
        with av.open(str(path)) as source:
            for frame in source.decode(audio=0):
                for normalized in resampler.resample(frame):
                    for packet in audio.encode(normalized):
                        output.mux(packet)
        for normalized in resampler.resample(None):
            for packet in audio.encode(normalized):
                output.mux(packet)
        for packet in audio.encode():
            output.mux(packet)
    return {"language": language, "path": str(target), "duration": duration,
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
            "reference": REFERENCES[language], "origin": "local Windows speech synthesis"}


if __name__ == "__main__":
    result = [build(language) for language in REFERENCES]
    (DIRECTORY / "samples.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
