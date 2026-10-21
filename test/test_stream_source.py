from fractions import Fraction
from unittest.mock import MagicMock

import numpy as np

from tools.stream_source import StreamSource


def test_hls_clock_uses_decoded_pts_not_sliding_playlist_duration(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    container.decode.return_value = [object()]
    av_open = MagicMock(return_value=container)
    monkeypatch.setattr("tools.stream_source.av.open", av_open)
    resampled = MagicMock()
    resampled.pts = 19752000
    resampled.time_base = Fraction(1, 16000)
    resampled.to_ndarray.return_value = np.zeros((1, 1600), dtype=np.int16)
    resampler = MagicMock()
    resampler.resample.return_value = [resampled]
    monkeypatch.setattr("tools.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("https://asr.test/live/index.m3u8")

    assert len(list(source.iter_chunks())) == 1
    assert source.first_pts_sec == 1234.5
    assert source.timeline_offset_sec == 0
    options = av_open.call_args.kwargs["options"]
    assert options["live_start_index"] == "-3"
def test_seek_starts_decoding_at_requested_local_position(tmp_path):
    import wave
    from stream_source import StreamSource

    sample = tmp_path / "two-seconds.wav"
    with wave.open(str(sample), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\x00\x00" * 32000)
    source = StreamSource(str(sample), start_sec=0.5)
    assert source.timeline_offset_sec == 0.5
    audio = b"".join(source.iter_chunks())
    assert len(audio) == 48000
