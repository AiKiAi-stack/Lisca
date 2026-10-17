from io import BytesIO
from unittest.mock import MagicMock

from tools.stream_source import StreamSource


def test_live_hls_starts_at_the_live_edge_with_matching_timeline(monkeypatch):
    """ASR audio and the HLS player must start on the same live segment."""
    manifest = (
        "#EXTM3U\n"
        "#EXT-X-TARGETDURATION:10\n"
        "#EXT-X-MEDIA-SEQUENCE:42\n"
        "#EXTINF:10,\nseg42.ts\n"
        "#EXTINF:10,\nseg43.ts\n"
        "#EXTINF:10,\nseg44.ts\n"
    ).encode()
    response = BytesIO(manifest)
    response.__enter__ = lambda: response
    response.__exit__ = lambda *_args: None
    monkeypatch.setattr("tools.stream_source.urllib.request.urlopen", lambda *_a, **_k: response)
    container = MagicMock()
    container.streams.audio = [object()]
    av_open = MagicMock(return_value=container)
    monkeypatch.setattr("tools.stream_source.av.open", av_open)

    source = StreamSource("https://asr.test/live/index.m3u8")

    assert source.timeline_offset_sec == 20
    options = av_open.call_args.kwargs["options"]
    assert options["live_start_index"] == "-1"
