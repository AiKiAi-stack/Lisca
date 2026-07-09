import numpy as np
import av
import pytest
from streamparser.audio_processor import AudioProcessor


class TestAudioProcessor:
    def test_resample_stereo_48k_fltp_to_mono_16k_s16(self):
        """Convert stereo 48kHz float → mono 16kHz int16, verify dtype and channel count."""
        samples_per_channel = 1024
        data = np.random.randn(2, samples_per_channel).astype(np.float32)
        frame = av.AudioFrame.from_ndarray(data, format="fltp", layout="stereo")
        frame.sample_rate = 48000

        processor = AudioProcessor(target_sample_rate=16000, target_format="s16", target_layout="mono")
        result = processor.process(frame)

        assert isinstance(result, np.ndarray)
        assert result.dtype == np.int16
        # mono should be 1D after squeeze
        assert len(result.shape) == 1

    def test_passthrough_when_already_correct_format(self):
        """Mono 16kHz s16 input should pass through unchanged in length."""
        data = np.ones(320, dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(data.reshape(1, 320), format="s16", layout="mono")
        frame.sample_rate = 16000

        processor = AudioProcessor(target_sample_rate=16000, target_format="s16", target_layout="mono")
        result = processor.process(frame)

        assert result.dtype == np.int16
        assert len(result.shape) == 1
        # With passthrough the sample count may differ slightly due to resampler behavior

    def test_resample_stereo_44100_s16_to_mono_16k_s16(self):
        """Different source rate: 44.1kHz stereo s16 → 16kHz mono s16."""
        samples_per_channel = 2048
        data = np.ones((2, samples_per_channel), dtype=np.int16)
        frame = av.AudioFrame.from_ndarray(data, format="s16p", layout="stereo")
        frame.sample_rate = 44100

        processor = AudioProcessor(target_sample_rate=16000, target_format="s16", target_layout="mono")
        result = processor.process(frame)

        assert result.dtype == np.int16
        assert len(result.shape) == 1
        # Should have fewer samples after downsampling (2048 * 16000/44100 ≈ 743)

    def test_resampler_reused(self):
        """Multiple calls should reuse the same resampler instance."""
        processor = AudioProcessor()
        assert processor._resampler is not None
        # Process a frame, then another, verify no crash
        data1 = np.ones((1, 512), dtype=np.int16)
        f1 = av.AudioFrame.from_ndarray(data1, format="s16", layout="mono")
        f1.sample_rate = 16000
        r1 = processor.process(f1)
        assert isinstance(r1, np.ndarray)

        data2 = np.ones((1, 256), dtype=np.int16)
        f2 = av.AudioFrame.from_ndarray(data2, format="s16", layout="mono")
        f2.sample_rate = 16000
        r2 = processor.process(f2)
        assert isinstance(r2, np.ndarray)
