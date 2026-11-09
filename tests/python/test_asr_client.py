import queue
import threading
import time
from unittest.mock import ANY, MagicMock, Mock, patch, call
import pytest
from lisca.asr.asr_client import ASRClient, ASRState
from lisca.asr.asr_config import ASRSettings


class TestASRClient:
    @patch("lisca.asr.asr_client.Recognition")
    def test_provider_send_failure_is_not_reported_as_transport_failure(self, mock_rec):
        from lisca.asr.asr_client import RecognitionFailure
        from lisca.asr.recognition_process import RecognitionProviderError

        mock_rec.return_value.send_audio_frame.side_effect = RecognitionProviderError(
            403, "Model.AccessDenied")
        client = ASRClient(ASRSettings(api_key="fake-test-key"))
        client.start()
        client.feed_audio(b"\x01\x00" * 1600)
        with pytest.raises(RecognitionFailure) as caught:
            client.stop()
        assert caught.value.status_code == 403
        assert caught.value.code == "Model.AccessDenied"

    @patch("lisca.asr.asr_client.Recognition")
    def test_eof_provider_error_keeps_structured_cause_after_shutdown(self, mock_rec):
        from lisca.asr.asr_client import RecognitionFailure

        client = ASRClient(ASRSettings(api_key="fake-test-key"))
        callback = mock_rec.call_args.kwargs["callback"]

        def fail_at_eof(**_kwargs):
            callback.on_error(Mock(status_code=403, code="Model.AccessDenied"))
            raise ConnectionError("Recognition worker failed before completion")

        mock_rec.return_value.stop.side_effect = fail_at_eof
        client.start()
        with pytest.raises(RecognitionFailure) as caught:
            client.stop()
        assert caught.value.status_code == 403
        assert caught.value.code == "Model.AccessDenied"
        mock_rec.return_value.cancel.assert_called_once()

    @patch("lisca.asr.asr_client.Recognition")
    def test_audio_thread_start_failure_cancels_started_transport(self, mock_rec):
        client = ASRClient(ASRSettings(api_key="fake-test-key"))
        with patch("lisca.asr.asr_client.Thread.start", side_effect=RuntimeError("no thread")):
            with pytest.raises(RuntimeError, match="no thread"):
                client.start()
        mock_rec.return_value.cancel.assert_called_once()
        assert client.state == ASRState.STOPPED
        with pytest.raises(RuntimeError):
            client.feed_audio(b"\x00" * 3200)

    @patch("lisca.asr.asr_client.Recognition")
    def test_cancel_interrupts_transport_before_waiting_for_audio_sender(self, mock_rec):
        """Cancellation releases an in-flight sender and discards later audio."""
        recognition = mock_rec.return_value
        entered, release = threading.Event(), threading.Event()
        delivered = []

        def send(data):
            entered.set()
            assert release.wait(10), "fake transport was not released"
            delivered.append(data)

        recognition.send_audio_frame.side_effect = send
        recognition.cancel.side_effect = lambda **_kwargs: release.set()
        client = ASRClient(ASRSettings(api_key="fake-test-key"))
        client.start()
        first, queued = b"a" * 3200, b"b" * 3200
        client.feed_audio(first)
        assert entered.wait(1)
        client.feed_audio(queued)
        try:
            started = time.monotonic()
            client.cancel()
            assert time.monotonic() - started < 1
            assert delivered == [first]
            recognition.cancel.assert_called_once()
            assert client.state == ASRState.STOPPED
            with pytest.raises(RuntimeError):
                client.feed_audio(b"c" * 3200)
        finally:
            release.set()
            client.stop()

    @patch("lisca.asr.asr_client.Recognition")
    def test_explicit_restart_never_sends_audio_discarded_by_cancel(self, mock_rec):
        recognition = mock_rec.return_value
        entered, release = threading.Event(), threading.Event()
        delivered = []

        def send(data):
            if not delivered:
                entered.set()
                assert release.wait(10)
            delivered.append(data)

        recognition.send_audio_frame.side_effect = send
        recognition.cancel.side_effect = lambda **_kwargs: release.set()
        client = ASRClient(ASRSettings(api_key="fake-test-key"))
        client.start()
        first, discarded, fresh = b"a" * 3200, b"b" * 3200, b"c" * 3200
        client.feed_audio(first)
        assert entered.wait(1)
        client.feed_audio(discarded)
        try:
            client.cancel()
            client.start()
            client.feed_audio(fresh)
            client.stop()
            assert delivered == [first, fresh]
        finally:
            release.set()
            client.stop()

    @patch("lisca.asr.asr_client.Recognition")
    def test_cancel_preserves_results_but_rejects_late_callbacks(self, mock_rec):
        client = ASRClient(ASRSettings(api_key="fake-test-key"))
        callback = mock_rec.call_args.kwargs["callback"]

        class Result:
            status_code = 403
            code = "Model.AccessDenied"

            def __init__(self, text):
                self.text = text

            def get_sentence(self):
                return {"text": self.text, "begin_time": 0, "end_time": 1000}

        client.start()
        callback.on_event(Result("kept"))
        client.cancel()
        callback.on_event(Result("late"))
        callback.on_error(Result("late error"))
        assert [item.text for item in client.subtitle_buffer] == ["kept"]
        client.raise_if_error()

    @patch("lisca.asr.asr_client.Recognition")
    def test_local_transport_failure_keeps_its_type(self, mock_rec):
        client = ASRClient(ASRSettings(api_key="fake-test-key"))
        callback = mock_rec.call_args.kwargs["callback"]
        failure = ConnectionError("safe transport failure")
        callback.on_error(type("Result", (), {"transport_error": failure})())
        with pytest.raises(ConnectionError) as caught:
            client.raise_if_error()
        assert caught.value is failure

    @patch("lisca.asr.asr_client.Recognition")
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

    @patch("lisca.asr.asr_client.Recognition")
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

    @patch("lisca.asr.asr_client.Recognition")
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

    @patch("lisca.asr.asr_client.Recognition")
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

    @patch("lisca.asr.asr_client.Recognition")
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
            call.stop(timeout=ANY),
        ]
        assert 0 < mock_instance.stop.call_args.kwargs["timeout"] <= 15

    @patch("lisca.asr.asr_client.Recognition")
    def test_callback_preserves_structured_sdk_error_without_raw_message(self, mock_rec):
        """保留 SDK 状态码/错误码，丢弃可能带敏感信息的服务端消息。"""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance
        client = ASRClient(ASRSettings(api_key="sk-test"))
        callback = mock_rec.call_args.kwargs["callback"]

        class FakeResult:
            status_code = 403
            code = "Model.AccessDenied"

            def get_message(self):
                return "private provider response"

        callback.on_error(FakeResult())

        with pytest.raises(RuntimeError) as caught:
            client.raise_if_error()
        assert caught.value.status_code == 403
        assert caught.value.code == "Model.AccessDenied"
        assert "private provider response" not in str(caught.value)

    @patch("lisca.asr.asr_client.Recognition")
    def test_callback_reports_error_when_sdk_stringification_fails(self, mock_rec):
        client = ASRClient(ASRSettings(api_key="sk-test"))
        callback = mock_rec.call_args.kwargs["callback"]

        class BrokenResult:
            def __str__(self):
                raise AttributeError("headers")

        callback.on_error(BrokenResult())
        with pytest.raises(RuntimeError) as caught:
            client.raise_if_error()
        assert caught.value.status_code is None
        assert caught.value.code is None

    @patch("lisca.asr.asr_client.Recognition")
    def test_subtitle_buffer_accessible(self, mock_rec):
        """subtitle_buffer should be an accessible deque attribute."""
        mock_instance = MagicMock()
        mock_rec.return_value = mock_instance

        client = ASRClient(ASRSettings(api_key="sk-test"))
        assert client.subtitle_buffer is not None
        from collections import deque
        assert isinstance(client.subtitle_buffer, deque)

    @patch("lisca.asr.asr_client.Recognition")
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

    @patch("lisca.asr.asr_client.Recognition")
    def test_callback_extracts_words(self, mock_rec):
        """on_event 应提取 sentence.words 字级时间戳（partial 尾字 end 可为 None）"""
        from collections import deque

        from lisca.asr.asr_client import _Callback

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


def test_full_local_audio_queue_is_a_capacity_failure_not_an_unknown_provider_error():
    from lisca.asr.recognition_process import RecognitionCapacityError

    with patch('lisca.asr.asr_client.Recognition'):
        client = ASRClient(ASRSettings(api_key='fake-test-key'))
    client._running.set()
    client._input_queue = Mock()
    client._input_queue.put.side_effect = queue.Full()
    with pytest.raises(RecognitionCapacityError):
        client.feed_audio(b'\x01\x00' * 1600)
