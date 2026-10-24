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
