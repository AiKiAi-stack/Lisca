[English](./README.md) | [中文](./README_zh.md)

# 直播与视频原文字幕

在自己的 Windows 电脑运行，通过浏览器观看直播或本地视频，识别音频并显示原语言字幕：中文语音显示中文，英文语音显示英文。当前优先做好原文识别与同步，主界面已暂停跨语言翻译。云端模式使用阿里云百炼 FunASR，用户提供自己的中国内地 API Key。

## Windows 开始使用

1. 安装 64 位 Python 3.10–3.12（推荐 3.12，启用 Python Launcher），将本仓库放到有空间的目录，例如 D 盘。
2. 双击 `start-windows.cmd`。首次启动会在项目的 `.venv` 安装 WebUI 依赖，不下载本地模型；随后打开 `http://127.0.0.1:8002/`。
3. 在「设置」填写并应用百炼 Key，确保已开通 `fun-asr-realtime`。当前识别与连接检查均不需要翻译模型。
4. 输入直播链接或打开本地视频，点击「生成字幕」。默认自动识别语种，也可在设置中指定中文或英文。
5. 播放器按音频时间显示短句。已完成识别的本地视频可暂停、继续及跳转并复用字幕；识别未完成时跳转可能需要重新建立识别会话。

音频会发送给百炼，费用由用户 API 账户承担。「检查连接」也会连接云端识别服务。Key 默认只在本次运行中使用，可选择通过 Windows DPAPI 加密保存；「删除配置」会删除保存文件并停止云端会话。Key 不存入浏览器 localStorage、URL 或普通日志。

本地视频在当前位置的字幕就绪后即可播放，后续音频继续处理；静音也能放行，无需等待长文件全部识别结束。识别完成后的暂停和跳转复用本次字幕；尚未准备的位置会先提示可能再次调用识别服务。正式字幕按语音时间切成短句，不逐词滚动，也不把迟到的长段字幕排队挤到后续画面。识别仍可能有错字或截断；真实中英文质量、公共直播和长时间稳定性仍需分别验证。详见[当前产品决策](docs/adr/0008-original-language-captions.md)、[实施状态](docs/plans/2026-10-23-original-caption-implementation-status.md)与[发布证据核对](docs/plans/2026-10-24-original-caption-release-evidence.md)。

2026-10-24 的自动回归使用真实播放器和模拟识别，不能当作真实语音准确率通过。干净 Windows 安装及第二台局域网设备验收仍待完成；当前尚未达到 PRD 发布门槛。

2026-10-25追加的Ubuntu真实识别、控制与直播结果见[验收记录](docs/plans/2026-10-24-original-caption-real-acceptance.md)与[逐来源支持边界](docs/research/2026-10-25-original-caption-support-matrix.md)；各项失败、未测同步和外部验收单独保留。

平台页面与公开 IPTV 清单仅是来源候选，不能保证所有地址始终可播；已知网络、账号和地区限制需按来源验证。参见[来源调研](docs/research/2026-10-21-media-source-baseline.md)和[产品需求](docs/plans/2026-10-21-live-translation-prd.md)。

启动诊断：从项目目录运行 `.venv\Scripts\python.exe tools\windows_launcher.py --check`，或直接使用 `.venv\Scripts\python.exe tools\webui.py --port 8002`；依赖清单是 `requirements-webui.txt`。启动日志位于 `logs/webui-windows.log`，配置位于项目的 `.data/`，视频副本位于 `uploads/`。

## 原有流媒体工具包

以下为原有 streamparser 命令行功能与开发接口；WebUI 云端首版不要求本地 GPU 或本地识别模型。

<p align="center">
  <img src="docs/streamparser.svg" alt="streamparser" width="800"/>
</p>

# streamparser

高性能流媒体处理与解析工具包，支持实时 ASR 字幕生成。

一站式处理直播流（RTSP/RTMP/HLS）或本地媒体文件，按需转码，并可接入阿里云 DashScope 实时语音识别，将字幕实时烧录到视频画面中。

## 特性

### 核心流处理

- **多协议支持** — RTSP、RTMP、HLS、HTTP、UDP、TCP、本地文件
- **硬件加速** — CUDA、VAAPI、QSV、Vulkan、VideoToolbox
- **灵活编码** — 视频：H.264、H.265、VP9、AV1；音频：AAC、MP3、FLAC、WAV
- **画质预设** — LOW（2Mbps/15fps）、MEDIUM（4Mbps/30fps）、HIGH（8Mbps/60fps）
- **自动分段** — 按时长或文件大小自动轮转容器
- **帧过滤器链** — 可插拔的帧处理管道
- **流健康检测** — 启动前连通性检查 + 帧质量分析
- **自动重连** — 可配置的指数退避重试机制

### ASR 实时字幕

- **实时语音识别** — 基于阿里云 DashScope `fun-asr-realtime` 模型
- **画面烧录字幕** — 通过 Pillow 将中文文本直接渲染到视频帧上
- **SRT 文件输出** — 与输出视频容器同步分段生成 `.srt` 字幕文件
- **字级时间戳** — 精确的句级和字级对齐，实现精准字幕
- **自动重连** — WebSocket 断开后指数退避自动恢复
- **CJK 字体支持** — 自动探测系统字体（文泉驿正黑、Droid Sans Fallback），可配置自定义字体路径

## 安装

需要 Python 3.10–3.12 和 FFmpeg 运行时库。

```bash
git clone https://github.com/your-org/streamparser.git
cd streamparser
pip install .
```

使用 ASR 字幕功能需要 DashScope API Key：

```bash
export DASHSCOPE_API_KEY=sk-xxxxxxxxxxxxxxxx
```

## 快速开始

### 基础流处理

读取 RTSP 流，以中等画质保存为 H.264 MP4：

```bash
streamparser rtsp://192.168.1.100:554/live
```

### YAML 配置文件

完整控制编解码器、画质和输出参数：

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

### 实时字幕

通过独立配置文件启用 ASR 字幕：

```bash
streamparser rtsp://192.168.1.100:554/live --asr examples/asr_config.example.yml
```

输出目录中视频与字幕文件成对出现：

```
output/
├── 0_20260709_143000.mp4
├── 0_20260709_143000.srt
├── 1_20260709_143030.mp4
└── 1_20260709_143030.srt
```

### 在局域网内分享实时字幕直播

在运行 WebUI 的电脑上启动服务后，输入直播地址并点击「开始」。服务默认监听局域网接口；管理页面仍只允许在主机本机打开。直播开始后点击「复制观众链接」，即可发给同一 Wi-Fi/局域网内的设备。若电脑有多块网卡，先在下拉菜单中选择观众设备能访问的主机地址。观众用浏览器打开链接即可加入当前直播，无需单独配置直播源或 ASR。

每场直播最多允许 3 台观众设备同时观看。观众链接只读，停止直播后立即失效；下一场直播会生成新链接。视频流量经主机转发，请确保主机防火墙允许 WebUI 端口（默认 `8002`）在专用/家庭网络中接收连接。此链接不能从局域网外访问。

### 本地 FunASR Vulkan（无 API Key，文件模式）

Windows Vulkan GGUF 安装可通过本机 HTTP 服务接入 WebUI。该模式对本地音视频文件整段转写，完成后开始播放；不支持直播实时字幕。服务启动与 WebUI 使用步骤见[本地 FunASR Vulkan 指南](docs/funasr-vulkan.md)。

## 配置说明

### 流媒体设置 (`config.yml`)

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `url` | string | — | 流地址或本地文件路径 |
| `type` | enum | `BOTH` | `VIDEO`、`AUDIO` 或 `BOTH` |
| `protocol` | enum | 自动 | 强制协议：`RTSP`、`RTMP`、`HLS`、`HTTP`、`TCP`、`UDP`、`FILE` |
| `codec.video` | string/enum | `h264` | 视频编码：`h264`、`h265`、`vp9`、`av1` |
| `codec.audio` | string/enum | `aac` | 音频编码：`aac`、`mp3`、`wav`、`flac` |
| `quality.read` | enum | `null` | 读取质量：`null`、`low`、`medium`、`high` |
| `quality.write` | enum | `medium` | 写入质量：`low`、`medium`、`high` |
| `directory` | string | `results` | 输出目录 |
| `max_container_duration` | int | `1800` | 最大分段时长（秒） |
| `max_container_byte_size` | int | `4GB` | 最大分段大小（字节） |
| `max_retry_count` | int | `5` | 最大重连次数 |
| `max_retry_wait_time` | float | `60.0` | 重连最大等待时间（秒） |
| `timeout` | float | `10.0` | 打开流超时（秒） |
| `queue_size` | int | `100` | 内部帧队列大小 |

### 硬件加速

在编解码器配置中添加 `hwaccel_type`：

```yaml
codec:
  video:
    name: h264
    encoder: libx264
    decoder: libx264
    format: mp4
    type: VIDEO
    hwaccel_type: CUDA    # CUDA、VAAPI、QSV、VULKAN 或 VIDEOTOOLBOX
```

### ASR 字幕设置 (`asr_config.yml`)

| 字段 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `provider` | string | `dashscope` | ASR 服务商 |
| `api_key` | string | `${DASHSCOPE_API_KEY}` | API Key（支持 `${环境变量}` 语法） |
| `workspace_id` | string | `""` | 百炼业务空间 ID |
| `region` | string | `cn-beijing` | 服务地域 |
| `model` | string | `fun-asr-realtime` | ASR 模型 |
| `reconnect.max_retries` | int | `10` | WebSocket 最大重连次数 |
| `reconnect.base_delay` | float | `1.0` | 初始重连等待（秒） |
| `reconnect.max_delay` | float | `30.0` | 重连等待上限（秒） |
| `subtitle.font_path` | string | `""` | 自定义字体路径（留空自动探测） |
| `subtitle.font_size` | int | `32` | 字幕字号 |
| `subtitle.max_lines` | int | `2` | 最大同时显示行数 |
| `subtitle.position` | string | `bottom` | 位置：`bottom` 或 `top` |
| `subtitle.offset_y` | int | `20` | 距边缘垂直偏移（像素） |
| `subtitle.text_color` | list | `[255,255,255]` | 文字颜色 RGB |
| `subtitle.stroke_color` | list | `[0,0,0]` | 描边颜色 RGB |
| `subtitle.stroke_width` | int | `2` | 描边宽度（像素） |

## 架构

```
                         StreamProcessor
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│  StreamReader ── 队列 ── _process() ── 队列 ── Writer       │
│      │                       │                               │
│      │ 音频帧 → ASR filter    │ 视频帧 → SubtitleOverlay     │
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
│  │  (Pillow 渲染)    (文件 I/O) │                            │
│  └─────────────────────────────┘                            │
└──────────────────────────────────────────────────────────────┘
```

系统运行在三个线程上：
- **Reader 线程** — PyAV 解码 + 帧过滤器链
- **Writer 线程** — PyAV 编码 + 容器封装
- **ASR 工作线程** — 音频重采样、WebSocket 收发、回调分发

### 数据流

1. `StreamReader` 通过 PyAV 从源解码音视频帧
2. 音频帧经过 `AudioProcessor`（重采样到 16kHz 单声道 s16）
3. PCM 数据以 100ms 为单位通过 WebSocket 发送到 DashScope
4. ASR 识别结果通过回调异步返回 → 存入 `subtitle_buffer`
5. `SubtitleOverlay`（注册为帧过滤器）读取缓冲区，用 Pillow 将文本渲染到视频帧上
6. `SRTWriter` 将完成的句子写入 `.srt` 文件，与输出容器分段同步
7. `StreamWriter` 将修改后的帧（含烧录字幕）编码写入输出文件

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行全部测试
pytest test/ -v

# 运行单个测试文件
pytest test/test_asr_client.py -v
```

测试遵循严格的 TDD 流程，外部依赖通过 `unittest.mock` 隔离，单元测试不产生真实 API 调用。

## 依赖

| 包 | 用途 |
|---|---|
| `av` (PyAV) | FFmpeg 绑定 — 音视频编解码 |
| `opencv-python` | 视频帧处理 |
| `numpy` | 数组运算 |
| `librosa` | 音频分析 |
| `dashscope` | 阿里云 ASR SDK |
| `Pillow` | CJK 字幕文字渲染 |
| `pyyaml` | 配置文件解析 |
| `concurrent-log-handler` | 多进程安全日志 |
