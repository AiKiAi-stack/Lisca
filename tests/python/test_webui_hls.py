"""ticket-11/19: HLS playlist 改写 + m3u 解析回归测试

覆盖：普通行（ts 分片/变体子列表）、# 行 URI 属性（EXT-X-KEY 密钥 /
EXT-X-MEDIA / EXT-X-STREAM-INF 变体）改写为本地代理 URL（ticket-19——
浏览器直连上游分片会被 UA/防盗链挡，全部走 /video?url=）；data: URI
原样保留；##EXT 双井号清洗；parse_m3u 频道列表解析。
"""

import pytest


from webui import _rewrite_hls_playlist, parse_m3u  # noqa: E402

STREAM = "https://cdn.example.com/live/channel/index.m3u8"
SEG_PROXY = "/video?url=https%3A%2F%2Fcdn.example.com%2Flive%2Fchannel%2Fseg_001.ts"
KEY_PROXY = "/video?url=https%3A%2F%2Fcdn.example.com%2Flive%2Fchannel%2Fkey.bin"


def test_segment_lines_rewritten_to_proxy():
    playlist = "#EXTM3U\n#EXT-X-TARGETDURATION:6\nseg_001.ts\nseg_002.ts\n"
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert SEG_PROXY in out
    assert "/video?url=https%3A%2F%2Fcdn.example.com%2Flive%2Fchannel%2Fseg_002.ts" in out
    assert "https://cdn.example.com/live/channel/seg_001.ts" not in out


def test_key_uri_attribute_rewritten_to_proxy():
    playlist = '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="key.bin"\nseg_001.ts\n'
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert f'URI="{KEY_PROXY}"' in out


def test_absolute_uri_proxied_and_data_untouched():
    playlist = (
        "#EXTM3U\n"
        '#EXT-X-KEY:METHOD=AES-128,URI="https://k.cdn.example/key.bin"\n'
        '#EXT-X-KEY:METHOD=AES-128,URI="data:text/plain;base64,AAAA"\n'
        "https://cdn.example.com/live/channel/seg_001.ts\n"
    )
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert 'URI="/video?url=https%3A%2F%2Fk.cdn.example%2Fkey.bin"' in out
    assert 'URI="data:text/plain;base64,AAAA"' in out
    assert SEG_PROXY in out


def test_variant_and_media_uri_attributes():
    playlist = (
        "#EXTM3U\n"
        "#EXT-X-STREAM-INF:BANDWIDTH=1280000\n"
        "low/index.m3u8\n"
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aac",NAME="English",URI="audio/en.m3u8"\n'
    )
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert "/video?url=https%3A%2F%2Fcdn.example.com%2Flive%2Fchannel%2Flow%2Findex.m3u8" in out
    assert 'URI="/video?url=https%3A%2F%2Fcdn.example.com%2Flive%2Fchannel%2Faudio%2Fen.m3u8"' in out


def test_double_hash_cleaned():
    playlist = "#EXTM3U\n##EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:6\nseg_001.ts\n"
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert "#EXT-X-VERSION:3" in out
    assert "##EXT-X-VERSION" not in out


def test_already_proxied_lines_untouched():
    playlist = "#EXTM3U\n/video?url=https%3A%2F%2Fx%2Fa.ts\n"
    out = _rewrite_hls_playlist(playlist, STREAM)
    assert "/video?url=https%3A%2F%2Fx%2Fa.ts" in out


def test_parse_m3u_channels():
    text = (
        '#EXTM3U x-tvg-url="http://epg/e.xml"\n'
        '#EXTINF:-1 tvg-name="CCTV1" group-title="央视频道", CCTV1\n'
        "http://a.example/live/cctv1.m3u8\n"
        '#EXTINF:-1 tvg-name="CCTV2" group-title="央视频道", CCTV2\n'
        "http://b.example/live/cctv2.m3u8\n"
        '#EXTINF:-1 tvg-name="卫视" group-title="卫视", 卫视\n'
        "http://c.example/live/ws.m3u8\n"
    )
    chs = parse_m3u(text)
    assert len(chs) == 3
    assert chs[0] == {"group": "央视频道", "name": "CCTV1",
                      "url": "http://a.example/live/cctv1.m3u8"}
    assert chs[1]["name"] == "CCTV2"
    assert chs[2] == {"group": "卫视", "name": "卫视", "url": "http://c.example/live/ws.m3u8"}


def test_parse_m3u_skips_entries_without_url():
    text = ('#EXTINF:-1, NoUrl\n'
            '#EXTINF:-1, HasUrl\nhttp://a.example/x.m3u8\n')
    chs = parse_m3u(text)
    assert len(chs) == 1
    assert chs[0]["name"] == "HasUrl"


def test_parse_m3u_rejects_single_hls_media_playlist():
    text = (
        "#EXTM3U\n"
        "#EXT-X-VERSION:3\n"
        "#EXT-X-TARGETDURATION:6\n"
        "#EXT-X-MEDIA-SEQUENCE:120\n"
        "#EXTINF:6.0,\n"
        "segment120.ts\n"
        "#EXTINF:6.0,\n"
        "segment121.ts\n"
    )
    with pytest.raises(ValueError, match="单路 HLS 播放流"):
        parse_m3u(text)
