<p align="center">
  <img src="docs/assets/readme-banner.svg" alt="Stream with ASR：直播与本地视频原文字幕" width="1200">
</p>

<h1 align="center">直播与视频原文字幕</h1>

<p align="center">在浏览器里观看直播或本地视频，听中文看中文，听英文看英文。</p>

<p align="center">
  <a href="https://github.com/AiKiAi-stack/stream-with-asr/actions/workflows/test.yml"><img src="https://github.com/AiKiAi-stack/stream-with-asr/actions/workflows/test.yml/badge.svg" alt="自动测试"></a>
  <img src="https://img.shields.io/badge/Python-3.10–3.12-3776AB?logo=python&amp;logoColor=white" alt="Python 3.10 至 3.12">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-4c9b77" alt="MIT 许可证"></a>
  <img src="https://img.shields.io/badge/Status-Experimental_preview-d5a34a" alt="实验预览版">
</p>

<p align="center">
  <a href="README.md">English</a> · <b>简体中文</b><br>
  <a href="#快速开始">快速开始</a> · <a href="#使用方式">使用方式</a> · <a href="#当前进展与边界">当前进展</a> · <a href="#开发与文档">开发与文档</a>
</p>

## 可以做什么

- 播放直播链接或本地视频，通过百炼 FunASR 生成原文字幕，默认自动识别中文、英文。
- 先选频道或文件，再点击播放；画面显示按语音时间切分的稳定短句，完整内容可在字幕记录查看。
- 本地字幕识别完成后，暂停、继续和跳转复用已有结果。
- 将直播的只读观众链接分享给同一局域网内的设备，复用主机的识别会话。

> **实验预览版。** 当前主界面专注原文字幕，跨语言翻译暂停。启动和固定输入长时回放已有预览验收记录；独立声学同步与覆盖率仍未通过正式验收。[查看当前进展](#当前进展与边界)。

## 快速开始

需要 **64 位 Python 3.10–3.12**（推荐 3.12）和已开通 `fun-asr-realtime` 的**百炼中国内地 API Key**。云端 WebUI 不要求本地 GPU、本地模型或另装系统 FFmpeg。

下载并解压本仓库，或克隆：

```bash
git clone https://github.com/AiKiAi-stack/stream-with-asr.git
cd stream-with-asr
```

### Windows

安装 Python 时启用 Python Launcher，然后双击 **`start-windows.cmd`**。

首次启动会创建项目内的 `.venv` 并安装 WebUI 依赖，随后自动打开 [http://127.0.0.1:8002/](http://127.0.0.1:8002/)。在「设置」填入并应用 Key，即可选择内容。

### Linux / 手动启动

在项目目录执行：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-webui.txt
python tools/webui.py --host 127.0.0.1 --port 8002
```

使用 Python 3.10 或 3.11 时，替换第一行的解释器名称。浏览器打开 [http://127.0.0.1:8002/](http://127.0.0.1:8002/)，在「设置」应用 Key。

## 使用方式

| 步骤 | 操作 |
| --- | --- |
| **1. 选择内容** | 粘贴直播链接；若是 M3U 频道清单，点击「选择频道」选台。也可点击「打开文件」选择本地视频。 |
| **2. 播放与字幕** | 点击「播放并生成字幕」，字幕就绪后开始播放。选台本身不会发起识别。 |

播放器下方可开关字幕、查看字幕记录或进入全屏；字号与位置在「设置」调整。

识别完成的本地文件可以直接跳转。尚未准备的位置可能需要重新识别，界面会提示。直播遇到字幕尚未就绪时，音视频可能一起短暂停下等待；公共源也可能受网络、地区或账号限制影响。

### 局域网观看

以 `python tools/webui.py --host 0.0.0.0 --port 8002` 启动，主机仍从本机地址打开管理页面。开始直播后，在设置中复制观众链接；多网卡时选择观众可访问的地址，并允许家庭/专用网络访问端口 `8002`。

每场直播最多 3 台观众设备。观众无需另填 Key 或重复启动识别，链接在直播停止后失效。

### 没有稳定的直播源？

可先用本地长视频验证观看流程。开发测试还可将文件按实时速度转为滚动 HLS；仓库附有[公开频道清单](examples/public-live-channels.m3u)和[中英文新闻回放方法](docs/research/p0-local-news-2026-11-04.md)。公开地址不保证随时可用，测试媒体需自行准备。

## 云端费用与 Key

音频会发送至百炼，识别费用由你的 API 账户承担；「检查连接」也会连接云端识别服务。当前默认流程不调用翻译模型。

Key 默认仅在服务内存中使用。Windows 可选择通过用户绑定的 DPAPI 加密保存；Key 不写入浏览器 localStorage、URL 或普通日志。「删除配置」会清除保存文件并停止云端会话。

本地离线转写可参考 [FunASR Vulkan 指南](docs/funasr-vulkan.md)，该模式须等待整段文件转写完成，不支持直播字幕。

## 当前进展与边界

以下为 **2026-11-04** 对 PR #89 候选的验证范围；预览版门槛见 [ADR-0009](docs/adr/0009-pr89-preview-acceptance-2026-11-04.md)。

| 项目 | 当前结果 |
| --- | --- |
| 启动 | 每语种 20 次固定 HLS 实测：中文 P95 **5.014 秒**、英文 **4.470 秒**，达到预览版 5.5 秒门槛；原 3 秒目标仍保留。 |
| 长时回放 | 固定中英文新闻各 **30 分钟**，0 公开错误；应用时间轴延迟代理增长均不超过 1 秒。该代理不代表独立声学同步。 |
| 音画字幕同步与有声覆盖 | **尚未验证通过**；少量短暂页面、空字幕及识别错误仍需继续改善。 |
| 公网来源与设备 | 公网直播需逐源验证；干净 Windows 安装与真实第二设备不在本轮验收范围，不计通过。 |

预览范围可用，正式首版门槛仍未全部通过。完整数据见[验收复测](docs/plans/2026-11-04-pr89-acceptance-recheck.md)；历史失败与已知质量问题保留在[恢复记录](docs/research/original-caption-recovery-2026-11-02.md)。

## 常见问题

<details>
<summary>有画面，却没有字幕？</summary>

先检查是否已在设置中应用 Key，以及是否点击了「播放并生成字幕」。确认播放器未关闭字幕，再查看状态提示与字幕记录。公共源可以换成本地视频对照；页面仍有错误时，打开设置中的「诊断日志」，反馈时不要附带 Key。

</details>

<details>
<summary>Windows 启动失败，怎样查看原因？</summary>

从项目目录执行：

```powershell
.venv\Scripts\python.exe tools\windows_launcher.py --check
```

启动日志位于 `logs/webui-windows.log`，配置位于 `.data/`，上传的视频副本位于 `uploads/`。

</details>

## 开发与文档

```bash
python -m pip install -e . pytest playwright
python -m pytest test/ -v
node --test test/*.test.cjs
```

前端检查使用 Node.js（CI 使用 22）。单元回归不需要真实 Key；真实识别验收单独执行并产生云端费用。

| 文档 | 内容 |
| --- | --- |
| [产品决策](docs/adr/0008-original-language-captions.md) · [术语](GLOSSARY.md) | 原文字幕的范围、行为与术语 |
| [预览验收调整](docs/adr/0009-pr89-preview-acceptance-2026-11-04.md) | 预览版门槛及正式发布缺口 |
| [新闻回放测试](docs/research/p0-local-news-2026-11-04.md) | 固定长视频、滚动 HLS 与字幕修正证据 |
| [Issues](https://github.com/AiKiAi-stack/stream-with-asr/issues) | 问题反馈与功能讨论；请附复现步骤、系统版本和脱敏日志 |
| [原工具包文档存档](docs/archive/streamparser_zh.md) | 旧流处理、配置与架构参考，含已不再提供的命令和组件 |

## 许可证

[MIT](LICENSE) · 云端识别由阿里云百炼 / DashScope 提供，项目包含基于 PyAV 的音视频处理代码。
