"""Exercise acoustic synchronization through its public scoring command."""
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import sys
import textwrap
import wave
import zlib

import numpy as np
import pytest


RATE = 16000
SCREEN_WIDTH = 640
SCREEN_HEIGHT = 360
CAPTION_BOUNDS = {"x": 100, "y": 200, "width": 400, "height": 50}


def _png_chunk(kind, payload):
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


SCORER = (Path(__file__).parents[1] / "acceptance" / "score_acoustic_acceptance.py")


def _caption_code(text):
    if text == "sentence":
        return 201
    if text == "01":
        return 202
    if not text:
        return 0
    return int(text.rsplit(" ", 1)[1])


def _screenshot(path, text):
    marker = _caption_code(text)
    rows = []
    for y in range(SCREEN_HEIGHT):
        row = bytearray(b"\xff" * (SCREEN_WIDTH * 3))
        if y == CAPTION_BOUNDS["y"]:
            offset = CAPTION_BOUNDS["x"] * 3
            row[offset:offset + 3] = bytes((marker, 0, 0))
        rows.append(b"\0" + row)
    payload = b"".join(rows)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", SCREEN_WIDTH, SCREEN_HEIGHT, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(payload))
        + _png_chunk(b"IEND", b""))


def _fake_ocr(tmp_path):
    executable = tmp_path / "fake-tesseract"
    executable.write_text(textwrap.dedent(f"""\
        #!{sys.executable}
        import av
        import sys

        with av.open(sys.stdin.buffer, format="png_pipe") as image:
            frame = next(image.decode(video=0))
            marker = int(frame.to_ndarray(format="rgb24")[0, 0, 0])
        if marker in (201, 202):
            print({{201: "sentence", 202: "01"}}[marker])
        elif marker:
            print(f"sentence {{marker:02d}}")
    """), encoding="utf-8")
    executable.chmod(0o755)
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir(exist_ok=True)
    return executable, tessdata


def _speech_fixture(path, count=50):
    samples = []
    spans = []
    notes = (110, 160, 230, 330, 470, 670, 950, 1350, 1900, 2700, 3800, 5300)
    for index in range(count):
        for frame in range(RATE):
            time_in_item = frame / RATE
            if 0.2 <= time_in_item < 0.8:
                envelope = min(1.0, (time_in_item - 0.2) * 80,
                               (0.8 - time_in_item) * 80)
                segment = int((time_in_item - 0.2) / 0.05)
                note_index = (index * 5 + segment * 7 + segment * segment) % len(notes)
                frequency = notes[note_index]
                phase = 2 * 3.141592653589793 * frequency * time_in_item
                voice = np.sin(phase) + 0.25 * np.sin(phase * 1.7 + index / 3)
                samples.append(round(9000 * envelope * voice))
            else:
                samples.append(0)
        spans.append({"item": index + 1, "spans": [{
            "start_seconds": index + 0.2, "end_seconds": index + 0.8,
            "start_uncertainty_seconds": 0.02, "end_uncertainty_seconds": 0.02,
        }]})
    # wave.open(..., "wb") returns a writer; pylint infers the reader type here.
    # pylint: disable=no-member
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(b"".join(int(value).to_bytes(2, "little", signed=True)
                                    for value in samples))
    # pylint: enable=no-member
    return spans


def _write_wave_samples(path, samples):
    # wave.open(..., "wb") returns a writer; pylint infers the reader type here.
    # pylint: disable=no-member
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(samples.astype("<i2", copy=False).tobytes())
    # pylint: enable=no-member


def _write_evidence(tmp_path, *, lag=0.0, missing_items=(), quiet_capture=False,
                    uncertainty=0.02, frozen=False, missing_voice_items=(),
                    missing_screenshots=False, split_first_caption=False,
                    cumulative_first_caption=False, pause_at=None,
                    pause_duration=0.6, mute_after_pause_media_time=None,
                    sampling_step_ms=50, caption_during_pause=False):
    source = tmp_path / "source.wav"
    voice_items = _speech_fixture(source)
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    reference = {
        "schema_version": 1, "dataset": "synthetic-independent-fixture",
        "config": "language=en; role=reference", "sha256": source_hash,
        "seconds": 50, "asset_sample_rate_hz": RATE, "items": [
            {"reference": f"sentence {index + 1:02d}",
             "asset_start_seconds": index, "asset_end_seconds": index + 1}
            for index in range(50)
        ],
    }
    voice_spans = {
        "schema_version": 1, "source_sha256": source_hash,
        "method": {"name": "synthetic-fixture-voice-intervals", "parameters": "fixed 20ms bounds"},
        "provenance": "independent synthetic test truth",
        "source_condition": "known_clean_single_speaker",
        "items": [({"item": item["item"], "spans": []}
                    if item["item"] in missing_voice_items else item)
                   for item in voice_items],
    }
    labels = [None if index + 1 in missing_items else f"sentence {index + 1:02d}"
              for index in range(50)]
    samples = []
    screenshots = []
    previous = (None, False)
    start_ms = 0
    step_ms = sampling_step_ms
    capture_duration = (52 if frozen else
                        50 + pause_duration if pause_at is not None else 50)
    for observed_ms in range(0, round(capture_duration * 1000) + 1, step_ms):
        capture_time = observed_ms / 1000
        paused = pause_at is not None and pause_at <= capture_time < pause_at + pause_duration
        source_time = (pause_at if paused else
                       capture_time - pause_duration
                       if pause_at is not None and capture_time >= pause_at + pause_duration else
                       min(25, capture_time) if frozen and capture_time < 27 else
                       capture_time - 2 if frozen and capture_time >= 27 else capture_time)
        item_index = min(49, int(source_time))
        item_offset = source_time - item_index
        if (caption_during_pause and paused
                and capture_time < pause_at + step_ms / 1000):
            item_offset = 0.3 + lag
        text = labels[item_index]
        if split_first_caption and item_index == 0:
            if 0.2 + lag <= item_offset < 0.4 + lag:
                text = "sentence"
            elif 0.5 + lag <= item_offset < 0.8 + lag:
                text = "01"
            else:
                text = None
        elif cumulative_first_caption and item_index == 0:
            if 0.2 + lag <= item_offset < 0.4 + lag:
                text = "sentence"
            elif 0.5 + lag <= item_offset < 0.8 + lag:
                text = "sentence 01"
            else:
                text = None
        visible = bool(text) and item_offset >= 0.2 + lag and item_offset < 0.8 + lag
        text = text if visible else ""
        sample = {
            "observed_after_ms": observed_ms,
            "media_time": source_time,
            "visible_text": text,
            "visible": visible,
            "paused": paused, "seeking": False, "buffering": False,
        }
        samples.append(sample)
        state = (text, visible)
        if state != previous:
            screen_path = tmp_path / "screens" / f"transition-{len(screenshots):04d}.png"
            screen_path.parent.mkdir(parents=True, exist_ok=True)
            _screenshot(screen_path, text)
            screenshot_hash = hashlib.sha256(screen_path.read_bytes()).hexdigest()
            screenshots.append({
                "path": str(screen_path.relative_to(tmp_path)),
                "sha256": screenshot_hash,
                "observed_after_ms": observed_ms,
                "uncertainty_ms": 5,
                "visible": visible, "visible_text": text,
                "viewport_before": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
                "viewport_after": {"width": SCREEN_WIDTH, "height": SCREEN_HEIGHT},
                "device_scale_factor_before": 1,
                "device_scale_factor_after": 1,
                "caption_bounds_before": CAPTION_BOUNDS if visible else None,
                "caption_bounds_after": CAPTION_BOUNDS if visible else None,
                "ocr_crop_bounds": CAPTION_BOUNDS,
                "ocr_crop_source": "caption" if visible else "last_visible_caption",
                "screenshot_width_px": SCREEN_WIDTH,
                "screenshot_height_px": SCREEN_HEIGHT,
            })
            previous = state
    capture_path = tmp_path / "played.wav"
    if quiet_capture:
        # wave.open(..., "wb") returns a writer; pylint infers the reader type here.
        # pylint: disable=no-member
        with wave.open(str(capture_path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(RATE)
            output.writeframes(bytes(source.stat().st_size - 44))
        # pylint: enable=no-member
    else:
        source_bytes = source.read_bytes()
        if frozen:
            capture_path.write_bytes(source_bytes[:44 + 25 * RATE * 2]
                                     + bytes(2 * RATE * 2)
                                     + source_bytes[44 + 25 * RATE * 2:])
        elif pause_at is not None:
            gap_at = pause_at
            source_samples = np.frombuffer(source_bytes[44:], dtype="<i2")
            gap_samples = round(pause_duration * RATE)
            capture_samples = np.concatenate((
                source_samples[:round(gap_at * RATE)],
                np.zeros(gap_samples, dtype=np.int16),
                source_samples[round(gap_at * RATE):]))
            if mute_after_pause_media_time is not None:
                mute_at = round((mute_after_pause_media_time + pause_duration) * RATE)
                capture_samples[mute_at:] = 0
            _write_wave_samples(capture_path, capture_samples)
        else:
            capture_path.write_bytes(source_bytes)
    capture_hash = hashlib.sha256(capture_path.read_bytes()).hexdigest()
    reference_path = tmp_path / "reference.json"
    reference_path.write_text(json.dumps(reference), encoding="utf-8")
    spans_path = tmp_path / "voice-spans.json"
    for item in voice_spans["items"]:
        for span in item["spans"]:
            span["start_uncertainty_seconds"] = uncertainty
            span["end_uncertainty_seconds"] = uncertainty
    spans_path.write_text(json.dumps(voice_spans), encoding="utf-8")
    recording_path = tmp_path / "recording.json"
    recording_path.write_text(json.dumps({
        "source_kind": "local_file", "input_sha256": source_hash,
        "input_sha256_after": source_hash, "input_unchanged": True,
        "simulated_recognition": False,
        "recording_status": "completed", "completion_signal": "end",
        "events": [{"type": "end", "reason": "completed"}], "errors": [],
    }), encoding="utf-8")
    report_path = tmp_path / "browser-report.json"
    if missing_screenshots:
        screenshots.clear()
    clock_times = list(range(0, round(capture_duration * 1000) + 1, 250))
    if clock_times[-1] < round(capture_duration * 1000):
        clock_times.append(round(capture_duration * 1000))
    clock_samples = [
        {"performance_time_after_ms": observed_ms, "context_time_seconds": 1 + observed_ms / 1000}
        for observed_ms in clock_times
    ]
    report_path.write_text(json.dumps({
        "candidate_revision": "867b5327cd90f222cc7d9e45ba012f9de1d3567b",
        "candidate_worktree_dirty": False,
        "language": "en", "recording_status": "completed",
        "simulated_recognition": False, "session_post_start_count": 1,
        "source_verified": True,
        "sampling": {"status": "valid", "tolerance_ms": 100,
                     "max_observed_gap_ms": step_ms, "sample_count": len(samples)},
        "timeline_samples": samples,
        "acoustic_capture": {
            "mode": "browser_web_audio_media_element",
            "audio": {
                "status": "captured",
                "path": str(capture_path.relative_to(tmp_path)), "sha256": capture_hash,
                "start_performance_after_ms": start_ms,
                "start_context_time_seconds": 1.0,
                "sample_rate_hz": RATE,
                "media_element_id": "audioEl",
                "clock_media_element_id": "player",
                "media_element_muted": False,
                "media_element_volume": 1.0,
                "clock_media_element_muted": True,
                "clock_uncertainty_ms": 10,
                "base_latency_seconds": 0.01,
                "output_latency_seconds": 0.02,
                "output_clock_samples": clock_samples,
            },
            "screenshots": screenshots,
            "physical_output_latency": "unknown",
        },
    }), encoding="utf-8")
    return source, reference_path, spans_path, recording_path, report_path


def _run_cli(tmp_path, paths, *, calibration=True, use_ocr=True, extra_args=()):
    output_path = tmp_path / "sync-score.json"
    command = [sys.executable, str(SCORER), "--reference", str(paths[1]),
               "--source", str(paths[0]), "--voice-spans", str(paths[2]),
               "--recording", str(paths[3]), "--report", str(paths[4]),
               "--language", "en", "--output", str(output_path)]
    if use_ocr:
        executable, tessdata = _fake_ocr(tmp_path)
        command.extend(["--ocr-executable", str(executable), "--tessdata-dir", str(tessdata)])
    if calibration:
        command.append("--calibration")
    command.extend(extra_args)
    completed = subprocess.run(command, capture_output=True, text=True)
    assert output_path.is_file(), completed.stderr + completed.stdout
    return completed, json.loads(output_path.read_text(encoding="utf-8"))


def test_public_cli_reports_synthetic_pass_as_calibration_only(tmp_path):
    paths = _write_evidence(tmp_path)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert result["sync_gate_passed"] is not True
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is True
    assert result["acceptance_status"] == "not_assessed"
    assert result["acoustic_sync_assessed"] is False
    assert result["samples"] == 50
    assert result["voice_span_source"]["provenance"] == "independent synthetic test truth"
    assert result["audio_alignment"]["method"] == "bounded_spectral_feature_cross_correlation"
    assert len(result["audio_alignment"]["epochs"]) == 1
    assert result["visual_evidence"]["verified_transition_count"] >= 100
    assert result["physical_output_latency"] == "unknown"


def test_calibration_labels_current_dirty_checkout_without_claiming_acceptance(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    report["candidate_worktree_dirty"] = True
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 0
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is True
    assert result["sync_gate_passed"] is None
    assert result["acceptance_status"] == "not_assessed"


def test_public_cli_rejects_real_caption_lag_over_fixed_tolerance(tmp_path):
    paths = _write_evidence(tmp_path, lag=0.75)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["sync_gate_passed"] is not True
    assert result["start_error_seconds"]["max_absolute_upper_bound"] > 0.5


def test_public_cli_reports_signed_start_and_end_lag_bounds(tmp_path):
    paths = _write_evidence(tmp_path, lag=0.7)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["calibration_thresholds_satisfied"] is False
    assert result["sync_gate_passed"] is None
    measured = [item for item in result["items"] if item["status"] == "measured"]
    assert len(measured) == 49
    assert result["items"][-1]["status"] == "caption_disappearance_unobserved"
    for item in measured:
        assert item["start_lag_interval_seconds"][0] <= 0.7
        assert item["start_lag_interval_seconds"][1] >= 0.7
        assert item["end_lag_interval_seconds"][0] <= 0.2
        assert item["end_lag_interval_seconds"][1] >= 0.2


def test_public_cli_rejects_muted_visual_player_as_audio_evidence(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    report["acoustic_capture"]["audio"]["media_element_id"] = "player"
    report["acoustic_capture"]["audio"]["media_element_muted"] = True
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 2
    assert result["sync_status"] == "measurement_invalid"
    assert result["invalid_reason"] == "captured_element_not_audible_source"


def test_public_cli_requires_exact_approved_reference_outside_calibration(tmp_path):
    paths = _write_evidence(tmp_path)

    completed, result = _run_cli(tmp_path, paths, calibration=False)

    assert completed.returncode == 2
    assert result["sync_status"] == "measurement_invalid"
    assert result["invalid_reason"] == "reference_manifest_unapproved"
    assert result["sync_gate_passed"] is None


def test_public_cli_pins_voice_estimate_artifact_before_source_analysis(tmp_path):
    paths = list(_write_evidence(tmp_path))
    approved = (Path(__file__).parents[2]
                / "docs/research/original-caption-reference-manifests/en-reference.json")
    reference = tmp_path / "approved-manifest-copy.json"
    reference.write_bytes(approved.read_bytes())
    paths[1] = reference

    completed, result = _run_cli(tmp_path, paths, calibration=False)

    assert completed.returncode == 2
    assert result["invalid_reason"] == "voice_span_artifact_unapproved"


def test_public_cli_rejects_blank_pixels_for_claimed_visible_caption(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    screenshot = report["acoustic_capture"]["screenshots"][1]
    screenshot_path = tmp_path / screenshot["path"]
    _screenshot(screenshot_path, "")
    screenshot["sha256"] = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["sync_gate_passed"] is None
    assert result["visual_evidence"]["unverified_transition_count"] > 0


def test_public_cli_rejects_unrelated_ocr_from_caption_crop(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    screenshot = report["acoustic_capture"]["screenshots"][1]
    screenshot_path = tmp_path / screenshot["path"]
    _screenshot(screenshot_path, "sentence 42")
    screenshot["sha256"] = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["visual_evidence"]["unverified_transition_count"] > 0


def test_public_cli_without_pixel_ocr_cannot_assess_caption_transitions(tmp_path):
    paths = _write_evidence(tmp_path)

    completed, result = _run_cli(tmp_path, paths, use_ocr=False)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["sync_gate_passed"] is None
    assert result["visual_evidence"]["unverified_transition_count"] > 0


def test_public_cli_rejects_mismatched_viewport_and_bounds(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    screenshot = report["acoustic_capture"]["screenshots"][1]
    screenshot["viewport_after"]["width"] -= 1
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 2
    assert result["sync_status"] == "measurement_invalid"
    assert result["invalid_reason"] == "screenshot_viewport_mismatch"


def test_public_cli_rejects_untrue_screenshot_pixel_dimensions(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    report["acoustic_capture"]["screenshots"][1]["screenshot_width_px"] -= 1
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 2
    assert result["invalid_reason"] == "screenshot_dimensions_mismatch"


def test_public_cli_rejects_caption_bounds_that_move_during_capture(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    report["acoustic_capture"]["screenshots"][1]["caption_bounds_after"]["x"] += 2
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 2
    assert result["invalid_reason"] == "screenshot_caption_bounds_unstable"


def test_public_cli_rejects_legacy_one_pixel_screenshot(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    screenshot = report["acoustic_capture"]["screenshots"][1]
    screenshot_path = tmp_path / screenshot["path"]
    screenshot_path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(b"\0\xff\xff\xff\xff"))
        + _png_chunk(b"IEND", b""))
    screenshot["sha256"] = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 2
    assert result["invalid_reason"] == "screenshot_viewport_mismatch"


def test_public_cli_malformed_reference_still_writes_invalid_result(tmp_path):
    paths = list(_write_evidence(tmp_path))
    paths[1].write_text("{ malformed", encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 2
    assert result["sync_status"] == "measurement_invalid"
    assert result["invalid_reason"] == "evidence_json_unreadable"
    assert json.loads((tmp_path / "sync-score.json").read_text()) == result


def test_public_cli_keeps_missing_captions_in_voice_coverage_denominator(tmp_path):
    paths = _write_evidence(tmp_path, missing_items={1, 2, 3})

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["coverage"]["voiced_coverage"] < 0.95
    assert result["coverage"]["missing_item_count"] == 3
    assert result["sync_gate_passed"] is None
    assert result["calibration_thresholds_satisfied"] is False


def test_public_cli_matches_split_captions_and_does_not_bridge_visible_gaps(tmp_path):
    paths = _write_evidence(tmp_path, split_first_caption=True)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    first = result["items"][0]
    assert first["status"] == "measured"
    assert len(first["matched_fragments"]) == 2
    assert first["visible_source_intervals"][1][0] > first["visible_source_intervals"][0][1]
    fragment_coverage = sum(
        max(0, min(span_end, visible_end) - max(span_start, visible_start))
        for visible_start, visible_end in first["visible_source_intervals"]
        for span_start, span_end in first["voiced_spans"])
    envelope_start, envelope_end = first["visible_source_interval"]
    envelope_coverage = sum(
        max(0, min(span_end, envelope_end) - max(span_start, envelope_start))
        for span_start, span_end in first["voiced_spans"])
    assert fragment_coverage < envelope_coverage
    expected_coverage = sum(
        max(0, min(span_end, visible_end) - max(span_start, visible_start))
        for row in result["items"] if row["status"] == "measured"
        for visible_start, visible_end in row["visible_source_intervals"]
        for span_start, span_end in row["voiced_spans"])
    assert result["coverage"]["covered_voiced_seconds"] == pytest.approx(expected_coverage)
    assert result["coverage"]["voiced_coverage"] >= 0.95


def test_public_cli_deduplicates_only_exact_ordered_caption_overlap(tmp_path):
    paths = _write_evidence(tmp_path, cumulative_first_caption=True)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 0, completed.stderr + completed.stdout
    first = result["items"][0]
    assert first["status"] == "measured"
    assert [fragment["text"] for fragment in first["matched_fragments"]] == [
        "sentence", "sentence 01"]
    assert first["visible_source_intervals"][1][0] > first["visible_source_intervals"][0][1]


def test_public_cli_requires_a_nearby_pixel_verified_caption_end(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    report["acoustic_capture"]["screenshots"] = [
        item for item in report["acoustic_capture"]["screenshots"]
        if not (item["visible"] is False and item["observed_after_ms"] == 800)
    ]
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["items"][0]["status"] == "caption_disappearance_unobserved"


def test_public_cli_requires_previous_caption_absent_from_end_crop(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    screenshot = next(item for item in report["acoustic_capture"]["screenshots"]
                      if item["visible"] is False and item["observed_after_ms"] == 800)
    screenshot_path = tmp_path / screenshot["path"]
    _screenshot(screenshot_path, "sentence 01")
    screenshot["sha256"] = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["items"][0]["status"] == "caption_disappearance_unobserved"


def test_public_cli_does_not_treat_unrelated_text_as_caption_disappearance(tmp_path):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    screenshot = next(item for item in report["acoustic_capture"]["screenshots"]
                      if item["visible"] is False and item["observed_after_ms"] == 800)
    screenshot_path = tmp_path / screenshot["path"]
    _screenshot(screenshot_path, "sentence 42")
    screenshot["sha256"] = hashlib.sha256(screenshot_path.read_bytes()).hexdigest()
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["calibration_thresholds_satisfied"] is False
    assert result["items"][0]["status"] == "caption_disappearance_unobserved"


def test_public_cli_fails_closed_when_waveform_does_not_support_voice_spans(tmp_path):
    paths = _write_evidence(tmp_path, quiet_capture=True)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["audio_alignment"]["status"] == "unmatched"


def test_public_cli_redacts_secrets_from_automated_method_metadata(tmp_path):
    paths = list(_write_evidence(tmp_path))
    spans = json.loads(paths[2].read_text())
    spans["method"]["parameters"] = {"api_key": "TEST_ONLY_SECRET_VALUE"}
    paths[2].write_text(json.dumps(spans), encoding="utf-8")

    completed, _result = _run_cli(tmp_path, paths)

    assert "TEST_ONLY_SECRET_VALUE" not in completed.stdout
    assert "TEST_ONLY_SECRET_VALUE" not in (tmp_path / "sync-score.json").read_text()


def test_public_cli_fails_closed_when_span_uncertainty_exceeds_tolerance(tmp_path):
    paths = _write_evidence(tmp_path, uncertainty=0.6)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["unknown_item_count"] == 50


def test_public_cli_counts_missing_voice_estimates_as_unknown_denominator(tmp_path):
    paths = _write_evidence(tmp_path, missing_voice_items={1, 2})

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["unknown_item_count"] == 2
    assert result["coverage"]["voiced_seconds"] >= 30.79


def test_public_cli_uses_full_item_denominator_for_uncertain_partial_voice_spans(tmp_path):
    paths = _write_evidence(tmp_path, uncertainty=0.6)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["coverage"]["voiced_seconds"] >= 50.0


def test_public_cli_clamps_only_submicrosecond_span_rounding(tmp_path):
    paths = list(_write_evidence(tmp_path))
    spans = json.loads(paths[2].read_text(encoding="utf-8"))
    spans["items"][0]["spans"][0]["end_seconds"] = 1.0000005
    paths[2].write_text(json.dumps(spans), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result.get("invalid_reason") != "voice_span_bounds_invalid"


def test_public_cli_rejects_voice_span_more_than_one_microsecond_outside_item(tmp_path):
    paths = list(_write_evidence(tmp_path))
    spans = json.loads(paths[2].read_text(encoding="utf-8"))
    spans["items"][0]["spans"][0]["end_seconds"] = 1.0000011
    paths[2].write_text(json.dumps(spans), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 2
    assert result["invalid_reason"] == "voice_span_bounds_invalid"


def test_public_cli_does_not_score_dom_only_when_pixel_transition_is_missing(tmp_path):
    paths = _write_evidence(tmp_path, missing_screenshots=True)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["visual_evidence"]["unverified_transition_count"] > 0


def test_public_cli_detects_a_frozen_playback_audio_segment(tmp_path):
    paths = _write_evidence(tmp_path, frozen=True)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["audio_alignment"]["status"] == "discontinuous_or_frozen"
    assert len(result["audio_alignment"]["epochs"]) == 1


def test_public_cli_keeps_sparse_clock_pause_boundary_unknown(tmp_path):
    paths = _write_evidence(tmp_path, lag=0.02, pause_at=25.0, sampling_step_ms=10)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    alignment = result["audio_alignment"]
    assert alignment["status"] == "unmatched"
    assert alignment["reason"] == "playback_epoch_boundary_ambiguous"
    assert len(alignment["epochs"]) == 2
    assert all(epoch["matched_anchor_count"] >= 2 for epoch in alignment["epochs"])
    assert result["unknown_item_count"] == 50


def test_public_cli_counts_all_items_as_unknown_when_alignment_is_unavailable(tmp_path):
    paths = _write_evidence(tmp_path, lag=0.02, pause_at=25.5, sampling_step_ms=10)
    spans = json.loads(paths[2].read_text(encoding="utf-8"))
    spans["items"][0]["spans"][0]["start_uncertainty_seconds"] = 0.6
    paths[2].write_text(json.dumps(spans), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["sync_gate_passed"] is None
    assert result["release_gate_passed"] is False
    assert result["audio_alignment"]["reason"] == "playback_epoch_boundary_ambiguous"
    assert result["samples"] == 50
    assert result["measured_item_count"] == 0
    assert result["missing_item_count"] == 0
    assert result["unknown_item_count"] == 50
    assert (result["measured_item_count"] + result["missing_item_count"]
            + result["unknown_item_count"]) == result["samples"]


def test_public_cli_rejects_a_pause_epoch_with_only_one_audio_anchor(tmp_path):
    paths = _write_evidence(tmp_path, pause_at=45.0,
                            mute_after_pause_media_time=46.0)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["sync_gate_passed"] is None
    alignment = result["audio_alignment"]
    assert alignment["status"] == "unmatched"
    assert alignment["matched_anchor_count"] >= alignment["required_anchor_count"]
    assert alignment["epochs"][1]["matched_anchor_count"] == 1


def test_public_cli_does_not_map_a_caption_transition_inside_a_pause(tmp_path):
    paths = _write_evidence(tmp_path, pause_at=25.0, caption_during_pause=True)

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 1
    assert result["sync_status"] == "calibration_only"
    assert result["calibration_thresholds_satisfied"] is False
    assert result["audio_alignment"]["status"] == "unmatched"
    assert result["audio_alignment"]["reason"] == "playback_epoch_boundary_ambiguous"
    assert result["unknown_item_count"] == 50


@pytest.mark.parametrize("conflicting", [
    pytest.param(False, id="identical"), pytest.param(True, id="same-time-different-context")])
def test_public_cli_rejects_conflicting_clock_duplicates_but_deduplicates_exact_ones(
        tmp_path, conflicting):
    paths = list(_write_evidence(tmp_path))
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    samples = report["acoustic_capture"]["audio"]["output_clock_samples"]
    original_count = len(samples)
    duplicate = samples[5].copy()
    if conflicting:
        duplicate["context_time_seconds"] += 0.001
    samples.insert(6, duplicate)
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    completed, result = _run_cli(tmp_path, paths)

    if conflicting:
        assert completed.returncode == 2
        assert result["sync_status"] == "measurement_invalid"
        assert result["invalid_reason"] == "output_clock_samples_invalid"
    else:
        assert completed.returncode == 0, completed.stderr + completed.stdout
        assert result["calibration_thresholds_satisfied"] is True
        assert result["audio_alignment"]["output_clock_sample_count"] == original_count


@pytest.mark.parametrize("bad_field", ["source_sha256", "capture_sha256", "screenshot_sha256"])
def test_public_cli_rejects_mismatched_evidence_hashes(tmp_path, bad_field):
    paths = list(_write_evidence(tmp_path))
    if bad_field == "source_sha256":
        data = json.loads(paths[2].read_text())
        data["source_sha256"] = "0" * 64
        paths[2].write_text(json.dumps(data))
    else:
        data = json.loads(paths[4].read_text())
        if bad_field == "capture_sha256":
            data["acoustic_capture"]["audio"]["sha256"] = "0" * 64
        else:
            data["acoustic_capture"]["screenshots"][0]["sha256"] = "0" * 64
        paths[4].write_text(json.dumps(data))

    completed, result = _run_cli(tmp_path, paths)

    assert completed.returncode == 2
    assert result["sync_status"] == "measurement_invalid"
    assert result["sync_gate_passed"] is None


def test_public_cli_requires_anchor_coverage_of_distinct_items(tmp_path):
    missing = [i for i in range(1, 51) if i > 41 or i % 2 == 0]
    paths = _write_evidence(tmp_path, missing_voice_items=missing)
    spans = json.loads(paths[2].read_text(encoding="utf-8"))
    for item in spans["items"]:
        if not item["spans"]:
            continue
        span = item["spans"][0]
        midpoint = (span["start_seconds"] + span["end_seconds"]) / 2
        item["spans"] = [dict(span, end_seconds=midpoint - 0.2),
                         dict(span, start_seconds=midpoint + 0.2)]
    paths[2].write_text(json.dumps(spans), encoding="utf-8")

    _, result = _run_cli(tmp_path, paths)

    alignment = result["audio_alignment"]
    assert alignment["status"] == "unmatched"
    assert alignment["matched_anchor_count"] >= alignment["required_anchor_count"]
    assert alignment["matched_item_count"] == 21
    assert result["calibration_thresholds_satisfied"] is False


def test_public_cli_rejects_unmarked_forward_jump_after_last_anchor(tmp_path):
    paths = _write_evidence(tmp_path)
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    for sample in report["timeline_samples"]:
        if sample["observed_after_ms"] >= 49900:
            sample["media_time"] += 2
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    _, result = _run_cli(tmp_path, paths)

    assert result["audio_alignment"]["status"] == "discontinuous_or_frozen"
    assert result["calibration_thresholds_satisfied"] is False


def test_public_cli_rejects_replayed_audio_across_pause_boundary(tmp_path):
    paths = _write_evidence(tmp_path, pause_at=25.0)
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    for sample in report["timeline_samples"]:
        if sample["observed_after_ms"] >= 25600:
            sample["media_time"] -= 1
    capture = tmp_path / report["acoustic_capture"]["audio"]["path"]
    source = np.frombuffer(paths[0].read_bytes()[44:], dtype="<i2")
    replayed = np.concatenate((source[:25 * RATE], np.zeros(round(0.6 * RATE), dtype=np.int16),
                               source[24 * RATE:-RATE]))
    _write_wave_samples(capture, replayed)
    report["acoustic_capture"]["audio"]["sha256"] = hashlib.sha256(capture.read_bytes()).hexdigest()
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    _, result = _run_cli(tmp_path, paths)

    assert result["audio_alignment"]["status"] == "discontinuous_or_frozen"
    assert result["audio_alignment"]["reason"] == "playback_epoch_source_time_repeated_or_reversed"
    assert result["calibration_thresholds_satisfied"] is False


def test_public_cli_does_not_extrapolate_epoch_boundary_clock(tmp_path):
    paths = _write_evidence(tmp_path)
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    report["acoustic_capture"]["audio"]["output_clock_samples"].pop()
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    _, result = _run_cli(tmp_path, paths)

    assert result["audio_alignment"]["status"] == "unmatched"
    assert result["audio_alignment"]["reason"] == "playback_epoch_boundary_clock_missing"
    assert result["calibration_thresholds_satisfied"] is False


def test_public_cli_rejects_forward_jump_after_playback_slowdown(tmp_path):
    paths = _write_evidence(tmp_path)
    report = json.loads(paths[4].read_text(encoding="utf-8"))
    for sample in report["timeline_samples"]:
        observed = sample["observed_after_ms"]
        if 49700 < observed < 49950:
            sample["media_time"] = 49.7 + (observed - 49700) * 0.4 / 1000
        elif observed >= 49950:
            sample["media_time"] = 49.93 + (observed - 49950) / 1000
    paths[4].write_text(json.dumps(report), encoding="utf-8")

    _, result = _run_cli(tmp_path, paths)

    assert result["audio_alignment"]["status"] == "discontinuous_or_frozen"
    assert result["audio_alignment"]["reason"] == "unmarked_playback_time_forward_jump"
    assert result["calibration_thresholds_satisfied"] is False
