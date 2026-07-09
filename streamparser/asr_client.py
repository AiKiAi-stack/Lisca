from collections import deque
from enum import Enum, auto
from queue import Empty, Queue
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
    def __init__(self, subtitle_buffer: deque, sentence_count: list) -> None:
        super().__init__()
        self._subtitle_buffer = subtitle_buffer
        self._sentence_count = sentence_count

    def on_open(self) -> None:
        pass

    def on_complete(self) -> None:
        pass

    def on_close(self) -> None:
        pass

    def on_error(self, result: RecognitionResult) -> None:
        pass

    def on_event(self, result: RecognitionResult) -> None:
        sentence = result.get_sentence()
        if not isinstance(sentence, dict):
            return
        self._sentence_count[0] += 1
        entry = SubtitleEntry(
            index=self._sentence_count[0],
            text=sentence.get("text", ""),
            start_time=sentence.get("begin_time", 0) / 1000.0,
            end_time=sentence.get("end_time", 0) / 1000.0,
            is_final=RecognitionResult.is_sentence_end(sentence),
        )
        self._subtitle_buffer.append(entry)


class ASRClient:
    def __init__(self, settings: ASRSettings, chunk_size: int = 3200) -> None:
        self.settings = settings
        self.chunk_size = chunk_size
        self.state = ASRState.IDLE
        self.subtitle_buffer: deque = deque()
        self._sentence_count = [0]

        callback = _Callback(self.subtitle_buffer, self._sentence_count)
        self._recognition = Recognition(
            model=settings.model or "fun-asr-realtime",
            format="pcm",
            sample_rate=16000,
            callback=callback,
        )
        self._input_queue: Queue = Queue()
        self._audio_buffer = bytearray()
        self._running = Event()
        self._worker: Optional[Thread] = None

    def start(self) -> None:
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
        self._recognition.stop()

    def feed_audio(self, data: bytes) -> None:
        self._input_queue.put(data)

    def _worker_loop(self) -> None:
        while self._running.is_set():
            try:
                chunk = self._input_queue.get(timeout=0.1)
            except Empty:
                continue
            self._audio_buffer.extend(chunk)
            while len(self._audio_buffer) >= self.chunk_size:
                send_data = bytes(self._audio_buffer[: self.chunk_size])
                self._audio_buffer = self._audio_buffer[self.chunk_size :]
                self._recognition.send_audio_frame(send_data)
