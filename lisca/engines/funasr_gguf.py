"""File-mode client for the local FunASR llama.cpp/GGUF HTTP service."""
import io
import json
import os
import re
import urllib.error
import urllib.request
import uuid
import wave
from collections import deque

import numpy as np

from .base import Engine, Subtitle

SAMPLE_RATE = 16000
_TIMECODE = re.compile(r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})$")
_SRT_RANGE = re.compile(r"^(.+?)\s+-->\s+(.+?)$")


def _seconds(value: str) -> float:
    match = _TIMECODE.match(value.strip())
    if not match:
        raise ValueError(f"无效的 SRT 时间戳: {value}")
    hours, minutes, seconds, millis = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


def _parse_srt(text: str, duration: float) -> list[Subtitle]:
    entries: list[Subtitle] = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        time_line = next((i for i, line in enumerate(lines) if _SRT_RANGE.match(line)), None)
        if time_line is None:
            continue
        match = _SRT_RANGE.match(lines[time_line])
        assert match is not None
        body = " ".join(lines[time_line + 1:]).strip()
        if not body:
            continue
        try:
            beg, end = _seconds(match.group(1)), _seconds(match.group(2))
        except ValueError:
            continue
        entries.append((beg, end, body, None))
    if entries:
        return entries
    plain_text = text.strip()
    return [(0.0, max(duration, 0.001), plain_text, None)] if plain_text else []


def _wav_bytes(chunks: list[np.ndarray]) -> bytes:
    audio = np.concatenate(chunks) if chunks else np.empty(0, dtype=np.float32)
    pcm = np.clip(np.rint(audio * 32768.0), -32768, 32767).astype("<i2").tobytes()
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return output.getvalue()


class FunASRGGUFEngine(Engine):
    """Buffers audio chunks and transcribes the complete recording at finish().

    This engine deliberately has no streaming-caption semantics. It is intended
    for local media files whose playback can wait for the finished transcript.
    """

    batch_mode = True

    def __init__(self, base_url: str | None = None, timeout: float = 600.0):
        self.base_url = (base_url or os.getenv(
            "FUNASR_GGUF_URL", "http://127.0.0.1:8765")).rstrip("/")
        self.timeout = timeout
        self._chunks: list[np.ndarray] = []
        self._duration = 0.0
        self._pending: deque[Subtitle] = deque()
        self._finished = False

    def transcribe_chunk(self, audio) -> Subtitle:
        chunk = np.asarray(audio, dtype=np.float32).reshape(-1).copy()
        if chunk.size:
            self._chunks.append(chunk)
            self._duration += chunk.size / SAMPLE_RATE
        return (None, None, "", None)

    def _transcribe(self) -> list[Subtitle]:
        boundary = "----codex" + uuid.uuid4().hex
        audio = _wav_bytes(self._chunks)
        body = (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="model"\r\n\r\nfunasr-gguf\r\n'
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="file"; filename="audio.wav"\r\n'
            "Content-Type: audio/wav\r\n\r\n"
        ).encode("ascii") + audio + f"\r\n--{boundary}--\r\n".encode("ascii")
        request = urllib.request.Request(
            self.base_url + "/v1/audio/transcriptions", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            raise RuntimeError(f"FunASR GGUF 服务返回 HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"无法连接本地 FunASR GGUF 服务 {self.base_url}；请先启动 Vulkan 服务。"
            ) from exc
        text = payload.get("text", "")
        if not isinstance(text, str):
            raise RuntimeError("FunASR GGUF 服务响应缺少文本字段 text")
        return _parse_srt(text, self._duration)

    def finish(self) -> Subtitle:
        if not self._finished:
            self._pending.extend(self._transcribe())
            self._finished = True
            self._chunks.clear()
        return self.poll()

    def poll(self) -> Subtitle:
        return self._pending.popleft() if self._pending else (None, None, "", None)
