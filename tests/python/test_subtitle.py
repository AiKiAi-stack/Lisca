# encoding:utf-8
import pytest
from lisca.asr.subtitle import SubtitleEntry


class TestSubtitleEntry:
    def test_create_basic(self):
        entry = SubtitleEntry(
            index=1, text="你好世界", start_time=0.5, end_time=2.0, is_final=True,
        )
        assert entry.text == "你好世界"
        assert entry.start_time == 0.5
        assert entry.end_time == 2.0
        assert entry.is_final is True

    def test_to_srt_format(self):
        entry = SubtitleEntry(
            index=1, text="你好世界", start_time=1.500, end_time=4.020, is_final=True,
        )
        srt = entry.to_srt()
        assert "1" in srt
        assert "00:00:01,500" in srt
        assert "00:00:04,020" in srt
        assert "你好世界" in srt

    def test_to_srt_returns_empty_when_no_end_time(self):
        entry = SubtitleEntry(index=2, text="partial", start_time=0.3, end_time=None, is_final=False)
        assert entry.to_srt() == ""

    def test_intermediate_not_final(self):
        entry = SubtitleEntry(index=2, text="今天天气", start_time=0.3, end_time=None, is_final=False)
        assert entry.is_final is False
        assert entry.end_time is None

    def test_format_time_various(self):
        entry = SubtitleEntry(index=1, text="", start_time=0.0, end_time=1.0, is_final=True)
        assert entry._format_time(0.0) == "00:00:00,000"
        assert entry._format_time(61.5) == "00:01:01,500"
        assert entry._format_time(3661.999) == "01:01:01,999"

    def test_index_is_mutable(self):
        entry = SubtitleEntry(index=5, text="hello", start_time=0.0, end_time=1.0, is_final=True)
        srt_before = entry.to_srt()
        assert "5" in srt_before
        entry.index = 1
        srt_after = entry.to_srt()
        assert "1" in srt_after
