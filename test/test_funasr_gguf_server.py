"""The bundled HTTP wrapper constructs the Vulkan CLI invocation correctly."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from funasr_gguf_server import ServerConfig, build_command  # noqa: E402


def test_build_command_forwards_model_vulkan_and_srt():
    config = ServerConfig(
        binary=r"D:\funasr\runtime\llama-funasr-sensevoice.exe",
        model=r"D:\funasr\sensevoice-small-q8.gguf",
        backend="vulkan",
        srt=True,
    )
    assert build_command(config, r"D:\work\audio.wav") == [
        r"D:\funasr\runtime\llama-funasr-sensevoice.exe",
        "-m", r"D:\funasr\sensevoice-small-q8.gguf",
        "-a", r"D:\work\audio.wav",
        "--backend", "vulkan", "--srt",
    ]
