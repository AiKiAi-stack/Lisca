"""Keep release accuracy scoring independent of provider output formatting."""
import asyncio
import json
import hashlib
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import wave

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, Response
import pytest
import uvicorn

from score_original_acceptance import edit_distance, score, tokens


class FakeRecognitionService:
    def __init__(self, events, hold_events=False):
        self.connected = threading.Event()
        self.release_events = threading.Event()
        self.media_path = None
        if not hold_events:
            self.release_events.set()
        self.app = FastAPI()

        @self.app.post("/api/cloud/sessions")
        async def create_session():
            return {"session": "test-session"}

        @self.app.delete("/api/cloud/sessions/test-session")
        async def close_session():
            return Response(status_code=204)

        @self.app.get("/sample.wav")
        async def serve_media():
            if self.media_path is None:
                return Response(status_code=404)
            return FileResponse(self.media_path, media_type="audio/wav")

        @self.app.websocket("/ws")
        async def send_events(websocket: WebSocket):
            await websocket.accept()
            self.connected.set()
            await asyncio.to_thread(self.release_events.wait, 10)
            for event in events:
                await websocket.send_json(event)

        self.socket = socket.socket()
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(("127.0.0.1", 0))
        self.socket.listen(128)
        self.socket.setblocking(False)
        self.port = self.socket.getsockname()[1]
        self.server = uvicorn.Server(uvicorn.Config(self.app, log_level="error", lifespan="off"))
        self.thread = threading.Thread(target=self.server.run,
                                       kwargs={"sockets": [self.socket]}, daemon=True)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def __enter__(self):
        self.thread.start()
        deadline = time.monotonic() + 5
        while not self.server.started and time.monotonic() < deadline:
            time.sleep(0.01)
        assert self.server.started
        return self

    def __exit__(self, *_):
        self.release_events.set()
        self.server.should_exit = True
        self.thread.join(timeout=5)


def write_wav(path, sample=b"\0\0"):
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(sample * 800)


def run_score_cli(tmp_path, manifest, recording, language="en"):
    manifest_path = tmp_path / "manifest.json"
    recording_path = tmp_path / "recording.json"
    report_path = tmp_path / "report.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    recording_path.write_text(json.dumps(recording), encoding="utf-8")
    subprocess.run([
        sys.executable, str(Path(__file__).with_name("score_original_acceptance.py")),
        str(manifest_path), str(recording_path), "--language", language, "--output", str(report_path),
    ], check=True, capture_output=True, text=True)
    return json.loads(report_path.read_text(encoding="utf-8"))


def test_recording_and_scoring_clis_bind_a_local_input_to_the_report(tmp_path):
    input_path = tmp_path / "sample.wav"
    write_wav(input_path)
    input_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    events = [
        {"type": "subtitle", "sentence_id": 1, "beg": 0,
         "is_final": True, "text": "Keep these words"},
        {"type": "end"},
    ]
    recording_path = tmp_path / "recording.json"
    with FakeRecognitionService(events) as service:
        subprocess.run([
            sys.executable, str(Path(__file__).with_name("run_original_acceptance.py")),
            str(input_path), "--server-url", service.url, "--output", str(recording_path),
        ], check=True, capture_output=True, text=True)
    manifest = {"dataset": "fixed", "config": "en", "sha256": input_hash, "seconds": 1,
                "items": [{"reference": "Keep these words"}]}

    report = run_score_cli(tmp_path, manifest, json.loads(recording_path.read_text(encoding="utf-8")))

    assert report["input_sha256"] == input_hash
    assert report["reference_sha256"] == input_hash
    assert report["input_matches_reference"] is True
    assert report["source_verified"] is True
    assert report["transcription_accuracy_passed"] is True
    assert report["sync_gate_passed"] is None
    assert report["release_gate_passed"] is False


def test_scoring_cli_reports_input_and_reference_hashes_separately(tmp_path):
    reference_hash = "a" * 64
    input_hash = "b" * 64
    manifest = {"dataset": "fixed", "config": "en", "sha256": reference_hash, "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    recording = {"source_kind": "local_file", "input_sha256": input_hash,
                 "input_sha256_after": input_hash, "input_unchanged": True,
                 "recording_status": "completed", "completion_signal": "end",
                 "events": [{"type": "subtitle", "sentence_id": 1, "beg": 0,
                             "is_final": True, "text": "Keep these words"}, {"type": "end"}],
                 "errors": []}

    report = run_score_cli(tmp_path, manifest, recording)

    assert report["reference_sha256"] == reference_hash
    assert report["input_sha256"] == input_hash
    assert report["input_matches_reference"] is False
    assert report["source_verified"] is False
    assert report["transcription_accuracy_passed"] is True
    assert report["sync_gate_passed"] is None
    assert report["release_gate_passed"] is False


def test_scoring_cli_names_a_reference_hash_mismatch_without_erasing_accuracy(tmp_path):
    input_hash = "b" * 64
    recording = {"source_kind": "local_file", "input_sha256": input_hash,
                 "input_sha256_after": input_hash, "input_unchanged": True,
                 "recording_status": "completed", "completion_signal": "end",
                 "events": [{"type": "subtitle", "sentence_id": 1, "beg": 0,
                             "is_final": True, "text": "Keep these words"}, {"type": "end"}],
                 "errors": []}
    manifest = {"dataset": "fixed", "config": "en", "sha256": "a" * 64, "seconds": 1,
                "items": [{"reference": "Keep these words"}]}

    report = run_score_cli(tmp_path, manifest, recording)

    assert report["input_matches_reference"] is False
    assert report["source_status"] == "hash_mismatch"
    assert report["source_verified"] is False
    assert report["transcription_accuracy_passed"] is True
    assert report["accuracy_gate_passed"] is False


def test_scoring_cli_keeps_legacy_recordings_unverified(tmp_path):
    manifest = {"dataset": "fixed", "config": "en", "sha256": "a" * 64, "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    legacy_recording = {"events": [
        {"type": "subtitle", "sentence_id": 1, "beg": 0,
         "is_final": True, "text": "Keep these words"},
        {"type": "end"},
    ], "errors": []}

    report = run_score_cli(tmp_path, manifest, legacy_recording)

    assert report["input_sha256"] is None
    assert report["input_matches_reference"] is None
    assert report["source_status"] == "legacy_missing_identity"
    assert report["source_verified"] is False
    assert report["transcription_accuracy_passed"] is True
    assert report["accuracy_gate_passed"] is False


def test_recording_cli_detects_local_input_replacement_before_scoring(tmp_path):
    input_path = tmp_path / "sample.wav"
    write_wav(input_path)
    original_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    recording_path = tmp_path / "recording.json"
    events = [
        {"type": "subtitle", "sentence_id": 1, "beg": 0,
         "is_final": True, "text": "Keep these words"},
        {"type": "end"},
    ]
    with FakeRecognitionService(events, hold_events=True) as service:
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).with_name("run_original_acceptance.py")),
            str(input_path), "--server-url", service.url, "--output", str(recording_path),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not service.connected.wait(5):
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"recorder did not reach the fake WebSocket service: {stdout} {stderr}")
        replacement = tmp_path / "replacement.wav"
        write_wav(replacement, sample=b"\x01\0")
        os.replace(replacement, input_path)
        service.release_events.set()
        stdout, stderr = process.communicate(timeout=10)

    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    assert process.returncode != 0, stdout + stderr
    assert recording["recording_status"] == "completed"
    assert recording["input_sha256"] == original_hash
    assert recording["input_sha256_after"] != original_hash
    assert recording["input_unchanged"] is False
    manifest = {"dataset": "fixed", "config": "en", "sha256": original_hash, "seconds": 1,
                "items": [{"reference": "Keep these words"}]}

    report = run_score_cli(tmp_path, manifest, recording)

    assert report["source_status"] == "input_changed"
    assert report["source_verified"] is False
    assert report["transcription_accuracy_passed"] is True
    assert report["accuracy_gate_passed"] is False


def test_recording_and_scoring_clis_preserve_remote_sample_identity_without_hashing_url(tmp_path):
    media_path = tmp_path / "remote.wav"
    write_wav(media_path)
    recording_path = tmp_path / "recording.json"
    events = [
        {"type": "subtitle", "sentence_id": 1, "beg": 0,
         "is_final": True, "text": "Keep these words"},
        {"type": "end"},
    ]
    with FakeRecognitionService(events) as service:
        service.media_path = media_path
        subprocess.run([
            sys.executable, str(Path(__file__).with_name("run_original_acceptance.py")),
            service.url + "/sample.wav?token=private", "--server-url", service.url,
            "--output", str(recording_path),
        ], check=True, capture_output=True, text=True)
    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    manifest = {"dataset": "live-sample", "config": "en", "sha256": "a" * 64, "seconds": 1,
                "items": [{"reference": "Keep these words"}]}

    report = run_score_cli(tmp_path, manifest, recording)

    assert recording["source_kind"] == "remote_stream"
    assert recording["input_sha256"] is None
    assert recording["source_url"].endswith("/sample.wav?[redacted]")
    assert "private" not in json.dumps(recording)
    assert recording["sample_interval"]["started_at_utc"]
    assert recording["sample_interval"]["ended_at_utc"]
    assert report["source_status"] == "not_verified"
    assert report["source_observation_status"] == "sampled_remote"
    assert report["input_matches_reference"] is None
    assert report["source_verified"] is False
    assert report["accuracy_gate_passed"] is False


def test_recording_and_scoring_clis_mark_user_cancellation_as_incomplete(tmp_path):
    input_path = tmp_path / "sample.wav"
    write_wav(input_path)
    recording_path = tmp_path / "recording.json"
    with FakeRecognitionService([], hold_events=True) as service:
        process = subprocess.Popen([
            sys.executable, str(Path(__file__).with_name("run_original_acceptance.py")),
            str(input_path), "--server-url", service.url, "--output", str(recording_path),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if not service.connected.wait(5):
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"recorder did not reach the fake WebSocket service: {stdout} {stderr}")
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=10)

    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    manifest = {"dataset": "fixed", "config": "en",
                "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(), "seconds": 1,
                "items": [{"reference": "Keep these words"}]}

    report = run_score_cli(tmp_path, manifest, recording)

    assert process.returncode != 0, stdout + stderr
    assert recording["recording_status"] == "cancelled"
    assert recording["completion_signal"] == "cancelled"
    assert report["session_without_errors"] is False
    assert report["transcription_accuracy_passed"] is False


def test_recording_and_scoring_clis_keep_error_distinct_from_transport_end(tmp_path):
    input_path = tmp_path / "sample.wav"
    write_wav(input_path)
    recording_path = tmp_path / "recording.json"
    events = [
        {"type": "subtitle", "sentence_id": 1, "beg": 0,
         "is_final": True, "text": "Keep these words"},
        {"type": "error", "message": "recognition failed"},
        {"type": "end"},
    ]
    with FakeRecognitionService(events) as service:
        process = subprocess.run([
            sys.executable, str(Path(__file__).with_name("run_original_acceptance.py")),
            str(input_path), "--server-url", service.url, "--output", str(recording_path),
        ], capture_output=True, text=True)
    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    manifest = {"dataset": "fixed", "config": "en",
                "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(), "seconds": 1,
                "items": [{"reference": "Keep these words"}]}

    report = run_score_cli(tmp_path, manifest, recording)

    assert process.returncode != 0
    assert recording["completion_signal"] == "end"
    assert recording["recording_status"] == "failed"
    assert report["session_without_errors"] is False
    assert report["transcription_accuracy_passed"] is False


def test_scoring_cli_requires_the_end_event_even_if_recording_claims_completion(tmp_path):
    reference_hash = "d" * 64
    manifest = {"dataset": "fixed", "config": "en", "sha256": reference_hash, "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    recording = {"source_kind": "local_file", "input_sha256": reference_hash,
                 "input_sha256_after": reference_hash, "input_unchanged": True,
                 "recording_status": "completed", "completion_signal": "end", "errors": [],
                 "events": [{"type": "subtitle", "sentence_id": 1, "beg": 0,
                             "is_final": True, "text": "Keep these words"}]}

    report = run_score_cli(tmp_path, manifest, recording)

    assert report["recording_status"] == "completed"
    assert report["completion_signal"] == "end"
    assert report["session_without_errors"] is False
    assert report["transcription_accuracy_passed"] is False


def test_recording_and_scoring_clis_reject_translated_caption_events(tmp_path):
    input_path = tmp_path / "sample.wav"
    write_wav(input_path)
    recording_path = tmp_path / "recording.json"
    events = [
        {"type": "subtitle", "sentence_id": 1, "beg": 0,
         "is_final": True, "text": "Keep these words", "translated": True},
        {"type": "end"},
    ]
    with FakeRecognitionService(events) as service:
        process = subprocess.run([
            sys.executable, str(Path(__file__).with_name("run_original_acceptance.py")),
            str(input_path), "--server-url", service.url, "--output", str(recording_path),
        ], capture_output=True, text=True)
    recording = json.loads(recording_path.read_text(encoding="utf-8"))
    manifest = {"dataset": "fixed", "config": "en",
                "sha256": hashlib.sha256(input_path.read_bytes()).hexdigest(), "seconds": 1,
                "items": [{"reference": "Keep these words"}]}

    report = run_score_cli(tmp_path, manifest, recording)

    assert process.returncode != 0
    assert recording["recording_status"] == "completed"
    assert report["translated_cues"] == 1
    assert report["transcription_accuracy_passed"] is False


def test_scoring_cli_uses_fixed_normalization_with_revisions_retractions_and_anonymous_captions(tmp_path):
    reference_hash = "a" * 64
    manifest = {"dataset": "fixed", "config": "en", "sha256": reference_hash, "seconds": 1,
                "items": [{"reference": "Don't close ３ windows!"}]}
    recording = {"source_kind": "local_file", "input_sha256": reference_hash,
                 "input_sha256_after": reference_hash, "input_unchanged": True,
                 "recording_status": "completed", "completion_signal": "end", "errors": [],
                 "events": [
                     {"type": "subtitle", "sentence_id": 1, "beg": 0,
                      "is_final": True, "text": "Wrong draft"},
                     {"type": "subtitle", "sentence_id": 1, "beg": 0,
                      "is_final": True, "text": "Don't close ３ windows!"},
                     {"type": "caption_remove", "sentence_id": 1},
                     {"type": "subtitle", "sentence_id": None, "beg": 0,
                      "is_final": True, "text": "Wrong"},
                     {"type": "subtitle", "sentence_id": None, "beg": 1,
                      "is_final": True, "text": "also wrong"},
                     {"type": "caption_remove", "sentence_id": None},
                     {"type": "subtitle", "sentence_id": None, "beg": 2,
                      "is_final": True, "text": "Don't close"},
                     {"type": "subtitle", "sentence_id": None, "beg": 3,
                      "is_final": True, "text": "３ windows!"},
                     {"type": "end"},
                 ]}

    report = run_score_cli(tmp_path, manifest, recording)

    assert report["error_rate"] == 0
    assert report["hypothesis_tokens"] == 4
    assert report["transcript"] == "Don't close ３ windows!"
    assert "NFKC, lowercase, remove punctuation" in report["normalization"]
    assert "retain written numeric forms" in report["normalization"]
    assert report["source_verified"] is True


def test_scoring_cli_reports_chinese_character_error_rate(tmp_path):
    reference_hash = "c" * 64
    manifest = {"dataset": "fixed-zh", "config": "zh", "sha256": reference_hash, "seconds": 1,
                "items": [{"reference": "别 太 装。"}]}
    recording = {"source_kind": "local_file", "input_sha256": reference_hash,
                 "input_sha256_after": reference_hash, "input_unchanged": True,
                 "recording_status": "completed", "completion_signal": "end", "errors": [],
                 "events": [{"type": "subtitle", "sentence_id": 1, "beg": 0,
                             "is_final": True, "text": "别太装"}, {"type": "end"}]}

    report = run_score_cli(tmp_path, manifest, recording, language="zh")

    assert report["metric"] == "CER"
    assert report["reference_tokens"] == 3
    assert report["hypothesis_tokens"] == 3
    assert report["error_rate"] == 0
    assert report["transcription_accuracy_passed"] is True


def test_written_numbers_are_not_silently_equated_with_spelled_numbers():
    assert tokens("Don't close ３ windows!", "en") == ["dont", "close", "3", "windows"]
    assert tokens("别 太 装。", "zh") == list("别太装")
    assert edit_distance(["three"], ["3"]) == 1
    assert edit_distance(["a", "b", "c"], ["a", "d", "e", "c"]) == 2


def test_retracted_and_revised_final_captions_do_not_duplicate_the_score():
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": 1, "beg": 0, "is_final": True, "text": "Bad"},
              {"type": "caption_remove", "sentence_id": 1},
              {"type": "subtitle", "sentence_id": 2, "beg": 0, "is_final": True, "text": "Keep"},
              {"type": "subtitle", "sentence_id": 2, "beg": 0, "is_final": True, "text": "Keep these words"},
              {"type": "end"}]
    result = score(manifest, {"events": events, "errors": []}, "en")
    assert result["error_rate"] == 0
    assert result["transcript"] == "Keep these words"
    assert result["transcription_accuracy_passed"]
    assert not result["accuracy_gate_passed"]
    assert not score(manifest, {"events": events, "errors": ["stopped early"]}, "en")["accuracy_gate_passed"]


@pytest.mark.parametrize("tail", [[], [{"type": "error", "message": "stopped early"}, {"type": "end"}]])
def test_incomplete_or_failed_event_stream_cannot_pass_without_an_error_summary(tail):
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": 1, "beg": 0, "is_final": True,
               "text": "Keep these words"}, *tail]
    result = score(manifest, {"events": events}, "en")
    assert result["error_rate"] == 0
    assert not result["session_without_errors"]
    assert not result["accuracy_gate_passed"]


def test_captions_with_null_sentence_ids_remain_distinct_by_timestamp():
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 2,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": None, "beg": 0, "is_final": True, "text": "Keep"},
              {"type": "subtitle", "sentence_id": None, "beg": 1, "is_final": True, "text": "these words"},
              {"type": "end"}]
    result = score(manifest, {"events": events}, "en")
    assert result["transcript"] == "Keep these words"
    assert result["transcription_accuracy_passed"]
    assert not result["accuracy_gate_passed"]


def test_anonymous_withdrawal_removes_anonymous_captions_but_keeps_identified_ones():
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 2,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": None, "beg": 0, "is_final": True, "text": "Wrong"},
              {"type": "subtitle", "sentence_id": None, "beg": 1, "is_final": True, "text": "also wrong"},
              {"type": "subtitle", "sentence_id": 1, "beg": 0, "is_final": True, "text": "Keep these words"},
              {"type": "caption_remove", "sentence_id": None}, {"type": "end"}]
    result = score(manifest, {"events": events}, "en")
    assert result["transcript"] == "Keep these words"
    assert result["reference_sha256"] == "test"
    assert result["input_sha256"] is None
    assert result["source_status"] == "legacy_missing_identity"
    assert result["source_verified"] is False
    assert result["transcription_accuracy_passed"]
    assert not result["accuracy_gate_passed"]


@pytest.mark.parametrize("summary", [{}, {"translated_cues": 0}])
def test_translation_in_events_cannot_be_hidden_by_the_summary(summary):
    manifest = {"dataset": "fixed", "config": "en", "sha256": "test", "seconds": 1,
                "items": [{"reference": "Keep these words"}]}
    events = [{"type": "subtitle", "sentence_id": 1, "beg": 0, "is_final": True,
               "text": "Keep these words", "translated": True}, {"type": "end"}]
    result = score(manifest, {"events": events, **summary}, "en")
    assert result["translated_cues"] == 1
    assert not result["accuracy_gate_passed"]
