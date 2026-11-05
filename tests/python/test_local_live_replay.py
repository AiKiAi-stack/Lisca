from contextlib import contextmanager
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import local_live_replay as replay


def test_command_paces_real_media_and_bounds_live_playlist():
    command = replay._command("ffmpeg", Path("/real video.mp4"))
    assert command[command.index("-i") + 1] == "/real video.mp4"
    assert command.index("-re") < command.index("-i")
    assert command[command.index("-hls_time") + 1] == "2"
    assert command[command.index("-hls_list_size") + 1] == "8"
    assert {"delete_segments", "omit_endlist", "temp_file", "independent_segments"} <= set(
        command[command.index("-hls_flags") + 1].split("+"))
    assert "expr:gte(t,n_forced*2)" in command
    assert "-stream_loop" not in command
    copied = replay._command("ffmpeg", Path("/real.mp4"), copy_media=True, loop=True)
    assert copied[copied.index("-stream_loop") + 1] == "-1"
    assert copied[copied.index("-c") + 1] == "copy"


@pytest.mark.parametrize("startup_failure", [False, True])
def test_encoder_is_killed_if_needed_and_only_its_temporary_files_are_removed(
        tmp_path, monkeypatch, startup_failure):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"existing source")
    untouched = tmp_path / "keep.txt"
    untouched.write_text("keep", encoding="utf-8")
    events = []

    class Encoder:
        def __init__(self, command, **kwargs):
            self.directory = kwargs["cwd"]
            (self.directory / "segment-000000000.ts").write_bytes(b"media")
            events.append(self)

        def poll(self):
            return None

        def terminate(self):
            events.append("terminate")

        def wait(self, timeout):
            events.append(("wait", timeout))
            if "kill" not in events:
                raise subprocess.TimeoutExpired("ffmpeg", timeout)

        def kill(self):
            events.append("kill")

    def ready(_process, _directory):
        if startup_failure:
            raise RuntimeError("failed startup")

    monkeypatch.setattr(replay.subprocess, "Popen", Encoder)
    monkeypatch.setattr(replay, "_wait_ready", ready)
    if startup_failure:
        with pytest.raises(RuntimeError, match="failed startup"):
            with replay.replay_media(source, tmp_path, "ffmpeg"):
                pytest.fail("failed encoder must not be served")
    else:
        with replay.replay_media(source, tmp_path, "ffmpeg") as directory:
            assert directory.is_dir()
    assert events[1:] == ["terminate", ("wait", 5), "kill", ("wait", 5)]
    assert not events[0].directory.exists()
    assert source.read_bytes() == b"existing source"
    assert untouched.read_text(encoding="utf-8") == "keep"


def test_launcher_serves_media_with_ordinary_webui_and_restores_state(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"real source")
    media = tmp_path / "media"
    media.mkdir()
    (media / "index.m3u8").write_text("#EXTM3U\nsegment-000000000.ts\n", encoding="utf-8")
    (media / "segment-000000000.ts").write_bytes(b"existing media")
    app = FastAPI()
    original_argv = sys.argv
    original_routes = list(app.router.routes)
    events = []

    @contextmanager
    def prepared(*_args, **_kwargs):
        events.append("media-start")
        try:
            yield media
        finally:
            events.append("media-stop")

    def regular_webui_main():
        assert sys.argv[1:] == ["--host", "127.0.0.1", "--port", "8002"]
        with TestClient(app) as client:
            response = client.get("/local-live/index.m3u8")
            assert response.text.startswith("#EXTM3U")
            assert response.headers["Cache-Control"] == "no-store"
            assert client.get("/local-live/segment-000000000.ts").content == b"existing media"
            assert client.get("/local-live/%2e%2e/source.mp4").status_code == 404
        events.append("ordinary-webui")
        raise RuntimeError("server exit")

    monkeypatch.setattr(replay, "replay_media", prepared)
    monkeypatch.setattr(replay.shutil, "which", lambda _name: "/configured/ffmpeg")
    webui = SimpleNamespace(app=app, main=regular_webui_main)
    with pytest.raises(RuntimeError, match="server exit"):
        replay.main(["--source", str(source)], webui_module=webui)
    assert events == ["media-start", "ordinary-webui", "media-stop"]
    assert sys.argv is original_argv
    assert app.router.routes == original_routes


def test_working_directory_cannot_be_in_checkout(tmp_path):
    with pytest.raises(ValueError, match="outside the checkout"):
        with replay.replay_media(tmp_path / "source.mp4", replay.ROOT / "media", "ffmpeg"):
            pytest.fail("must reject assets inside checkout")


def test_two_media_channels_share_playlist_and_exitstack_cleans_both(tmp_path, monkeypatch):
    sources = [tmp_path / "zh.mp4", tmp_path / "en.mp4"]
    for source in sources:
        source.write_bytes(b"existing media")
    events = []
    app = FastAPI()
    original_routes = list(app.router.routes)

    @contextmanager
    def prepared(source, *_args, **_kwargs):
        directory = tmp_path / source.stem
        directory.mkdir()
        (directory / "index.m3u8").write_text(f"#EXTM3U\n{source.stem}.ts\n", encoding="utf-8")
        events.append(("start", source.stem))
        try:
            yield directory
        finally:
            events.append(("stop", source.stem))

    def regular_webui_main():
        with TestClient(app) as client:
            response = client.get("/local-live-channels.m3u")
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.text == (
                "#EXTM3U\n#EXTINF:-1,中文新闻\n"
                "http://127.0.0.1:8002/local-live/index.m3u8\n"
                "#EXTINF:-1,English news\n"
                "http://127.0.0.1:8002/local-live-2/index.m3u8\n")
            assert client.get("/local-live/index.m3u8").text.endswith("zh.ts\n")
            assert client.get("/local-live-2/index.m3u8").text.endswith("en.ts\n")
        events.append("ordinary-webui")

    monkeypatch.setattr(replay, "replay_media", prepared)
    monkeypatch.setattr(replay.shutil, "which", lambda _name: "/configured/ffmpeg")
    assert replay.main([
        "--source", str(sources[0]), "--source", str(sources[1]),
        "--label", "中文新闻", "--label", "English news",
    ], webui_module=SimpleNamespace(app=app, main=regular_webui_main)) == 0
    assert events == [("start", "zh"), ("start", "en"), "ordinary-webui",
                      ("stop", "en"), ("stop", "zh")]
    assert app.router.routes == original_routes
