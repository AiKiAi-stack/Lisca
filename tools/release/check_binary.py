"""Extract and run a portable release without a system Python or real ASR key."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    with socket.socket() as check:
        check.bind(("127.0.0.1", 8002))
    with tempfile.TemporaryDirectory(prefix="lisca-smoke-") as temporary:
        root = Path(temporary)
        shutil.unpack_archive(str(args.archive.resolve()), root)
        bundle = root / "Lisca"
        executable = bundle / ("Lisca.exe" if os.name == "nt" else "Lisca")
        resolver = bundle / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME", "LISCA_DATA_DIR", "DASHSCOPE_API_KEY")}
        # Remove the runner's Python and yt-dlp commands from the application's PATH.
        env["PATH"] = os.environ.get("SystemRoot", "C:/Windows") + "/System32" if os.name == "nt" else "/usr/bin:/bin"
        def run(command):
            result = subprocess.run(command, cwd=root, env=env,
                                    capture_output=True, encoding="utf-8", errors="replace", timeout=60)
            assert result.returncode == 0, (command, result.returncode, result.stdout, result.stderr)
            return result
        assert "--port" in run([str(executable), "--help"]).stdout
        report = json.loads(run([str(executable), "--check"]).stdout)
        assert report == {"audio_decode": "ok", "asr_imports": "ok", "spawn": "ok"}
        assert run([str(resolver), "--version"]).stdout.strip()
        assert "Youtube" in run([str(resolver), "--list-extractors"]).stdout
        with (root / "server.log").open("w", encoding="utf-8") as log:
            server = subprocess.Popen([str(executable), "--host", "127.0.0.1", "--port", "8002"],
                                      cwd=root, env=env, stdout=log, stderr=log)
            try:
                for _ in range(120):
                    assert server.poll() is None, (root / "server.log").read_text()
                    try:
                        with urlopen("http://127.0.0.1:8002/api/cloud", timeout=1) as response:
                            assert not json.load(response)["configured"]
                        break
                    except OSError:
                        time.sleep(0.25)
                else:
                    raise AssertionError("Portable server did not start within 30 seconds")
                for route in ("/", "/i18n.js", "/source_library.js", "/player.css",
                              "/ui-components.css", "/caption_presenter.js", "/hls.min.js",
                              "/playback_guard.js", "/recovery_controller.js", "/icons.svg", "/favicon.svg"):
                    with urlopen("http://127.0.0.1:8002" + route, timeout=5) as response:
                        assert response.status == 200 and response.read(), route
                data = (b'--lisca-boundary\r\nContent-Disposition: form-data; name="file"; '
                        b'filename="fixture.bin"\r\nContent-Type: application/octet-stream\r\n\r\n'
                        b'portable-check\r\n--lisca-boundary--\r\n')
                request = Request("http://127.0.0.1:8002/upload", data=data,
                                  headers={"Content-Type": "multipart/form-data; boundary=lisca-boundary"})
                with urlopen(request, timeout=5) as response:
                    uploaded = Path(json.load(response)["path"])
                # Windows may return the long form of an 8.3 temporary path.
                assert uploaded.parent.samefile(bundle / "data/uploads"), uploaded
                assert uploaded.read_bytes() == b"portable-check"
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
        print("Portable archive passed: native audio, SDK imports, spawned worker, yt-dlp, HTTP assets and upload storage.")


if __name__ == "__main__":
    main()
