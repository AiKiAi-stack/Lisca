import queue
import threading
from unittest.mock import MagicMock, Mock, patch, call
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
    def test_stop_flushes_partial_audio_frame(self, mock_rec):
        """结束流时也要把不足一帧的尾部音频送给识别服务。"""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance
        client = ASRClient(ASRSettings(api_key="sk-test"), chunk_size=3200)
        client.start()

        tail = b"\x01" * 1600
        client.feed_audio(tail)
        client.stop()

        mock_instance.send_audio_frame.assert_called_once_with(tail)
        mock_instance.stop.assert_called_once()

    @patch("streamparser.asr_client.Recognition")
    def test_stop_drains_queued_audio_before_stopping_recognition(self, mock_rec):
        """stop 应排空已接收队列，且在最后一帧发送后才关闭 SDK。"""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance
        recorder = Mock()
        recorder.attach_mock(mock_instance.send_audio_frame, "send_audio_frame")
        recorder.attach_mock(mock_instance.stop, "stop")
        client = ASRClient(ASRSettings(api_key="sk-test"), chunk_size=3200)
        client.start()
        frames = [b"\x01" * 3200, b"\x02" * 1600]
        for frame in frames:
            client.feed_audio(frame)

        client.stop()

        assert recorder.mock_calls == [
            call.send_audio_frame(frames[0]),
            call.send_audio_frame(frames[1]),
            call.stop(),
        ]

    @patch("streamparser.asr_client.Recognition")
    def test_callback_exposes_sdk_errors(self, mock_rec):
        """SDK 的异步错误必须可由引擎轮询并反馈给用户。"""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance
        client = ASRClient(ASRSettings(api_key="sk-test"))
        callback = mock_rec.call_args.kwargs["callback"]

        class FakeResult:
            def get_message(self):
                return "invalid audio format"

        callback.on_error(FakeResult())

        with pytest.raises(RuntimeError, match="invalid audio format"):
            client.raise_if_error()

    @patch("streamparser.asr_client.Recognition")
    def test_callback_reports_error_when_sdk_stringification_fails(self, mock_rec):
        client = ASRClient(ASRSettings(api_key="sk-test"))
        callback = mock_rec.call_args.kwargs["callback"]

        class BrokenResult:
            def __str__(self):
                raise AttributeError("headers")

        callback.on_error(BrokenResult())
        with pytest.raises(RuntimeError, match="DashScope recognition failed"):
            client.raise_if_error()

    @patch("streamparser.asr_client.Recognition")
    def test_subtitle_buffer_accessible(self, mock_rec):
        """subtitle_buffer should be an accessible deque attribute."""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance

        client = ASRClient(ASRSettings(api_key="sk-test"))
        assert client.subtitle_buffer is not None
        from collections import deque
        assert isinstance(client.subtitle_buffer, deque)

    @patch("streamparser.asr_client.Recognition")
    def test_recognition_kwargs_forwarded(self, mock_rec):
        """断句灵敏度/心跳/语种参数应透传给 Recognition（ticket-10）"""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance

        ASRClient(ASRSettings(api_key="sk-test"),
                  max_sentence_silence=400, heartbeat=True,
                  language_hints=["zh"], multi_threshold_mode_enabled=True)
        kwargs = mock_rec.call_args.kwargs
        assert kwargs["max_sentence_silence"] == 400
        assert kwargs["heartbeat"] is True
        assert kwargs["language_hints"] == ["zh"]
        assert kwargs["multi_threshold_mode_enabled"] is True

    @patch("streamparser.asr_client.Recognition")
    def test_callback_extracts_words(self, mock_rec):
        """on_event 应提取 sentence.words 字级时间戳（partial 尾字 end 可为 None）"""
        from collections import deque

        from streamparser.asr_client import _Callback

        class FakeResult:
            def __init__(self, sentence):
                self._sentence = sentence

            def get_sentence(self):
                return self._sentence

        cb = _Callback(deque(), [0], queue.Queue())
        cb.on_event(FakeResult({
            "text": "你好世界", "begin_time": 1000, "end_time": 3000,
            "sentence_id": 12,
            "words": [
                {"begin_time": 1000, "end_time": 1500, "text": "你"},
                {"begin_time": 1500, "end_time": 2000, "text": "好"},
                {"begin_time": 2000, "end_time": 3000, "text": "世界"},
            ],
        }))
        entry = cb._subtitle_buffer.popleft()  # noqa: SLF001 - 测试直接访问
        assert entry.words == [(1.0, 1.5, "你"), (1.5, 2.0, "好"), (2.0, 3.0, "世界")]
        assert entry.sentence_id == 12

        # partial：中间结果尾字缺 end_time → None（句终才补）
        cb.on_event(FakeResult({
            "text": "你好", "begin_time": 1000, "end_time": None,
            "words": [
                {"begin_time": 1000, "end_time": 1500, "text": "你"},
                {"begin_time": 1500, "end_time": None, "text": "好"},
            ],
        }))
        entry2 = cb._subtitle_buffer.popleft()  # noqa: SLF001
        assert entry2.words[-1] == (1.5, None, "好")
