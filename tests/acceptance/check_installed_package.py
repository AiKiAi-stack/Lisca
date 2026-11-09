"""Check a built wheel's CLI, assets and writable data outside the checkout."""
import argparse
import asyncio
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-root", type=Path, required=True)
    args = parser.parse_args()
    expected = args.package_root.resolve()
    package = importlib.import_module("lisca")
    assert Path(package.__file__).resolve().is_relative_to(expected), package.__file__
    assert (expected / "lisca/web/licenses/DAISYUI-MIT.txt").is_file()
    assert (expected / "lisca/_vendor/whisper_streaming/LICENSE").is_file()
    importlib.import_module("lisca.asr.asr_client")
    importlib.import_module("lisca._vendor.whisper_streaming.whisper_online")

    async def check(data):
        import httpx
        from lisca.web.app import app

        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 12345))
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            for route in ("/", "/i18n.js", "/source_library.js", "/player.css",
                          "/ui-components.css", "/caption_presenter.js", "/hls.min.js",
                          "/playback_guard.js", "/recovery_controller.js", "/icons.svg", "/favicon.svg"):
                response = await client.get(route)
                assert response.status_code == 200 and response.content, route
            response = await client.post("/upload", files={"file": ("fixture.bin", b"package-check")})
            assert response.status_code == 200
            uploaded = Path(response.json()["path"])
            assert uploaded.parent == data / "uploads", uploaded
            assert uploaded.read_bytes() == b"package-check"
            assert not (await client.get("/api/cloud")).json()["configured"]

    previous = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="lisca-installed-") as temporary:
        data = Path(temporary).resolve()
        try:
            os.chdir(data)
            os.environ.pop("LISCA_DATA_DIR", None)
            subprocess.run([sys.executable, "-m", "lisca", "--help"],
                           check=True, capture_output=True, text=True, timeout=30)
            asyncio.run(check(data))
        finally:
            os.chdir(previous)
    print("Installed Lisca: CLI, ASR imports, vendored backend, static assets and upload storage passed.")


if __name__ == "__main__":
    main()
