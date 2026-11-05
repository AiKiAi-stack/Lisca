"""The bundled HTTP wrapper constructs the Vulkan CLI invocation correctly."""


from funasr_gguf_server import (  # noqa: E402
    ServerConfig, _parse_extra_args, build_arg_parser, build_command,
)


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


def test_server_defaults_to_approved_loopback_port():
    defaults = build_arg_parser().parse_args(["--binary", "funasr", "--model", "model.gguf"])
    assert (defaults.host, defaults.port) == ("127.0.0.1", 8765)


def test_extra_args_respect_quoted_values():
    assert _parse_extra_args(['--name "path with spaces"']) == ["--name", "path with spaces"]
