"""ticket-11: HLS playlist 相对引用改写回归测试

覆盖：普通行（ts 分片）、# 行 URI 属性（EXT-X-KEY 密钥 / EXT-X-MEDIA /
EXT-X-STREAM-INF 变体）改写为绝对 CDN URL；绝对/data: URI 原样保留。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from webui import _rewrite_hls_playlist  # noqa: E402

STREAM = "https://cdn.example.com/live/channel/index.m3u8"


def test_segment_lines_rewritten_to_absolute():
    playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:6\nseg_001.ts\nseg_002.ts\n"
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert "https://cdn.example.com/live/channel/seg_001.ts" in out
    assert "https://cdn.example.com/live/channel/seg_002.ts" in out


def test_key_uri_attribute_rewritten():
    playlist = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nseg_001.ts\n'
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert 'URI="https://cdn.example.com/live/channel/key.bin"' in out


def test_absolute_and_data_uris_untouched():
    playlist = (
        "#EXTM3U\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="https://k.cdn.example/key.bin"\n'
        "#EXT-X-KEY:METHOD=NONE\n"
        "https://cdn.example.com/live/channel/seg_001.ts\n"
    )
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert 'URI="https://k.cdn.example/key.bin"' in out
    assert "https://cdn.example.com/live/channel/seg_001.ts" in out


def test_variant_and_media_uri_attributes():
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1280000\n"
        "low/index.m3u8\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English",URI="audio/en.m3u8"\n'
    )
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert "https://cdn.example.com/live/channel/low/index.m3u8" in out
    assert 'URI="https://cdn.example.com/live/channel/audio/en.m3u8"' in out
