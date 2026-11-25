import base64
import hashlib
import json
import struct

import pytest

from browser_pcm_capture import EngineeringPcmWriter


def pcm_batch(*, sequence=0, frame=128, channels=2, values=(0.25, -0.5, 0.75, -1.0)):
    return {
        "kind": "pcm_batch", "context_id": "fixture-context", "sequence": sequence,
        "sample_rate_hz": 48000, "channel_count": channels,
        "quanta": [{"current_frame": frame, "frame_count": len(values) // channels}],
        "pcm_base64": base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode(),
        "delivered_after_ms": 999,
    }


def browser_end(*, batches=1, frames=2, end_frame=130):
    return {"status": "captured", "context_id": "fixture-context",
            "batch_count": batches, "frame_count": frames, "first_frame": 128,
            "end_frame": end_frame, "sample_rate_hz": 48000, "channel_count": 2,
            "signal_observed": True}


def test_streamed_pcm_preserves_every_float_channel_and_frame_label(tmp_path):
    destination = tmp_path / "output.f32le"
    writer = EngineeringPcmWriter(destination, tmp_path)

    assert writer.receive(pcm_batch()) == {"status": "accepted"}
    result = writer.finish(browser_end())

    expected = b"\x00\x00\x80\x3e\x00\x00\x00\xbf\x00\x00\x40\x3f\x00\x00\x80\xbf"
    assert destination.read_bytes() == expected
    assert result["status"] == "captured"
    assert result["byte_count"] == 16
    assert result["sha256"] == hashlib.sha256(expected).hexdigest()
    assert result["measurement_scope"] == "browser_output_timestamp_estimate"
    assert result["output_clock_error_bound_ms"] is None
    ledger = [json.loads(line) for line in (tmp_path / result["metadata_path"]).read_text().splitlines()]
    assert ledger[0]["quanta"] == [{"current_frame": 128, "frame_count": 2}]
    assert ledger[0]["byte_offset"] == 0
    assert ledger[0]["byte_count"] == 16
    assert ledger[0]["sha256"] == result["sha256"]
    assert "pcm_base64" not in ledger[0]


@pytest.mark.parametrize("change", [
    {"sequence": 2},
    {"quanta": [{"current_frame": 131, "frame_count": 2}]},
    {"quanta": [{"current_frame": 129, "frame_count": 2}]},
    {"sample_rate_hz": 44100},
    {"channel_count": 1},
    {"context_id": "different-context"},
    {"pcm_base64": "AA=="},
    {"pcm_base64": "not base64"},
    {"quanta": [{"current_frame": 130, "frame_count": 1},
                {"current_frame": 132, "frame_count": 1}]},
])
def test_discontinuous_or_malformed_pcm_is_unknown_without_padding(tmp_path, change):
    writer = EngineeringPcmWriter(tmp_path / "output.f32le", tmp_path)
    first = pcm_batch()
    writer.receive(first)
    second = {**pcm_batch(sequence=1, frame=130), **change}

    assert writer.receive(second)["status"] == "rejected"
    result = writer.finish(browser_end(batches=2, frames=4, end_frame=132))

    assert result["status"] == "unknown"
    assert result["integrity_errors"]
    assert (tmp_path / result["path"]).read_bytes() == base64.b64decode(first["pcm_base64"])


def test_context_pause_barriers_and_raw_output_clock_pairs_remain_in_evidence(tmp_path):
    writer = EngineeringPcmWriter(tmp_path / "output.f32le", tmp_path)
    for segment, state in ((1, "running"), (2, "suspended"), (3, "running")):
        assert writer.receive({
            "kind": "context_state", "context_id": "fixture-context", "clock_segment": segment,
            "state": state, "mapping_barrier": True, "context_time_seconds": 128 / 48000,
            "call_before_after_ms": segment * 100, "call_after_after_ms": segment * 100 + 1,
        })["status"] == "accepted"
    clock = {"kind": "clock_sample", "context_id": "fixture-context", "clock_segment": 3,
             "state": "running", "context_time_seconds": 128 / 48000,
             "performance_time_after_ms": 302.25, "call_before_after_ms": 303,
             "call_after_after_ms": 303.1, "usable": True}
    writer.receive(clock)
    writer.receive(pcm_batch())

    result = writer.finish(browser_end())

    assert result["status"] == "captured"
    assert result["clock_sample_count"] == 1
    assert result["context_state_count"] == 3
    records = [json.loads(line) for line in (tmp_path / result["metadata_path"]).read_text().splitlines()]
    assert records[:3][1]["state"] == "suspended"
    assert records[3] == clock
    assert "clock_uncertainty_ms" not in result


def test_periodic_pixel_records_stream_alongside_pcm_without_changing_audio(tmp_path):
    writer = EngineeringPcmWriter(tmp_path / "output.f32le", tmp_path)
    pixel = {"kind": "pixel_observation", "context_id": "fixture-context",
             "path": "pixels/pixel-000001.png", "observed_after_ms": 100,
             "before_after_ms": 90, "after_after_ms": 110}
    assert writer.receive(pixel)["status"] == "accepted"
    writer.receive(pcm_batch())
    result = writer.finish(browser_end())
    assert result["status"] == "captured" and result["pixel_observation_count"] == 1
    records = [json.loads(line) for line in (tmp_path / result["metadata_path"]).read_text().splitlines()]
    assert records[0] == pixel


def test_missing_tail_or_browser_overflow_cannot_be_a_successful_capture(tmp_path):
    writer = EngineeringPcmWriter(tmp_path / "missing-tail.f32le", tmp_path)
    writer.receive(pcm_batch())
    missing_tail = writer.finish(browser_end(batches=2, frames=4, end_frame=132))
    assert missing_tail["status"] == "unknown"
    assert "final_frame_accounting_mismatch" in missing_tail["integrity_errors"]

    writer = EngineeringPcmWriter(tmp_path / "overflow.f32le", tmp_path)
    writer.receive(pcm_batch())
    assert writer.receive({"kind": "capture_error", "context_id": "fixture-context",
                           "reason": "worklet_queue_overflow"})["status"] == "accepted"
    overflow = writer.finish(browser_end())
    assert overflow["status"] == "unknown"
    assert "worklet_queue_overflow" in overflow["integrity_errors"]


@pytest.mark.parametrize("payload", [None, [], {}, {"kind": "made_up"}])
def test_malformed_bridge_event_is_rejected_and_empty_capture_is_unknown(tmp_path, payload):
    writer = EngineeringPcmWriter(tmp_path / "output.f32le", tmp_path)
    assert writer.receive(payload)["status"] == "rejected"
    assert writer.finish({"status": "missing"})["status"] == "unknown"
