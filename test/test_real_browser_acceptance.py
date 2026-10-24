import json
import time

import run_real_browser_acceptance as acceptance


def test_capped_recording_keeps_cancelled_truth_and_redacts_transport_secrets(tmp_path):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"fixed local test input")
    output = tmp_path / "recording.json"
    source_before = {"identity": acceptance.file_identity(media),
                     "sha256": acceptance.sha256_file(media)}
    secret = "bearer-value-that-must-not-escape"
    events = [
        acceptance.sanitize_ws_event({
            "type": "subtitle", "beg": 0, "end": 1, "text": "Keep these words",
            "is_final": True, "sentence_id": "1", "authorization": f"Bearer {secret}",
            "session_url": f"wss://media.example/ws?session={secret}",
        }, 25),
    ]

    recording = acceptance.write_browser_recording(
        media_path=str(media), output_path=output, events=events, status="cancelled",
        completion_signal="cancelled", completion_reason="cancelled", errors=[],
        started_at="2026-10-25T00:00:00+00:00", started=time.monotonic() - 5,
        metadata={"session_post_start_count": 1}, source_before=source_before,
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert recording["recording_status"] == saved["recording_status"] == "cancelled"
    assert recording["completion_reason"] == "cancelled"
    assert not any(event["type"] == "end" for event in saved["events"])
    assert recording["simulated_recognition"] is False
    assert recording["real_accuracy_tested"] is False
    assert recording["acoustic_sync_assessed"] is False
    assert recording["user_audio_heard"] is False
    assert recording["input_unchanged"] is True
    assert saved["final_cues"][0]["text"] == "Keep these words"
    assert "source_verified" not in saved
    artifact_text = output.read_text(encoding="utf-8")
    assert secret not in artifact_text
    assert "Bearer " not in artifact_text
    assert "session_url" not in artifact_text


def test_optional_share_http_failure_is_diagnostic_but_session_start_is_fatal():
    share_diagnostic, share_failure = acceptance.classify_http_response(
        "POST", "/share/start", 500)
    session_diagnostic, session_failure = acceptance.classify_http_response(
        "POST", "/api/cloud/sessions", 500)
    stop_path = "/api/cloud/sessions/private-capability"
    stop_diagnostic, stop_failure = acceptance.classify_http_response(
        "DELETE", stop_path, 500)

    assert share_diagnostic == {
        "type": "http_response_error", "endpoint_category": "share_start", "status": 500}
    assert share_failure is None
    assert session_diagnostic == {
        "type": "http_response_error", "endpoint_category": "session_start", "status": 500}
    assert session_failure == {"type": "session_start_http_error", "status": 500}
    assert stop_diagnostic == {
        "type": "http_response_error", "endpoint_category": "session_stop", "status": 500}
    assert stop_failure is None
    assert "private-capability" not in str((session_diagnostic, session_failure,
                                            stop_diagnostic, stop_failure))


def test_player_failure_classification_is_separate_from_console_diagnostics():
    console_kind, console = acceptance.classify_runtime_error(
        "console", "Failed to load resource: HTTP 500 https://host.invalid/private?token=secret")
    page_kind, _page_error = acceptance.classify_runtime_error("page", "uncaught callback")
    websocket_kind, websocket_error = acceptance.classify_runtime_error("websocket")

    assert console_kind == "diagnostic"
    assert console["type"] == "console_error"
    assert "secret" not in console["message"]
    assert page_kind == "failure"
    assert websocket_kind == "failure"
    assert websocket_error == {"type": "websocket_transport_error"}
    assert acceptance.fatal_player_sample({"status_is_error": False,
                                            "media_error_code": 0}) is None
    assert acceptance.fatal_player_sample({"status_is_error": True,
                                            "media_error_code": 0}) == {"type": "ui_fatal"}
    assert acceptance.fatal_player_sample({"status_is_error": False,
                                            "media_error_code": 4}) == {
        "type": "media_error", "code": 4}
