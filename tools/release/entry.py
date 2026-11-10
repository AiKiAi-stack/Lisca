"""Bootstrap the portable WebUI and its bundled yt-dlp command."""
import multiprocessing
import os
from pathlib import Path
import sys


def check_runtime():
    """Exercise bundled native libraries, SDK imports and a spawned child offline."""
    import io
    import json
    import wave

    import av
    from dashscope.audio.asr import Recognition, RecognitionCallback
    from lisca.asr.asr_client import ASRClient

    assert Recognition and RecognitionCallback and ASRClient
    audio = io.BytesIO()
    with wave.open(audio, "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    audio.seek(0)
    with av.open(audio) as media:
        assert sum(frame.samples for frame in media.decode(audio=0)) == 16000
    child = multiprocessing.get_context("spawn").Process(target=os.getpid)
    child.start()
    child.join(15)
    if child.is_alive():
        child.terminate()
        child.join(5)
    assert child.exitcode == 0, f"Spawned worker failed: {child.exitcode}"
    print(json.dumps({"audio_decode": "ok", "asr_imports": "ok", "spawn": "ok"}))


if __name__ == "__main__":
    # PyInstaller needs this before importing the server, including on Linux.
    multiprocessing.freeze_support()
    if Path(sys.executable).stem.lower() == "yt-dlp":
        import yt_dlp

        yt_dlp.main()
    elif sys.argv[1:] == ["--check"]:
        check_runtime()
    else:
        os.environ.setdefault("LISCA_DATA_DIR", str(Path(sys.executable).parent / "data"))
        from lisca.web.app import main

        main()
