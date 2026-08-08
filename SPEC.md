# SPEC：直播实时字幕工具（asr_project）

## 1. 目标（Why）

为观看直播/视频提供**实时中文字幕**，服务于听力障碍人群及日常观看场景。
核心诉求：**准确、句子级、低延迟**。

## 2. 已确认决策（grill 结论，2026-08-08）

| # | 决策 | 结论 |
|---|------|------|
| D1 | 目标语言 | 中文为主（引擎需支持多语言自动识别，中文精度优先） |
| D2 | 架构 | **引擎抽象层**：本地模型 / 外部 API 可插拔切换，用户可配置 |
| D3 | 引擎路线 | 前期用现成方案验证链路；精度天花板靠阿里云 FunASR API 或本地 SenseVoice |
| D4 | 输入模式 | **拉流模式**：用户输入视频流/音频流地址（或本地文件），系统拉流生成字幕；**不捕获本机声卡**（用户为 SSH 远程使用场景） |
| D5 | 输入形式 | 全部支持 + 自动识别：直接流地址（m3u8/flv/mp4 等）、平台页面链接（yt-dlp 解析）、本地文件 |
| D6 | 字幕形态 | 句子级（自带标点/时间戳），延迟目标 < 3s |
| D7 | 当前引擎 | faster-whisper small（int8_float32，适配 GTX 1050 Ti 4GB / Pascal 无 fp16） |

## 3. 架构

```
输入（流URL / 页面链接 / 本地文件）
        │
        ▼
[拉流解码] PyAV 解码 → 重采样 16kHz mono int16 PCM
        │
        ▼
[引擎抽象层] Engine 接口
   ├─ FasterWhisperEngine（当前：faster-whisper small int8_float32, GPU）
   ├─ AliyunFunASREngine（后期：阿里云百炼 dashscope SDK，句子级）
   └─ SenseVoiceEngine（后期：本地 SenseVoice，<1GB 显存）
        │
        ▼
[流式处理] whisper_streaming OnlineASRProcessor（LocalAgreement 确认 + 滑窗）
        │
        ▼
[字幕输出] 终端实时打印（beg end text，毫秒时间戳）；后续可扩展 SRT/悬浮窗
```

## 4. 技术约束（环境实测）

- 宿主机：Ubuntu 22.04，Python 3.10.12，Docker 29.1.3
- GPU：GTX 1050 Ti 4GB（Pascal，**不支持高效 fp16**，用 int8_float32/int8）
- 无系统 CUDA toolkit；cuBLAS/cuDNN 通过 pip 包（nvidia-cublas-cu12, nvidia-cudnn-cu12）提供，需 LD_LIBRARY_PATH 指向 venv 内 nvidia lib
- 网络：GitHub/HF 被墙（gh-proxy.com / hf-mirror.com 可用）；**ModelScope 直连**（模型下载走它）；pypi 走清华源
- 音频解码：PyAV（av 包，捆绑 ffmpeg 库）—— **无需系统 ffmpeg，无需 sudo**
- venv：`.venv`（virtualenv 创建，因系统缺 python3.10-venv）

## 5. 里程碑

- **M0 ✅**：whisper_streaming 验证 —— 本地文件实时字幕（延迟 ~0.5s）、系统音频链路（parec→nc→server）均已打通
- **M1 ✅**：拉流字幕工具 `tools/stream_subs.py`（URL/文件 → 实时字幕，ticket-01/02）
- **M2 ✅**：输入自动识别 `tools/input_resolver.py`（流地址 / 平台页面 yt-dlp / 本地文件，ticket-03）
- **M3 ✅**：引擎抽象层 `tools/engines/`（Engine 接口 + FasterWhisperEngine + create_engine，ticket-04）
- **M4 ⏳**：阿里云 FunASR API 引擎（`AliyunFunASREngine` 已实现复用 streamparser ASRClient，**待用户提供 DASHSCOPE_API_KEY + workspace_id 实测**，ticket-05 issue #3）
- **M5 ✅/🔜**：webUI 单页 MVP（`tools/webui.py`，流地址输入 + 引擎下拉 + 大字号字幕，ticket-06）；SRT 导出/悬浮窗/SenseVoice 待后续
- **CI ✅**：GitHub Actions —— Test（pytest 53 用例 × 3.10-3.12）+ Pylint（errors-only，排除 vendored）全绿

## 6. 已知限制

- whisper small 中文精度一般（繁体输出、错字）→ M4/M5 引擎升级解决
- 无语音/音乐段 whisper 会幻觉重复（"詞曲 李宗盛"刷屏）→ VAD 或引擎升级缓解
- 拉流 URL 需可达（直播流地址常有时效性，需用户提供有效地址）
