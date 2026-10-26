"""ticket-03: input_resolver 测试"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from input_resolver import classify, resolve_stream_url  # noqa: E402


class TestClassify:
    def test_local_file(self, tmp_path):
        f = tmp_path / "demo.wav"
        f.write_bytes(b"\x00" * 100)
        kind, target = classify(str(f))
        assert kind == "file"
        assert target == str(f)

    def test_direct_stream_url(self):
        kind, target = classify("https://example.com/live/index.m3u8")
        assert kind == "stream"
        assert target == "https://example.com/live/index.m3u8"

    def test_bilibili_page(self):
        assert classify("https://www.bilibili.com/video/BV1xx411c7mD")[0] == "page"

    def test_b23_short_link(self):
        assert classify("https://b23.tv/abc123")[0] == "page"

    def test_youtube_page(self):
        assert classify("https://www.youtube.com/watch?v=example")[0] == "page"
        assert classify("https://youtu.be/example")[0] == "page"

    def test_platform_name_in_query_is_not_a_platform_host(self):
        assert classify("https://media.example/live.m3u8?name=bilibili.com")[0] == "stream"

    def test_douyin_page(self):
        assert classify("https://www.douyin.com/video/123456")[0] == "page"

    def test_xiaohongshu_page(self):
        assert classify("https://www.xiaohongshu.com/explore/abc")[0] == "page"

    def test_rtsp_stream(self):
        assert classify("rtsp://192.168.1.10:554/live")[0] == "stream"

    def test_invalid_input_raises(self):
        with pytest.raises(ValueError):
            classify("/nonexistent/path/xyz.wav")


class TestResolveStreamUrl:
    def test_stream_url_passthrough(self):
        url = "https://example.com/live/index.m3u8"
        assert resolve_stream_url(url) == url

    def test_page_url_uses_ytdlp(self, monkeypatch):
        def fake_which(name):
            return "/usr/bin/yt-dlp"

        class FakeProc:
            returncode = 0
            stdout = "https://example.com/real-stream.m3u8\n"
            stderr = ""

        def fake_run(cmd, **kwargs):
            assert cmd[0] == "yt-dlp"
            return FakeProc()

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(sys.modules["input_resolver"].shutil, "which", fake_which)
        assert resolve_stream_url("https://www.bilibili.com/video/BV1xx411c7mD") == \
            "https://example.com/real-stream.m3u8"

    def test_page_url_without_ytdlp(self, monkeypatch):
        monkeypatch.setattr(sys.modules["input_resolver"].shutil, "which", lambda n: None)
        with pytest.raises(RuntimeError, match="yt-dlp"):
            resolve_stream_url("https://www.bilibili.com/video/BV1xx411c7mD")

    def test_ytdlp_failure(self, monkeypatch):
        class FakeProc:
            returncode = 1
            stdout = ""
            stderr = "ERROR: Unsupported URL"

        monkeypatch.setattr(sys.modules["input_resolver"].shutil, "which", lambda n: "/usr/bin/yt-dlp")
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: FakeProc())
        with pytest.raises(RuntimeError, match="解析失败"):
            resolve_stream_url("https://www.douyin.com/video/123456")
