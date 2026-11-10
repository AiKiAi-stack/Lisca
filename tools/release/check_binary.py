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


def binary_architecture(path):
    """Read the native executable header independently of its filename."""
    with path.open("rb") as binary:
        header = binary.read(64)
        if header[:2] == b"MZ":
            binary.seek(int.from_bytes(header[60:64], "little"))
            assert binary.read(4) == b"PE\0\0"
            return {0x8664: "x64", 0xAA64: "arm64"}[int.from_bytes(binary.read(2), "little")]
        if header[:4] == b"\x7fELF":
            assert header[4:6] == b"\x02\x01", "Expected little-endian ELF64"
            return {62: "x64", 183: "arm64"}[int.from_bytes(header[18:20], "little")]
        assert header[:4] == b"\xcf\xfa\xed\xfe", "Expected a thin Mach-O 64-bit executable"
        return {0x01000007: "x64", 0x0100000C: "arm64"}[int.from_bytes(header[4:8], "little")]


def extract_archive(archive, root):
    if archive.suffix != ".dmg":
        shutil.unpack_archive(str(archive), root)
        return
    mount = root / "mounted"
    mount.mkdir()
    subprocess.run(["hdiutil", "attach", str(archive), "-nobrowse", "-readonly",
                    "-mountpoint", str(mount)], check=True, timeout=60)
    try:
        # The normal DMG install copies the folder to writable storage first.
        shutil.copytree(mount / "Lisca", root / "Lisca", symlinks=True)
    finally:
        subprocess.run(["hdiutil", "detach", str(mount)], check=True, timeout=30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--platform", required=True,
                        choices=[f"{system}-{arch}" for system in ("windows", "linux", "macos")
                                 for arch in ("x64", "arm64")])
    args = parser.parse_args()
    expected_system, expected_architecture = args.platform.rsplit("-", 1)
    with socket.socket() as check:
        check.bind(("127.0.0.1", 8002))
    with tempfile.TemporaryDirectory(prefix="lisca-smoke-") as temporary:
        root = Path(temporary)
        extract_archive(args.archive.resolve(), root)
        bundle = root / "Lisca"
        executable = bundle / ("Lisca.exe" if os.name == "nt" else "Lisca")
        resolver = bundle / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
        assert binary_architecture(executable) == expected_architecture, f"Expected {args.platform} application binary"
        assert binary_architecture(resolver) == expected_architecture, f"Expected {args.platform} resolver binary"
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
        assert all(report[key] == "ok" for key in ("audio_decode", "asr_imports", "spawn"))
        assert report["system"] == {"windows": "Windows", "linux": "Linux", "macos": "Darwin"}[expected_system]
        metadata = json.loads((bundle / "build-info.json").read_text(encoding="utf-8"))
        assert metadata["target"] == args.platform
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
