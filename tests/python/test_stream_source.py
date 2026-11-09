from fractions import Fraction
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest

from lisca.stream_source import AudioTimelineError, StreamSource


def test_hls_clock_uses_decoded_pts_not_sliding_playlist_duration(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    container.decode.return_value = [object()]
    av_open = MagicMock(return_value=container)
    monkeypatch.setattr("lisca.stream_source.av.open", av_open)

    class Body(httpx.SyncByteStream):
        def __iter__(self):
            return iter(())

        def close(self):
            pass

    monkeypatch.setattr("httpx.HTTPTransport.handle_request", lambda *_a, **_k: (
        httpx.Response(200, stream=Body())
    ))
    resampled = MagicMock()
    resampled.pts = 19752000
    resampled.time_base = Fraction(1, 16000)
    resampled.sample_rate = 16000
    resampled.to_ndarray.return_value = np.zeros((1, 1600), dtype=np.int16)
    resampler = MagicMock()
    resampler.resample.side_effect = lambda frame: [resampled] if frame is not None else []
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("https://asr.test/live/index.m3u8")

    assert len(list(source.iter_chunks())) == 1
    assert source.first_pts_sec == 1234.5
    assert source.timeline_offset_sec == 0
    options = av_open.call_args.kwargs["options"]
    assert options["live_start_index"] == "-3"


def test_iter_chunks_fills_internal_pts_gap_without_absolute_prefill(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    decoded_frames = [object(), object()]
    container.decode.return_value = decoded_frames
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    first = MagicMock()
    first.pts = 19_752_000  # 1234.5 seconds, absolute source PTS
    first.time_base = Fraction(1, 16000)
    first.sample_rate = 16000
    first.to_ndarray.return_value = np.full((1, 160), 1200, dtype=np.int16)
    second = MagicMock()
    second.pts = 19_752_480  # starts 20 ms after the first 10 ms frame ends
    second.time_base = Fraction(1, 16000)
    second.sample_rate = 16000
    second.to_ndarray.return_value = np.full((1, 160), -700, dtype=np.int16)

    resampler = MagicMock()
    resampled_by_input = {decoded_frames[0]: [first], decoded_frames[1]: [second]}
    resampler.resample.side_effect = lambda frame: (
        [] if frame is None else resampled_by_input[frame]
    )
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("synthetic.mp4", chunk_sec=0.01)
    audio = np.frombuffer(b"".join(source.iter_chunks()), dtype=np.int16)

    assert source.first_pts_sec == 1234.5
    assert audio.shape == (640,)
    assert np.all(audio[:160] == 1200)
    assert np.all(audio[160:480] == 0)
    assert np.all(audio[480:] == -700)


def test_iter_chunks_accumulates_fractional_pts_gaps_across_frames(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    decoded_frames = [object() for _ in range(10)]
    container.decode.return_value = decoded_frames
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    frames = []
    for index in range(10):
        frame = MagicMock()
        frame.pts = index * 4
        frame.time_base = Fraction(1, 48000)
        frame.sample_rate = 16000
        frame.to_ndarray.return_value = np.array([[100 + index]], dtype=np.int16)
        frames.append(frame)

    resampler = MagicMock()
    by_input = {decoded: [frame] for decoded, frame in zip(decoded_frames, frames)}
    resampler.resample.side_effect = lambda frame: [] if frame is None else by_input[frame]
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("synthetic-fractional-gaps.mp4", chunk_sec=0.001)
    audio = np.frombuffer(b"".join(source.iter_chunks()), dtype=np.int16)

    expected = np.array(
        [100, 101, 0, 102, 103, 104, 0, 105, 106, 107, 0, 108, 109],
        dtype=np.int16,
    )
    assert np.array_equal(audio, expected)


def test_iter_chunks_flushes_resampler_tail_on_normal_eof(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    container.decode.return_value = [object()]
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    body = MagicMock()
    body.pts = 0
    body.time_base = Fraction(1, 16000)
    body.sample_rate = 16000
    body.to_ndarray.return_value = np.full((1, 160), 200, dtype=np.int16)
    tail = MagicMock()
    tail.pts = 160
    tail.time_base = Fraction(1, 16000)
    tail.sample_rate = 16000
    tail.to_ndarray.return_value = np.full((1, 16), -300, dtype=np.int16)

    resampler = MagicMock()
    resampler.resample.side_effect = lambda frame: [tail] if frame is None else [body]
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("synthetic.wav", chunk_sec=0.01)
    audio = np.frombuffer(b"".join(source.iter_chunks()), dtype=np.int16)

    assert audio.shape == (176,)
    assert np.all(audio[:160] == 200)
    assert np.all(audio[160:] == -300)


def test_iter_chunks_rejects_large_backward_pts_reset(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    decoded_frames = [object(), object(), object()]
    container.decode.return_value = decoded_frames
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    frames = []
    for pts, value in ((1_600_000, 100), (160_000, 200), (160_480, 300)):
        frame = MagicMock()
        frame.pts = pts
        frame.time_base = Fraction(1, 16000)
        frame.sample_rate = 16000
        frame.to_ndarray.return_value = np.full((1, 160), value, dtype=np.int16)
        frames.append(frame)

    resampler = MagicMock()
    by_input = dict(zip(decoded_frames, ([frame] for frame in frames)))
    resampler.resample.side_effect = lambda frame: [] if frame is None else by_input[frame]
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("synthetic-live-reset.m3u8", chunk_sec=0.01)
    with pytest.raises(AudioTimelineError, match="向后跳变"):
        b"".join(source.iter_chunks())


def test_iter_chunks_trims_small_pts_overlaps_without_repeating_audio(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    decoded_frames = [object(), object()]
    container.decode.return_value = decoded_frames
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    first = MagicMock()
    first.pts = 0
    first.time_base = Fraction(1, 16000)
    first.sample_rate = 16000
    first.to_ndarray.return_value = np.arange(160, dtype=np.int16)[None, :]
    second = MagicMock()
    second.pts = 80  # overlaps the preceding frame by 5 ms
    second.time_base = Fraction(1, 16000)
    second.sample_rate = 16000
    second.to_ndarray.return_value = (1000 + np.arange(160, dtype=np.int16))[None, :]

    resampler = MagicMock()
    by_input = {decoded_frames[0]: [first], decoded_frames[1]: [second]}
    resampler.resample.side_effect = lambda frame: [] if frame is None else by_input[frame]
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("synthetic-overlap.mp4", chunk_sec=0.01)
    audio = np.frombuffer(b"".join(source.iter_chunks()), dtype=np.int16)

    expected = np.concatenate((np.arange(160), 1000 + np.arange(80, 160))).astype(np.int16)
    assert np.array_equal(audio, expected)


def test_iter_chunks_rejects_large_forward_pts_discontinuity(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    decoded_frames = [object(), object()]
    container.decode.return_value = decoded_frames
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    frames = []
    for pts, value in ((0, 100), (32_160, 200)):
        frame = MagicMock()
        frame.pts = pts
        frame.time_base = Fraction(1, 16000)
        frame.sample_rate = 16000
        frame.to_ndarray.return_value = np.full((1, 160), value, dtype=np.int16)
        frames.append(frame)

    resampler = MagicMock()
    by_input = {decoded_frames[0]: [frames[0]], decoded_frames[1]: [frames[1]]}
    resampler.resample.side_effect = lambda frame: [] if frame is None else by_input[frame]
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("synthetic-large-gap.mp4", chunk_sec=0.01)
    with pytest.raises(AudioTimelineError, match="正向间隙"):
        b"".join(source.iter_chunks())


def test_iter_chunks_rejects_pts_gap_after_cumulative_limit(monkeypatch):
    container = MagicMock()
    container.streams.audio = [object()]
    decoded_frames = [object() for _ in range(62)]
    container.decode.return_value = decoded_frames
    monkeypatch.setattr("lisca.stream_source.av.open", lambda *_args, **_kwargs: container)

    frames = []
    for index in range(62):
        frame = MagicMock()
        frame.pts = index * 16_001
        frame.time_base = Fraction(1, 16000)
        frame.sample_rate = 16000
        frame.to_ndarray.return_value = np.ones((1, 1), dtype=np.int16)
        frames.append(frame)

    resampler = MagicMock()
    by_input = {decoded: [frame] for decoded, frame in zip(decoded_frames, frames)}
    resampler.resample.side_effect = lambda frame: [] if frame is None else by_input[frame]
    monkeypatch.setattr("lisca.stream_source.av.AudioResampler", lambda **_kwargs: resampler)

    source = StreamSource("synthetic-many-gaps.mp4")
    with pytest.raises(AudioTimelineError, match="累计超过"):
        list(source.iter_chunks())


def test_seek_starts_decoding_at_requested_local_position(tmp_path):
    import wave

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
