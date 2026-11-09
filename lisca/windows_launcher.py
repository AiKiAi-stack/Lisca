"""Windows entry point: a repo-local environment and one WebUI on port 8002."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen
import venv
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8002/"
MODULES = ("av", "dashscope", "numpy", "httpx", "fastapi", "multipart", "uvicorn", "yt_dlp")


def existing_service():
    """Do not replace another process just because it owns the expected port."""
    try:
        with socket.create_connection(("127.0.0.1", 8002), timeout=1):
            pass
    except OSError:
        return False
    try:
        with urlopen(URL + "api/cloud", timeout=3) as response:
            data = json.load(response)
        if data.get("translation_model") != "qwen-mt-flash":
            raise ValueError("different service")
    except (OSError, URLError, ValueError):
        raise RuntimeError("Port 8002 is already in use. Stop the previous service and retry.") from None
    return True


def prepare_environment():
    if not (3, 10) <= sys.version_info[:2] <= (3, 12):
        raise RuntimeError("Use 64-bit Python 3.10, 3.11 or 3.12 (recommended: 3.12).")
    environment = ROOT / ".venv"
    python = environment / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.exists():
        print("Creating a local Python environment in", environment, flush=True)
        venv.create(environment, with_pip=True)
    requirements = ROOT / "requirements-webui.txt"
    fingerprint = hashlib.sha256(requirements.read_bytes()).hexdigest()
    stamp = environment / "webui-requirements.sha256"
    if not stamp.exists() or stamp.read_text() != fingerprint:
        print("Installing cloud WebUI dependencies. Models are not downloaded.", flush=True)
        subprocess.run([str(python), "-m", "pip", "--cache-dir", str(ROOT / ".cache/pip"),
                        "install", "-r", str(requirements)],
                       cwd=ROOT, check=True)
        stamp.write_text(fingerprint)
    return python


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report dependencies without installing or starting")
    args = parser.parse_args()
    if args.check:
        missing = [name for name in MODULES if importlib.util.find_spec(name) is None]
        print(json.dumps({"python": sys.version.split()[0], "missing": missing,
                          "project_directory": str(ROOT)}, ensure_ascii=False))
        return 1 if missing else 0
    if existing_service():
        print("WebUI is already running:", URL, flush=True)
        webbrowser.open(URL)
        return 0
    python = prepare_environment()
    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    with (logs / "webui-windows.log").open("a", encoding="utf-8") as log:
        process = subprocess.Popen([str(python), "-u", "-m", "lisca",
                                    "--host", "0.0.0.0", "--port", "8002"],
                                   cwd=ROOT, stdout=log, stderr=log,
                                   env={**os.environ, "PATH": str(python.parent) + os.pathsep + os.environ.get("PATH", "")},
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    for _ in range(60):
        if process.poll() is not None:
            raise RuntimeError("WebUI exited. See logs/webui-windows.log for diagnostics.")
        try:
            if existing_service():
                print("WebUI is running:", URL, flush=True)
                webbrowser.open(URL)
                return 0
        except RuntimeError:
            pass
        time.sleep(0.5)
    process.terminate()
    raise RuntimeError("WebUI startup timed out. See logs/webui-windows.log.")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
