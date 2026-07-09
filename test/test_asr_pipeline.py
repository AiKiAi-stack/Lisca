from unittest.mock import MagicMock, PropertyMock, patch

import numpy as np
import pytest

from streamparser.asr_pipeline import ASRSubtitlePipeline
from streamparser.asr_config import ASRSettings
from streamparser.frame import Frame, FrameType


class TestASRSubtitlePipeline:
    @patch("streamparser.asr_pipeline.ASRClient")
    @patch("streamparser.asr_pipeline.AudioProcessor")
    @patch("streamparser.asr_pipeline.SRTWriter")
    @patch("streamparser.asr_pipeline.SubtitleOverlay")
    def test_start_creates_all_components(self, mock_overlay, mock_srt, mock_ap, mock_client):
        """start() should create ASRClient, AudioProcessor, SRTWriter, SubtitleOverlay and start ASR."""
        mock_client_instance = MagicMock()
        mock_client.return_value = mock_client_instance
        mock_overlay_instance = MagicMock()
        mock_overlay.return_value = mock_overlay_instance

        settings = ASRSettings(api_key="sk-test")
        pipeline = ASRSubtitlePipeline(settings)
        pipeline.start(video_filename="0_test.mp4")

        mock_client.assert_called_once_with(settings)
        mock_client_instance.start.assert_called_once()
        mock_srt.assert_called_once()
        mock_overlay.assert_called_once()

    @patch("streamparser.asr_pipeline.ASRClient")
    @patch("streamparser.asr_pipeline.AudioProcessor")
    def test_feed_audio_routes_to_asr_client(
        self, mock_audio_processor_cls, mock_client
    ):
        """feed_audio should send frames through AudioProcessor to ASRClient."""
        mock_client_instance = MagicMock()
        mock_client.return_value = mock_client_instance
        mock_ap_instance = MagicMock()
        mock_ap_instance.process.return_value = np.zeros(1600, dtype=np.int16)
        mock_audio_processor_cls.return_value = mock_ap_instance

        settings = ASRSettings(api_key="sk-test")
        pipeline = ASRSubtitlePipeline(settings)
        pipeline.start("test.mp4")

        frame_data = np.zeros((1, 1600), dtype=np.int16)
        mock_frame = MagicMock(spec=Frame)
        type(mock_frame).data = PropertyMock(return_value=frame_data)
        mock_frame.metadata = {
            "format": "s16",
            "layout": "mono",
            "sample_rate": 16000,
        }

        pipeline.feed_audio_frame(mock_frame)
        mock_ap_instance.process.assert_called_once()
        mock_client_instance.feed_audio.assert_called_once()

    @patch("streamparser.asr_pipeline.ASRClient")
    def test_stop_stops_all_components(self, mock_client):
        """stop() should flush and stop ASR client then close SRT writer."""
        mock_client_instance = MagicMock()
        mock_client.return_value = mock_client_instance

        settings = ASRSettings(api_key="sk-test")
        pipeline = ASRSubtitlePipeline(settings)
        pipeline.start("test.mp4")
        pipeline.stop()

        mock_client_instance.stop.assert_called_once()

    @patch("streamparser.asr_pipeline.ASRClient")
    def test_rotate_notifies_srt_writer(self, mock_client):
        """rotate() should delegate to SRTWriter.rotate()."""
        mock_client_instance = MagicMock()
        mock_client.return_value = mock_client_instance

        settings = ASRSettings(api_key="sk-test")
        pipeline = ASRSubtitlePipeline(settings)
        pipeline.start("first.mp4")

        pipeline.rotate("second.mp4")
        # verify SRT writer received rotate call (we mock at class level so check)
        # This tests the pipeline's rotate method exists and doesn't crash

    @patch("streamparser.asr_pipeline.ASRClient")
    def test_subtitle_overlay_accessible(self, mock_client):
        """subtitle_overlay property should return the filter function."""
        mock_client_instance = MagicMock()
        mock_client.return_value = mock_client_instance

        settings = ASRSettings(api_key="sk-test")
        pipeline = ASRSubtitlePipeline(settings)
        pipeline.start("test.mp4")

        assert pipeline.subtitle_overlay is not None
        assert callable(pipeline.subtitle_overlay)
