"""Actual DashScope SDK against a loopback-only service, through the facade."""

import asyncio
import base64
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import queue
import signal
import socket
import struct
import sys
import threading
import time
from urllib.parse import urlsplit

import dashscope
import pytest

import lisca.asr.recognition_process as process_module
from lisca.asr.recognition_process import RecognitionProcess


class LocalService:
    """The small WebSocket protocol surface used by the installed SDK."""

    def __init__(self, mode):
        self.mode = mode
        self.connected = threading.Event()
        self.closed = threading.Event()
        self.started = threading.Event()
        self.finished = threading.Event()
        self.workspace = None
        self.messages = queue.Queue()
        self.audio = []
        self.requests = 0
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        self._thread.start()
        assert self._ready.wait(3), "local service did not start"
        return self

    def __exit__(self, *_args):
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(3)
        assert not self._thread.is_alive(), "local service did not stop"

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        server = self._loop.run_until_complete(
            asyncio.start_server(self._handle, "127.0.0.1", 0))
        self.url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/inference"
        self._ready.set()
        try:
            self._loop.run_forever()
        finally:
            server.close()
            self._loop.run_until_complete(server.wait_closed())
            tasks = asyncio.all_tasks(self._loop)
            for task in tasks:
                task.cancel()
            self._loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            self._loop.close()

    async def _handle(self, reader, writer):
        self.requests += 1
        self.connected.set()
        try:
            request = (await reader.readuntil(b"\r\n\r\n")).decode("ascii")
            if self.mode == "handshake":
                await reader.read()
                return
            headers = dict(line.split(": ", 1) for line in request.split("\r\n")[1:]
                           if ": " in line)
            self.workspace = next((value for name, value in headers.items()
                                   if name.lower() == "x-dashscope-workspace"), None)
            key = next(value for name, value in headers.items()
                       if name.lower() == "sec-websocket-key")
            accept = base64.b64encode(hashlib.sha1(
                (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest())
            writer.write(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                         b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + accept + b"\r\n\r\n")
            await writer.drain()
            task_message = None
            while True:
                opcode, data = await self._receive(reader)
                if opcode == 8:
                    return
                if opcode == 2:
                    self.audio.append(data)
                    if self.mode == "normal" and len(self.audio) == 1:
                        assert task_message is not None, "PCM arrived before run-task"
                        await self._send(writer, task_message, "result-generated", {
                            "sentence": {"text": "原文草稿", "begin_time": 0,
                                         "end_time": None}})
                    continue
                if opcode != 1:
                    continue
                message = json.loads(data)
                self.messages.put(message)
                action = message["header"]["action"]
                if action == "run-task":
                    task_message = message
                    self.started.set()
                    if self.mode == "error":
                        await self._send(writer, message, "task-failed", error=True)
                    elif self.mode != "no_task_start":
                        await self._send(writer, message, "task-started")
                elif action == "finish-task":
                    self.finished.set()
                    if self.mode == "normal":
                        await self._send(writer, message, "result-generated", {
                            "sentence": {"text": "原文尾部", "begin_time": 0,
                                         "end_time": 150, "words": [
                                             {"text": "尾部", "begin_time": 100,
                                              "end_time": 150}]}})
                        await self._send(writer, message, "task-finished", {"finished": True})
                    elif self.mode == "no_complete":
                        await self._send(writer, message, "task-finished")
                    elif self.mode == "error_at_finish":
                        await self._send(writer, message, "task-failed", error=True)
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            finally:
                self.closed.set()

    @staticmethod
    async def _receive(reader):
        first, second = await reader.readexactly(2)
        length = second & 127
        if length == 126:
            length = struct.unpack("!H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await reader.readexactly(8))[0]
        mask = await reader.readexactly(4) if second & 128 else None
        data = await reader.readexactly(length)
        if mask:
            data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        return first & 15, data

    @staticmethod
    async def _send(writer, message, event, output=None, error=False):
        response = {"header": {"event": event,
                               "task_id": message["header"]["task_id"]},
                    "payload": {"output": output} if output else {}}
        if error:
            response["header"].update(error_code="Model.AccessDenied",
                                      error_message="private-provider-dummy-local-test-key")
        data = json.dumps(response).encode()
        writer.write(bytes([129, len(data)]) + data if len(data) < 126 else
                     bytes([129, 126]) + struct.pack("!H", len(data)) + data)
        await writer.drain()


class Callback:
    def __init__(self):
        self.events = []
        self.transport_errors = []
        self.error = threading.Event()

    def on_open(self):
        self.events.append(("open", None))

    def on_event(self, result):
        self.events.append(("sentence", result.get_sentence()))

    def on_error(self, result):
        self.events.append(("error", (result.status_code, result.code)))
        failure = getattr(result, "transport_error", None)
        if failure is not None:
            self.transport_errors.append(failure)
        self.error.set()

    def on_complete(self):
        self.events.append(("complete", None))

    def on_close(self):
        self.events.append(("close", None))


@pytest.mark.skipif(os.name != "posix", reason="uses POSIX TCP reset linger")
def test_loopback_connection_reset_is_recorded_as_closed():
    with LocalService("handshake") as service:
        endpoint = urlsplit(service.url)
        connection = socket.create_connection((endpoint.hostname, endpoint.port), timeout=2)
        try:
            connection.sendall(b"GET /inference HTTP/1.1\r\nHost: localhost\r\n\r\n")
            assert service.connected.wait(2)
            connection.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        finally:
            connection.close()
        assert service.closed.wait(2), "reset socket was closed without recording the witness"


@pytest.fixture
def recognizer(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    recognizers = []

    def create(service, **kwargs):
        monkeypatch.setattr(dashscope, "base_websocket_api_url", service.url)
        callback = Callback()
        recognizer = RecognitionProcess(model="fun-asr-realtime", format="pcm",
                                        sample_rate=16000, callback=callback,
                                        api_key="dummy-local-test-key", **kwargs)
        recognizers.append(recognizer)
        return recognizer, callback

    yield create
    for recognizer in recognizers:
        recognizer.cancel()


def assert_exited(recognizer, mode):
    evidence = recognizer.get_shutdown_evidence()
    assert evidence["worker_exit_verified"] is True
    assert evidence["worker_alive"] is False
    assert evidence["listener_alive"] is False
    assert evidence["sender_alive"] is False
    assert evidence["shutdown_mode"] == mode
    with pytest.raises(ProcessLookupError):
        os.kill(evidence["worker_pid"], 0)


def test_cancel_closes_unanswered_http_handshake(recognizer):
    with LocalService("handshake") as service:
        recognition, callback = recognizer(service)
        started = time.monotonic()
        recognition.start()
        assert time.monotonic() - started < 1
        try:
            assert service.connected.wait(8), "actual SDK did not connect"
            started = time.monotonic()
            recognition.cancel(timeout=3)
            assert time.monotonic() - started < 3
            assert service.closed.wait(2), "cancel did not close the actual connection"
            assert_exited(recognition, "cancelled")
            assert ("complete", None) not in callback.events
            assert service.requests == 1
        finally:
            recognition.cancel()


def test_eof_preserves_pcm_order_tail_result_and_real_complete(recognizer):
    with LocalService("normal") as service:
        recognition, callback = recognizer(service, workspace="local-workspace",
                                           language_hints=["zh"],
                                           disfluency_removal_enabled=True,
                                           endpoint="business-parameter")
        assert recognition.get_first_package_delay() is None
        recognition.start()
        frames = [b"\x01" * 3200, b"\x02" * 1600]
        for frame in frames:
            recognition.send_audio_frame(frame)
        recognition.stop(timeout=6)

        assert service.audio == frames
        assert [kind for kind, _value in callback.events] == [
            "open", "sentence", "sentence", "complete", "close"]
        assert callback.events[2][1] == {
            "text": "原文尾部", "begin_time": 0, "end_time": 150,
            "words": [{"text": "尾部", "begin_time": 100, "end_time": 150}]}
        request = service.messages.get_nowait()
        assert request["payload"]["model"] == "fun-asr-realtime"
        assert request["payload"]["parameters"]["language_hints"] == ["zh"]
        assert request["payload"]["parameters"]["disfluency_removal_enabled"] is True
        assert request["payload"]["parameters"]["endpoint"] == "business-parameter"
        assert service.workspace == "local-workspace"
        assert_exited(recognition, "completed")
        evidence = recognition.get_shutdown_evidence()
        assert evidence["server_complete_observed"] is True
        assert evidence["sdk_stop_returned"] is True
        assert recognition.get_first_package_delay() >= 0
        assert recognition.get_last_package_delay() >= 0
        assert service.requests == 1


@pytest.mark.parametrize("mode, ready", [("no_task_start", "started"),
                                        ("no_finish", "started")])
def test_cancel_discards_backlog_and_closes_stalled_websocket(recognizer, mode, ready):
    with LocalService(mode) as service:
        recognition, callback = recognizer(service)
        recognition.start()
        assert getattr(service, ready).wait(8)
        for _ in range(16):
            recognition.send_audio_frame(b"\x01" * 3200)
        started = time.monotonic()
        recognition.cancel(timeout=3)
        assert time.monotonic() - started < 3
        assert service.closed.wait(2)
        assert_exited(recognition, "cancelled")
        assert ("complete", None) not in callback.events
        if mode == "no_task_start":
            assert service.audio == []
            assert not service.finished.is_set()
        with pytest.raises(ConnectionError):
            recognition.send_audio_frame(b"\x00" * 3200)
        assert service.requests == 1


def test_eof_deadline_kills_worker_without_faking_complete(recognizer):
    with LocalService("no_finish") as service:
        recognition, callback = recognizer(service)
        recognition.start()
        assert service.started.wait(8)
        recognition.send_audio_frame(b"\x01" * 3200)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            recognition.stop(timeout=1)
        assert time.monotonic() - started < 1.3
        assert service.finished.is_set()
        assert service.closed.wait(2)
        assert_exited(recognition, "forced")
        assert ("complete", None) not in callback.events
        assert recognition.get_shutdown_evidence()["server_complete_observed"] is False
        assert recognition.get_shutdown_evidence()["sdk_stop_returned"] is False


def test_cancel_interrupts_graceful_stop_while_evidence_remains_readable(recognizer):
    with LocalService("no_finish") as service:
        recognition, _callback = recognizer(service)
        recognition.start()
        recognition.send_audio_frame(b"\x01" * 3200)
        failures = []

        def finish():
            try:
                recognition.stop(timeout=6)
            except Exception as failure:
                failures.append(failure)

        finishing = threading.Thread(target=finish, daemon=True)
        finishing.start()
        try:
            assert service.finished.wait(8)
            observed = time.monotonic()
            evidence = recognition.get_shutdown_evidence()
            assert time.monotonic() - observed < 0.2
            assert evidence["worker_alive"] is True
            assert evidence["server_complete_observed"] is False
            started = time.monotonic()
            recognition.cancel(timeout=2)
            assert time.monotonic() - started < 2
            finishing.join(1)
            assert not finishing.is_alive()
            assert failures == []
            assert_exited(recognition, "cancelled")
        finally:
            recognition.cancel()
            finishing.join(1)


def test_cancel_before_ready_and_start_are_single_use(recognizer):
    with LocalService("handshake") as service:
        recognition, callback = recognizer(service)
        recognition.start()
        with pytest.raises(ConnectionError):
            recognition.start()
        recognition.cancel(timeout=3)
        assert_exited(recognition, "cancelled")
        with pytest.raises(ConnectionError):
            recognition.start()
        assert ("complete", None) not in callback.events
        assert service.requests <= 1


@pytest.mark.skipif(os.name != "posix", reason="uses the owned child's POSIX stop signal")
def test_parent_audio_buffer_is_bounded_when_worker_cannot_consume(recognizer):
    with LocalService("no_task_start") as service:
        recognition, _callback = recognizer(service)
        recognition.start()
        assert service.started.wait(8)
        os.kill(recognition.get_shutdown_evidence()["worker_pid"], signal.SIGSTOP)
        try:
            for _ in range(64):
                started = time.monotonic()
                try:
                    recognition.send_audio_frame(b"\x01" * 65536)
                except TimeoutError:
                    assert time.monotonic() - started < 0.7
                    break
            else:
                pytest.fail("stopped consumer accepted an unbounded audio backlog")
            recognition.cancel(timeout=3)
            assert_exited(recognition, "cancelled")
            assert service.audio == []
        finally:
            recognition.cancel()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux peak-RSS guard")
def test_worker_memory_limit_is_a_local_failure_before_sdk_connects(recognizer, monkeypatch):
    monkeypatch.setattr(process_module, "WORKER_MEMORY_LIMIT_BYTES", 1)
    with LocalService("normal") as service:
        recognition, callback = recognizer(service)
        recognition.start()
        assert callback.error.wait(5), "worker memory exit did not reach the error callback"
        assert len(callback.transport_errors) == 1
        assert isinstance(callback.transport_errors[0], process_module.RecognitionCapacityError)
        with pytest.raises(process_module.RecognitionCapacityError):
            recognition.stop(timeout=3)
        assert_exited(recognition, "failed")
        assert service.requests == 0
        evidence = recognition.get_shutdown_evidence()
        assert evidence["memory_limit_enforced"] is True
        assert evidence["memory_limit_bytes"] == 1
        assert evidence["error_kind"] == "capacity"
        assert evidence["server_complete_observed"] is False
        assert callback.events == [("error", (None, None))]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux peak-RSS guard")
def test_worker_memory_guard_bounds_sdk_backlog_after_connection(recognizer, monkeypatch):
    monkeypatch.setattr(process_module, "WORKER_MEMORY_LIMIT_BYTES", 96 * 1024 * 1024)
    with LocalService("no_task_start") as service:
        recognition, callback = recognizer(service)
        recognition.start()
        assert service.started.wait(8)
        frame = b"\x01" * (1024 * 1024)
        for _ in range(160):
            try:
                recognition.send_audio_frame(frame)
            except (ConnectionError, TimeoutError):
                break
        assert callback.error.wait(5), "SDK backlog exceeded the local memory budget without exiting"
        assert isinstance(callback.transport_errors[0], process_module.RecognitionCapacityError)
        with pytest.raises(process_module.RecognitionCapacityError):
            recognition.stop(timeout=3)
        assert_exited(recognition, "failed")
        assert service.closed.wait(2)
        assert service.audio == []
        assert ("complete", None) not in callback.events


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux address-space peak-RSS guard")
def test_memory_budget_ignores_the_spawn_owners_large_rss(recognizer, monkeypatch):
    monkeypatch.setattr(process_module, "WORKER_MEMORY_LIMIT_BYTES", 96 * 1024 * 1024)
    owner_allocation = bytearray(128 * 1024 * 1024)
    with LocalService("normal") as service:
        recognition, _callback = recognizer(service)
        recognition.start()
        recognition.send_audio_frame(b"\x01" * 3200)
        recognition.stop(timeout=6)
        assert_exited(recognition, "completed")
        assert len(owner_allocation) == 128 * 1024 * 1024


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux capacity exit status")
def test_capacity_exit_stays_typed_before_listener_can_report_it(recognizer, monkeypatch):
    monkeypatch.setattr(process_module, "WORKER_MEMORY_LIMIT_BYTES", 96 * 1024 * 1024)
    entered, release = threading.Event(), threading.Event()
    with LocalService("no_task_start") as service:
        recognition, callback = recognizer(service)

        def delayed_open():
            entered.set()
            release.wait(8)

        callback.on_open = delayed_open
        recognition.start()
        try:
            assert entered.wait(5)
            frame = b"\x01" * (1024 * 1024)
            capacity_seen = False
            for _ in range(160):
                try:
                    recognition.send_audio_frame(frame)
                except process_module.RecognitionCapacityError:
                    capacity_seen = True
                    break
            if not capacity_seen:
                deadline = time.monotonic() + 2
                while (recognition.get_shutdown_evidence()["worker_alive"]
                       and time.monotonic() < deadline):
                    release.wait(0.01)
                with pytest.raises(process_module.RecognitionCapacityError):
                    recognition.send_audio_frame(frame)
            assert not callback.error.is_set()
        finally:
            release.set()
        with pytest.raises(process_module.RecognitionCapacityError):
            recognition.stop(timeout=3)
        assert_exited(recognition, "failed")


def test_sdk_stop_return_cannot_replace_server_complete(recognizer):
    with LocalService("no_complete") as service:
        recognition, callback = recognizer(service)
        recognition.start()
        recognition.send_audio_frame(b"\x01" * 3200)
        with pytest.raises(ConnectionError):
            recognition.stop(timeout=6)
        assert_exited(recognition, "failed")
        assert recognition.get_shutdown_evidence()["sdk_stop_returned"] is True
        assert recognition.get_shutdown_evidence()["server_complete_observed"] is False
        assert ("complete", None) not in callback.events


def test_single_pcm_packet_cannot_bypass_buffer_byte_limit(recognizer):
    with LocalService("normal") as service:
        recognition, _callback = recognizer(service)
        recognition.start()
        with pytest.raises(process_module.RecognitionCapacityError):
            recognition.send_audio_frame(b"\x00" * (1024 * 1024 + 1))
        recognition.cancel()
        assert_exited(recognition, "cancelled")


def test_unserializable_options_fail_safely_without_starting_a_request(recognizer):
    class LocalOption:
        def __reduce__(self):
            raise RuntimeError("private-provider-dummy-local-test-key")

    with LocalService("normal") as service:
        recognition, _callback = recognizer(service, phrase_id=LocalOption())
        with pytest.raises(ConnectionError) as caught:
            recognition.start()
        assert str(caught.value) == "Recognition worker could not start"
        evidence = recognition.get_shutdown_evidence()
        assert evidence["shutdown_mode"] == "failed"
        assert evidence["worker_pid"] is None
        assert evidence["worker_exit_verified"] is False
        assert evidence["listener_alive"] is False
        assert evidence["sender_alive"] is False
        assert service.requests == 0


@pytest.mark.parametrize("failed_thread", ["asr-events-", "asr-input-"])
def test_thread_start_failure_reaps_the_partially_started_worker(recognizer, monkeypatch,
                                                                 failed_thread):
    original_start = threading.Thread.start

    def fail_platform_thread(thread):
        if thread.name.startswith(failed_thread):
            raise RuntimeError("private-provider-dummy-local-test-key")
        return original_start(thread)

    with LocalService("handshake") as service:
        recognition, _callback = recognizer(service)
        monkeypatch.setattr(threading.Thread, "start", fail_platform_thread)
        with pytest.raises(ConnectionError) as caught:
            recognition.start()
        assert str(caught.value) == "Recognition worker could not start"
        assert_exited(recognition, "failed")
        assert service.requests <= 1


@pytest.mark.parametrize("mode", ["error", "error_at_finish"])
def test_provider_error_preserves_type_without_message_or_sensitive_evidence(recognizer, capfd, mode):
    with LocalService(mode) as service:
        recognition, callback = recognizer(service)
        recognition.start()
        if mode == "error":
            assert callback.error.wait(5)
        with pytest.raises(process_module.RecognitionProviderError) as caught:
            recognition.stop(timeout=3)
        assert caught.value.status_code == 44
        assert caught.value.code == "Model.AccessDenied"
        assert ("error", (44, "Model.AccessDenied")) in callback.events
        assert callback.transport_errors == []
        assert ("complete", None) not in callback.events
        assert_exited(recognition, "failed")
        evidence = recognition.get_shutdown_evidence()
        with pytest.raises(TypeError):
            evidence["worker_alive"] = True
        captured = capfd.readouterr()
        safe_output = json.dumps(dict(evidence)) + str(caught.value) + captured.out + captured.err
        for private in ("private-provider", "dummy-local-test-key", service.url,
                        "Model.AccessDenied", "api_key", "transcript", "request_id"):
            assert private not in safe_output
        assert evidence["observation_scope"] == "isolated_sdk_adapter"
        assert evidence["schema_version"] == 1
        assert evidence["server_complete_observed"] is False


def test_provider_error_stays_typed_while_its_callback_is_blocked(recognizer):
    entered, release = threading.Event(), threading.Event()
    with LocalService("error") as service:
        recognition, callback = recognizer(service)
        original_error = callback.on_error

        def delayed_error(result):
            entered.set()
            release.wait(8)
            original_error(result)

        callback.on_error = delayed_error
        recognition.start()
        try:
            assert entered.wait(5), "actual SDK provider error did not reach the listener"
            assert not callback.error.is_set()
            with pytest.raises(process_module.RecognitionProviderError) as caught:
                recognition.send_audio_frame(b"\x01" * 3200)
            assert caught.value.status_code == 44
            assert caught.value.code == "Model.AccessDenied"
            assert str(caught.value) == "DashScope recognition request failed"
            assert not isinstance(caught.value, ConnectionError)
        finally:
            release.set()
        with pytest.raises(process_module.RecognitionProviderError) as stopped:
            recognition.stop(timeout=3)
        assert stopped.value.status_code == 44
        assert stopped.value.code == "Model.AccessDenied"
        assert callback.error.is_set()
        assert callback.transport_errors == []
        assert_exited(recognition, "failed")


def test_explicit_new_instance_cannot_receive_old_callbacks(recognizer):
    with LocalService("normal") as service:
        first, first_callback = recognizer(service)
        first.start()
        first.send_audio_frame(b"\x01" * 3200)
        first.stop(timeout=6)
        first_events = list(first_callback.events)
        second, second_callback = recognizer(service)
        second.start()
        second.send_audio_frame(b"\x02" * 3200)
        second.stop(timeout=6)
        assert first_callback.events == first_events
        assert ("complete", None) in second_callback.events
        assert service.audio == [b"\x01" * 3200, b"\x02" * 3200]
        assert service.requests == 2
        assert first.get_shutdown_evidence()["worker_pid"] != second.get_shutdown_evidence()["worker_pid"]
        assert_exited(first, "completed")
        assert_exited(second, "completed")


def _blocked_owner(endpoint, report):
    dashscope.base_websocket_api_url = endpoint
    recognition = RecognitionProcess(model="fun-asr-realtime", format="pcm", sample_rate=16000,
                                     callback=Callback(), api_key="dummy-local-test-key")
    recognition.start()
    report.send(recognition.get_shutdown_evidence()["worker_pid"])
    report.close()
    recognition.stop(timeout=60)


def _pid_has_exited(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    if sys.platform.startswith("linux"):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(")", 1)[1].split()[0] == "Z"
        except (FileNotFoundError, ProcessLookupError):
            return True
    return False


@pytest.mark.skipif(os.name != "posix", reason="kills the test owner to exercise its watchdog")
def test_worker_exits_when_owner_is_killed_inside_sdk_stop(recognizer):
    with LocalService("no_finish") as service:
        context = multiprocessing.get_context("spawn")
        report, child_report = context.Pipe(duplex=False)
        owner = context.Process(target=_blocked_owner, args=(service.url, child_report))
        worker_pid = None
        owner.start()
        child_report.close()
        try:
            assert report.poll(8), "owner did not start a recognition worker"
            worker_pid = report.recv()
            assert service.finished.wait(8), "SDK did not reach its blocked stop"
            started = time.monotonic()
            owner.kill()
            owner.join(3)
            assert not owner.is_alive()
            assert service.closed.wait(3), "orphan SDK worker kept its socket alive"
            deadline = time.monotonic() + 1
            while not _pid_has_exited(worker_pid) and time.monotonic() < deadline:
                threading.Event().wait(0.01)
            assert _pid_has_exited(worker_pid)
            assert time.monotonic() - started < 4
            assert service.requests == 1
        finally:
            if owner.is_alive():
                owner.kill()
            owner.join(3)
            owner.close()
            report.close()
            if worker_pid is not None and not _pid_has_exited(worker_pid):
                os.kill(worker_pid, signal.SIGKILL)
