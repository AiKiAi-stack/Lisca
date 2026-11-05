# ASR Real-Time Subtitle Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Add real-time ASR subtitle burning + SRT file generation to streamparser, using DashScope fun-asr-realtime API.

**Architecture:** New module `ASRSubtitlePipeline` encapsulates ASRClient (DashScope WS connection) + SubtitleOverlay (Pillow text rendering on video frames) + SRTWriter (file output). Pipeline runs as a peer to StreamReader/StreamWriter, coordinated by StreamProcessor.

**Tech Stack:** dashscope (ASR SDK), Pillow (CJK text rendering), PyAV AudioResampler (audio preprocessing)

---

## Task 1: Dependencies and Project Setup

**Files:**
- Modify: `pyproject.toml`
- Modify: `requirements.txt`
- Create: `examples/asr_config.example.yml`

**Step 1:** Add `dashscope` and `Pillow` dependencies to pyproject.toml

```toml
dependencies = [
    "av>=14.0.1",
    "concurrent-log-handler>=0.9.25",
    "dashscope>=1.25.0",
    "librosa>=0.10.0",
    "numpy>=1.24.0",
    "opencv-python>=4.10.0.84",
    "Pillow>=10.0.0",
    "pyyaml>=6.0.2",
]
```

Also add to `requirements.txt`:
```
dashscope
Pillow
```

**Step 2:** Install dependencies

```bash
pip install dashscope Pillow
```

**Step 3:** Create example ASR config

```yaml
# examples/asr_config.example.yml
provider: dashscope
api_key: ${DASHSCOPE_API_KEY}
workspace_id: ""
region: cn-beijing
model: fun-asr-realtime
reconnect:
  max_retries: 10
  base_delay: 1.0
  max_delay: 30.0
subtitle:
  font_path: ""
  font_size: 32
  max_lines: 2
  position: bottom
  offset_y: 20
  text_color: [255, 255, 255]
  stroke_color: [0, 0, 0]
  stroke_width: 2
```

---

## Task 2: ASR Configuration Data Class + Tests

**TDD — Red first**

**Files:**
- Create: `streamparser/asr_config.py`
- Create: `tests/python/test_asr_config.py`

**Step 1: Write failing test**

```python
# tests/python/test_asr_config.py
import os
import pytest
from streamparser.asr_config import ASRSettings, ASRProvider, SubtitleConfig, ReconnectConfig

class TestASRSettings:
    def test_from_dict_basic(self):
        data = {
            "provider": "dashscope",
            "api_key": "${DASHSCOPE_API_KEY}",
            "workspace_id": "ws-123",
            "region": "cn-beijing",
            "model": "fun-asr-realtime",
        }
        settings = ASRSettings.from_dict(data)
        assert settings.provider == ASRProvider.DASHSCOPE
        assert settings.api_key == "${DASHSCOPE_API_KEY}"
        assert settings.workspace_id == "ws-123"
        assert settings.model == "fun-asr-realtime"

    def test_from_dict_resolves_env_var(self, monkeypatch):
        monkeypatch.setenv("MY_KEY", "sk-test-123")
        data = {"provider": "dashscope", "api_key": "${MY_KEY}"}
        settings = ASRSettings.from_dict(data)
        assert settings.api_key == "sk-test-123"

    def test_from_dict_defaults(self):
        data = {"provider": "dashscope", "api_key": "${DASHSCOPE_API_KEY}"}
        settings = ASRSettings.from_dict(data)
        assert settings.workspace_id == ""

    def test_websocket_url_construction(self):
        settings = ASRSettings(api_key="sk-test", workspace_id="ws-42", region="cn-beijing")
        assert "ws-42.cn-beijing.maas.aliyuncs.com" in settings.websocket_url

    def test_subtitle_config_defaults(self):
        config = SubtitleConfig()
        assert config.font_size == 32
        assert config.max_lines == 2
        assert config.position == "bottom"

    def test_reconnect_config_defaults(self):
        config = ReconnectConfig()
        assert config.max_retries == 10
        assert config.base_delay == 1.0
        assert config.max_delay == 30.0
```

**Step 2: Run, verify FAIL**

```bash
cd /mnt/c/Users/jiacheng.wan/Documents/projects/streamparser && python -m pytest tests/python/test_asr_config.py -v
```
Expected: ModuleNotFoundError

**Step 3: Write minimal implementation**

```python
# streamparser/asr_config.py
import os
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


class ASRProvider(Enum):
    DASHSCOPE = auto()


@dataclass
class ReconnectConfig:
    max_retries: int = 10
    base_delay: float = 1.0
    max_delay: float = 30.0

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ReconnectConfig":
        if data is None:
            return cls()
        return cls(
            max_retries=data.get("max_retries", 10),
            base_delay=data.get("base_delay", 1.0),
            max_delay=data.get("max_delay", 30.0),
        )


@dataclass
class SubtitleConfig:
    font_path: str = ""
    font_size: int = 32
    max_lines: int = 2
    position: str = "bottom"
    offset_y: int = 20
    text_color: Tuple[int, int, int] = (255, 255, 255)
    stroke_color: Tuple[int, int, int] = (0, 0, 0)
    stroke_width: int = 2

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "SubtitleConfig":
        if data is None:
            return cls()
        return cls(
            font_path=data.get("font_path", ""),
            font_size=data.get("font_size", 32),
            max_lines=data.get("max_lines", 2),
            position=data.get("position", "bottom"),
            offset_y=data.get("offset_y", 20),
            text_color=tuple(data.get("text_color", [255, 255, 255])),
            stroke_color=tuple(data.get("stroke_color", [0, 0, 0])),
            stroke_width=data.get("stroke_width", 2),
        )


WEBSOCKET_URL_PATTERN = "wss://{workspace_id}.{region}.maas.aliyuncs.com/api-ws/v1/inference"


@dataclass
class ASRSettings:
    provider: ASRProvider = ASRProvider.DASHSCOPE
    api_key: str = ""
    workspace_id: str = ""
    region: str = "cn-beijing"
    model: str = "fun-asr-realtime"
    reconnect: ReconnectConfig = field(default_factory=ReconnectConfig)
    subtitle: SubtitleConfig = field(default_factory=SubtitleConfig)

    def __post_init__(self):
        if self.api_key.startswith("${") and self.api_key.endswith("}"):
            env_var = self.api_key[2:-1]
            self.api_key = os.environ.get(env_var, self.api_key)

    @property
    def websocket_url(self) -> str:
        return WEBSOCKET_URL_PATTERN.format(
            workspace_id=self.workspace_id or "dashscope",
            region=self.region,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ASRSettings":
        data = deepcopy(data)
        if "provider" in data:
            data["provider"] = ASRProvider[data.pop("provider").upper()]
        if "reconnect" in data:
            data["reconnect"] = ReconnectConfig.from_dict(data.pop("reconnect"))
        if "subtitle" in data:
            data["subtitle"] = SubtitleConfig.from_dict(data.pop("subtitle"))
        return cls(**data)
```

**Step 4: Run test, verify PASS**

```bash
cd /mnt/c/Users/jiacheng.wan/Documents/projects/streamparser && python -m pytest tests/python/test_asr_config.py -v
```
Expected: all 6 tests pass

**Step 5: Commit**

```bash
git add tests/python/test_asr_config.py streamparser/asr_config.py && git commit -m "feat: add ASR configuration data classes"
```

---

## Task 3: SubtitleEntry + subtitle_buffer (Thread-Safe Deque)

**TDD — Red first**

**Files:**
- Create: `streamparser/subtitle.py`
- Create: `tests/python/test_subtitle.py`

**Step 1: Write failing test**

```python
# tests/python/test_subtitle.py
import pytest
from streamparser.subtitle import SubtitleEntry

class TestSubtitleEntry:
    def test_create_basic(self):
        entry = SubtitleEntry(
            index=1,
            text="你好世界",
            start_time=0.5,
            end_time=2.0,
            is_final=True,
        )
        assert entry.text == "你好世界"
        assert entry.start_time == 0.5
        assert entry.end_time == 2.0
        assert entry.is_final is True

    def test_to_srt_format(self):
        entry = SubtitleEntry(
            index=1,
            text="你好世界",
            start_time=1.500,
            end_time=4.020,
            is_final=True,
        )
        srt = entry.to_srt()
        assert "1" in srt
        assert "00:00:01,500" in srt
        assert "00:00:04,020" in srt
        assert "你好世界" in srt

    def test_intermediate_not_final(self):
        entry = SubtitleEntry(
            index=2,
            text="今天天气",
            start_time=0.3,
            end_time=None,
            is_final=False,
        )
        assert entry.is_final is False
        assert entry.end_time is None

    def test_format_time_zero(self):
        entry = SubtitleEntry(index=1, text="", start_time=0.0, end_time=1.0, is_final=True)
        assert entry._format_time(0.0) == "00:00:00,000"
        assert entry._format_time(61.5) == "00:01:01,500"
        assert entry._format_time(3661.999) == "01:01:01,999"
```

**Step 2: Run, verify FAIL**

**Step 3: Write minimal implementation**

```python
# streamparser/subtitle.py
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict, List, Optional


@dataclass
class SubtitleEntry:
    index: int
    text: str
    start_time: float
    end_time: Optional[float] = None
    is_final: bool = False

    def _format_time(self, seconds: float) -> str:
        ms = int((seconds % 1) * 1000)
        total_sec = int(seconds)
        h = total_sec // 3600
        m = (total_sec % 3600) // 60
        s = total_sec % 60
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    def to_srt(self) -> str:
        if self.end_time is None:
            return ""
        return (
            f"{self.index}\n"
            f"{self._format_time(self.start_time)} --> {self._format_time(self.end_time)}\n"
            f"{self.text}\n"
        )
```

**Step 4: Run, verify PASS**

**Step 5: Commit**

---

## Task 4: AudioProcessor (PyAV Resampler + Buffer)

**TDD — Red first**

**Files:**
- Create: `streamparser/audio_processor.py`
- Create: `test/test_audio_processor.py`

**Step 1: Write failing test**

```python
# test/test_audio_processor.py
import numpy as np
import av
import pytest
from streamparser.audio_processor import AudioProcessor

class TestAudioProcessor:
    def test_resample_stereo_48k_to_mono_16k_int16(self):
        # Create a synthetic stereo 48kHz float audio frame
        samples_per_channel = 1024
        data = np.random.randn(2, samples_per_channel).astype(np.float32)
        frame = av.AudioFrame.from_ndarray(data, format="fltp", layout="stereo")
        frame.sample_rate = 48000

        processor = AudioProcessor(target_sample_rate=16000, target_format="s16", target_layout="mono")
        result = processor.process(frame)

        assert isinstance(result, np.ndarray)
        assert result.dtype == np.int16
        assert len(result.shape) == 1  # mono = 1D after squeeze
        # ~1024 * (16000/48000) ≈ 341 samples

    def test_passthrough_when_already_correct_format(self):
        data = np.ones(320, dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(data.reshape(1, 320), format="s16", layout="mono")
        frame.sample_rate = 16000

        processor = AudioProcessor(target_sample_rate=16000, target_format="s16", target_layout="mono")
        result = processor.process(frame)

        assert result.dtype == np.int16
        assert len(result) == 320
```

**Step 2: Run, verify FAIL**

**Step 3: Write minimal implementation**

```python
# streamparser/audio_processor.py
import av
import numpy as np
from av.frame import Frame as AVFrame
from dataclasses import dataclass


@dataclass
class AudioProcessor:
    target_sample_rate: int = 16000
    target_format: str = "s16"
    target_layout: str = "mono"

    def __post_init__(self):
        self._resampler = av.AudioResampler(
            format=self.target_format,
            layout=self.target_layout,
            rate=self.target_sample_rate,
        )

    def process(self, frame: AVFrame) -> np.ndarray:
        resampled_frames = self._resampler.resample(frame)
        arrays = [f.to_ndarray() for f in resampled_frames]
        if not arrays:
            return np.array([], dtype=np.int16)
        result = np.concatenate(arrays, axis=-1) if len(arrays) > 1 else arrays[0]
        return result.squeeze().astype(np.int16)
```

**Step 4: Run, verify PASS**

**Step 5: Commit**

---

## Task 5: ASRClient (DashScope WS wrapper)

**TDD — Red first**

**Files:**
- Create: `streamparser/asr_client.py`
- Create: `tests/python/test_asr_client.py`

**Note:** ASRClient wraps DashScope Recognition. The unit test should use a mock/stub for the Recognition class since we don't want real API calls in unit tests.

**Step 1: Write failing test**

```python
# tests/python/test_asr_client.py
import queue
import threading
from unittest.mock import MagicMock, patch
import pytest
from streamparser.asr_client import ASRClient, ASRState
from streamparser.asr_config import ASRSettings

class TestASRClient:
    @patch("streamparser.asr_client.Recognition")
    def test_start_stop_lifecycle(self, mock_recognition_cls):
        mock_rec = MagicMock()
        mock_recognition_cls.return_value = mock_rec

        settings = ASRSettings(api_key="sk-test")
        client = ASRClient(settings)
        
        assert client.state == ASRState.IDLE
        client.start()
        assert client.state == ASRState.RUNNING
        mock_rec.start.assert_called_once()
        
        client.stop()
        assert client.state == ASRState.STOPPED
        mock_rec.stop.assert_called_once()

    @patch("streamparser.asr_client.Recognition")
    def test_feed_audio_buffers(self, mock_recognition_cls):
        mock_rec = MagicMock()
        mock_recognition_cls.return_value = mock_rec

        settings = ASRSettings(api_key="sk-test")
        client = ASRClient(settings, chunk_size=3200)
        client.start()
        
        # Feed less than chunk_size, should buffer
        client.feed_audio(b"\x00" * 1600)
        mock_rec.send_audio_frame.assert_not_called()
        
        # Feed enough to trigger send
        client.feed_audio(b"\x00" * 1600)
        assert mock_rec.send_audio_frame.call_count == 1
        assert len(mock_rec.send_audio_frame.call_args[0][0]) == 3200

    @patch("streamparser.asr_client.Recognition")
    def test_sentence_callback_updates_buffer(self, mock_recognition_cls):
        mock_rec = MagicMock()
        mock_recognition_cls.return_value = mock_rec

        settings = ASRSettings(api_key="sk-test")
        client = ASRClient(settings)
        client.start()
        
        # Verify subtitle_buffer is accessible
        assert client.subtitle_buffer is not None
```

**Step 2: Run, verify FAIL**

**Step 3: Write minimal implementation**

```python
# streamparser/asr_client.py
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from .asr_config import ASRSettings
from .logger import Loggers
from .subtitle import SubtitleEntry

LOGGER = Loggers().get_logger(__name__)


class ASRState(Enum):
    IDLE = auto()
    RUNNING = auto()
    STOPPED = auto()


class _Callback(RecognitionCallback):
    def __init__(self, client: "ASRClient"):
        self._client = client

    def on_open(self):
        LOGGER.info("ASR connection opened")

    def on_event(self, result: RecognitionResult):
        sentence = result.get_sentence()
        if isinstance(sentence, dict) and "text" in sentence:
            is_final = RecognitionResult.is_sentence_end(sentence)
            entry = SubtitleEntry(
                index=self._client._sentence_count + 1,
                text=sentence["text"],
                start_time=sentence.get("begin_time", 0) / 1000.0,
                end_time=(sentence["end_time"] / 1000.0) if sentence.get("end_time") else None,
                is_final=is_final,
            )
            self._client._on_sentence(entry)

    def on_complete(self):
        LOGGER.info("ASR recognition complete")

    def on_error(self, result: RecognitionResult):
        LOGGER.error(f"ASR error: {result.message}")
        self._client._on_error(str(result.message))

    def on_close(self):
        LOGGER.info("ASR connection closed")


@dataclass
class ASRClient:
    settings: ASRSettings
    chunk_size: int = 3200

    state: ASRState = ASRState.IDLE
    subtitle_buffer: deque = field(default_factory=deque)

    _recognition: Optional[Recognition] = field(default=None, init=False)
    _worker: Optional[threading.Thread] = field(default=None, init=False)
    _input_queue: queue.Queue = field(default_factory=queue.Queue, init=False)
    _audio_buffer: bytearray = field(default_factory=bytearray, init=False)
    _running: threading.Event = field(default_factory=threading.Event, init=False)
    _sentence_count: int = field(default=0, init=False)

    def start(self):
        if self.state != ASRState.IDLE:
            return
        self._running.set()
        self._recognition = Recognition(
            model=self.settings.model,
            format="pcm",
            sample_rate=16000,
            callback=_Callback(self),
        )
        self._recognition.start()
        self._worker = threading.Thread(target=self._worker_loop, name="asr_worker", daemon=True)
        self._worker.start()
        self.state = ASRState.RUNNING

    def stop(self):
        self._running.clear()
        self._input_queue.put(None)  # sentinel
        if self._worker and self._worker.is_alive():
            self._worker.join(timeout=5.0)
        if self._recognition:
            self._recognition.stop()
        self.state = ASRState.STOPPED

    def feed_audio(self, pcm_bytes: bytes):
        self._input_queue.put(pcm_bytes)

    def _worker_loop(self):
        while self._running.is_set():
            try:
                chunk = self._input_queue.get(timeout=0.5)
                if chunk is None:
                    break
                self._audio_buffer.extend(chunk)
                while len(self._audio_buffer) >= self.chunk_size:
                    self._recognition.send_audio_frame(bytes(self._audio_buffer[:self.chunk_size]))
                    self._audio_buffer = self._audio_buffer[self.chunk_size:]
            except queue.Empty:
                continue

    def _on_sentence(self, entry: SubtitleEntry):
        if entry.is_final:
            self._sentence_count += 1
        self.subtitle_buffer.append(entry)

    def _on_error(self, message: str):
        LOGGER.warning(f"ASR error: {message}")
```

**Step 4: Run, verify PASS**

**Step 5: Commit**

---

## Task 6: SRTWriter (segmented SRT output)

**TDD — Red first**

**Files:**
- Create: `streamparser/srt_writer.py`
- Create: `tests/python/test_srt_writer.py`

**Step 1: Write failing test**

```python
# tests/python/test_srt_writer.py
import os
import tempfile
import pytest
from unittest.mock import MagicMock
from streamparser.srt_writer import SRTWriter
from streamparser.subtitle import SubtitleEntry

class TestSRTWriter:
    def test_open_close_creates_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = SRTWriter(base_dir=tmpdir, prefix="test")
            writer.open("0_test.mp4")
            assert writer._current_file is not None
            assert os.path.exists(writer._current_file)
            writer.close()

    def test_write_entry_appends_to_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = SRTWriter(base_dir=tmpdir, prefix="test")
            writer.open("0_test.mp4")
            entry = SubtitleEntry(index=1, text="hello", start_time=0.5, end_time=1.0, is_final=True)
            writer.write_entry(entry)
            writer._file.flush()
            with open(writer._current_file) as f:
                content = f.read()
            assert "hello" in content
            assert "00:00:00,500" in content
            writer.close()

    def test_rotate_switches_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = SRTWriter(base_dir=tmpdir, prefix="test")
            writer.open("0_video.mp4")
            old_file = writer._current_file
            writer.rotate("1_video.mp4")
            new_file = writer._current_file
            assert old_file != new_file
            assert os.path.exists(old_file)
            writer.close()
```

**Step 2: Run, verify FAIL**

**Step 3: Write minimal implementation**

```python
# streamparser/srt_writer.py
import os
from pathlib import Path

from .logger import Loggers
from .subtitle import SubtitleEntry

LOGGER = Loggers().get_logger(__name__)


class SRTWriter:
    def __init__(self, base_dir: str = "results", prefix: str = "subtitle"):
        self._base_dir = base_dir
        self._prefix = prefix
        self._file = None
        self._current_file = None
        self._entry_index = 0

    def open(self, video_filename: str):
        """Open SRT file paired with the given video filename."""
        name = Path(video_filename).stem
        self._current_file = os.path.join(self._base_dir, f"{name}.srt")
        self._file = open(self._current_file, "w", encoding="utf-8")
        self._entry_index = 0
        LOGGER.info(f"SRT file opened: {self._current_file}")

    def write_entry(self, entry: SubtitleEntry):
        if self._file is None or not entry.is_final:
            return
        self._entry_index += 1
        entry.index = self._entry_index
        srt_text = entry.to_srt()
        if srt_text:
            self._file.write(srt_text + "\n")
            self._file.flush()

    def rotate(self, video_filename: str):
        """Close current SRT and open a new one for the next video segment."""
        self.close()
        self.open(video_filename)

    def close(self):
        if self._file:
            self._file.close()
            self._file = None
            self._current_file = None
            LOGGER.info("SRT file closed")
```

**Step 4: Run, verify PASS**

**Step 5: Commit**

---

## Task 7: SubtitleOverlay (Pillow text rendering)

**TDD — Red first**

**Files:**
- Modify: `streamparser/subtitle.py` (add SubtitleOverlay)
- Create: `test/test_subtitle_overlay.py`

**Step 1: Write failing test**

```python
# test/test_subtitle_overlay.py
import numpy as np
import pytest
from streamparser.subtitle import SubtitleOverlay, SubtitleEntry
from streamparser.frame import Frame, FrameType

class TestSubtitleOverlay:
    def test_overlay_cjk_text(self):
        overlay = SubtitleOverlay(font_size=24)
        entry = SubtitleEntry(index=1, text="测试字幕", start_time=0.0, end_time=5.0, is_final=True)
        overlay.update_entry(entry)

        frame_data = np.zeros((480, 640, 3), dtype=np.uint8)
        frame = Frame(data=frame_data, id=0, time=1.0, type=FrameType.VIDEO, rate=30.0)

        result = overlay(frame)
        assert result.type == FrameType.VIDEO
        # Frame should still be RGB, shape unchanged
        assert result.data.shape == (480, 640, 3)
        # Should have some non-zero pixels (text drawn)
        assert result.data.sum() > 0

    def test_skip_non_video_frame(self):
        overlay = SubtitleOverlay()
        audio_data = np.zeros(1024, dtype=np.int16)
        frame = Frame(data=audio_data, id=0, time=0.0, type=FrameType.AUDIO, rate=48000.0)
        result = overlay(frame)
        assert result is frame  # unchanged, same object
```

**Step 2: Run, verify FAIL**

**Step 3: Write minimal implementation**

Add to `streamparser/subtitle.py`:

```python
class SubtitleOverlay:
    def __init__(self, font_path: str = "", font_size: int = 32):
        from PIL import ImageFont

        self._current_entry: Optional[SubtitleEntry] = None
        self._font_size = font_size
        self._font = None
        if font_path and os.path.exists(font_path):
            self._font = ImageFont.truetype(font_path, font_size)

    def update_entry(self, entry: SubtitleEntry):
        self._current_entry = entry

    def __call__(self, frame):
        if frame.type != FrameType.VIDEO:
            return frame
        if self._current_entry is None:
            return frame
        self._render(frame)
        return frame

    def _render(self, frame):
        from PIL import Image, ImageDraw, ImageFont
        import os

        if self._font is None:
            try:
                font_paths = [
                    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
                    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
                ]
                for fp in font_paths:
                    if os.path.exists(fp):
                        self._font = ImageFont.truetype(fp, self._font_size)
                        break
            except Exception:
                self._font = ImageFont.load_default()

        if self._font is None:
            return

        img = Image.fromarray(frame.data, mode="RGB")
        draw = ImageDraw.Draw(img)
        text = self._current_entry.text

        h, w = frame.data.shape[:2]
        bbox = draw.textbbox((0, 0), text, font=self._font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        x = max(0, (w - tw) // 2)
        y = max(0, h - th - 40)

        # Stroke
        draw.text((x - 2, y), text, font=self._font, fill=(0, 0, 0))
        draw.text((x + 2, y), text, font=self._font, fill=(0, 0, 0))
        draw.text((x, y - 2), text, font=self._font, fill=(0, 0, 0))
        draw.text((x, y + 2), text, font=self._font, fill=(0, 0, 0))
        # Text
        draw.text((x, y), text, font=self._font, fill=(255, 255, 255))

        frame.data = np.array(img, dtype=np.uint8)
```

**Step 4: Run, verify PASS**

**Step 5: Commit**

---

## Task 8: ASRSubtitlePipeline (Orchestrator)

**Files:**
- Create: `streamparser/asr_pipeline.py`
- Create: `test/test_asr_pipeline.py`

Wires together ASRClient, SRTWriter, SubtitleOverlay. Manages rotation callbacks, time synchronization, and lifecycle.

---

## Task 9: Wire into StreamProcessor (entrypoint.py)

**Files:**
- Modify: `streamparser/entrypoint.py`

Minimal changes: add ASR config loading, pipeline creation, and audio/video handling in _process().

---

## Task 10: Integration test with sample media

End-to-end test with a short test video file.
