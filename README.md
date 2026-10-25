# Live and video source-language captions

Run on your Windows PC and watch in a browser with original-language subtitles: Chinese speech stays Chinese and English speech stays English. The current UI prioritizes speech recognition and caption timing; cross-language translation is paused. Cloud recognition uses your own mainland-China DashScope API key for FunASR.

Install 64-bit Python 3.10–3.12 (3.12 recommended, including the Python Launcher), place the checkout on a drive with free space, and run `start-windows.cmd`. It creates a project-local environment, installs `requirements-webui.txt`, and opens <http://127.0.0.1:8002/>. Configure your key in Settings, open a live URL or local video, and choose Generate captions. Language detection is automatic by default; Chinese and English hints are optional.

Audio is sent to DashScope and billed to your API account. Keys are in memory by default; optional persistence uses Windows user-bound DPAPI encryption. The connection check uses ASR only. No translation request is made by the current UI or the default session API.

Local audio can be processed ahead of playback. Once recognition completes, pause and seek reuse the captions without another recognition request. Captions follow audio timestamps and appear as short phrases rather than word-by-word drafts. The supplied 30.12-second Chinese video took about 8.1 seconds to recognize on this machine; other files can differ. Recognition errors remain possible. Live-source coverage, other languages and long-running playback require further acceptance testing.

The 2026-10-25 Ubuntu continuation records real corpus scores, control checks and live-source limitations in the [acceptance record](docs/plans/2026-10-24-original-caption-real-acceptance.md) and [source matrix](docs/research/2026-10-25-original-caption-support-matrix.md). Windows and independent timing acceptance remain separate.

See the [Chinese getting-started guide](README_zh.md), [current product decision](docs/adr/0008-original-language-captions.md), [PRD](docs/plans/2026-10-21-live-translation-prd.md), and [UI acceptance record](docs/ui-design.md). The original toolkit documentation follows.

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

### Share a live caption stream on your LAN

Start the WebUI on the computer hosting the stream, enter the source, and click **Start**. The service listens on LAN interfaces by default, while the management page remains accessible only from the host computer. After the live session starts, click **Copy viewer link** and send it to devices on the same Wi-Fi/LAN. If the host has multiple network adapters, first select the address reachable from the viewer devices. Viewers can open the link in a browser without entering the source or starting another ASR session.

Each live session allows up to three concurrent viewer devices. Viewer links are read-only and expire when the stream stops; a new stream gets a new link. Video is relayed through the host, so allow the WebUI port (default `8002`) through the host firewall on private networks. The link is not accessible from outside the LAN.

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
