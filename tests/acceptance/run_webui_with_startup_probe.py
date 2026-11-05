"""Run the regular WebUI entry point with the opt-in PCM timing hook installed."""
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "tools"))

from startup_timing_probe import install_stream_source_probe
import webui  # pylint: disable=import-error


def main():
    output_path = os.environ.get("STREAM_ASR_STARTUP_PROBE_LOG")
    if not output_path:
        raise SystemExit("Set STREAM_ASR_STARTUP_PROBE_LOG to a private JSONL output path.")
    installed = install_stream_source_probe(webui, output_path)
    print(f"Fixed startup PCM timing probe installed: {installed['output_path']}", flush=True)
    webui.main()


if __name__ == "__main__":
    main()
