"""Launch installed module entry points from outside the source directory."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("module", ["lisca", "lisca.stream_subs", "lisca.funasr_gguf_server"])
def test_module_help_outside_checkout(module, tmp_path):
    data = tmp_path / "private-data"
    result = subprocess.run(
        [sys.executable, "-m", module, "--help"], cwd=tmp_path,
        env={**os.environ, "LISCA_DATA_DIR": str(data)},
        check=True, capture_output=True, text=True, timeout=30,
    )
    assert "usage:" in result.stdout
    if module == "lisca":
        assert "--port" in result.stdout
        assert (data / "uploads").is_dir()


def test_windows_launcher_check_without_starting_service(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "lisca.windows_launcher", "--check"],
        cwd=tmp_path, check=True, capture_output=True, text=True, timeout=30,
    )
    report = json.loads(result.stdout)
    assert report["missing"] == []
    assert (Path(report["project_directory"]) / "start-windows.cmd").is_file()


def test_portable_help_with_windows_redirected_output_encoding(tmp_path):
    entry = Path(__file__).resolve().parents[2] / "tools/release/entry.py"
    result = subprocess.run(
        [sys.executable, str(entry), "--help"], cwd=tmp_path,
        env={**os.environ, "PYTHONIOENCODING": "cp1252",
             "LISCA_DATA_DIR": str(tmp_path / "data")},
        capture_output=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "实时字幕" in result.stdout and "--port" in result.stdout
