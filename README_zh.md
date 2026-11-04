<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/lisca-wordmark-dark.svg">
    <img src="docs/assets/lisca-wordmark-light.svg" alt="Lisca" width="320">
  </picture>
</p>

<h3 align="center">给直播加上实时字幕</h3>

<p align="center">
  <a href="#开始使用">开始使用</a> ·
  <a href="#观看效果">观看效果</a> ·
  <a href="#功能">功能</a> ·
  <a href="https://github.com/AiKiAi-stack/stream-with-asr/issues">反馈问题</a> ·
  <a href="README.md">English</a>
</p>

<p align="center">
  <a href="https://github.com/AiKiAi-stack/stream-with-asr/actions/workflows/test.yml"><img src="https://github.com/AiKiAi-stack/stream-with-asr/actions/workflows/test.yml/badge.svg" alt="自动测试"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-363a3f" alt="MIT 许可证"></a>
  <a href="#当前状态"><img src="https://img.shields.io/badge/status-preview-ed6a3b" alt="实验预览版"></a>
</p>

**Lisca**（**LI**ve **S**tream **CA**ptions）让你在浏览器里观看带原文字幕的直播，也支持本地视频。粘贴直播地址、选好频道，就能一边看节目一边读字幕：中文语音出中文，英文语音出英文。

## 观看效果

<p align="center">
  <img src="docs/assets/live-captions-zh.png" alt="中文新闻实时回放：频道选择、播放器与自动生成的原文字幕" width="960">
</p>

<p align="center"><sub>中文新闻的滚动 HLS 回放，使用真实语音识别。节目来源：<a href="https://www.youtube.com/watch?v=gnF2NFYjRPE">公视新闻</a>。</sub></p>

<details>
<summary>查看英文字幕效果</summary>

![英文新闻实时回放与自动字幕](docs/assets/live-captions-en.png)

英文语音保留英文。节目来源：[PBS NewsHour](https://www.youtube.com/watch?v=TSM3yOPmscI)。两张截图来自固定新闻视频的实时回放，测试方法见[回放记录](docs/research/p0-local-news-2026-11-04.md)。

</details>

## 开始使用

需要 **64 位 Python 3.10–3.12**（推荐 3.12）和已开通 `fun-asr-realtime` 的**百炼中国内地 API Key**。云端模式无需本地 GPU、模型或单独安装系统 FFmpeg。

### 安装与启动

[下载源码 ZIP](https://github.com/AiKiAi-stack/stream-with-asr/archive/refs/heads/main.zip) 并解压，或克隆：

```bash
git clone https://github.com/AiKiAi-stack/stream-with-asr.git
cd stream-with-asr
```

**Windows：**安装 Python 时启用 Python Launcher，双击 `start-windows.cmd`。首次运行会安装依赖，随后自动打开 [127.0.0.1:8002](http://127.0.0.1:8002/)。

<details>
<summary>Linux / 手动启动</summary>

在项目目录执行；使用 Python 3.10 或 3.11 时，替换首行解释器名称：

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-webui.txt
python tools/webui.py --host 127.0.0.1 --port 8002
```

浏览器打开 [127.0.0.1:8002](http://127.0.0.1:8002/)。

</details>

### 看第一场直播

1. 打开「设置」，填写并应用百炼 Key。
2. 粘贴直播地址。若是 M3U 频道清单，点击「选择频道」选台。
3. 点击「播放并生成字幕」，等待字幕就绪后观看。

选台不会启动识别。字幕开关、记录和全屏位于播放器下方，字号与位置可在设置中调整。可从[公开频道清单](examples/public-live-channels.m3u)寻找来源；地址是否可播取决于网络与来源自身。

音频会发送到百炼，费用由你的 API 账户承担；「检查连接」也会连接云端识别服务。Key 默认仅保留在服务内存，Windows 可选择通过 DPAPI 加密保存。

## 功能

- **直播原文字幕。** 支持直播链接与 M3U 选台，自动识别中文、英文；画面按语音时间显示稳定短句。
- **在浏览器里观看。** 调整字幕样式、查看完整记录，或进入全屏。
- **本地视频。** 点击「打开文件」即可使用同一播放器；识别完成后，暂停和跳转复用已有字幕。
- **局域网分享。** 主机识别一份，最多 3 台观众设备通过只读链接加入观看。

<details>
<summary>如何分享给同一局域网的设备？</summary>

以 `python tools/webui.py --host 0.0.0.0 --port 8002` 启动。主机从本机地址打开管理页面，开始直播后在设置中复制观众链接；多网卡时选择观众能访问的地址，并允许家庭/专用网络访问端口 `8002`。观众无需另填 Key，直播停止后链接失效。

</details>

## 当前状态

Lisca 目前是实验预览版，专注原文字幕，跨语言翻译暂停。中英文固定新闻源均完成了 30 分钟实时回放；启动达到当前预览门槛。独立声学同步和有声覆盖仍未验证通过，部分短暂页面、空字幕及识别错误还需改善。详见[预览范围](docs/adr/0009-pr89-preview-acceptance-2026-11-04.md)与[实际测试记录](docs/plans/2026-11-04-pr89-acceptance-recheck.md)。

公共直播源受网络、地区和账号限制影响。没有稳定来源时，可先用本地视频，或参考[新闻模拟直播方法](docs/research/p0-local-news-2026-11-04.md)。本地离线整段转写另见 [FunASR Vulkan 指南](docs/funasr-vulkan.md)。

## 帮助与贡献

遇到问题请在 [Issues](https://github.com/AiKiAi-stack/stream-with-asr/issues) 附上复现步骤、系统版本与脱敏日志。设置中的「诊断日志」可辅助排查；请勿公开 API Key。

<details>
<summary>开发与运行测试</summary>

```bash
python -m pip install -e . pytest playwright
python -m pytest test/ -v
node --test test/*.test.cjs
```

前端检查需要 Node.js（CI 使用 22）。单元回归不需要真实 Key，真实识别验收单独执行并产生费用。

[产品决策](docs/adr/0008-original-language-captions.md) · [术语](GLOSSARY.md) · [原工具包存档](docs/archive/streamparser_zh.md)

</details>

<details>
<summary>Windows 启动诊断与配置位置</summary>

从项目目录运行 `.venv\Scripts\python.exe tools\windows_launcher.py --check`。启动日志在 `logs/webui-windows.log`，配置在 `.data/`，上传的视频副本在 `uploads/`。Key 不写入浏览器 localStorage、URL 或普通日志；「删除配置」会清除保存文件并停止云端会话。

</details>

[MIT License](LICENSE) · 语音识别由百炼 FunASR 提供，音视频处理基于 PyAV。
