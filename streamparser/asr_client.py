from collections import deque
from enum import Enum, auto
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Optional

from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult

from streamparser.asr_config import ASRSettings
from streamparser.subtitle import SubtitleEntry


class ASRState(Enum):
    IDLE = auto()
    RUNNING = auto()
    STOPPED = auto()


class _Callback(RecognitionCallback):
    def __init__(self, subtitle_buffer: deque, sentence_count: list, errors: Queue) -> None:
        super().__init__()
        self._subtitle_buffer = subtitle_buffer
        self._sentence_count = sentence_count
        self._errors = errors

    def on_open(self) -> None:
        pass

    def on_complete(self) -> None:
        pass

    def on_close(self) -> None:
        pass

    def on_error(self, result: RecognitionResult) -> None:
        message = ""
        get_message = getattr(result, "get_message", None)
        if callable(get_message):
            try:
                message = str(get_message() or "")
            except Exception:  # noqa: BLE001 - SDK callback must not throw
                message = ""
        if not message:
            try:
                message = str(result)
            except Exception:  # SDK response stringification can itself fail
                message = "DashScope recognition failed"
        self._errors.put(message or "DashScope ASR returned an unspecified error")

    def on_event(self, result: RecognitionResult) -> None:
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
        self.state = ASRState.IDLE
        self.subtitle_buffer: deque = deque()
        self._sentence_count = [0]

        self._errors: Queue = Queue()
        callback = _Callback(self.subtitle_buffer, self._sentence_count, self._errors)
        self._recognition = Recognition(
            model=settings.model or "fun-asr-realtime",
            format="pcm",
            sample_rate=16000,
            callback=callback,
            api_key=settings.api_key,
            **recognition_kwargs,
        )
        self._input_queue: Queue = Queue(maxsize=100)
        self._audio_buffer = bytearray()
        self._running = Event()
        self._cancelled = Event()
        self._worker: Optional[Thread] = None

    def start(self) -> None:
        self._cancelled.clear()
        self.state = ASRState.RUNNING
        self._running.set()
        self._recognition.start()
        self._worker = Thread(target=self._worker_loop, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self.state = ASRState.STOPPED
        self._running.clear()
        if self._worker is not None:
            self._worker.join(timeout=5)
            if self._worker.is_alive():
                raise RuntimeError("ASR audio worker did not stop within 5 seconds")
        self._recognition.stop()

    def raise_if_error(self) -> None:
        """Raise the next asynchronous SDK error on the caller's thread."""
        try:
            message = self._errors.get_nowait()
        except Empty:
            return
        raise RuntimeError(f"DashScope ASR error: {message}")

    def cancel(self) -> None:
        """Discard unsent audio on user cancellation instead of billing a backlog."""
        self._cancelled.set()
        self.stop()

    def feed_audio(self, data: bytes) -> None:
        self.raise_if_error()
        if not self._running.is_set():
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
                send_data = bytes(self._audio_buffer[: self.chunk_size])
                self._audio_buffer = self._audio_buffer[self.chunk_size :]
                try:
                    self._recognition.send_audio_frame(send_data)
                except Exception:
                    self._errors.put("ASR audio connection failed")
                    self._running.clear()
                    self._cancelled.set()
                    return
        if self._audio_buffer and not self._cancelled.is_set():
            self._recognition.send_audio_frame(bytes(self._audio_buffer))
            self._audio_buffer.clear()
