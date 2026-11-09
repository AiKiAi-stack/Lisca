"""A cancellable owner for the SDK's otherwise unbounded shutdown threads."""

import logging
import math
import multiprocessing
import os
from queue import Empty, Full, Queue
import sys
from threading import Event, Lock, RLock, Thread
import time
from types import MappingProxyType


MAX_PCM_FRAME_BYTES = 1024 * 1024
WORKER_MEMORY_LIMIT_BYTES = 512 * 1024 * 1024
_CAPACITY_EXITCODE = 73


class RecognitionCapacityError(ConnectionError):
    """A local audio or worker memory budget was exceeded."""


class RecognitionProviderError(RuntimeError):
    """Structured provider failure without its raw message or request data."""

    def __init__(self, status_code=None, code=None):
        self.status_code = status_code
        self.code = code
        super().__init__("DashScope recognition request failed")


def _check_memory_budget(memory_limit):
    if sys.platform.startswith("linux"):
        try:
            # ru_maxrss also includes the spawn child's pre-exec copy of its owner.
            with open("/proc/self/status", encoding="ascii") as status:
                for line in status:
                    if line.startswith("VmHWM:"):
                        if int(line.split()[1]) * 1024 > memory_limit:
                            os._exit(_CAPACITY_EXITCODE)
                        return
        except (OSError, ValueError):
            pass
        os._exit(1)


def _watch_owner(memory_limit):
    owner = multiprocessing.parent_process()
    while owner is not None and owner.is_alive():
        _check_memory_budget(memory_limit)
        time.sleep(0.1)
    os._exit(1)


def _recognize(configuration, endpoint, audio, events, memory_limit):
    _check_memory_budget(memory_limit)
    # This also runs while SDK imports, connects, or blocks inside stop().
    Thread(target=_watch_owner, args=(memory_limit,), daemon=True).start()
    logging.disable(logging.CRITICAL)
    sink = open(os.devnull, "w", encoding="utf-8")  # Child-only vendor output suppression.
    sys.stdout = sys.stderr = sink
    write_lock = Lock()

    def send(kind, value=None):
        with write_lock:
            events.send((kind, value))

    exit_code = 1
    try:
        import dashscope  # pylint: disable=import-outside-toplevel
        from dashscope.audio.asr import Recognition  # pylint: disable=import-outside-toplevel

        dashscope.base_websocket_api_url = endpoint

        class Callback:
            failed = Event()
            saw_error = False

            def on_open(self):
                send("open")

            def on_event(self, result):
                sentence = result.get_sentence()
                if isinstance(sentence, dict):
                    send("sentence", sentence)

            def on_error(self, result):
                status = getattr(result, "status_code", None)
                code = getattr(result, "code", None)
                send("error", (status if isinstance(status, int) else None,
                               code if isinstance(code, (str, int)) else None))
                self.saw_error = True

            def on_complete(self):
                send("complete")

            def on_close(self):
                send("close")
                if self.saw_error:
                    self.failed.set()

        callback = Callback()
        recognition = Recognition(callback=callback, **configuration)
        recognition.start()
        while True:
            if callback.failed.is_set():
                break
            if not audio.poll(0.1):
                continue
            frame = audio.recv()
            if frame is None:
                recognition.stop()
                send("stopped", (_delay(recognition.get_first_package_delay()),
                                 _delay(recognition.get_last_package_delay())))
                exit_code = 0
                break
            recognition.send_audio_frame(frame)
    except (Exception, SystemExit):  # No provider text or raw SDK objects cross IPC.
        try:
            send("failed")
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        audio.close()
        with write_lock:
            events.close()
    # Final acknowledgements are synchronous; vendor threads cannot hold exit open.
    os._exit(exit_code)


def _delay(value):
    return float(value) if isinstance(value, (float, int)) and math.isfinite(value) and value >= 0 else None


def _join_process(process, deadline):
    while process.exitcode is None and time.monotonic() < deadline:
        process.join(max(0, min(0.05, deadline - time.monotonic())))


class _Result:
    def __init__(self, sentence=None, status_code=None, code=None, transport_error=None):
        self._sentence = sentence
        self.status_code = status_code
        self.code = code
        self.transport_error = transport_error

    def get_sentence(self):
        return self._sentence


class RecognitionProcess:
    """One explicit recognition start, with bounded parent input and shutdown."""

    def __init__(self, model, format, sample_rate, callback, api_key,
                 **recognition_kwargs):
        import dashscope  # pylint: disable=import-outside-toplevel

        recognition_kwargs.setdefault("request_timeout", 5)
        self._configuration = dict(recognition_kwargs, model=model, format=format,
                                   sample_rate=sample_rate, api_key=api_key)
        self._endpoint = dashscope.base_websocket_api_url
        self._callback = callback
        self._lock = RLock()
        self._input_lock = Lock()
        self._shutdown_lock = Lock()
        self._cancelled = Event()
        self._worker_exited = Event()
        self._input = Queue(maxsize=32)
        self._process = None
        self._listener = None
        self._sender = None
        self._events = None
        self._audio = None
        self._pid = None
        self._exit_verified = False
        self._stopping = False
        self._mode = "not_started"
        self._server_complete = False
        self._sdk_stop_returned = False
        self._failed = False
        self._provider_failed = False
        self._provider_error = None
        self._error_kind = None
        self._first_delay = None
        self._last_delay = None
        self._memory_limit = WORKER_MEMORY_LIMIT_BYTES

    def start(self):
        threads_started = False
        with self._lock:
            if self._mode != "not_started" or self._stopping:
                raise ConnectionError("Recognition cannot be started again")
            context = multiprocessing.get_context("spawn")
            child_audio, self._audio = context.Pipe(duplex=False)
            self._events, child_events = context.Pipe(duplex=False)
            self._process = context.Process(target=_recognize,
                                            args=(self._configuration, self._endpoint, child_audio,
                                                  child_events, self._memory_limit),
                                            daemon=True)
            try:
                self._listener = Thread(target=self._listen, daemon=True)
                self._sender = Thread(target=self._send, daemon=True)
                self._process.start()
            except Exception:
                self._audio.close()
                self._events.close()
                self._process.close()
                self._process = None
                self._configuration = None
                self._endpoint = None
                self._mode = "failed"
                raise ConnectionError("Recognition worker could not start") from None
            finally:
                child_audio.close()
                child_events.close()
            self._pid = self._process.pid
            self._configuration = None
            self._endpoint = None
            self._mode = "running"
            self._listener.name = f"asr-events-{self._pid}"
            self._sender.name = f"asr-input-{self._pid}"
            try:
                self._listener.start()
                self._sender.start()
                threads_started = True
            except Exception:
                pass
        if not threads_started:
            self.stop(timeout=5, cancel=True)
            with self._lock:
                self._mode = "failed"
            raise ConnectionError("Recognition worker could not start") from None

    def _raise_if_capacity(self):
        if (self._error_kind == "capacity" or (self._process is not None
                                               and self._process.exitcode == _CAPACITY_EXITCODE)):
            self._error_kind = "capacity"
            self._failed = True
            if self._mode == "running":
                self._mode = "failed"
            raise RecognitionCapacityError("Recognition worker exceeded its memory limit")

    def _raise_if_failed(self):
        self._raise_if_capacity()
        if self._provider_error is not None:
            raise RecognitionProviderError(*self._provider_error) from None

    def send_audio_frame(self, data):
        deadline = time.monotonic() + 0.5
        if not self._input_lock.acquire(timeout=0.5):
            with self._lock:
                self._raise_if_failed()
            raise TimeoutError("Recognition audio queue is busy")
        try:
            with self._lock:
                self._raise_if_failed()
                if self._mode != "running" or self._stopping or self._failed:
                    raise ConnectionError("Recognition is stopped")
                if not self._process.is_alive():
                    self._raise_if_failed()
                    raise ConnectionError("Recognition is stopped")
            if not isinstance(data, bytes):
                raise TypeError("Audio frame must be bytes")
            if len(data) > MAX_PCM_FRAME_BYTES:
                raise RecognitionCapacityError("Recognition audio frame exceeds the byte limit")
            try:
                self._input.put(data, timeout=max(0, deadline - time.monotonic()))
            except Full:
                with self._lock:
                    self._raise_if_failed()
                raise TimeoutError("Recognition audio queue is full") from None
            if self._cancelled.is_set() or self._worker_exited.is_set():
                self._discard_input()
                with self._lock:
                    self._raise_if_failed()
                    raise ConnectionError("Recognition is stopped")
        finally:
            self._input_lock.release()

    def _send(self):
        try:
            while not self._cancelled.is_set() and not self._worker_exited.is_set():
                try:
                    frame = self._input.get(timeout=0.05)
                except Empty:
                    continue
                if self._cancelled.is_set():
                    break
                self._audio.send(frame)
                if frame is None:
                    break
        except (BrokenPipeError, EOFError, OSError):
            pass
        finally:
            self._audio.close()

    def _listen(self):
        try:
            while True:
                kind, value = self._events.recv()
                if self._cancelled.is_set():
                    continue
                with self._lock:
                    if kind == "complete":
                        self._server_complete = True
                    elif kind == "stopped":
                        self._sdk_stop_returned = True
                        self._first_delay, self._last_delay = value
                    elif kind in ("failed", "error"):
                        if kind == "error":
                            self._provider_error = value
                        self._failed = True
                        self._provider_failed = kind == "error" or self._provider_failed
                if kind == "sentence":
                    method, arguments = "on_event", (_Result(sentence=value),)
                elif kind == "error":
                    method, arguments = "on_error", (_Result(status_code=value[0], code=value[1]),)
                elif kind in ("open", "complete", "close"):
                    method, arguments = "on_" + kind, ()
                else:
                    continue
                handler = getattr(self._callback, method, None)
                if handler is not None and not self._cancelled.is_set():
                    try:
                        handler(*arguments)
                    except Exception:  # Keep draining IPC even if a consumer callback fails.
                        self._failed = True
        except (EOFError, OSError):
            pass
        finally:
            self._events.close()
            self._worker_exited.set()
            self._discard_input()
            self._report_exit_failure()

    def _report_exit_failure(self):
        failure = None
        with self._lock:
            process = self._process
            if process is None or self._cancelled.is_set():
                return
        _join_process(process, time.monotonic() + 0.2)
        with self._lock:
            if self._cancelled.is_set():
                return
            exitcode = process.exitcode
            if exitcode == _CAPACITY_EXITCODE:
                self._error_kind = "capacity"
                failure = RecognitionCapacityError("Recognition worker exceeded its memory limit")
            elif exitcode is not None and exitcode != 0 and not self._provider_failed:
                failure = ConnectionError("Recognition worker exited before completion")
            if exitcode is not None and exitcode != 0:
                self._failed = True
                self._mode = "failed"
        handler = getattr(self._callback, "on_error", None)
        if failure is not None and handler is not None and not self._cancelled.is_set():
            try:
                handler(_Result(transport_error=failure))
            except Exception:
                self._failed = True

    def stop(self, timeout=15.0, cancel=False):
        deadline = time.monotonic() + max(0, timeout)
        with self._lock:
            if self._process is None and self._mode != "not_started":
                return
            first_stop = not self._stopping
            self._stopping = True
            if self._process is None:
                self._configuration = None
                self._endpoint = None
                self._mode = "cancelled" if cancel else "not_started"
                return
            if cancel:
                self._cancelled.set()
                self._mode = "cancelled"
                self._discard_input()
                if self._process.is_alive():
                    self._process.kill()
        if not self._shutdown_lock.acquire(timeout=max(0, deadline - time.monotonic())):
            raise TimeoutError("Recognition shutdown is still in progress")
        try:
            if self._process is None:
                return
            process = self._process
            timed_out = False
            if not process.is_alive():
                self._discard_input()
            if first_stop and not self._cancelled.is_set() and process.is_alive():
                if not self._input_lock.acquire(timeout=max(0, deadline - time.monotonic() - 0.3)):
                    timed_out = True
                else:
                    try:
                        if not self._cancelled.is_set():
                            self._input.put(None, timeout=max(0, deadline - time.monotonic() - 0.3))
                    except Full:
                        timed_out = True
                    finally:
                        self._input_lock.release()
            if not timed_out:
                # The listener drains all callbacks before the process handle is reaped.
                if self._listener.ident is not None:
                    self._listener.join(max(0, deadline - time.monotonic() - 0.3))
                _join_process(process, deadline - 0.3)
                timed_out = process.is_alive() and not self._cancelled.is_set()
            if process.is_alive():
                self._cancelled.set()
                self._discard_input()
                process.kill()
                _join_process(process, deadline)
            for thread in (self._sender, self._listener):
                if thread.ident is not None:
                    thread.join(max(0, deadline - time.monotonic()))
            self._discard_input()
            with self._lock:
                self._exit_verified = process.exitcode is not None and not process.is_alive()
                if (not self._exit_verified or self._sender.is_alive()
                        or self._listener.is_alive()):
                    self._mode = "forced"
                    raise TimeoutError("Recognition worker did not exit")
                successful = (process.exitcode == 0 and self._server_complete
                              and self._sdk_stop_returned and not self._failed)
                if process.exitcode == _CAPACITY_EXITCODE:
                    self._error_kind = "capacity"
                self._audio.close()
                self._events.close()
                process.close()
                self._process = None
                if self._mode == "cancelled":
                    return
                if timed_out and self._error_kind != "capacity":
                    self._mode = "forced"
                    raise TimeoutError("Recognition completion timed out")
                if not successful:
                    self._mode = "failed"
                    self._raise_if_failed()
                    raise ConnectionError("Recognition worker failed before completion")
                self._mode = "completed"
        finally:
            self._shutdown_lock.release()

    def _discard_input(self):
        while True:
            try:
                self._input.get_nowait()
            except Empty:
                return

    def cancel(self, timeout=5.0):
        self.stop(timeout=timeout, cancel=True)

    def get_shutdown_evidence(self):
        with self._lock:
            alive = self._process is not None and self._process.is_alive()
            exited = self._exit_verified or (self._process is not None
                                             and self._process.exitcode is not None and not alive)
            return MappingProxyType({
                "schema_version": 1,
                "observation_scope": "isolated_sdk_adapter",
                "worker_pid": self._pid,
                "worker_alive": alive,
                "worker_exit_verified": exited,
                "listener_alive": self._listener is not None and self._listener.is_alive(),
                "sender_alive": self._sender is not None and self._sender.is_alive(),
                "shutdown_mode": self._mode,
                "server_complete_observed": self._server_complete,
                "sdk_stop_returned": self._sdk_stop_returned,
                "memory_limit_enforced": sys.platform.startswith("linux"),
                "memory_limit_bytes": self._memory_limit,
                "error_kind": self._error_kind,
            })

    def get_first_package_delay(self):
        return self._first_delay

    def get_last_package_delay(self):
        return self._last_delay
