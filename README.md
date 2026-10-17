[English](./README.md) | [中文](./README_zh.md)

<p align="center">
  <img src="docs/streamparser.svg" alt="streamparser" width="800"/>
</p>

# streamparser

A high-performance stream media processing and parsing toolkit with real-time ASR subtitle support.

Process live streams (RTSP/RTMP/HLS) or local media files, re-encode them with configurable quality, and optionally generate real-time burned-in subtitles via Alibaba Cloud DashScope speech recognition — all in a single pipeline.

## Features

### Core Stream Processing

- **Multi-protocol support** — RTSP, RTMP, HLS, HTTP, UDP, TCP, and local files
- **Hardware acceleration** — CUDA, VAAPI, QSV, Vulkan, VideoToolbox
- **Configurable encoding** — H.264, H.265, VP9, AV1 for video; AAC, MP3, FLAC, WAV for audio
- **Quality presets** — LOW (2Mbps/15fps), MEDIUM (4Mbps/30fps), HIGH (8Mbps/60fps)
- **Automatic segmentation** — Container rotation by duration or file size
- **Frame filtering** — Pluggable filter chain for custom frame processing
- **Stream health monitoring** — Pre-flight availability check + quality analysis on startup
- **Auto-retry** — Configurable retry with exponential backoff for unstable streams

### ASR Real-Time Subtitles

- **Real-time speech recognition** — Powered by Alibaba Cloud DashScope `fun-asr-realtime`
- **Burned-in subtitles** — Chinese text rendered directly onto video frames via Pillow
- **SRT file output** — Segmented `.srt` files synchronized with output video containers
- **Word-level timestamps** — Precise sentence and word alignment for accurate captioning
- **Auto-reconnect** — Configurable exponential backoff on WebSocket disconnection
- **CJK font support** — Auto-detection of system fonts (WenQuanYi Zen Hei, Droid Sans Fallback)

## Installation

Requires Python 3.10–3.12 and FFmpeg runtime libraries.

```bash
git clone https://github.com/your-org/streamparser.git
cd streamparser
pip install .
```

For ASR subtitle support, you also need a DashScope API key:

```bash
export DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
```

## Quick Start

### Basic stream processing

Read an RTSP stream and save as H.264 MP4 with medium quality:

```bash
streamparser rtsp://192.168.1.100:554/live
```

### Stream processing with YAML config

For full control over codecs, quality, and output:

```yaml
# config.yml
url: rtsp://192.168.1.100:554/live
type: BOTH
codec:
  video: h264
  audio: aac
quality:
  read: null
  write: high
directory: output
max_container_duration: 1800
max_retry_count: 5
```

```bash
streamparser config.yml
```

### Real-time subtitles

Add ASR subtitle generation with a separate config file:

```bash
streamparser rtsp://192.168.1.100:554/live --asr examples/asr_config.example.yml
```

The output directory will contain paired video and subtitle files:

```
output/
├── 0_20260709_143000.mp4
├── 0_20260709_143000.srt
├── 1_20260709_143030.mp4
└── 1_20260709_143030.srt
```

### Local FunASR Vulkan (no API key, file mode)

A Windows Vulkan GGUF install can be connected to the WebUI through a local HTTP service. This mode transcribes an entire local audio/video file before playback; it does not provide live captions. See the [local FunASR Vulkan guide](docs/funasr-vulkan.md) for setup and usage.

## Configuration

### Stream settings (`config.yml`)

| Field | Type | Default | Description |
|---|---|---|---|
| `url` | string | — | Stream URL or local file path |
| `type` | enum | `BOTH` | `VIDEO`, `AUDIO`, or `BOTH` |
| `protocol` | enum | auto | Force protocol: `RTSP`, `RTMP`, `HLS`, `HTTP`, `TCP`, `UDP`, `FILE` |
| `codec.video` | string/enum | `h264` | `h264`, `h265`, `vp9`, `av1` |
| `codec.audio` | string/enum | `aac` | `aac`, `mp3`, `wav`, `flac` |
| `quality.read` | enum | `null` | Read quality: `null`, `low`, `medium`, `high` |
| `quality.write` | enum | `medium` | Write quality: `low`, `medium`, `high` |
| `directory` | string | `results` | Output directory |
| `max_container_duration` | int | `1800` | Max segment duration (seconds) |
| `max_container_byte_size` | int | `4GB` | Max segment size (bytes) |
| `max_retry_count` | int | `5` | Max reconnection attempts |
| `max_retry_wait_time` | float | `60.0` | Max wait between retries (seconds) |
| `timeout` | float | `10.0` | Stream open timeout (seconds) |
| `queue_size` | int | `100` | Internal frame queue size |

### Hardware acceleration

Add a `hwaccel_type` field to your codec config:

```yaml
codec:
  video:
    name: h264
    encoder: libx264
    decoder: libx264
    format: mp4
    type: VIDEO
    hwaccel_type: CUDA    # CUDA, VAAPI, QSV, VULKAN, or VIDEOTOOLBOX
```

### ASR subtitle settings (`asr_config.yml`)

| Field | Type | Default | Description |
|---|---|---|---|
| `provider` | string | `dashscope` | ASR provider |
| `api_key` | string | `${DASHSCOPE_API_KEY}` | API key (supports `${ENV_VAR}` syntax) |
| `workspace_id` | string | `""` | DashScope workspace ID |
| `region` | string | `cn-beijing` | Service region |
| `model` | string | `fun-asr-realtime` | ASR model |
| `reconnect.max_retries` | int | `10` | Max WebSocket reconnect attempts |
| `reconnect.base_delay` | float | `1.0` | Initial reconnect delay (seconds) |
| `reconnect.max_delay` | float | `30.0` | Max reconnect delay cap |
| `subtitle.font_size` | int | `32` | Subtitle font size |
| `subtitle.max_lines` | int | `2` | Max simultaneous lines |
| `subtitle.position` | string | `bottom` | `bottom` or `top` |
| `subtitle.offset_y` | int | `20` | Vertical offset from edge (pixels) |
| `subtitle.text_color` | list | `[255,255,255]` | RGB text color |
| `subtitle.stroke_color` | list | `[0,0,0]` | RGB stroke color |
| `subtitle.stroke_width` | int | `2` | Stroke width (pixels) |

## Architecture

```
                         StreamProcessor
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  StreamReader ── Queue ── _process() ── Queue ── Writer     │
│      │                       │                               │
│      │ audio → ASR filter    │ video → SubtitleOverlay       │
│      │                       │                               │
│      └───────┬───────────────┘                               │
│              ▼                                               │
│  ┌─────────────────────────────┐                            │
│  │   ASRSubtitlePipeline        │                            │
│  │  ┌───────────────────────┐  │                            │
│  │  │  ASRClient             │  │                            │
│  │  │  DashScope WebSocket   │  │                            │
│  │  │  AudioProcessor        │  │                            │
│  │  └───────────┬───────────┘  │                            │
│  │              ▼               │                            │
│  │  ┌───────────────────────┐  │                            │
│  │  │   subtitle_buffer      │  │                            │
│  │  └─────┬─────────┬───────┘  │                            │
│  │        ▼         ▼           │                            │
│  │  SubtitleOverlay  SRTWriter  │                            │
│  │  (Pillow render)  (file I/O) │                            │
│  └─────────────────────────────┘                            │
└──────────────────────────────────────────────────────────────┘
```

The pipeline runs on three threads:
- **Reader thread** — PyAV decode + frame filter chain
- **Writer thread** — PyAV encode + mux to container
- **ASR worker thread** — Audio resampling, WebSocket send/receive, callback dispatch

### Data flow

1. `StreamReader` decodes audio/video frames from the source via PyAV
2. Audio frames are filtered through `AudioProcessor` (resample to 16kHz mono s16)
3. PCM bytes are sent to DashScope via WebSocket in 100ms chunks
4. ASR results arrive asynchronously via callback → stored in `subtitle_buffer`
5. `SubtitleOverlay` (registered as a frame filter) reads the buffer and renders text onto video frames using Pillow
6. `SRTWriter` writes finalized sentences to `.srt` files, synchronized with output container segmentation
7. `StreamWriter` encodes the modified frames (with burned-in subtitles) into the output file

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest test/ -v

# Run specific test file
pytest test/test_asr_client.py -v
```

Tests follow strict TDD with `unittest.mock` for external dependencies. No real API calls are made during unit testing.

## Dependencies

| Package | Purpose |
|---|---|
| `av` (PyAV) | FFmpeg bindings — audio/video decode/encode |
| `opencv-python` | Video frame processing |
| `numpy` | Array operations |
| `librosa` | Audio analysis |
| `dashscope` | Alibaba Cloud ASR SDK |
| `Pillow` | CJK subtitle text rendering |
| `pyyaml` | Configuration file parsing |
| `concurrent-log-handler` | Multi-process safe logging |
