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
  <a href="https://github.com/AiKiAi-stack/Lisca/issues">Feedback</a> ·
  <a href="README_zh.md">简体中文</a>
</p>

<p align="center">
  <a href="https://github.com/AiKiAi-stack/Lisca/actions/workflows/test.yml"><img src="https://github.com/AiKiAi-stack/Lisca/actions/workflows/test.yml/badge.svg" alt="Tests"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-363a3f" alt="MIT license"></a>
  <a href="#status"><img src="https://img.shields.io/badge/status-preview-ed6a3b" alt="Experimental preview"></a>
</p>

Lisca (LIve Stream CAptions) adds original-language captions to live streams in your browser. Paste a stream URL or choose a channel from a playlist to start watching. Chinese speech produces Chinese captions; English speech produces English captions. You can also open a local video.

## See it in action

<p align="center">
  <img src="docs/assets/live-captions-en.png" alt="English news replay with channel selection, browser playback and generated captions" width="960">
</p>

<p align="center"><sub>English UI replaying a recorded recognition result with local news video. Program source: <a href="https://www.youtube.com/watch?v=TSM3yOPmscI">PBS NewsHour</a>.</sub></p>

<details>
<summary>See Chinese captions</summary>

![Chinese news replay with generated captions](docs/assets/live-captions-zh.png)

Program source: [PTS News](https://www.youtube.com/watch?v=gnF2NFYjRPE). Both screenshots use recorded recognition results with local news playback. See the [replay record](docs/research/p0-local-news-2026-11-04.md) for details.

</details>

## Get started

You need 64-bit Python 3.10–3.12 (3.12 recommended) and a DashScope API key for the mainland China region with `fun-asr-realtime` enabled. Cloud mode does not require a local GPU, model downloads or a separate system FFmpeg installation.

### Install and launch

[Download the source ZIP](https://github.com/AiKiAi-stack/Lisca/archive/refs/heads/main.zip) and extract it, or clone:

```bash
git clone https://github.com/AiKiAi-stack/Lisca.git
cd Lisca
```

On Windows, enable the Python Launcher when installing Python, then double-click `start-windows.cmd`. The first run installs dependencies and opens the WebUI. Use the [English interface](http://127.0.0.1:8002/?lang=en), or choose English at the top right.

<details>
<summary>Linux / manual setup</summary>

Run these commands from the repository directory. If you use Python 3.10 or 3.11, change the interpreter name on the first line:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-webui.txt
python tools/webui.py --host 127.0.0.1 --port 8002
```

Open [127.0.0.1:8002](http://127.0.0.1:8002/?lang=en) in your browser.

</details>

### Watch your first stream

1. Open Settings, enter your DashScope key and apply it.
2. Paste a stream URL. For an M3U playlist, click Select channel.
3. Click Play with captions, then wait for captions to be ready.

Choosing a channel does not start recognition. Below the player, you can toggle captions, view the transcript and enter fullscreen. Adjust caption size and position in Settings. Use the top-right language selector to switch between English and Chinese. Interface language does not change the speech language. You can try the [public channel list](examples/public-live-channels.m3u), though availability depends on your network and the source.

Audio is sent to DashScope and billed to your API account. The connection check also connects to cloud ASR. Keys stay in server memory by default; Windows offers optional DPAPI-encrypted persistence.

## Features

- Open a live stream or choose an M3U channel. Lisca detects Chinese and English automatically and displays stable phrases using speech timestamps.
- Adjust caption styling, read the full transcript or watch in fullscreen.
- Open local videos in the same player. Once transcription finishes, pausing and seeking use the existing captions.
- Share a live session over your LAN. One host runs recognition, and up to three viewer devices can join through read-only links.

<details>
<summary>How do I share with devices on my LAN?</summary>

Launch the server with `python tools/webui.py --host 0.0.0.0 --port 8002`. Open the management page locally on the host, start a live session and copy its viewer link from Settings. Choose a reachable address if you have multiple adapters, and allow port `8002` through your private-network firewall. Viewers need no separate key. The link expires when the session stops.

</details>

## Status

Lisca is an experimental preview for original-language captions. Cross-language translation is paused. Fixed Chinese and English news inputs have each completed 30 minutes of real-time replay, and startup meets the current preview threshold. Independent acoustic synchronization and caption coverage during speech have not passed validation. Some captions appear too briefly, some intervals have no captions, and recognition errors remain. See the [preview scope](docs/adr/0009-pr89-preview-acceptance-2026-11-04.md) and [test record](docs/plans/2026-11-04-pr89-acceptance-recheck.md).

Public streams may be restricted by network, region or account. If you cannot find a reliable source, try a local video or use the [news replay method](docs/research/p0-local-news-2026-11-04.md). For offline whole-file transcription, see the [FunASR Vulkan guide](docs/funasr-vulkan.md).

## Help and contribute

To report a problem, open an [Issue](https://github.com/AiKiAi-stack/Lisca/issues) with steps to reproduce it, your OS version and redacted logs. You can find the diagnostic log in Settings. Never publish your API key.

<details>
<summary>Development and tests</summary>

```bash
python -m pip install -e . pytest playwright
python -m pytest test/ -v
node --test test/*.test.cjs
```

Frontend checks need Node.js (CI uses 22). Unit tests do not need a real key. Acceptance tests with real speech recognition run separately and incur cloud charges.

[Product decision](docs/adr/0008-original-language-captions.md) · [Glossary](GLOSSARY.md) · [Historical toolkit archive](docs/archive/streamparser.md)

</details>

<details>
<summary>Windows launch diagnostics and configuration</summary>

From the repository directory, run `.venv\Scripts\python.exe tools\windows_launcher.py --check`. The launch log is `logs/webui-windows.log`, configuration is under `.data/`, and uploaded video copies are under `uploads/`. Keys are not stored in browser localStorage, URLs or ordinary logs. Deleting the configuration clears the saved file and stops cloud sessions.

</details>

[MIT License](LICENSE). Speech recognition uses DashScope FunASR; media processing uses PyAV.
