<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/lisca-wordmark-dark.svg">
    <img src="docs/assets/lisca-wordmark-light.svg" alt="Lisca" width="320">
  </picture>
</p>

<h3 align="center">Live captions for the streams you watch</h3>

<p align="center">
  <a href="#get-started">Get started</a> ·
  <a href="#see-it-in-action">See it in action</a> ·
  <a href="#features">Features</a> ·
  <a href="https://github.com/AiKiAi-stack/stream-with-asr/issues">Feedback</a> ·
  <a href="README_zh.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/AiKiAi-stack/stream-with-asr/actions/workflows/test.yml"><img src="https://github.com/AiKiAi-stack/stream-with-asr/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-363a3f" alt="MIT license"></a>
  <a href="#status"><img src="https://img.shields.io/badge/status-preview-ed6a3b" alt="Experimental preview"></a>
</p>

**Lisca** (**LI**ve **S**tream **CA**ptions) adds original-language captions to live streams in your browser, with local video support too. Paste a stream URL, pick your channel and watch with captions. Chinese speech stays Chinese; English speech stays English.

## See it in action

<p align="center">
  <img src="docs/assets/live-captions-en.png" alt="English news replay with channel selection, browser playback and generated captions" width="960">
</p>

<p align="center"><sub>Rolling HLS news replay with real speech recognition. Program source: <a href="https://www.youtube.com/watch?v=TSM3yOPmscI">PBS NewsHour</a>.</sub></p>

<details>
<summary>See Chinese captions</summary>

![Chinese news replay with generated captions](docs/assets/live-captions-zh.png)

Chinese speech stays Chinese. Program source: [PTS News](https://www.youtube.com/watch?v=gnF2NFYjRPE). Both screenshots come from fixed news videos replayed in real time; see the [replay record](docs/research/p0-local-news-2026-11-04.md) for the method.

</details>

## Get started

You need **64-bit Python 3.10–3.12** (3.12 recommended) and a **mainland-China DashScope API key** with `fun-asr-realtime` enabled. Cloud mode needs no local GPU, model download or separate system FFmpeg installation.

### Install and launch

[Download the source ZIP](https://github.com/AiKiAi-stack/stream-with-asr/archive/refs/heads/main.zip) and extract it, or clone:

```bash
git clone https://github.com/AiKiAi-stack/stream-with-asr.git
cd stream-with-asr
```

**Windows:** enable the Python Launcher when installing Python, then double-click `start-windows.cmd`. The first run installs dependencies and opens [127.0.0.1:8002](http://127.0.0.1:8002/).

<details>
<summary>Linux / manual setup</summary>

Run from the repository directory. Substitute your interpreter name on the first line if using Python 3.10 or 3.11:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-webui.txt
python tools/webui.py --host 127.0.0.1 --port 8002
```

Open [127.0.0.1:8002](http://127.0.0.1:8002/) in your browser.

</details>

### Watch your first stream

1. Open Settings (**设置**) and apply your DashScope key.
2. Paste a stream URL. For an M3U playlist, click Select channel (**选择频道**).
3. Click Play and generate captions (**播放并生成字幕**), then wait for captions to become ready.

Choosing a channel does not start recognition. Caption controls, history and fullscreen are below the player; size and position are in Settings. The UI currently uses Chinese labels. Try the [public channel list](examples/public-live-channels.m3u); availability depends on your network and the source itself.

Audio is sent to DashScope and billed to your API account. The connection check also connects to cloud ASR. Keys stay in server memory by default; Windows offers optional DPAPI-encrypted persistence.

## Features

- **Live captions.** Open a stream or select an M3U channel. Chinese and English are detected automatically; stable phrases follow speech timestamps.
- **Browser playback.** Adjust caption styling, read the full transcript or watch in fullscreen.
- **Local video.** Open a file in the same player. Once transcription finishes, pause and seek reuse existing captions.
- **LAN sharing.** One host runs recognition; up to three viewer devices join through read-only links.

<details>
<summary>How do I share with devices on my LAN?</summary>

Start with `python tools/webui.py --host 0.0.0.0 --port 8002`. Open the management page locally on the host, start a live session and copy its viewer link from Settings. Choose a reachable address if you have multiple adapters, and allow port `8002` through your private-network firewall. Viewers need no separate key. The link expires when the session stops.

</details>

## Status

Lisca is an experimental preview focused on original-language captions; cross-language translation is paused. Fixed Chinese and English news inputs have each completed 30 minutes of real-time replay, and startup meets the current preview threshold. Independent acoustic synchronization and voiced coverage have not passed validation. Some short caption pages, blank intervals and recognition errors still need improvement. See the [preview scope](docs/adr/0009-pr89-preview-acceptance-2026-11-04.md) and [test record](docs/plans/2026-11-04-pr89-acceptance-recheck.md).

Public streams can have network, regional or account restrictions. Start with a local video if you need a reliable input, or use the [news replay method](docs/research/p0-local-news-2026-11-04.md). For offline whole-file transcription, see the [FunASR Vulkan guide](docs/funasr-vulkan.md).

## Help and contribute

Report problems in [Issues](https://github.com/AiKiAi-stack/stream-with-asr/issues) with reproduction steps, your OS version and redacted logs. The diagnostic log in Settings can help; never publish your API key.

<details>
<summary>Development and tests</summary>

```bash
python -m pip install -e . pytest playwright
python -m pytest test/ -v
node --test test/*.test.cjs
```

Frontend checks need Node.js (CI uses 22). Unit regressions need no real key; real recognition acceptance runs separately and incurs cloud charges.

[Product decision](docs/adr/0008-original-language-captions.md) · [Glossary](GLOSSARY.md) · [Historical toolkit archive](docs/archive/streamparser.md)

</details>

<details>
<summary>Windows launch diagnostics and configuration</summary>

From the repository directory, run `.venv\Scripts\python.exe tools\windows_launcher.py --check`. The launch log is `logs/webui-windows.log`, configuration is under `.data/`, and uploaded video copies are under `uploads/`. Keys are not stored in browser localStorage, URLs or ordinary logs. Deleting the configuration clears the saved file and stops cloud sessions.

</details>

[MIT License](LICENSE). Speech recognition uses DashScope FunASR; media processing uses PyAV.
