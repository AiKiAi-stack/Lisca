# Local FunASR Vulkan file transcription

The bundled server wraps the local llama.cpp/GGUF executable with an
OpenAI-compatible `POST /v1/audio/transcriptions` endpoint. The WebUI engine
buffers a local media file, sends its audio to the service, receives the
complete SRT transcript, and starts playback after transcription finishes.
This mode does not provide live or incremental captions.

## Start the service (PowerShell)

Set the paths for the local install, then launch the service from the repository
virtual environment:

```powershell
$funasrRoot = 'D:\codex_tmp\2026-10-17\amd-asr-ollama\work\funasr-win-vulkan'
& .\.venv\Scripts\python.exe tools\funasr_gguf_server.py `
  --host 127.0.0.1 --port 8765 `
  --binary "$funasrRoot\runtime\llama-funasr-sensevoice.exe" `
  --model "$funasrRoot\sensevoice-small-q8.gguf" `
  --backend vulkan --srt `
  --work-dir "$funasrRoot\service-tmp"
```

The service listens only on loopback by default. Check it with:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

The default listen port is `8765`. Uploads are limited to 1024 MiB by
default; change that limit with `--max-upload-mb`. Inference runs one request
at a time so concurrent clients do not launch overlapping Vulkan model jobs.

To submit a WAV directly:

```powershell
curl.exe -F 'file=@D:\path\to\audio.wav' `
  -F 'model=funasr-gguf' `
  http://127.0.0.1:8765/v1/audio/transcriptions
```

## Run the WebUI

In another PowerShell window, from the repository root:

```powershell
& .\.venv\Scripts\python.exe tools\webui.py --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, select **FunASR Vulkan（文件模式）**, choose a
local audio/video file, and connect. The transcript is prepared first; the
video then plays with sentence-level SRT timing. Live URLs are rejected by this
engine. The API key is not needed.

The engine endpoint can be changed with `FUNASR_GGUF_URL`. The request timeout
defaults to ten minutes.
