import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import wave
import threading
import time

import pytest

import long_acceptance_probe as probe
import run_long_acceptance_webui as runner
import run_real_browser_acceptance as browser_collector
from tools.stream_source import StreamSource


SECRET_URL = "https://stream.example/live/index.m3u8?token=private-capability"
PCM = b"\x01\x00" * 3 + b"\xfe\xff" * 2


class FakeStreamSource:
    def __init__(self, url_or_path):
        self.url_or_path = url_or_path
        self.first_pts_sec = 123.25
        self.timeline_offset_sec = 4.5
        self.closed = False

    def iter_chunks(self):
        yield b"\x01\x00" * 3
        yield b"\xfe\xff" * 2

    def close(self):
        self.closed = True


def write_manifest(path, input_value=SECRET_URL, source_content_sha256=None):
    identity = hashlib.sha256(input_value.encode("utf-8")).hexdigest()
    case = {
        "case_id": "zh-live-01", "generation": 1,
        "input_identity_kind": "url" if input_value.startswith(("http://", "https://")) else "path",
        "input_identity_sha256": identity,
    }
    if source_content_sha256 is not None:
        case["source_content_sha256"] = source_content_sha256
    path.write_text(json.dumps({
        "schema_version": 1,
        "cases": [case],
    }), encoding="utf-8")
    return identity


def read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_public_stream_source_yields_exact_wav_and_chunk_timeline(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    identity = write_manifest(manifest)
    output = tmp_path / "capture-output"
    stop_file = tmp_path / "collector-stop.json"
    webui = SimpleNamespace(StreamSource=FakeStreamSource)

    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024, invalid_stop_file=stop_file
    )
    source = webui.StreamSource(SECRET_URL)
    chunks = list(source.iter_chunks())
    source.close()
    installation.restore()

    assert chunks == [b"\x01\x00" * 3, b"\xfe\xff" * 2]
    wav_path = output / "pcm" / "zh-live-01-g1.wav"
    events_path = output / "pcm" / "zh-live-01-g1.jsonl"
    with wave.open(str(wav_path), "rb") as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate()) == (1, 2, 16000)
        assert reader.getnframes() == 5
        assert reader.readframes(5) == PCM
    rows = read_events(events_path)
    assert [row["event"] for row in rows] == ["pcm_chunk", "pcm_chunk", "capture_summary"]
    assert [(row["sample_start"], row["sample_end"]) for row in rows[:2]] == [(0, 3), (3, 5)]
    assert all(row["input_identity_sha256"] == identity for row in rows)
    assert all(row["source_first_pts_sec"] == 123.25 for row in rows[:2])
    assert all(row["timeline_offset_sec"] == 4.5 for row in rows[:2])
    assert rows[0]["observed_monotonic_ns"] <= rows[1]["observed_monotonic_ns"]
    summary = rows[-1]
    assert summary["status"] == "completed"
    assert summary["valid"] is True and summary["complete"] is True
    assert summary["source_samples_observed"] == summary["source_samples_captured"] == 5
    assert summary["pcm_sha256"] == hashlib.sha256(PCM).hexdigest()
    assert summary["wav_sha256"] == hashlib.sha256(wav_path.read_bytes()).hexdigest()
    assert summary["first_ingress_monotonic_ns"] <= summary["last_ingress_monotonic_ns"]
    receipt = json.loads((output / "run-closed.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "valid" and receipt["valid"] is True
    assert receipt["capture_count"] == receipt["expected_capture_count"] == 1
    assert receipt["captures"][0]["case_id"] == "zh-live-01"
    all_output = "".join(p.read_text(encoding="utf-8", errors="ignore") for p in output.rglob("*.*"))
    assert SECRET_URL not in all_output
    assert "private-capability" not in all_output


def test_real_local_wav_capture_matches_public_stream_source_pcm(tmp_path):
    source_path = tmp_path / "known-local.wav"
    expected = (b"\x01\x00" * 1600) + (b"\xff\x7f" * 1600)
    with wave.open(str(source_path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(expected)
    manifest = tmp_path / "local-wav-cases.json"
    content_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    identity = write_manifest(manifest, str(source_path), content_sha)
    output = tmp_path / "local-wav-capture"
    webui = SimpleNamespace(StreamSource=StreamSource)

    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024 * 1024,
        invalid_stop_file=tmp_path / "local-wav-stop.json"
    )
    source = webui.StreamSource(str(source_path))
    try:
        yielded = b"".join(source.iter_chunks())
    finally:
        source.close()
        installation.restore()

    captured = output / "pcm" / "zh-live-01-g1.wav"
    with wave.open(str(captured), "rb") as reader:
        pcm = reader.readframes(reader.getnframes())
    rows = read_events(output / "pcm" / "zh-live-01-g1.jsonl")
    assert yielded == expected
    assert pcm == yielded
    assert rows[-1]["complete"] is True
    assert rows[-1]["source_samples_observed"] == len(expected) // 2
    assert rows[-1]["pcm_sha256"] == hashlib.sha256(expected).hexdigest()
    assert all(row["input_identity_sha256"] == identity for row in rows)
    assert all(row["source_content_sha256"] == content_sha for row in rows)
    assert identity != content_sha
    assert str(source_path) not in "".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in output.rglob("*.*")
    )


def test_manifest_rejects_non_object_json_root(tmp_path):
    manifest = tmp_path / "invalid-root.json"
    manifest.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="manifest must have"):
        probe.load_case_manifest(manifest)


def test_breaking_source_iteration_is_cancelled_not_normal_eof(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "cancel-output"
    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=tmp_path / "cancel-stop.json"
    )
    source = webui.StreamSource(SECRET_URL)
    iterator = iter(source.iter_chunks())
    assert next(iterator) == b"\x01\x00" * 3
    iterator.close()
    source.close()
    installation.restore()

    summary = read_events(output / "pcm" / "zh-live-01-g1.jsonl")[-1]
    assert summary["source_stream_status"] == "cancelled"
    assert summary["complete"] is False
    assert summary["valid"] is True
    assert summary["source_samples_observed"] == summary["source_samples_captured"] == 3


def test_close_source_before_engine_stop_then_iterator_close_allows_next_generation(tmp_path):
    manifest = tmp_path / "sequential-cases.json"
    identity = write_manifest(manifest)
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [
        {"case_id": "zh-live-01", "generation": generation,
         "input_identity_kind": "url", "input_identity_sha256": identity}
        for generation in (1, 2)
    ]}), encoding="utf-8")
    output = tmp_path / "sequential-output"

    class FakeRecognition:
        def start(self):
            return None

        def stop(self):
            return None

    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=tmp_path / "sequential-stop.json",
        sdk_recognition_class=FakeRecognition,
    )

    first_source = webui.StreamSource(SECRET_URL)
    first_iterator = iter(first_source.iter_chunks())
    first_engine = FakeRecognition()
    first_engine.start()
    assert next(first_iterator) == b"\x01\x00" * 3
    first_source.close()
    first_engine.stop()
    first_iterator.close()

    second_source = webui.StreamSource(SECRET_URL)
    second_engine = FakeRecognition()
    second_engine.start()
    assert list(second_source.iter_chunks()) == [b"\x01\x00" * 3, b"\xfe\xff" * 2]
    second_engine.stop()
    second_source.close()
    installation.restore()

    first = read_events(output / "pcm" / "zh-live-01-g1.jsonl")[-1]
    second = read_events(output / "pcm" / "zh-live-01-g2.jsonl")[-1]
    assert first["source_stream_status"] == "cancelled" and first["valid"] is True
    assert first["sdk"]["stop_successes"] == 1
    assert second["source_stream_status"] == "completed" and second["complete"] is True
    assert second["sdk"]["stop_successes"] == 1
    assert not (tmp_path / "sequential-stop.json").exists()


def test_restore_waits_for_delayed_sdk_stop_before_summary(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "delayed-stop-output"

    class FakeRecognition:
        def start(self):
            return None

        def stop(self):
            return None

    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=tmp_path / "delayed-stop-signal.json",
        sdk_recognition_class=FakeRecognition, shutdown_timeout=0.5,
    )
    source = webui.StreamSource(SECRET_URL)
    recognition = FakeRecognition()
    recognition.start()
    assert len(list(source.iter_chunks())) == 2
    source.close()
    stopper = threading.Thread(target=lambda: (time.sleep(0.05), recognition.stop()))
    stopper.start()
    installation.restore()
    stopper.join(timeout=1)

    summary = read_events(output / "pcm" / "zh-live-01-g1.jsonl")[-1]
    assert summary["sdk"]["stop_calls"] == summary["sdk"]["stop_successes"] == 1
    assert summary["sdk_shutdown_observed"] is True
    assert summary["valid"] is True


def test_restore_does_not_validate_capture_while_writer_is_closing(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "writer-close-race-output"
    close_entered, release_close, close_done = (
        threading.Event(), threading.Event(), threading.Event()
    )

    class BlockingWriter:
        def __init__(self, path):
            self.writer = wave.open(str(path), "wb")
            self.writer.setparams((1, 2, 16000, 0, "NONE", "not compressed"))

        def write(self, data):
            self.writer.writeframesraw(data)

        def close(self):
            close_entered.set()
            release_close.wait(2)
            self.writer.close()
            close_done.set()

    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=tmp_path / "writer-close-race-stop.json",
        writer_factory=BlockingWriter, writer_join_timeout=1.0,
        shutdown_timeout=0.05,
    )
    source = webui.StreamSource(SECRET_URL)
    assert len(list(source.iter_chunks())) == 2
    closer = threading.Thread(target=source.close)
    closer.start()
    assert close_entered.wait(1)

    installation.restore()

    receipt = json.loads((output / "run-closed.json").read_text(encoding="utf-8"))
    assert close_done.is_set() is False
    assert receipt["valid"] is False
    assert receipt["captures"][0]["writer_terminated"] is False
    assert "writer_shutdown_timeout" in receipt["invalid_reasons"]
    release_close.set()
    closer.join(timeout=2)
    assert close_done.is_set() is True


def test_restore_timeout_marks_unobserved_sdk_stop_invalid(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "missing-stop-output"

    class FakeRecognition:
        def start(self):
            return None

        def stop(self):
            return None

    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=tmp_path / "missing-stop-signal.json",
        sdk_recognition_class=FakeRecognition, shutdown_timeout=0.02,
    )
    source = webui.StreamSource(SECRET_URL)
    recognition = FakeRecognition()
    recognition.start()
    assert len(list(source.iter_chunks())) == 2
    source.close()
    installation.restore()

    rows = read_events(output / "pcm" / "zh-live-01-g1.jsonl")
    summary = rows[-1]
    assert summary["status"] == "invalid"
    assert summary["sdk_shutdown_observed"] is False
    assert summary["sdk"]["stop_calls"] == 0
    assert "sdk_stop_not_observed" in summary["invalid_reasons"]
    assert json.loads((tmp_path / "missing-stop-signal.json").read_text())[
        "reason"] == "sdk_stop_not_observed"


def test_pcm_cap_fails_closed_but_does_not_change_yielded_audio(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "capped-output"
    stop_file = tmp_path / "cap-stop.json"
    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=6, invalid_stop_file=stop_file
    )
    source = webui.StreamSource(SECRET_URL)
    chunks = list(source.iter_chunks())
    source.close()
    installation.restore()

    assert chunks == [b"\x01\x00" * 3, b"\xfe\xff" * 2]
    rows = read_events(output / "pcm" / "zh-live-01-g1.jsonl")
    summary = rows[-1]
    assert summary["status"] == "invalid"
    assert summary["complete"] is False
    assert summary["invalid_reasons"] == ["pcm_cap_exceeded"]
    assert summary["source_samples_observed"] == 5
    assert summary["source_samples_captured"] == 3
    assert json.loads(stop_file.read_text(encoding="utf-8"))["reason"] == "pcm_cap_exceeded"
    assert SECRET_URL not in stop_file.read_text(encoding="utf-8")


def test_writer_queue_overflow_stops_and_marks_capture_invalid(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "overflow-output"
    stop_file = tmp_path / "overflow-stop.json"
    entered, release = threading.Event(), threading.Event()

    class ThreeChunkSource(FakeStreamSource):
        def iter_chunks(self):
            yield b"\x01\x00"
            yield b"\x02\x00"
            yield b"\x03\x00"

    class BlockedWriter:
        def __init__(self, path):
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            self.stream = os.fdopen(fd, "wb")

        def write(self, data):
            entered.set()
            assert release.wait(3)
            self.stream.write(data)

        def close(self):
            self.stream.close()

    webui = SimpleNamespace(StreamSource=ThreeChunkSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024, invalid_stop_file=stop_file,
        queue_size=1, writer_factory=BlockedWriter
    )
    source = webui.StreamSource(SECRET_URL)
    iterator = iter(source.iter_chunks())
    assert next(iterator) == b"\x01\x00"
    assert entered.wait(1)
    assert next(iterator) == b"\x02\x00"
    assert next(iterator) == b"\x03\x00"
    assert json.loads(stop_file.read_text(encoding="utf-8"))["reason"] == "writer_queue_overflow"
    release.set()
    assert list(iterator) == []
    source.close()
    installation.restore()

    summary = read_events(output / "pcm" / "zh-live-01-g1.jsonl")[-1]
    assert summary["status"] == "invalid"
    assert "writer_queue_overflow" in summary["invalid_reasons"]
    assert summary["dropped_samples"] >= 1
    assert summary["complete"] is False


def test_wav_write_failure_signals_stop_without_raising_through_source(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "writer-failure-output"
    stop_file = tmp_path / "writer-failure-stop.json"

    class FailingWriter:
        def __init__(self, path):
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            self.stream = os.fdopen(fd, "wb")

        def write(self, _data):
            raise OSError("simulated disk failure")

        def close(self):
            self.stream.close()

    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024, invalid_stop_file=stop_file,
        writer_factory=FailingWriter
    )
    source = webui.StreamSource(SECRET_URL)
    chunks = list(source.iter_chunks())
    source.close()
    installation.restore()

    assert chunks == [b"\x01\x00" * 3, b"\xfe\xff" * 2]
    signal = json.loads(stop_file.read_text(encoding="utf-8"))
    assert signal["reason"] == "wav_write_failure"
    rows = read_events(output / "pcm" / "zh-live-01-g1.jsonl")
    assert rows[-1]["status"] == "invalid"
    assert "wav_write_failure" in rows[-1]["invalid_reasons"]
    assert SECRET_URL not in stop_file.read_text(encoding="utf-8")


def test_stop_signal_write_failure_keeps_capture_invalid(tmp_path, monkeypatch):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "stop-write-failure-output"
    stop_file = tmp_path / "stop-write-failure.json"
    real_write = probe._exclusive_json

    def fail_stop(path, row):
        if Path(path) == stop_file:
            raise OSError("simulated stop-file failure")
        return real_write(path, row)

    monkeypatch.setattr(probe, "_exclusive_json", fail_stop)
    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=6,
        invalid_stop_file=stop_file
    )
    source = webui.StreamSource(SECRET_URL)
    chunks = list(source.iter_chunks())
    source.close()
    installation.restore()

    assert chunks == [b"\x01\x00" * 3, b"\xfe\xff" * 2]
    rows = read_events(output / "pcm" / "zh-live-01-g1.jsonl")
    assert rows[-1]["status"] == "invalid"
    assert set(rows[-1]["invalid_reasons"]) == {
        "pcm_cap_exceeded", "stop_signal_write_failure"
    }
    assert not stop_file.exists()


def test_invalid_marker_uses_public_collector_stop_file_and_control_loop(
        tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    stop_file = tmp_path / "paired-collector-stop.json"
    args = browser_collector.parse_args([
        "--media", SECRET_URL, "--label", "probe-stop-contract",
        "--output", str(tmp_path / "paired-collector-output"),
        "--stop-file", str(stop_file),
    ])
    assert args.stop_file == stop_file
    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, tmp_path / "paired-probe-output", max_pcm_bytes=6,
        invalid_stop_file=stop_file,
    )
    source = webui.StreamSource(SECRET_URL)
    assert len(list(source.iter_chunks())) == 2
    source.close()
    installation.restore()
    assert json.loads(stop_file.read_text(encoding="utf-8"))["reason"] == "pcm_cap_exceeded"

    stopped = []

    class FakeRequest:
        method = "DELETE"

    class FakeResponse:
        request = FakeRequest()
        url = "http://127.0.0.1:8002/api/cloud/sessions/test-id"
        status = 200

    class FakePage:
        def __init__(self):
            self.handlers = {}

        def set_default_timeout(self, _timeout):
            return None

        def route(self, _pattern, _callback):
            return None

        def on(self, event, callback):
            self.handlers[event] = callback

        def goto(self, *_args, **_kwargs):
            return None

        def locator(self, selector):
            return FakeLocator(self, selector)

        def evaluate(self, *_args, **_kwargs):
            return []

        def wait_for_timeout(self, _timeout):
            return None

    class FakeLocator:
        def __init__(self, page, selector):
            self.page, self.selector = page, selector

        def fill(self, _value):
            return None

        def click(self):
            return None

        def is_visible(self):
            return self.selector == "#stop"

    class FakeBrowser:
        def __init__(self):
            self.page = FakePage()

        def new_page(self, **_kwargs):
            return self.page

        def close(self):
            return None

    class FakePlaywrightContext:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    fake_browser = FakeBrowser()

    def stop_if_active(_page):
        stopped.append("collector-observed-invalid-marker")
        fake_browser.page.handlers["response"](FakeResponse())

    monkeypatch.setattr(browser_collector, "sync_playwright", FakePlaywrightContext)
    monkeypatch.setattr(browser_collector, "launch_browser", lambda _playwright: fake_browser)
    monkeypatch.setattr(browser_collector, "stop_if_active", stop_if_active)
    browser_collector.collect(args)
    assert stopped == ["collector-observed-invalid-marker"]
    with pytest.raises(SystemExit) as help_exit:
        runner._arguments(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "same path" in help_text
    assert "not monitor the marker or stop recognition by itself" in runner.__doc__


def test_public_sdk_counters_are_case_scoped_and_wrappers_restore(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    identity = write_manifest(manifest)
    output = tmp_path / "sdk-output"

    class FakeRecognition:
        def start(self):
            return "started"

        def send_audio_frame(self, data):
            return len(data)

        def stop(self):
            return "stopped"

        def complete(self):
            return "completed"

    original = {name: getattr(FakeRecognition, name) for name in
                ("start", "send_audio_frame", "stop", "complete")}
    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=tmp_path / "sdk-stop.json",
        sdk_recognition_class=FakeRecognition
    )
    source = webui.StreamSource(SECRET_URL)
    recognition = FakeRecognition()
    assert recognition.start() == "started"
    chunks = list(source.iter_chunks())
    assert recognition.send_audio_frame(chunks[0]) == len(chunks[0])
    assert recognition.send_audio_frame(chunks[1]) == len(chunks[1])
    assert recognition.complete() == "completed"
    source.close()
    assert recognition.stop() == "stopped"
    installation.restore()

    assert all(getattr(FakeRecognition, name) is method for name, method in original.items())
    summary = read_events(output / "pcm" / "zh-live-01-g1.jsonl")[-1]
    sdk = summary["sdk"]
    assert summary["case_id"] == "zh-live-01" and summary["generation"] == 1
    assert summary["input_identity_sha256"] == identity
    assert sdk["start_calls"] == sdk["start_successes"] == 1
    assert sdk["send_audio_frame_calls"] == sdk["send_audio_frame_successes"] == 2
    assert sdk["send_audio_frame_bytes"] == len(PCM)
    assert sdk["stop_calls"] == sdk["stop_successes"] == 1
    assert sdk["complete_calls"] == sdk["complete_successes"] == 1
    assert 0 < sdk["first_send_monotonic_ns"] <= sdk["last_send_monotonic_ns"]
    assert SECRET_URL not in (output / "pcm" / "zh-live-01-g1.jsonl").read_text(encoding="utf-8")


@pytest.mark.parametrize("late_method", ["start", "send_audio_frame"])
def test_late_sdk_activity_after_eof_marks_run_invalid_until_restore(tmp_path, late_method):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "late-sdk-output"
    stop_file = tmp_path / "late-sdk-stop.json"

    class FakeRecognition:
        def start(self):
            return "started"

        def send_audio_frame(self, data):
            return len(data)

        def stop(self):
            return "stopped"

        def complete(self):
            return "completed"

    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=stop_file, sdk_recognition_class=FakeRecognition,
    )
    source = webui.StreamSource(SECRET_URL)
    recognition = FakeRecognition()
    recognition.start()
    assert len(list(source.iter_chunks())) == 2
    source.close()
    assert recognition.stop() == "stopped"

    assert not stop_file.exists()
    assert recognition.complete() == "completed"
    assert not stop_file.exists()
    if late_method == "start":
        assert recognition.start() == "started"
    else:
        assert recognition.send_audio_frame(b"late") == len(b"late")
    assert json.loads(stop_file.read_text(encoding="utf-8"))["reason"] == (
        "sdk_activity_after_capture_finalize"
    )
    installation.restore()

    summary = read_events(output / "pcm" / "zh-live-01-g1.jsonl")[-1]
    assert summary["valid"] is True  # The paired stop marker invalidates the overall run.
    assert "late" not in stop_file.read_text(encoding="utf-8")


def test_late_sdk_marker_write_failure_is_preserved_in_run_receipt(
        tmp_path, monkeypatch):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "late-marker-failure-output"
    stop_file = tmp_path / "late-marker-failure-stop.json"

    class FakeRecognition:
        def start(self):
            return None

        def send_audio_frame(self, data):
            return len(data)

        def stop(self):
            return None

    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=stop_file, sdk_recognition_class=FakeRecognition,
    )
    source = webui.StreamSource(SECRET_URL)
    recognition = FakeRecognition()
    recognition.start()
    assert len(list(source.iter_chunks())) == 2
    source.close()
    recognition.stop()
    assert not stop_file.exists()

    original_exclusive_json = probe._exclusive_json

    def fail_stop_marker(path, row):
        if Path(path) == stop_file:
            raise OSError("simulated marker write failure")
        return original_exclusive_json(path, row)

    monkeypatch.setattr(probe, "_exclusive_json", fail_stop_marker)
    assert recognition.send_audio_frame(b"late") == len(b"late")
    installation.restore()

    receipt = json.loads((output / "run-closed.json").read_text(encoding="utf-8"))
    assert receipt["valid"] is False
    assert receipt["stop_signal_write_failed"] is True
    assert receipt["capture_count"] == receipt["expected_capture_count"] == 1
    assert "sdk_activity_after_capture_finalize" in receipt["invalid_reasons"]
    assert "stop_signal_write_failure" in receipt["invalid_reasons"]
    assert receipt["captures"][0]["valid"] is False
    assert SECRET_URL not in json.dumps(receipt)


def test_launcher_returns_nonzero_when_run_receipt_cannot_be_written(
        tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "receipt-write-failure-output"
    stop_file = tmp_path / "receipt-write-failure-stop.json"
    original_exclusive_json = probe._exclusive_json

    def fail_run_receipt(path, row):
        if Path(path).name == "run-closed.json":
            raise OSError("simulated receipt write failure")
        return original_exclusive_json(path, row)

    monkeypatch.setattr(probe, "_exclusive_json", fail_run_receipt)
    calls = []
    webui = SimpleNamespace(
        StreamSource=FakeStreamSource,
        main=lambda: calls.append(sys.argv[1:]),
    )
    prior_argv = sys.argv
    result = runner.main(
        ["--manifest", str(manifest), "--output", str(output),
         "--max-pcm-bytes", "1024", "--invalid-stop-file", str(stop_file)],
        webui_module=webui,
    )

    assert result != 0
    assert calls == [["--host", "127.0.0.1", "--port", "8002"]]
    assert sys.argv is prior_argv
    error = capsys.readouterr().err
    assert "run-closed receipt" in error
    assert str(tmp_path) not in error


def test_public_sdk_completion_callback_is_forwarded_and_counted(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "callback-output"

    class FakeCallbackBase:
        def on_open(self):
            pass

        def on_close(self):
            pass

        def on_error(self, _result):
            pass

        def on_event(self, _result):
            pass

        def on_complete(self):
            pass

    class FakeCallback(FakeCallbackBase):
        def __init__(self):
            self.completed = 0

        def on_complete(self):
            self.completed += 1

    class FakeRecognition:
        def __init__(self, *, callback):
            self.callback = callback

        def start(self):
            return None

        def send_audio_frame(self, _data):
            return None

        def stop(self):
            self.callback.on_complete()

    original_recognition_init = FakeRecognition.__init__
    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=tmp_path / "callback-stop.json",
        sdk_recognition_class=FakeRecognition,
        sdk_callback_class=FakeCallbackBase,
    )
    source = webui.StreamSource(SECRET_URL)
    original_callback = FakeCallback()
    recognition = FakeRecognition(callback=original_callback)
    recognition.start()
    chunks = list(source.iter_chunks())
    for chunk in chunks:
        recognition.send_audio_frame(chunk)
    source.close()
    recognition.stop()
    installation.restore()

    assert original_callback.completed == 1
    assert FakeRecognition.__init__ is original_recognition_init
    sdk = read_events(output / "pcm" / "zh-live-01-g1.jsonl")[-1]["sdk"]
    assert sdk["complete_method_available"] is False
    assert sdk["complete_callbacks"] == 1
    assert sdk["start_successes"] == sdk["stop_successes"] == 1
    assert sdk["send_audio_frame_successes"] == 2


def test_source_identity_mismatch_is_not_captured_or_leaked(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "mismatch-output"
    stop_file = tmp_path / "mismatch-stop.json"
    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024, invalid_stop_file=stop_file
    )
    other = "https://stream.example/not-allowed.m3u8?token=another-secret"
    source = webui.StreamSource(other)
    chunks = list(source.iter_chunks())
    source.close()
    installation.restore()

    assert chunks == [b"\x01\x00" * 3, b"\xfe\xff" * 2]
    assert not list((output / "pcm").glob("*.wav"))
    signal = json.loads(stop_file.read_text(encoding="utf-8"))
    assert signal["reason"] == "source_not_allowed"
    assert other not in stop_file.read_text(encoding="utf-8")
    assert "another-secret" not in stop_file.read_text(encoding="utf-8")

def test_each_manifest_generation_gets_an_exclusive_capture_pair(tmp_path):
    manifest = tmp_path / "generations.json"
    identity = hashlib.sha256(SECRET_URL.encode()).hexdigest()
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [
        {"case_id": "zh-live-01", "generation": 1,
         "input_identity_kind": "url", "input_identity_sha256": identity},
        {"case_id": "zh-live-01", "generation": 2,
         "input_identity_kind": "url", "input_identity_sha256": identity},
    ]}), encoding="utf-8")
    output = tmp_path / "generation-output"
    webui = SimpleNamespace(StreamSource=FakeStreamSource)
    installation = probe.install_probe(
        webui, manifest, output, max_pcm_bytes=1024,
        invalid_stop_file=tmp_path / "generation-stop.json"
    )
    for generation in (1, 2):
        source = webui.StreamSource(SECRET_URL)
        assert len(list(source.iter_chunks())) == 2
        source.close()
    installation.restore()

    for generation in (1, 2):
        stem = f"zh-live-01-g{generation}"
        rows = read_events(output / "pcm" / f"{stem}.jsonl")
        assert rows[-1]["generation"] == generation
        assert (output / "pcm" / f"{stem}.wav").is_file()


def test_opt_in_cli_launches_regular_webui_and_restores_observers(tmp_path):
    manifest = tmp_path / "allowed-cases.json"
    write_manifest(manifest)
    output = tmp_path / "cli-output"
    stop_file = tmp_path / "cli-stop.json"
    calls = []

    class FakeRecognition:
        def start(self):
            calls.append("provider-start")

        def send_audio_frame(self, _data):
            calls.append("provider-send")

        def stop(self):
            calls.append("provider-stop")

    original_source_iter = FakeStreamSource.iter_chunks
    original_sdk_start = FakeRecognition.start
    webui = SimpleNamespace(StreamSource=FakeStreamSource,
                            main=lambda: calls.append(("regular-webui-main", sys.argv[1:])))
    result = runner.main([
        "--manifest", str(manifest), "--output", str(output),
        "--max-pcm-bytes", "8192", "--invalid-stop-file", str(stop_file),
    ], webui_module=webui, sdk_recognition_class=FakeRecognition)

    assert result == 0
    assert calls == [("regular-webui-main", ["--host", "127.0.0.1", "--port", "8002"])]
    assert FakeStreamSource.iter_chunks is original_source_iter
    assert FakeRecognition.start is original_sdk_start
    safe_manifest = (output / "capture-manifest.json").read_text(encoding="utf-8")
    assert SECRET_URL not in safe_manifest and "private-capability" not in safe_manifest
    assert '"input_identity_kind": "url"' in safe_manifest
    receipt = json.loads((output / "run-closed.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "not_assessed" and receipt["valid"] is False
    assert receipt["capture_count"] == 0 and receipt["expected_capture_count"] == 1


def test_hash_only_manifest_rejects_raw_source_identifiers(tmp_path):
    manifest = tmp_path / "raw-input-forbidden.json"
    identity = hashlib.sha256(SECRET_URL.encode()).hexdigest()
    manifest.write_text(json.dumps({"schema_version": 1, "cases": [{
        "case_id": "zh-live-01", "generation": 1, "input_identity_kind": "url",
        "input_identity_sha256": identity, "input_url": SECRET_URL,
    }]}), encoding="utf-8")

    with pytest.raises(ValueError, match="raw URLs or paths"):
        probe.load_case_manifest(manifest)
