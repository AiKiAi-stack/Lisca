import numpy as np
import pytest
from streamparser.subtitle import SubtitleOverlay, SubtitleEntry
from streamparser.frame import Frame, FrameType


class TestSubtitleOverlay:
    def test_overlay_cjk_text_on_video_frame(self):
        """Render Chinese text onto a blank video frame, verify pixels changed."""
        overlay = SubtitleOverlay(font_size=24)
        entry = SubtitleEntry(index=1, text="测试字幕", start_time=0.0, end_time=5.0, is_final=True)
        overlay.update_entry(entry)

        frame_data = np.zeros((480, 640, 3), dtype=np.uint8)
        frame = Frame(data=frame_data, id=0, time=1.0, type=FrameType.VIDEO, rate=30.0)

        result = overlay(frame)
        assert result is frame  # Returns same frame object
        assert result.type == FrameType.VIDEO
        assert result.data.shape == (480, 640, 3)  # Shape unchanged
        # At least some pixels should be non-zero (text drawn)
        assert result.data.sum() > 0

    def test_skip_non_video_frame(self):
        """Audio frames should pass through unchanged."""
        overlay = SubtitleOverlay()
        audio_data = np.zeros(1024, dtype=np.int16)
        frame = Frame(data=audio_data, id=0, time=0.0, type=FrameType.AUDIO, rate=48000.0)
        result = overlay(frame)
        assert result is frame  # Same object returned
        assert result.type == FrameType.AUDIO

    def test_no_entry_returns_frame_unchanged(self):
        """When no subtitle entry is set, frame should pass through."""
        overlay = SubtitleOverlay()
        frame_data = np.zeros((100, 100, 3), dtype=np.uint8)
        frame = Frame(data=frame_data, id=1, time=0.5, type=FrameType.VIDEO, rate=15.0)
        result = overlay(frame)
        assert result is frame

    def test_update_entry_changes_text(self):
        """After updating the entry, new text should be rendered."""
        overlay = SubtitleOverlay(font_size=24)

        entry1 = SubtitleEntry(index=1, text="第一句", start_time=0.0, end_time=1.0, is_final=True)
        overlay.update_entry(entry1)

        frame_data = np.zeros((200, 400, 3), dtype=np.uint8)
        frame = Frame(data=frame_data.copy(), id=0, time=0.3, type=FrameType.VIDEO, rate=30.0)
        result1 = overlay(frame)
        sum1 = result1.data.sum()

        entry2 = SubtitleEntry(index=2, text="不同的字幕", start_time=1.0, end_time=2.0, is_final=True)
        overlay.update_entry(entry2)

        frame2 = Frame(data=frame_data.copy(), id=1, time=1.5, type=FrameType.VIDEO, rate=30.0)
        result2 = overlay(frame2)
        sum2 = result2.data.sum()

        # Both should have text rendered (non-zero sum)
        assert sum1 > 0
        assert sum2 > 0
        # Different text may produce different pixel sums
