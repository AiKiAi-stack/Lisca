"""Engineering estimates use saved PCM and pixels through the public CLI."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import textwrap
import wave

import numpy as np
import pytest
from score_acoustic_acceptance import _png_rgb


SCORER = Path(__file__).parents[1] / "acceptance" / "score_engineering_sync.py"
RATE = 8000


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _capture(tmp_path, samples):
    pcm = tmp_path / "output.f32le"
    pcm.write_bytes(np.asarray(samples, dtype="<f4").tobytes())
    installation = {
        "status": "installed", "media_element_id": "audioEl",
        "media_element_muted": False, "media_element_volume": 1,
        "routing": "media_element_audio_worklet_destination", "transparent_pcm": True,
        "context_id": "test-context", "sample_rate_hz": RATE,
        "sampler_performance_origin_ms": 100,
        "output_clock_error_bound_ms": None,
    }
    rows = [{"kind": "context_state", "context_id": "test-context",
             "clock_segment": 1, "state": "running", "mapping_barrier": True,
             "context_time_seconds": 0, "context_frame": 0,
             "call_before_after_ms": 0, "call_after_after_ms": 0}]
    for index in range(int(len(samples) / RATE * 10) + 1):
        milliseconds = index * 100
        rows.append({
            "kind": "clock_sample", "context_id": "test-context", "clock_segment": 1,
            "state": "running", "context_time_seconds": milliseconds / 1000,
            "performance_time_ms": 100 + milliseconds,
            "performance_time_after_ms": milliseconds,
            "call_before_after_ms": milliseconds + 20,
            "call_after_after_ms": milliseconds + 21, "usable": True,
        })
    frame = 0
    sequence = 0
    data = pcm.read_bytes()
    while frame < len(samples):
        # The contract records actual lengths; no fixed 128-frame assumption.
        count = min((64, 128, 192)[sequence % 3], len(samples) - frame)
        block = data[frame * 4:(frame + count) * 4]
        rows.append({
            "kind": "pcm_batch", "context_id": "test-context", "sequence": sequence,
            "sample_rate_hz": RATE, "channel_count": 1,
            "quanta": [{"current_frame": frame, "frame_count": count}],
            "byte_offset": frame * 4, "byte_count": len(block),
            "sha256": hashlib.sha256(block).hexdigest(),
            "delivered_after_ms": 90000 + sequence * 71,
        })
        sequence += 1
        frame += count
    summary = {**installation, "kind": "capture_summary", "status": "captured",
               "batch_count": sequence, "frame_count": len(samples),
               "first_frame": 0, "end_frame": len(samples), "channel_count": 1,
               "signal_observed": True, "worklet_failed": False}
    rows.append(summary)
    ledger = tmp_path / "frames.jsonl"
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    audio = {**summary, "path": pcm.name, "sha256": _hash(pcm),
             "metadata_path": ledger.name, "metadata_sha256": _hash(ledger),
             "byte_count": pcm.stat().st_size, "format": "f32le", "layout": "interleaved",
             "integrity_errors": [], "clock_mapping_status": "browser_api_estimate_only"}
    report = tmp_path / "capture.report.json"
    report.write_text(json.dumps({
        "candidate_revision": "d" * 40, "candidate_worktree_dirty": False,
        "engineering_sync": {"mode": "browser_audio_worklet_pcm",
                             "measurement_scope": "browser_output_timestamp_estimate",
                             "candidate_revision": "d" * 40,
                             "candidate_worktree_dirty": False,
                             "installation": installation, "audio": audio, "screenshots": []},
    }), encoding="utf-8")
    return report


def _run(tmp_path, report, *extra):
    output = tmp_path / "score.json"
    completed = subprocess.run(
        [sys.executable, str(SCORER), "--report", str(report), "--language", "en",
         "--output", str(output), *map(str, extra)], capture_output=True, text=True, check=False)
    assert output.exists(), completed.stderr
    return completed.returncode, json.loads(output.read_text())["engineering_sync"]


def test_complete_pcm_without_frozen_reference_stays_unknown(tmp_path):
    report = _capture(tmp_path, np.full(RATE, 0.2))
    code, result = _run(tmp_path, report)
    assert code == 1
    assert result["status"] == "unknown"
    assert result["unknown_reasons"] == ["frozen_reference_missing"]
    assert result["capture_integrity"]["status"] == "validated"
    assert result["capture_integrity"]["frame_count"] == RATE
    assert result["formal_gate_passed"] is None
    assert result["physical_output_latency"] == "unknown"
    assert result["source_progress"]["status"] == "unknown"


def _voice_and_pixels(tmp_path, *, pause=False, lag=0, repeat=False):
    rng = np.random.default_rng(1971)
    voice = np.convolve(rng.normal(size=9600), np.ones(5) / 5, mode="same") * 0.12
    integers = np.round(voice * 32768).astype("<i2")
    voice = integers.astype(np.float32) / 32768
    source = tmp_path / "reference.wav"
    original = np.concatenate((np.zeros(3200), voice, np.zeros(3200)))
    with wave.open(str(source), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(np.round(original * 32768).astype("<i2").tobytes())
    reference = tmp_path / "reference.json"
    reference.write_text(json.dumps({
        "schema_version": 1, "language": "en", "sha256": _hash(source),
        "asset_sample_rate_hz": RATE, "seconds": 2,
        "items": [{"reference": "independent sentence", "asset_start_seconds": 0,
                   "asset_end_seconds": 2}],
    }), encoding="utf-8")
    spans = tmp_path / "spans.json"
    spans.write_text(json.dumps({
        "schema_version": 1, "source_sha256": _hash(source),
        "source_condition": "synthetic_tone_fixture", "provenance": "independent_control",
        "method": {"name": "frozen synthetic voice", "parameters": {"uncertainty_seconds": 0.01}},
        "items": [{"item": 1, "spans": [{"start_seconds": 0.4, "end_seconds": 1.6,
                   "start_uncertainty_seconds": 0.01, "end_uncertainty_seconds": 0.01}]}],
    }), encoding="utf-8")
    output_voice = (np.concatenate((voice[:4800], np.zeros(4800), voice[4800:]))
                    if pause else voice)
    if repeat:
        output_voice = np.concatenate((output_voice, np.zeros(4000), voice))
    output_pcm = np.concatenate((np.zeros(11200), output_voice, np.zeros(16000)))
    report = _capture(tmp_path, output_pcm)
    data = json.loads(report.read_text())
    ledger = tmp_path / "frames.jsonl"
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    # The API estimates context+200ms. Delivery clocks deliberately disagree.
    for row in rows:
        if row["kind"] == "clock_sample":
            row["performance_time_ms"] += 200
            row["performance_time_after_ms"] += 200
            row["call_before_after_ms"] += 200
            row["call_after_after_ms"] += 200
    start, end = 1.6 + lag, 2.8 + (0.6 if pause else 0) + lag
    bounds = {"x": 0, "y": 0, "width": 40, "height": 20}
    roi = {"x": 10, "y": 20, "width": 40, "height": 20}
    ticks = 131 if repeat else 91
    for tick in range(ticks):
        observed = tick * 50
        visible = (start * 1000 <= observed < end * 1000
                   or repeat and 3300 <= observed < 4500)
        image = np.full((20, 40, 3), 255, dtype=np.uint8)
        if visible:
            image[0, 0] = (42, 0, 0)
        path = tmp_path / f"pixel-{tick:03d}.png"
        path.write_bytes(_png_rgb(image))
        rows.append({
            "kind": "pixel_observation", "observation_index": tick + 1,
            "context_id": "test-context", "path": path.name, "sha256": _hash(path),
            "byte_count": path.stat().st_size,
            "observed_after_ms": observed, "uncertainty_ms": 2,
            "before_after_ms": observed - 2, "after_after_ms": observed + 2,
            "dom_state_stable": True, "visible": visible,
            "visible_text": "independent sentence" if visible else "",
            "viewport_before": {"width": 80, "height": 60},
            "viewport_after": {"width": 80, "height": 60},
            "device_scale_factor_before": 1, "device_scale_factor_after": 1,
            "caption_bounds_before": {"x": 18, "y": 28, "width": 10, "height": 2} if visible else None,
            "caption_bounds_after": {"x": 18, "y": 28, "width": 10, "height": 2} if visible else None,
            "ocr_crop_bounds": bounds, "ocr_crop_source": "fixed_caption_roi",
            "roi_bounds": roi, "shadow_margin_css_px": 8,
            "screenshot_width_px": 40, "screenshot_height_px": 20,
        })
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    data["engineering_sync"]["audio"]["metadata_sha256"] = _hash(ledger)
    data["engineering_sync"]["pixel_capture"] = {
        "metadata_path": ledger.name, "metadata_sha256": _hash(ledger),
        "ledger_kind": "pixel_observation", "observation_count": ticks,
        "target_interval_ms": 100, "fixed_roi": roi, "shadow_margin_css_px": 8,
        "status": "captured",
    }
    report.write_text(json.dumps(data), encoding="utf-8")
    executable = tmp_path / "pixel-reader"
    executable.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import av
        import sys
        with av.open(sys.stdin.buffer, format="png_pipe") as image:
            frame = next(image.decode(video=0))
            if tuple(frame.to_ndarray(format="rgb24")[0, 0]) == (42, 0, 0):
                print("independent sentence")
    """), encoding="utf-8")
    executable.chmod(0o755)
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    return report, ("--fixture", "--reference", reference, "--source", source,
                    "--voice-spans", spans, "--ocr-executable", executable,
                    "--tessdata-dir", tessdata)


def test_frozen_waveform_and_pixel_observations_produce_signed_estimates(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path)
    code, result = _run(tmp_path, report, *arguments)
    assert code == 1  # A one-item synthetic control cannot meet minimum 50.
    assert result["items"][0]["status"] == "measured"
    assert result["items"][0]["audio_spans"][0]["start_seconds"] == pytest.approx([1.589875, 1.610125])
    assert result["start_error_seconds"]["max_absolute_upper_bound"] < 0.12
    assert result["end_error_seconds"]["max_absolute_upper_bound"] < 0.12
    assert result["coverage"]["reference_voiced_seconds"] == pytest.approx(1.2)
    assert result["unknown_reasons"] == ["minimum_measured_items_not_met"]


def test_voiced_pause_keeps_six_hundred_milliseconds_in_wall_coordinates(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path, pause=True)
    _, result = _run(tmp_path, report, *arguments)
    item = result["items"][0]
    assert item["status"] == "measured"
    assert item["audio_spans"][0]["end_seconds"] == pytest.approx([3.389875, 3.410125])
    assert result["end_error_seconds"]["max_absolute_upper_bound"] < 0.12
    assert result["coverage"]["reference_voiced_seconds"] == pytest.approx(1.2)
    assert result["coverage"]["sampled_voiced_coverage_lower_estimate"] > 0.9
    assert result["coverage"]["unconditional"]["voiced_coverage_lower_bound"] == 0


def test_actual_pixel_lag_is_detected_even_with_favorable_dom_commands(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path, lag=0.75)
    data = json.loads(report.read_text())
    data["timeline_samples"] = [{"observed_after_ms": 1600, "visible_text": "independent sentence",
                                  "visible": True, "media_time": 0.4}]
    data["commanded_caption_lag_seconds"] = 0
    report.write_text(json.dumps(data))
    _, result = _run(tmp_path, report, *arguments)
    assert result["items"][0]["status"] == "measured"
    assert result["start_error_seconds"]["p95_absolute_lower_bound"] > 0.5
    assert result["end_error_seconds"]["p95_absolute_lower_bound"] > 0.5
    assert result["sampled_engineering_thresholds_satisfied"] is False
    assert result["formal_gate_passed"] is None


def test_identical_pcm_and_pixels_cannot_identify_quiet_skip_replay(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path)
    _, first = _run(tmp_path, report, *arguments)
    data = json.loads(report.read_text())
    data["timeline_samples"] = [
        {"observed_after_ms": 100, "media_time": 0.15},
        {"observed_after_ms": 150, "media_time": 0.10},
    ]
    data["quiet_source_operation"] = "skip_50ms_then_replay_50ms"
    report.write_text(json.dumps(data))
    _, second = _run(tmp_path, report, *arguments)
    assert first == second
    assert second["source_progress"]["status"] == "unknown"


def test_all_compatible_audio_and_natural_text_repeats_remain_ambiguous(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path, repeat=True)
    _, result = _run(tmp_path, report, *arguments)
    item = result["items"][0]
    assert item["status"] == "unknown"
    assert len(item["audio_occurrences"][0]) == 2
    assert len(item["caption_occurrences"]) == 2
    assert result["replay_or_ambiguous_audio_item_count"] == 1
    assert result["coverage"]["sampled_voiced_coverage_lower_estimate"] == 0
    assert result["coverage"]["reference_voiced_seconds"] == pytest.approx(1.2)


def _rewrite_ledger(report, change):
    data = json.loads(report.read_text())
    ledger = report.parent / data["engineering_sync"]["audio"]["metadata_path"]
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    change(rows)
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    data["engineering_sync"]["audio"]["metadata_sha256"] = _hash(ledger)
    if "pixel_capture" in data["engineering_sync"]:
        data["engineering_sync"]["pixel_capture"]["metadata_sha256"] = _hash(ledger)
    report.write_text(json.dumps(data))


@pytest.mark.parametrize(("kind", "field", "value", "reason"), [
    ("pcm_batch", "sequence", 7, "capture_batch_sequence_or_format_invalid"),
    ("pcm_batch", "channel_count", True, "capture_batch_sequence_or_format_invalid"),
    ("pcm_batch", "byte_offset", False, "capture_batch_sequence_or_format_invalid"),
    ("pcm_batch", "byte_count", 999, "capture_batch_byte_count_invalid"),
    ("pcm_batch", "sha256", "0" * 64, "capture_batch_hash_or_length_mismatch"),
    ("capture_summary", "frame_count", 123, "capture_final_counter_mismatch"),
    ("clock_sample", "usable", "true", "capture_clock_usable_flag_missing"),
])
def test_corrupt_ledger_is_rejected_after_whole_ledger_hash_is_updated(tmp_path, kind, field, value, reason):
    report = _capture(tmp_path, np.full(RATE, 0.2))
    _rewrite_ledger(report, lambda rows: next(row for row in rows if row["kind"] == kind).__setitem__(field, value))
    code, result = _run(tmp_path, report)
    assert code == 2
    assert result["status"] == "measurement_invalid"
    assert result["invalid_reason"] == reason
    assert result["formal_gate_passed"] is None


def test_frame_overlap_is_not_repaired_with_padding(tmp_path):
    report = _capture(tmp_path, np.full(RATE, 0.2))

    def overlap(rows):
        batches = [row for row in rows if row["kind"] == "pcm_batch"]
        batches[1]["quanta"][0]["current_frame"] -= 1

    _rewrite_ledger(report, overlap)
    code, result = _run(tmp_path, report)
    assert code == 2
    assert result["invalid_reason"] == "capture_frame_gap_overlap_or_reorder"


def test_missing_final_worklet_summary_cannot_hide_a_dropped_tail(tmp_path):
    report = _capture(tmp_path, np.full(RATE, 0.2))
    _rewrite_ledger(report, lambda rows: rows.pop())
    code, result = _run(tmp_path, report)
    assert code == 2
    assert result["invalid_reason"] == "capture_final_summary_or_pcm_missing"


def test_unusable_clock_barrier_prevents_reusing_old_segment(tmp_path):
    report = _capture(tmp_path, np.full(RATE, 0.2))

    def barrier(rows):
        index = next(index for index, row in enumerate(rows) if row["kind"] == "clock_sample")
        rows.insert(index, {**rows[index], "usable": False, "mapping_barrier": True})

    _rewrite_ledger(report, barrier)
    code, result = _run(tmp_path, report)
    assert code == 2
    assert result["invalid_reason"] == "capture_usable_clock_crosses_barrier"


def test_clock_gap_is_not_fitted_or_extrapolated_to_voice_boundary(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path)
    _rewrite_ledger(report, lambda rows: rows.__setitem__(slice(None), [
        row for row in rows if not (row["kind"] == "clock_sample"
                                    and 1.1 < row["context_time_seconds"] < 1.7)]))
    _, result = _run(tmp_path, report, *arguments)
    assert result["items"][0]["status"] == "unknown"
    assert "audio_clock_mapping_missing_or_order_ambiguous" in result["items"][0]["unknown_reasons"]


def test_unknown_vad_start_keeps_item_and_original_denominator_fallback(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path)
    spans = tmp_path / "spans.json"
    data = json.loads(spans.read_text())
    data["items"][0]["spans"][0]["start_uncertainty_seconds"] = 0.7
    spans.write_text(json.dumps(data))
    _, result = _run(tmp_path, report, *arguments)
    assert result["samples"] == 1
    assert result["unknown_item_count"] == 1
    assert result["reference_voice_unknown_items"] == [1]
    assert result["coverage"]["reference_voiced_seconds"] == 2
    assert result["coverage"]["sampled_voiced_coverage_lower_estimate"] == 0


def test_product_mode_rejects_unapproved_fixture_instead_of_selecting_it(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path)
    code, result = _run(tmp_path, report, *arguments[1:])
    assert code == 2
    assert result["invalid_reason"] == "reference_manifest_unapproved"


def test_tampered_reference_audio_is_rejected_before_matching(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path)
    with (tmp_path / "reference.wav").open("ab") as source:
        source.write(b"different bytes")
    code, result = _run(tmp_path, report, *arguments)
    assert code == 2
    assert result["invalid_reason"] == "frozen_reference_source_hash_mismatch"


def test_actual_png_hash_is_required_even_without_reference(tmp_path):
    report, _arguments = _voice_and_pixels(tmp_path)
    (tmp_path / "pixel-000.png").write_bytes(b"not the captured image")
    code, result = _run(tmp_path, report)
    assert code == 2
    assert result["invalid_reason"] == "screenshot_hash_mismatch"


def test_long_pixel_gap_gets_no_continuity_or_coverage_credit(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path)

    def remove_images(rows):
        rows[:] = [row for row in rows if not (row["kind"] == "pixel_observation"
                                              and 1800 <= row["observed_after_ms"] <= 2200)]
        for index, row in enumerate((row for row in rows if row["kind"] == "pixel_observation"), 1):
            row["observation_index"] = index

    _rewrite_ledger(report, remove_images)
    data = json.loads(report.read_text())
    data["engineering_sync"]["pixel_capture"]["observation_count"] -= 9
    report.write_text(json.dumps(data))
    _, result = _run(tmp_path, report, *arguments)
    assert result["items"][0]["status"] == "unknown"
    assert result["visual_evidence"]["maximum_observed_gap_seconds"] == pytest.approx(0.5)
    assert result["coverage"]["sampled_voiced_coverage_lower_estimate"] == 0


def test_ocr_unavailable_is_unknown_and_gets_no_estimated_coverage_credit(tmp_path):
    report, arguments = _voice_and_pixels(tmp_path)
    _, result = _run(tmp_path, report, *arguments[:7])
    assert result["items"][0]["status"] == "unknown"
    assert result["visual_evidence"]["unknown_ocr_observation_count"] == 91
    assert result["coverage"]["sampled_voiced_coverage_lower_estimate"] == 0
