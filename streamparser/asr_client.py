from collections import deque
from enum import Enum, auto
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
import time
from typing import Optional

from dashscope.audio.asr import RecognitionCallback, RecognitionResult

from streamparser.asr_config import ASRSettings
from streamparser.recognition_process import (RecognitionProcess as Recognition,
                                              RecognitionProviderError)
from streamparser.subtitle import SubtitleEntry

CANCEL_TIMEOUT_SECONDS = 5.0
FINALIZE_TIMEOUT_SECONDS = 15.0
MAX_INPUT_CHUNK_BYTES = 1024 * 1024


class ASRState(Enum):
    IDLE = auto()
    RUNNING = auto()
    STOPPED = auto()


class RecognitionFailure(RuntimeError):
    """SDK callback failure with structured fields and no raw provider message."""

    def __init__(self, status_code=None, code=None):
        self.status_code = status_code
        self.code = code
        super().__init__("DashScope recognition request failed")


class ASRTransportFailure(ConnectionError):
    """Audio transport failed; intentionally does not retain exception text."""


class _Callback(RecognitionCallback):
    def __init__(self, subtitle_buffer: deque, sentence_count: list, errors: Queue,
                 cancelled=None) -> None:
        super().__init__()
        self._subtitle_buffer = subtitle_buffer
        self._sentence_count = sentence_count
        self._errors = errors
        self._cancelled = cancelled

    def on_open(self) -> None:
        pass

    def on_complete(self) -> None:
        pass

    def on_close(self) -> None:
        pass

    def on_error(self, result: RecognitionResult) -> None:
        if self._cancelled is not None and self._cancelled.is_set():
            return
        transport_error = getattr(result, "transport_error", None)
        if isinstance(transport_error, (ConnectionError, TimeoutError)):
            self._errors.put(transport_error)
            return
        self._errors.put(RecognitionFailure(getattr(result, "status_code", None),
                                            getattr(result, "code", None)))

    def on_event(self, result: RecognitionResult) -> None:
        if self._cancelled is not None and self._cancelled.is_set():
            return
        sentence = result.get_sentence()
        if not isinstance(sentence, dict):
            return
        self._sentence_count[0] += 1
        words = sentence.get("words") or []
        word_list = []
        for w in words:
            if not isinstance(w, dict):
                continue
            beg = (w.get("begin_time") or 0) / 1000.0
            end = w.get("end_time")
            end_sec = end / 1000.0 if end is not None else None
            word_list.append((beg, end_sec, w.get("text", "")))
        entry = SubtitleEntry(
            index=self._sentence_count[0],
            text=sentence.get("text", ""),
            # 新版 dashscope 部分事件时间戳可能为 None，容错为 0
            start_time=(sentence.get("begin_time") or 0) / 1000.0,
            end_time=(sentence.get("end_time") or 0) / 1000.0,
            is_final=RecognitionResult.is_sentence_end(sentence),
            words=word_list or None,
            sentence_id=sentence.get("sentence_id"),
        )
        self._subtitle_buffer.append(entry)


class ASRClient:
    def __init__(self, settings: ASRSettings, chunk_size: int = 3200,
                 **recognition_kwargs) -> None:
        self.settings = settings
        self.chunk_size = chunk_size
        if not isinstance(chunk_size, int) or not 0 < chunk_size <= MAX_INPUT_CHUNK_BYTES:
            raise ValueError("ASR chunk size is outside the supported input budget")
        self.state = ASRState.IDLE
        self.subtitle_buffer: deque = deque()
        self._sentence_count = [0]

        self._errors: Queue = Queue()
        self._input_queue: Queue = Queue(maxsize=100)
        self._audio_buffer = bytearray()
        self._running = Event()
        self._cancelled = Event()
        self._input_lock = Lock()
        self._worker: Optional[Thread] = None
        callback = _Callback(self.subtitle_buffer, self._sentence_count, self._errors,
                             self._cancelled)
        self._recognition = Recognition(
            model=settings.model or "fun-asr-realtime",
            format="pcm",
            sample_rate=16000,
            callback=callback,
            api_key=settings.api_key,
            **recognition_kwargs,
        )

    def start(self) -> None:
        if self._running.is_set() or (self._worker is not None and self._worker.is_alive()):
            raise RuntimeError("ASR session is already running")
        self._cancelled.clear()
        self._running.set()
        try:
            self._recognition.start()
            self._worker = Thread(target=self._worker_loop, daemon=True)
            self._worker.start()
        except Exception:
            self._running.clear()
            self._cancelled.set()
            self.state = ASRState.STOPPED
            self._recognition.cancel(timeout=CANCEL_TIMEOUT_SECONDS)
            raise
        self.state = ASRState.RUNNING

    def stop(self) -> None:
        if self.state == ASRState.STOPPED and (self._worker is None or not self._worker.is_alive()):
            return
        deadline = time.monotonic() + FINALIZE_TIMEOUT_SECONDS
        with self._input_lock:
            self._running.clear()
        try:
            self._join_worker(deadline)
            self.raise_if_error()
            self._recognition.stop(timeout=max(0.0, deadline - time.monotonic()))
            self.raise_if_error()
        except Exception as failure:
            if isinstance(failure, RecognitionProviderError):
                failure = RecognitionFailure(failure.status_code, failure.code)
            try:
                self.raise_if_error()
            except Exception as reported_failure:
                failure = reported_failure
            self._cancelled.set()
            try:
                self._recognition.cancel(timeout=max(0.0, deadline - time.monotonic()))
                self._join_worker(deadline)
            except Exception as cleanup_error:
                raise failure from cleanup_error
            raise failure
        finally:
            self.state = ASRState.STOPPED

    def _join_worker(self, deadline):
        if self._worker is not None and self._worker.ident is not None:
            self._worker.join(timeout=min(5.0, max(0.0, deadline - time.monotonic())))
            if self._worker.is_alive():
                raise TimeoutError("ASR audio worker did not exit within the shutdown budget")

    def raise_if_error(self) -> None:
        """Raise the next asynchronous SDK error on the caller's thread."""
        try:
            failure = self._errors.get_nowait()
        except Empty:
            return
        if isinstance(failure, BaseException):
            raise failure
        raise RuntimeError("DashScope ASR returned an unspecified error")

    def cancel(self) -> None:
        """Discard unsent audio on user cancellation instead of billing a backlog."""
        deadline = time.monotonic() + CANCEL_TIMEOUT_SECONDS
        self._cancelled.set()
        self._running.clear()
        try:
            self._recognition.cancel(timeout=max(0.0, deadline - time.monotonic()))
        finally:
            self._join_worker(deadline)
        with self._input_lock:
            while True:
                try:
                    self._input_queue.get_nowait()
                except Empty:
                    break
            self._audio_buffer.clear()
        self.state = ASRState.STOPPED

    def feed_audio(self, data: bytes) -> None:
        self.raise_if_error()
        if not isinstance(data, bytes) or len(data) > MAX_INPUT_CHUNK_BYTES:
            raise ValueError("ASR audio input is outside the supported chunk budget")
        with self._input_lock:
            if not self._running.is_set() or self._cancelled.is_set():
                raise RuntimeError("ASR session is stopped")
            try:
                self._input_queue.put(data, timeout=0.5)
            except Full:
                raise RuntimeError("ASR audio queue is full; please check the connection") from None

    def _worker_loop(self) -> None:
        while True:
            if self._cancelled.is_set():
                self._audio_buffer.clear()
                break
            try:
                chunk = self._input_queue.get(timeout=0.1)
            except Empty:
                if not self._running.is_set():
                    break
                continue
            self._audio_buffer.extend(chunk)
            while len(self._audio_buffer) >= self.chunk_size:
                if self._cancelled.is_set():
                    self._audio_buffer.clear()
                    return
                send_data = bytes(self._audio_buffer[: self.chunk_size])
                self._audio_buffer = self._audio_buffer[self.chunk_size :]
                if not self._send_audio_frame(send_data):
                    return
        if self._audio_buffer and not self._cancelled.is_set():
            if not self._send_audio_frame(bytes(self._audio_buffer)):
                return
            self._audio_buffer.clear()

    def _send_audio_frame(self, data: bytes) -> bool:
        try:
            self._recognition.send_audio_frame(data)
        except Exception as error:
            self._audio_buffer.clear()
            if not self._cancelled.is_set():
                if isinstance(error, RecognitionProviderError):
                    error = RecognitionFailure(error.status_code, error.code)
                self._errors.put(error if isinstance(error, (ConnectionError, TimeoutError,
                                                             RecognitionFailure))
                                 else ASRTransportFailure())
            self._running.clear()
            self._cancelled.set()
            return False
        return True
