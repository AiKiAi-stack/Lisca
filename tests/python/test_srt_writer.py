import os
import tempfile
import pytest
from lisca.asr.srt_writer import SRTWriter
from lisca.asr.subtitle import SubtitleEntry


class TestSRTWriter:
    def test_open_creates_file_with_correct_name(self):
        """open() should create a .srt file named after the video file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = SRTWriter(base_dir=tmpdir)
            writer.open("0_video.mp4")
            expected_path = os.path.join(tmpdir, "0_video.srt")
            assert os.path.exists(expected_path)
            assert writer._current_file == expected_path
            writer.close()

    def test_write_final_entry_appends_srt(self):
        """Only final entries should be written to the SRT file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = SRTWriter(base_dir=tmpdir)
            writer.open("test.mp4")
            entry = SubtitleEntry(index=1, text="hello", start_time=0.5, end_time=1.0, is_final=True)
            writer.write_entry(entry)
            writer._file.flush()

            with open(writer._current_file) as f:
                content = f.read()
            assert "hello" in content
            assert "00:00:00,500" in content
            assert "00:00:01,000" in content
            writer.close()

    def test_skips_non_final_entry(self):
        """Entries with is_final=False should NOT be written."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = SRTWriter(base_dir=tmpdir)
            writer.open("test.mp4")
            entry = SubtitleEntry(index=1, text="partial", start_time=0.3, end_time=None, is_final=False)
            writer.write_entry(entry)
            writer._file.flush()

            with open(writer._current_file) as f:
                content = f.read()
            assert content == ""  # Nothing written
            writer.close()

    def test_rotate_switches_files(self):
        """rotate() should close old file and open new one based on video filename."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = SRTWriter(base_dir=tmpdir)
            writer.open("0_seg.mp4")
            old_path = writer._current_file
            writer.rotate("1_seg.mp4")
            new_path = writer._current_file

            assert old_path != new_path
            assert os.path.exists(old_path)  # Old file still exists
            assert os.path.exists(new_path)   # New file created
            writer.close()

    def test_entry_index_resets_on_rotation(self):
        """After rotation, entry index should reset for the new file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            writer = SRTWriter(base_dir=tmpdir)
            writer.open("first.mp4")
            entry1 = SubtitleEntry(index=999, text="one", start_time=0.0, end_time=1.0, is_final=True)
            writer.write_entry(entry1)
            writer._file.flush()

            writer.rotate("second.mp4")
            # After rotation, index should be back to 1
            entry2 = SubtitleEntry(index=888, text="two", start_time=0.0, end_time=1.0, is_final=True)
            writer.write_entry(entry2)
            writer._file.flush()

            with open(writer._current_file) as f:
                content = f.read()
            assert "1" in content  # First entry in new file = index 1
            writer.close()
