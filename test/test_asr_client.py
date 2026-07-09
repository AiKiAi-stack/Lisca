import queue
import threading
from unittest.mock import MagicMock, patch, call
import pytest
from streamparser.asr_client import ASRClient, ASRState
from streamparser.asr_config import ASRSettings


class TestASRClient:
    @patch("streamparser.asr_client.Recognition")
    def test_start_stop_lifecycle(self, mock_rec):
        """start() should set state to RUNNING and call recognition.start(); stop() sets STOPPED."""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance

        settings = ASRSettings(api_key="sk-test")
        client = ASRClient(settings)

        assert client.state == ASRState.IDLE
        client.start()
        assert client.state == ASRState.RUNNING
        mock_instance.start.assert_called_once()

        client.stop()
        assert client.state == ASRState.STOPPED
        mock_instance.stop.assert_called_once()

    @patch("streamparser.asr_client.Recognition")
    def test_feed_audio_buffers_below_chunk_size(self, mock_rec):
        """Audio under chunk size should be buffered, not sent immediately."""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance

        settings = ASRSettings(api_key="sk-test")
        client = ASRClient(settings, chunk_size=3200)
        client.start()

        client.feed_audio(b"\x00" * 1600)
        # Not enough to trigger send yet - wait a bit for worker thread
        import time; time.sleep(0.1)
        mock_instance.send_audio_frame.assert_not_called()

        client.stop()

    @patch("streamparser.asr_client.Recognition")
    def test_feed_audio_sends_when_buffer_full(self, mock_rec):
        """When cumulative audio reaches chunk_size, should call send_audio_frame."""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance

        settings = ASRSettings(api_key="sk-test")
        client = ASRClient(settings, chunk_size=3200)
        client.start()

        client.feed_audio(b"\x00" * 3200)
        import time; time.sleep(0.2)
        assert mock_instance.send_audio_frame.call_count >= 1

        client.stop()

    @patch("streamparser.asr_client.Recognition")
    def test_subtitle_buffer_accessible(self, mock_rec):
        """subtitle_buffer should be an accessible deque attribute."""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance

        client = ASRClient(ASRSettings(api_key="sk-test"))
        assert client.subtitle_buffer is not None
        from collections import deque
        assert isinstance(client.subtitle_buffer, deque)
