"""Score browser-visible captions against independent voice spans and captured audio."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import struct
import zlib

import av
import numpy as np

from score_original_acceptance import edit_distance, tokens
from run_original_acceptance import redact_recording


SYNC_SAMPLE_MINIMUM = 50
P95_LIMIT_SECONDS = 0.5
MAXIMUM_LIMIT_SECONDS = 1.0
COVERAGE_MINIMUM = 0.95
MAX_EDGE_UNCERTAINTY_SECONDS = 0.5
MIN_CORRELATION = 0.55
MIN_CORRELATION_MARGIN = 0.03
ALIGNMENT_WINDOW_SECONDS = 0.8
ALIGNMENT_SEARCH_SECONDS = 1.0
FEATURE_FRAME_SECONDS = 0.02
SCREENSHOT_MATCH_LIMIT_MS = 100
BOUNDARY_SERIALIZATION_TOLERANCE_SECONDS = 0.000001
MAX_FRAGMENT_GROUP_EPISODES = 8
MAX_ITEM_WINDOW_SKEW_SECONDS = 2.0
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
APPROVED_INPUTS = {
    "zh": {"manifest_sha256": "fd511cd47f6221787f7325d6a935bd7bcf9bface55006822238a89388c8668b1",
           "source_sha256": "d40bb3a49d88cf35f52574650d593573048f89f95aba09b704b2fdbe25811317",
           "voice_spans_sha256": "ed5c5f77c2010809cbc3c7143dd44ad1bcd693a09d17587fb18da3c09a7e4aa6",
           "item_count": 59},
    "en": {"manifest_sha256": "d422460296e400bbc86a0910a90afd4e727cfc62ba19f7f66324fe58211489c6",
           "source_sha256": "4b7d55148fed309e1d71d941d75e89a64b1068a1473163f5f6a9ac4ab6fd7523",
           "voice_spans_sha256": "8c3979428b5b591365630d6978acb672307447252f5dcd7e9cb475268f6a4bb3",
           "item_count": 67},
}
PIXEL_TEXT_SIMILARITY_MINIMUM = 0.65
OCR_TIMEOUT_SECONDS = 10


class EvidenceError(ValueError):
    """Evidence cannot be scored safely or consistently."""


def _validate_approved_artifacts(reference_path, source_spans_path, language):
    approved = APPROVED_INPUTS[language]
    if _sha256(reference_path) != approved["manifest_sha256"]:
        raise EvidenceError("reference_manifest_unapproved")
    reference = _read_json(reference_path)
    if (not isinstance(reference, dict)
            or reference.get("sha256") != approved["source_sha256"]
            or not isinstance(reference.get("items"), list)
            or len(reference["items"]) != approved["item_count"]):
        raise EvidenceError("reference_manifest_unapproved")
    if _sha256(source_spans_path) != approved["voice_spans_sha256"]:
        raise EvidenceError("voice_span_artifact_unapproved")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _finite(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value))


def _safe_artifact(report_path, value):
    relative = Path(str(value))
    if relative.is_absolute() or ".." in relative.parts:
        raise EvidenceError("artifact_path_invalid")
    root = Path(report_path).resolve().parent
    artifact = (root / relative).resolve()
    if not artifact.is_relative_to(root):
        raise EvidenceError("artifact_path_invalid")
    if not artifact.is_file():
        raise EvidenceError("artifact_missing")
    return artifact


def _read_audio(path, rate, include_timing=False):
    pieces = []
    timing = {}
    try:
        with av.open(str(path)) as container:
            if not container.streams.audio:
                raise EvidenceError("audio_track_missing")
            stream = container.streams.audio[0]
            timing.update({
                "codec": stream.codec_context.name,
                "time_base": str(stream.time_base),
                "stream_start_pts": stream.start_time,
                "stream_duration_ticks": stream.duration,
                "decoded_frame_count": 0,
                "decoded_sample_count": 0,
                "first_frame_pts": None,
                "last_frame_pts": None,
                "first_frame_time_seconds": None,
                "last_frame_time_seconds": None,
            })
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
            for frame in container.decode(stream):
                if timing["first_frame_pts"] is None:
                    timing["first_frame_pts"] = frame.pts
                    timing["first_frame_time_seconds"] = (
                        float(frame.pts * frame.time_base) if frame.pts is not None else None)
                timing["last_frame_pts"] = frame.pts
                timing["last_frame_time_seconds"] = (
                    float(frame.pts * frame.time_base) if frame.pts is not None else None)
                timing["decoded_frame_count"] += 1
                timing["decoded_sample_count"] += frame.samples
                pieces.extend(resampler.resample(frame))
            pieces.extend(resampler.resample(None))
    except EvidenceError:
        raise
    except (av.error.FFmpegError, OSError, ValueError) as exc:
        raise EvidenceError("audio_decode_failed") from exc
    if not pieces:
        raise EvidenceError("audio_track_empty")
    audio = np.concatenate([piece.to_ndarray().reshape(-1) for piece in pieces]).astype(np.float32) / 32768
    timing["resampled_rate_hz"] = rate
    timing["resampled_duration_seconds"] = len(audio) / rate
    return (audio, timing) if include_timing else audio


def _load_identity(reference_path, source_path, spans_path, recording_path,
                   report_path, language, calibration=False):
    try:
        reference = json.loads(Path(reference_path).read_text(encoding="utf-8"))
        source_spans = json.loads(Path(spans_path).read_text(encoding="utf-8"))
        recording = json.loads(Path(recording_path).read_text(encoding="utf-8"))
        report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError("evidence_json_unreadable") from exc
    source_hash = _sha256(source_path)
    reference_hash = reference.get("sha256")
    if not isinstance(reference_hash, str) or not SHA256_PATTERN.fullmatch(reference_hash.lower()):
        raise EvidenceError("reference_hash_invalid")
    if source_spans.get("schema_version") != 1:
        raise EvidenceError("voice_span_schema_unsupported")
    if source_hash != reference_hash.lower():
        raise EvidenceError("source_reference_hash_mismatch")
    if recording.get("source_kind") != "local_file" or recording.get("input_sha256") != source_hash:
        raise EvidenceError("recording_input_hash_mismatch")
    if (recording.get("input_unchanged") is not True
            or recording.get("input_sha256_after") != source_hash
            or recording.get("recording_status") != "completed"
            or recording.get("completion_signal") != "end"
            or recording.get("errors")):
        raise EvidenceError("recording_not_complete_and_clean")
    end_observed = any(event.get("type") == "end" and event.get("reason") == "completed"
                       for event in recording.get("events", []))
    if recording.get("simulated_recognition") is not False or not end_observed:
        raise EvidenceError("recording_not_real_completed_session")
    if report.get("recording_status") != "completed" or report.get("language") != language:
        raise EvidenceError("browser_report_identity_mismatch")
    if (report.get("simulated_recognition") is not False
            or report.get("session_post_start_count", 0) < 1
            or report.get("errors")):
        raise EvidenceError("browser_report_not_real_clean_session")
    if report.get("source_verified") is not True:
        raise EvidenceError("browser_report_source_not_verified")
    if report.get("sampling", {}).get("status") != "valid":
        raise EvidenceError("browser_sampling_invalid")
    if not isinstance(report.get("candidate_revision"), str) or not re.fullmatch(
            r"[0-9a-f]{40,64}", report["candidate_revision"].lower()):
        raise EvidenceError("candidate_revision_missing")
    dirty = report.get("candidate_worktree_dirty")
    if dirty is not False and not (calibration and dirty is True):
        raise EvidenceError("candidate_worktree_not_clean")
    if reference.get("language") not in (None, language):
        raise EvidenceError("reference_language_mismatch")
    if source_spans.get("source_sha256") != source_hash:
        raise EvidenceError("voice_span_source_hash_mismatch")
    condition = source_spans.get("source_condition")
    if (condition != "known_clean_single_speaker"
            and not (calibration and condition == "synthetic_tone_fixture")):
        raise EvidenceError("voice_classification_unknown")
    method = source_spans.get("method")
    if not isinstance(method, dict) or not method.get("name") or not method.get("parameters"):
        raise EvidenceError("voice_span_method_missing")
    if not source_spans.get("provenance"):
        raise EvidenceError("voice_span_provenance_missing")
    return reference, source_spans, recording, report, source_hash


def _validate_voice_spans(reference, source_spans):
    reference_items = reference.get("items")
    spans_items = source_spans.get("items")
    if not isinstance(reference_items, list) or not isinstance(spans_items, list):
        raise EvidenceError("voice_span_items_missing")
    if len(reference_items) != len(spans_items):
        raise EvidenceError("voice_span_item_count_mismatch")
    result = []
    seen = set()
    for index, (reference_item, span_item) in enumerate(zip(reference_items, spans_items), 1):
        if span_item.get("item") != index or index in seen:
            raise EvidenceError("voice_span_item_identity_mismatch")
        seen.add(index)
        start = reference_item.get("asset_start_seconds")
        end = reference_item.get("asset_end_seconds")
        if not _finite(start) or not _finite(end) or end <= start:
            raise EvidenceError("reference_item_interval_invalid")
        spans = span_item.get("spans")
        if not isinstance(spans, list):
            raise EvidenceError("voice_span_list_invalid")
        parsed = []
        previous_end = start
        for span in spans:
            begin = span.get("start_seconds")
            finish = span.get("end_seconds")
            begin_uncertainty = span.get("start_uncertainty_seconds")
            end_uncertainty = span.get("end_uncertainty_seconds")
            if not all(_finite(value) for value in
                       (begin, finish, begin_uncertainty, end_uncertainty)):
                raise EvidenceError("voice_span_bounds_invalid")
            if (begin < start - BOUNDARY_SERIALIZATION_TOLERANCE_SECONDS
                    or finish > end + BOUNDARY_SERIALIZATION_TOLERANCE_SECONDS
                    or finish <= begin or begin < previous_end
                    or begin_uncertainty < 0 or end_uncertainty < 0):
                raise EvidenceError("voice_span_bounds_invalid")
            begin = max(begin, start)
            finish = min(finish, end)
            parsed.append({"start": float(begin), "end": float(finish),
                           "start_uncertainty": float(begin_uncertainty),
                           "end_uncertainty": float(end_uncertainty)})
            previous_end = finish
        result.append({"item": index, "reference": reference_item.get("reference", ""),
                       "reference_interval": [float(start), float(end)], "spans": parsed})
    return result


def _check_source_energy(source, rate, items):
    for item in items:
        if not item["spans"]:
            item["energy_check"] = "span_missing"
            continue
        item["energy_check"] = "supported"
        for span in item["spans"]:
            if max(span["start_uncertainty"], span["end_uncertainty"]) > MAX_EDGE_UNCERTAINTY_SECONDS:
                item["energy_check"] = "uncertainty_exceeds_limit"
                break
            length = span["end"] - span["start"]
            core_begin = span["start"] + min(0.1, length / 4)
            core_end = span["end"] - min(0.1, length / 4)
            core = source[max(0, round(core_begin * rate)):min(len(source), round(core_end * rate))]
            if not core.size or float(np.sqrt(np.mean(np.square(core)))) < 0.0005:
                item["energy_check"] = "source_energy_not_supported"
                break
            supported = True
            for edge in (span["start"], span["end"]):
                radius = max(0.02, min(0.1,
                                       span["start_uncertainty"] if edge == span["start"]
                                       else span["end_uncertainty"]) + 0.03)
                left = max(0, round((edge - radius) * rate))
                right = min(len(source), round((edge + radius) * rate))
                local = source[left:right]
                if not local.size or float(np.sqrt(np.mean(np.square(local)))) < 0.00005:
                    supported = False
            if not supported:
                item["energy_check"] = "boundary_not_supported"
                break


def _output_clock(audio):
    samples = audio.get("output_clock_samples")
    if not isinstance(samples, list) or len(samples) < 2:
        raise EvidenceError("output_clock_samples_missing")
    pairs = []
    for sample in samples:
        perf = sample.get("performance_time_after_ms")
        context = sample.get("context_time_seconds")
        if _finite(perf) and _finite(context):
            pairs.append((float(perf), float(context)))
    pairs.sort()
    pairs = list(dict.fromkeys(pairs))
    if len(pairs) < 2 or any(b[0] <= a[0] or b[1] <= a[1]
                             for a, b in zip(pairs, pairs[1:])):
        raise EvidenceError("output_clock_samples_invalid")
    max_gap = max(b[0] - a[0] for a, b in zip(pairs, pairs[1:]))
    if max_gap > 1000:
        raise EvidenceError("output_clock_gap_too_large")
    return pairs, max_gap / 2000


def _context_time_at(perf_ms, clock):
    for before, after in zip(clock, clock[1:]):
        if before[0] <= perf_ms <= after[0]:
            ratio = (perf_ms - before[0]) / (after[0] - before[0])
            return before[1] + ratio * (after[1] - before[1])
    return None


def _bounds(value, viewport, error):
    if not isinstance(value, dict):
        raise EvidenceError(error)
    coordinates = [value.get(key) for key in ("x", "y", "width", "height")]
    if not all(_finite(coordinate) for coordinate in coordinates):
        raise EvidenceError(error)
    x, y, width, height = map(float, coordinates)
    if (x < 0 or y < 0 or width < 2 or height < 2
            or x + width > viewport["width"] or y + height > viewport["height"]):
        raise EvidenceError(error)
    return (math.floor(x), math.floor(y), math.ceil(x + width), math.ceil(y + height))


def _bounds_match(first, second):
    return (first is not None and second is not None
            and all(abs(float(first[key]) - float(second[key])) <= 1
                    for key in ("x", "y", "width", "height")))


def _png_rgb(image):
    height, width, _channels = image.shape
    pixels = b"".join(b"\0" + image[row].tobytes() for row in range(height))

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b""))


def _resolve_ocr(executable, tessdata_dir):
    if not executable or not tessdata_dir:
        return None, None
    candidate = Path(executable).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        command = str(candidate.resolve())
    else:
        command = shutil.which(str(executable))
    if not command:
        return None, None
    data = Path(tessdata_dir).expanduser().resolve()
    if not data.is_dir():
        return None, None
    return command, data


def _ocr_caption(image, crop, executable, tessdata_dir, language):
    x0, y0, x1, y1 = crop
    crop_png = _png_rgb(image[y0:y1, x0:x1, :3])
    languages = "chi_sim+eng" if language == "zh" else "eng"
    try:
        completed = subprocess.run(
            [executable, "stdin", "stdout", "-l", languages,
             "--tessdata-dir", str(tessdata_dir), "--psm", "6"],
            input=crop_png, capture_output=True, timeout=OCR_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode:
        return None
    return completed.stdout.decode("utf-8", errors="replace").strip()


def _text_matches(visible_text, ocr_text, language):
    expected, observed = tokens(visible_text, language), tokens(ocr_text, language)
    if not expected or not observed:
        return False
    similarity = 1 - edit_distance(expected, observed) / max(len(expected), len(observed))
    return similarity >= PIXEL_TEXT_SIMILARITY_MINIMUM


def _screen_artifacts(report_path, capture, language, ocr_executable, tessdata_dir):
    screenshots = capture.get("screenshots")
    if not isinstance(screenshots, list):
        raise EvidenceError("visual_evidence_missing")
    executable, data_dir = _resolve_ocr(ocr_executable, tessdata_dir)
    verified = []
    ocr_cache = {}
    for item in screenshots:
        path = _safe_artifact(report_path, item.get("path", ""))
        if _sha256(path) != item.get("sha256"):
            raise EvidenceError("screenshot_hash_mismatch")
        try:
            with av.open(str(path), format="png_pipe") as image:
                frame = next(image.decode(video=0), None)
                if frame is None or not frame.width or not frame.height:
                    raise EvidenceError("screenshot_format_invalid")
                pixels = frame.to_ndarray(format="rgb24")
        except (av.error.FFmpegError, OSError, ValueError) as exc:
            raise EvidenceError("screenshot_unreadable") from exc
        if (not _finite(item.get("observed_after_ms"))
                or not _finite(item.get("uncertainty_ms"))
                or item.get("uncertainty_ms") < 0
                or not isinstance(item.get("visible"), bool)
                or not isinstance(item.get("visible_text"), str)):
            raise EvidenceError("screenshot_metadata_invalid")
        viewport_before, viewport_after = (item.get("viewport_before"),
                                           item.get("viewport_after"))
        if (not isinstance(viewport_before, dict) or not isinstance(viewport_after, dict)
                or viewport_before != viewport_after
                or not all(isinstance(viewport_before.get(key), int)
                           and not isinstance(viewport_before.get(key), bool)
                           and viewport_before[key] >= 2 for key in ("width", "height"))
                or pixels.shape[1] != viewport_before["width"]
                or pixels.shape[0] != viewport_before["height"]):
            raise EvidenceError("screenshot_viewport_mismatch")
        if (item.get("screenshot_width_px") != pixels.shape[1]
                or item.get("screenshot_height_px") != pixels.shape[0]):
            raise EvidenceError("screenshot_dimensions_mismatch")
        if (item.get("device_scale_factor_before") != 1
                or item.get("device_scale_factor_after") != 1):
            raise EvidenceError("screenshot_device_scale_invalid")
        crop_value = item.get("ocr_crop_bounds")
        crop = _bounds(crop_value, viewport_before, "screenshot_bounds_invalid")
        if item["visible"]:
            before_bounds, after_bounds = (item.get("caption_bounds_before"),
                                           item.get("caption_bounds_after"))
            if (item.get("ocr_crop_source") != "caption"
                    or not _bounds_match(before_bounds, after_bounds)
                    or not _bounds_match(after_bounds, crop_value)):
                raise EvidenceError("screenshot_caption_bounds_unstable")
        elif item.get("ocr_crop_source") != "last_visible_caption":
            raise EvidenceError("screenshot_disappearance_bounds_missing")
        crop_png = _png_rgb(pixels[crop[1]:crop[3], crop[0]:crop[2], :3])
        crop_hash = hashlib.sha256(crop_png).hexdigest()
        if crop_hash not in ocr_cache:
            recognized = (_ocr_caption(pixels, crop, executable, data_dir, language)
                          if executable else None)
            if recognized is None:
                executable = None
            ocr_cache[crop_hash] = recognized
        ocr_text = ocr_cache[crop_hash]
        verified.append({**item, "_ocr_text": ocr_text,
                         "_pixel_text_match": (_text_matches(item["visible_text"],
                                                              ocr_text or "", language)
                                               if item["visible"] else None)})
    return verified


def _caption_episodes(report, screenshots, clock, audio, language):
    samples = report.get("timeline_samples")
    if not isinstance(samples, list) or len(samples) < 2:
        raise EvidenceError("visible_timeline_missing")
    sampling = report["sampling"]
    sample_uncertainty = float(sampling.get("max_observed_gap_ms", 100)) / 2000
    start_context = audio.get("start_context_time_seconds")
    start_capture = audio.get("start_performance_after_ms")
    if not _finite(start_context) or not _finite(start_capture):
        raise EvidenceError("audio_clock_origin_missing")
    episodes = []
    active = None
    transitions = []
    previous = ("", False)
    for sample in samples:
        observed = sample.get("observed_after_ms")
        if not _finite(observed):
            continue
        text = str(sample.get("visible_text") or "")
        visible = sample.get("visible") is True and bool(text.strip())
        state = (text if visible else "", visible)
        if state != previous:
            transitions.append({"time_ms": float(observed), "text": state[0], "visible": visible})
            if active is not None:
                active["end_ms"] = float(observed)
                episodes.append(active)
                active = None
            if visible:
                active = {"text": text, "start_ms": float(observed), "start_uncertainty": 0.0,
                          "end_ms": None, "end_uncertainty": 0.0}
            previous = state
        elif active is not None:
            active["end_uncertainty"] = sample_uncertainty
    if active is not None:
        episodes.append(active)
    screenshots_by_state = []
    for index, screenshot in enumerate(screenshots):
        screenshots_by_state.append({
            "index": index,
            "time_ms": float(screenshot["observed_after_ms"]),
            "text": screenshot["visible_text"] if screenshot["visible"] else "",
            "visible": screenshot["visible"],
            "uncertainty": float(screenshot["uncertainty_ms"]) / 1000,
            "ocr_text": screenshot["_ocr_text"],
            "pixel_text_match": screenshot["_pixel_text_match"],
            "crop_bounds": screenshot["ocr_crop_bounds"],
        })
    verified_transitions = []
    unused_screenshots = {screen["index"] for screen in screenshots_by_state}
    for transition_index, transition in enumerate(transitions):
        previous_caption = next((row["text"] for row in reversed(transitions[:transition_index])
                                 if row["visible"]), None)
        previous_caption_screen = next((screen for screen in reversed(screenshots_by_state)
                                        if previous_caption and screen["visible"]
                                        and screen["text"] == previous_caption
                                        and screen["time_ms"] <= transition["time_ms"]), None)
        matching = [screen for screen in screenshots_by_state
                    if screen["index"] in unused_screenshots
                    and screen["text"] == transition["text"]
                    and screen["visible"] == transition["visible"]
                    and (not transition["visible"] or screen["pixel_text_match"])
                    and (transition["visible"] or not previous_caption
                         or (previous_caption_screen is not None
                             and screen["ocr_text"] is not None
                             and _bounds_match(screen["crop_bounds"],
                                               previous_caption_screen["crop_bounds"])
                             and not tokens(screen["ocr_text"], language)))
                    and abs(screen["time_ms"] - transition["time_ms"])
                    <= SCREENSHOT_MATCH_LIMIT_MS]
        if not matching:
            continue
        selected = min(matching, key=lambda screen: abs(screen["time_ms"] - transition["time_ms"]))
        unused_screenshots.remove(selected["index"])
        # The screenshot timestamp is the visual observation. DOM samples bound its
        # transition, but they are not substituted for the saved pixel evidence.
        screen_time_ms = selected["time_ms"]
        transition_time_ms = transition["time_ms"]
        context = _context_time_at(transition_time_ms, clock)
        if context is None:
            continue
        epoch = next((candidate for candidate in audio["epochs"]
                      if candidate["start_performance_after_ms"] <= transition_time_ms
                      <= candidate["end_performance_after_ms"]), None)
        if epoch is None:
            continue
        capture_time = context - float(start_context)
        source_time = capture_time + epoch["source_offset_seconds"]
        uncertainty_components = {
            "timeline_sampling_half_gap_seconds": sample_uncertainty,
            "screenshot_capture_bracket_seconds": selected["uncertainty"],
            "screenshot_to_sample_transition_seconds": abs(
                screen_time_ms - transition_time_ms) / 1000,
            "performance_context_clock_origin_seconds": audio["clock_uncertainty_seconds"],
            "output_timestamp_interpolation_seconds": audio["output_clock_uncertainty_seconds"],
            "source_capture_alignment_seconds": epoch["alignment_uncertainty_seconds"],
            "reported_base_and_output_latency_seconds": audio["latency_bound_seconds"],
        }
        uncertainty = sum(uncertainty_components.values())
        verified_transitions.append({"time_ms": transition_time_ms,
                                     "screenshot_observed_after_ms": screen_time_ms,
                                     "playback_epoch_index": epoch["index"],
                                     "text": transition["text"],
                                     "visible": transition["visible"],
                                     "pixel_verification": (
                                         "caption_text_match" if transition["visible"]
                                         else "previous_caption_absent"),
                                     "source_time": source_time,
                                     "uncertainty_seconds": uncertainty,
                                     "uncertainty_components": uncertainty_components})
    return episodes, transitions, verified_transitions


def _align_capture(source, capture, rate, items, report, audio):
    if source.size < rate or capture.size < rate:
        raise EvidenceError("audio_capture_too_short")
    start_context = audio.get("start_context_time_seconds")
    source_rate = float(audio.get("sample_rate_hz") or rate)
    if not _finite(start_context) or source_rate <= 0:
        raise EvidenceError("audio_capture_rate_mismatch")
    if not _finite(audio.get("start_performance_after_ms")):
        raise EvidenceError("audio_clock_origin_missing")
    clock_uncertainty = audio.get("clock_uncertainty_ms")
    base_latency = audio.get("base_latency_seconds")
    output_latency = audio.get("output_latency_seconds")
    if not all(_finite(value) and value >= 0 for value in
               (clock_uncertainty, base_latency, output_latency)):
        raise EvidenceError("audio_latency_bounds_missing")
    clock_pairs, output_clock_uncertainty = _output_clock(audio)
    source_features = _spectral_features(source, rate)
    capture_features = _spectral_features(capture, rate)
    timeline = report["timeline_samples"]
    required = min(len(items), max(10, math.ceil(len(items) * 0.8)))
    epochs = []
    epoch_by_time = {}
    active_epoch = None
    previous_observed = None
    previous_media_time = None
    timeline_error = None
    for sample in timeline:
        observed = sample.get("observed_after_ms")
        if not _finite(observed):
            continue
        observed = float(observed)
        if previous_observed is not None and observed <= previous_observed:
            timeline_error = ("unmatched", "timeline_observation_time_not_increasing")
            break
        elapsed = (observed - previous_observed) / 1000 if previous_observed is not None else 0
        previous_observed = observed
        if sample.get("paused") or sample.get("seeking"):
            active_epoch = None
            previous_media_time = None
            continue
        media_time = sample.get("media_time")
        if not _finite(media_time):
            timeline_error = ("unmatched", "playback_epoch_media_time_missing")
            break
        media_time = float(media_time)
        if active_epoch is None:
            active_epoch = {
                "index": len(epochs), "start_performance_after_ms": observed,
                "end_performance_after_ms": observed, "start_media_time": media_time,
            }
            epochs.append(active_epoch)
        elif previous_media_time is not None and media_time <= previous_media_time:
            timeline_error = ("discontinuous_or_frozen",
                              "unmarked_playback_time_repeat_or_reverse")
            break
        elif (media_time - previous_media_time > elapsed + 2 * float(clock_uncertainty) / 1000
              or media_time - active_epoch["start_media_time"]
              > (observed - active_epoch["start_performance_after_ms"]) / 1000
              + 2 * float(clock_uncertainty) / 1000):
            timeline_error = ("discontinuous_or_frozen", "unmarked_playback_time_forward_jump")
            break
        else:
            active_epoch["end_performance_after_ms"] = observed
        previous_media_time = media_time
        epoch_by_time[observed] = active_epoch["index"]
    if timeline_error:
        status, reason = timeline_error
        return {"status": status, "method": "bounded_spectral_feature_cross_correlation",
                "matched_anchor_count": 0, "required_anchor_count": required,
                "source_offset_seconds": None, "residual_seconds": None,
                "reason": reason, "epochs": epochs}
    anchors = []
    used_capture_buckets = set()
    for item in items:
        if item.get("energy_check") != "supported":
            continue
        for span in item["spans"]:
            target = (span["start"] + span["end"]) / 2
            choices = [sample for sample in timeline
                       if _finite(sample.get("observed_after_ms"))
                       and _finite(sample.get("media_time"))
                       and not sample.get("paused") and not sample.get("seeking")
                       and not sample.get("buffering")
                       and span["start"] <= sample["media_time"] <= span["end"]]
            if not choices:
                continue
            selected = min(choices, key=lambda sample: abs(sample["media_time"] - target))
            epoch_index = epoch_by_time.get(float(selected["observed_after_ms"]))
            if epoch_index is None:
                continue
            context = _context_time_at(float(selected["observed_after_ms"]), clock_pairs)
            if context is None:
                continue
            capture_time = context - float(start_context)
            bucket = round(capture_time / 0.5)
            if bucket in used_capture_buckets:
                continue
            used_capture_buckets.add(bucket)
            best = _correlate(source_features, capture_features, rate, capture_time,
                              float(selected["media_time"]))
            if best is not None:
                best["item"] = item["item"]
                best["epoch_index"] = epoch_index
                best["capture_time"] = capture_time
                anchors.append(best)
    anchors_by_epoch = {
        epoch["index"]: [anchor for anchor in anchors
                         if anchor["epoch_index"] == epoch["index"]]
        for epoch in epochs
    }
    unanchored_epochs = []
    for epoch in epochs:
        phase_anchors = anchors_by_epoch[epoch["index"]]
        epoch["matched_anchor_count"] = len(phase_anchors)
        if len(phase_anchors) < 2:
            unanchored_epochs.append(epoch["index"])
    matched_item_count = len({anchor["item"] for anchor in anchors})
    if matched_item_count < required:
        return {"status": "unmatched", "method": "bounded_spectral_feature_cross_correlation",
                "matched_anchor_count": len(anchors), "matched_item_count": matched_item_count,
                "required_anchor_count": required, "source_offset_seconds": None,
                "residual_seconds": None, "epochs": epochs}
    if unanchored_epochs:
        return {"status": "unmatched", "method": "bounded_spectral_feature_cross_correlation",
                "matched_anchor_count": len(anchors), "required_anchor_count": required,
                "source_offset_seconds": None, "residual_seconds": None,
                "unanchored_epoch_indices": unanchored_epochs, "epochs": epochs}
    residuals = []
    for epoch in epochs:
        phase_anchors = anchors_by_epoch[epoch["index"]]
        offsets = [anchor["offset"] for anchor in phase_anchors]
        median_offset = statistics.median(offsets)
        residual = max(abs(value - median_offset) for value in offsets)
        epoch["source_offset_seconds"] = median_offset
        epoch["residual_seconds"] = residual
        epoch["alignment_uncertainty_seconds"] = max(residual, FEATURE_FRAME_SECONDS / 2)
        residuals.append(residual)
        if residual > 0.1:
            return {"status": "discontinuous_or_frozen",
                    "method": "bounded_spectral_feature_cross_correlation",
                    "matched_anchor_count": len(anchors), "required_anchor_count": required,
                    "source_offset_seconds": None, "residual_seconds": residual,
                    "residual_scope": "within_playback_epoch",
                    "epochs": epochs}
        boundary_times = [_context_time_at(epoch[key], clock_pairs)
                          for key in ("start_performance_after_ms", "end_performance_after_ms")]
        if any(value is None for value in boundary_times):
            return {"status": "unmatched", "method": "bounded_spectral_feature_cross_correlation",
                    "matched_anchor_count": len(anchors), "required_anchor_count": required,
                    "source_offset_seconds": None, "residual_seconds": residual,
                    "reason": "playback_epoch_boundary_clock_missing", "epochs": epochs}
        epoch["source_start_seconds"] = boundary_times[0] - float(start_context) + median_offset
        epoch["source_end_seconds"] = boundary_times[1] - float(start_context) + median_offset
    for previous, following in zip(epochs, epochs[1:]):
        difference = following["source_start_seconds"] - previous["source_end_seconds"]
        uncertainty = (following["alignment_uncertainty_seconds"]
                       + previous["alignment_uncertainty_seconds"]
                       + 2 * (float(clock_uncertainty) / 1000 + output_clock_uncertainty))
        if difference <= uncertainty:
            repeated = difference < -uncertainty
            return {"status": "discontinuous_or_frozen" if repeated else "unmatched",
                    "method": "bounded_spectral_feature_cross_correlation",
                    "matched_anchor_count": len(anchors), "required_anchor_count": required,
                    "source_offset_seconds": None, "residual_seconds": max(residuals),
                    "reason": ("playback_epoch_source_time_repeated_or_reversed" if repeated
                               else "playback_epoch_boundary_ambiguous"),
                    "epochs": epochs}
    residual = max(residuals)
    alignment_uncertainty = max(residual, FEATURE_FRAME_SECONDS / 2)
    output_clock_gaps = [after[0] - before[0]
                         for before, after in zip(clock_pairs, clock_pairs[1:])]
    return {"status": "aligned", "method": "bounded_spectral_feature_cross_correlation",
            "matched_anchor_count": len(anchors), "matched_item_count": matched_item_count,
            "required_anchor_count": required,
            "source_offset_seconds": (epochs[0]["source_offset_seconds"]
                                      if len(epochs) == 1 else None),
            "residual_seconds": residual,
            "residual_scope": "within_playback_epoch",
            "epochs": epochs,
            "median_correlation": statistics.median(anchor["correlation"] for anchor in anchors),
            "clock_uncertainty_seconds": float(clock_uncertainty) / 1000,
            "output_clock_uncertainty_seconds": output_clock_uncertainty,
            "output_clock_sample_count": len(clock_pairs),
            "output_clock_first_sample": {
                "performance_time_after_ms": clock_pairs[0][0],
                "context_time_seconds": clock_pairs[0][1]},
            "output_clock_last_sample": {
                "performance_time_after_ms": clock_pairs[-1][0],
                "context_time_seconds": clock_pairs[-1][1]},
            "output_clock_max_gap_ms": max(output_clock_gaps),
            "audio_capture_element": audio.get("media_element_id"),
            "base_latency_seconds": float(base_latency),
            "output_latency_seconds": float(output_latency),
            "latency_mapping": "getOutputTimestamp maps rendered output to performance time",
            "latency_bound_seconds": float(base_latency) + float(output_latency),
            "codec_delay_disposition": (
                "decoder PTS is post-decode; source alignment absorbs the effective Opus pre-skip "
                "and recorder-start offset, which cannot be separated here"),
            "alignment_uncertainty_seconds": alignment_uncertainty,
            "physical_output_latency": report.get("acoustic_capture", {}).get(
                "physical_output_latency", "unknown")}


def _correlate(source, capture, rate, capture_time, player_time):
    hop = round(FEATURE_FRAME_SECONDS * rate)
    size = round(ALIGNMENT_WINDOW_SECONDS * rate / hop)
    capture_start = round((capture_time - ALIGNMENT_WINDOW_SECONDS / 2) * rate / hop)
    if capture_start < 0 or capture_start + size > len(capture):
        return None
    observed = capture[capture_start:capture_start + size]
    if float(np.linalg.norm(observed)) < 0.1:
        return None
    center = round((player_time - ALIGNMENT_WINDOW_SECONDS / 2) * rate / hop)
    radius = round(ALIGNMENT_SEARCH_SECONDS * rate / hop)
    candidates = []
    for source_start in range(max(0, center - radius), min(len(source) - size, center + radius) + 1):
        expected = source[source_start:source_start + size]
        correlation = float(np.mean(np.sum(observed * expected, axis=1)))
        capture_window_start = capture_time - ALIGNMENT_WINDOW_SECONDS / 2
        candidates.append((correlation, source_start * hop / rate - capture_window_start))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    best_correlation, best_offset = candidates[0]
    second = next((score for score, offset in candidates[1:]
                  if abs(offset - best_offset) > 0.1), -1.0)
    if best_correlation < MIN_CORRELATION or best_correlation - second < MIN_CORRELATION_MARGIN:
        return None
    return {"correlation": best_correlation, "offset": best_offset}


def _spectral_features(audio, rate):
    """Build normalized 20 ms band-energy features resilient to browser Opus encoding."""
    frame_size = max(80, round(rate * FEATURE_FRAME_SECONDS))
    if len(audio) < frame_size:
        return np.empty((0, 7), dtype=np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(audio, frame_size)[::frame_size]
    spectrum = np.abs(np.fft.rfft(
        frames * np.hanning(frame_size).astype(np.float32), n=512, axis=1)) ** 2
    frequencies = np.fft.rfftfreq(512, 1 / rate)
    upper = min(7900, rate / 2 - rate / 512)
    edges = np.geomspace(80, upper, 8)
    bands = tuple(zip(edges[:-1], edges[1:]))
    features = np.stack([
        np.log(np.mean(spectrum[:, (frequencies >= low) & (frequencies < high)], axis=1) + 1e-9)
        for low, high in bands
    ], axis=1)
    features -= features.mean(axis=1, keepdims=True)
    norms = np.linalg.norm(features, axis=1, keepdims=True)
    return (features / np.maximum(norms, 1e-9)).astype(np.float32)


def _episode_mapping(items, episodes, transitions, verified_transitions, language):
    translated_episodes = []
    for episode in episodes:
        start = next((row for row in verified_transitions
                      if row["visible"] and row["text"] == episode["text"]
                      and abs(row["time_ms"] - episode["start_ms"])
                      <= SCREENSHOT_MATCH_LIMIT_MS), None)
        end_candidates = [row for row in verified_transitions
                          if (not row["visible"] or row["text"] != episode["text"])
                          and episode["end_ms"] is not None
                          and abs(row["time_ms"] - episode["end_ms"])
                          <= SCREENSHOT_MATCH_LIMIT_MS]
        end = (min(end_candidates, key=lambda row: abs(row["time_ms"] - episode["end_ms"]),
                    default=None) if episode["end_ms"] is not None else None)
        if start is None:
            continue
        episode_copy = dict(episode)
        episode_copy["source_start"] = start["source_time"]
        episode_copy["source_end"] = end["source_time"] if end else None
        episode_copy["start_uncertainty"] = start["uncertainty_seconds"]
        if end:
            episode_copy["end_uncertainty"] = end["uncertainty_seconds"]
        translated_episodes.append(episode_copy)

    unmatched = set(range(len(translated_episodes)))
    mappings = []
    for item in items:
        if item.get("energy_check") != "supported" or not item["spans"]:
            mappings.append({"item": item["item"], "status": "voice_span_unknown"})
            continue
        reference = tokens(item["reference"], language)
        voice_start = item["spans"][0]
        voice_end = item["spans"][-1]
        window_start = voice_start["start"] - MAX_ITEM_WINDOW_SKEW_SECONDS
        window_end = voice_end["end"] + MAX_ITEM_WINDOW_SKEW_SECONDS
        center = (voice_start["start"] + voice_end["end"]) / 2
        candidates = []
        candidate_keys = set()
        available = sorted(unmatched)
        for position, first_index in enumerate(available):
            group_indices = []
            group_tokens = []
            for last_index in available[position:position + MAX_FRAGMENT_GROUP_EPISODES]:
                if last_index != first_index + len(group_indices):
                    break
                candidate_episode = translated_episodes[last_index]
                candidate_end = (candidate_episode["source_end"]
                                 if candidate_episode["source_end"] is not None
                                 else candidate_episode["source_start"])
                if candidate_episode["source_start"] > window_end:
                    break
                if candidate_end < window_start:
                    if not group_indices:
                        continue
                    break
                group_indices.append(last_index)
                fragment_tokens = tokens(candidate_episode["text"], language)
                overlap = min(len(group_tokens), len(fragment_tokens))
                while overlap and group_tokens[-overlap:] != fragment_tokens[:overlap]:
                    overlap -= 1
                group_tokens.extend(fragment_tokens[overlap:])
                if not group_tokens:
                    continue
                distance = edit_distance(reference, group_tokens)
                similarity = 1 - distance / max(len(reference), len(group_tokens), 1)
                group_start = translated_episodes[group_indices[0]]["source_start"]
                final_episode = translated_episodes[group_indices[-1]]
                group_end = (final_episode["source_end"]
                             if final_episode["source_end"] is not None
                             else final_episode["source_start"])
                if group_end < window_start or group_start > window_end:
                    continue
                key = tuple(group_indices)
                if key in candidate_keys:
                    continue
                candidate_keys.add(key)
                candidates.append({"similarity": similarity,
                                   "time_distance": abs(center - (group_start + group_end) / 2),
                                   "indices": group_indices.copy(),
                                   "tokens": group_tokens.copy()})
        candidates.sort(key=lambda candidate: (-candidate["similarity"],
                                                candidate["time_distance"],
                                                len(candidate["indices"])))
        candidates = [candidate for candidate in candidates if candidate["similarity"] >= 0.8]
        if not candidates:
            mappings.append({"item": item["item"], "status": "missing_caption"})
            continue
        if (len(candidates) > 1
                and candidates[0]["similarity"] - candidates[1]["similarity"] < 0.02
                and abs(candidates[0]["time_distance"] - candidates[1]["time_distance"]) < 0.5):
            first, second = candidates[:2]
            first_indices, second_indices = set(first["indices"]), set(second["indices"])
            same_phrase_overlap = (first["tokens"] == second["tokens"]
                                  and (first_indices < second_indices
                                       or second_indices < first_indices))
            if same_phrase_overlap:
                candidates[0] = max((first, second), key=lambda row: len(row["indices"]))
            else:
                mappings.append({"item": item["item"], "status": "caption_match_ambiguous"})
                continue
        selected = candidates[0]
        selected_indices = selected["indices"]
        unmatched.difference_update(selected_indices)
        fragments = [translated_episodes[index] for index in selected_indices]
        if any(fragment["source_end"] is None for fragment in fragments):
            mappings.append({"item": item["item"], "status": "caption_disappearance_unobserved"})
            continue
        first_fragment, last_fragment = fragments[0], fragments[-1]
        start_error = abs(first_fragment["source_start"] - voice_start["start"])
        end_error = abs(last_fragment["source_end"] - voice_end["end"])
        start_offset = first_fragment["source_start"] - voice_start["start"]
        end_offset = last_fragment["source_end"] - voice_end["end"]
        start_uncertainty = (voice_start["start_uncertainty"]
                             + first_fragment["start_uncertainty"])
        end_uncertainty = (voice_end["end_uncertainty"]
                           + last_fragment["end_uncertainty"])
        mappings.append({
            "item": item["item"], "status": "measured",
            "start_error_upper_bound_seconds": (start_error + voice_start["start_uncertainty"]
                                                 + first_fragment["start_uncertainty"]),
            "end_error_upper_bound_seconds": (end_error + voice_end["end_uncertainty"]
                                               + last_fragment["end_uncertainty"]),
            "start_lag_interval_seconds": [start_offset - start_uncertainty,
                                            start_offset + start_uncertainty],
            "end_lag_interval_seconds": [end_offset - end_uncertainty,
                                          end_offset + end_uncertainty],
            "visible_source_interval": [first_fragment["source_start"], last_fragment["source_end"]],
            "visible_source_intervals": [[fragment["source_start"], fragment["source_end"]]
                                         for fragment in fragments],
            "matched_fragments": [{"text": fragment["text"],
                                   "source_interval": [fragment["source_start"],
                                                       fragment["source_end"]]}
                                  for fragment in fragments],
            "voiced_spans": [[span["start"], span["end"]] for span in item["spans"]],
        })
    return mappings, translated_episodes


def _coverage(items, mappings):
    by_item = {row["item"]: row for row in mappings}
    denominator = 0.0
    for item in items:
        if item.get("energy_check") == "supported" and item["spans"]:
            denominator += sum(span["end"] - span["start"] for span in item["spans"])
        else:
            denominator += item["reference_interval"][1] - item["reference_interval"][0]
    covered = 0.0
    missing = 0
    unknown = 0
    for item in items:
        mapping = by_item.get(item["item"], {"status": "missing_caption"})
        if mapping["status"] == "missing_caption":
            missing += 1
        elif mapping["status"] != "measured":
            unknown += 1
        else:
            intervals = sorted(mapping.get("visible_source_intervals") or
                               [mapping["visible_source_interval"]])
            merged = []
            for begin, end in intervals:
                if merged and begin <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], end)
                else:
                    merged.append([begin, end])
            for begin, end in mapping["voiced_spans"]:
                covered += sum(max(0.0, min(end, visible_end) - max(begin, visible_start))
                               for visible_start, visible_end in merged)
    ratio = covered / denominator if denominator else None
    return {"voiced_seconds": denominator, "covered_voiced_seconds": covered,
            "voiced_coverage": ratio, "missing_item_count": missing,
            "unknown_item_count": unknown}


def _percentile95(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _apply_calibration_label(result, calibration, thresholds_satisfied=False):
    if calibration:
        result.update({
            "sync_status": "calibration_only",
            "sync_gate_passed": None,
            "acceptance_status": "not_assessed",
            "calibration_thresholds_satisfied": bool(thresholds_satisfied),
            "acoustic_sync_assessed": False,
        })
    return result


def score(reference_path, source_path, spans_path, recording_path, report_path, language,
          *, calibration=False, ocr_executable=None, tessdata_dir=None):
    """Score one captured local browser session; return unknown when evidence is incomplete."""
    if not calibration:
        _validate_approved_artifacts(reference_path, spans_path, language)
    reference, source_spans, recording, report, source_hash = _load_identity(
        reference_path, source_path, spans_path, recording_path, report_path, language,
        calibration=calibration)
    if not calibration and source_spans.get("provenance") != "automated_estimate_not_human":
        raise EvidenceError("voice_span_provenance_invalid")
    items = _validate_voice_spans(reference, source_spans)
    capture_data = report.get("acoustic_capture")
    if not isinstance(capture_data, dict) or capture_data.get("mode") != "browser_web_audio_media_element":
        raise EvidenceError("browser_audio_capture_missing")
    audio_data = capture_data.get("audio")
    if not isinstance(audio_data, dict) or audio_data.get("status") != "captured":
        raise EvidenceError("browser_audio_capture_missing")
    if (audio_data.get("media_element_id") != "audioEl"
            or audio_data.get("media_element_muted") is not False
            or not _finite(audio_data.get("media_element_volume"))
            or audio_data["media_element_volume"] <= 0):
        raise EvidenceError("captured_element_not_audible_source")
    audio_path = _safe_artifact(report_path, audio_data.get("path", ""))
    if _sha256(audio_path) != audio_data.get("sha256"):
        raise EvidenceError("capture_audio_hash_mismatch")
    screenshots = _screen_artifacts(report_path, capture_data, language,
                                    ocr_executable, tessdata_dir)
    rate = reference.get("asset_sample_rate_hz")
    if not _finite(rate) or rate <= 0:
        raise EvidenceError("reference_sample_rate_missing")
    source = _read_audio(source_path, int(rate))
    captured, decoded_timing = _read_audio(audio_path, int(rate), include_timing=True)
    _check_source_energy(source, int(rate), items)
    alignment = _align_capture(source, captured, int(rate), items, report, audio_data)
    alignment["decoded_audio_timing"] = decoded_timing
    if alignment["status"] != "aligned":
        result = _unknown_result(
            language, reference, source_hash, source_spans, alignment["status"], len(items),
            alignment,
        )
        denominator = sum(
            (sum(span["end"] - span["start"] for span in item["spans"])
             if item.get("energy_check") == "supported" and item["spans"]
             else item["reference_interval"][1] - item["reference_interval"][0])
            for item in items)
        result["coverage"] = {"voiced_seconds": denominator,
                               "covered_voiced_seconds": 0.0,
                               "voiced_coverage": None,
                               "unknown_reason": alignment["status"]}
        result["candidate_revision"] = report["candidate_revision"]
        return _apply_calibration_label(result, calibration)
    audio_data = dict(audio_data)
    audio_data.update(alignment)
    clock, _clock_gap = _output_clock(audio_data)
    start_output_context = _context_time_at(
        float(audio_data["start_performance_after_ms"]), clock)
    if start_output_context is None:
        raise EvidenceError("audio_context_start_not_clock_calibrated")
    start_bridge_delta = start_output_context - float(audio_data["start_context_time_seconds"])
    audio_data["capture_start_output_context_delta_seconds"] = start_bridge_delta
    alignment["capture_start_output_context_delta_seconds"] = start_bridge_delta
    episodes, transitions, verified = _caption_episodes(
        report, screenshots, clock, audio_data, language)
    mappings, _mapped_episodes = _episode_mapping(items, episodes, transitions, verified, language)
    coverage = _coverage(items, mappings)
    measured = [row for row in mappings if row["status"] == "measured"]
    start_errors = [row["start_error_upper_bound_seconds"] for row in measured]
    end_errors = [row["end_error_upper_bound_seconds"] for row in measured]
    unknown_items = len(items) - len(measured) - coverage["missing_item_count"]
    p95_start, p95_end = _percentile95(start_errors), _percentile95(end_errors)
    max_start, max_end = max(start_errors, default=None), max(end_errors, default=None)
    enough = len(measured) >= SYNC_SAMPLE_MINIMUM
    visual_evidence_complete = len(verified) == len(transitions)
    uncertain_voice_items = sum(item.get("energy_check") != "supported" for item in items)
    passes = bool(enough and p95_start is not None and p95_end is not None
                  and p95_start <= P95_LIMIT_SECONDS and p95_end <= P95_LIMIT_SECONDS
                  and max_start <= MAXIMUM_LIMIT_SECONDS and max_end <= MAXIMUM_LIMIT_SECONDS
                  and coverage["voiced_coverage"] is not None
                  and coverage["voiced_coverage"] >= COVERAGE_MINIMUM
                  and unknown_items == 0 and uncertain_voice_items == 0
                  and visual_evidence_complete)
    status = ("unknown" if unknown_items or uncertain_voice_items or not visual_evidence_complete
              else "passed" if passes else "failed")
    result = {
        "schema_version": 1, "language": language,
        "candidate_revision": report["candidate_revision"],
        "reference_sha256": reference["sha256"], "input_sha256": source_hash,
        "recording_status": recording["recording_status"],
        "sync_status": status,
        "sync_gate_passed": True if passes else None if status == "unknown" else False,
        "acoustic_sync_assessed": status in ("passed", "failed"),
        "release_gate_passed": False,
        "measurement_scope": "browser_audio_context_render_output",
        "physical_output_latency": capture_data.get("physical_output_latency", "unknown"),
        "audio_capture_element": audio_data.get("media_element_id"),
        "samples": len(items), "measured_item_count": len(measured),
        "missing_item_count": coverage["missing_item_count"],
        "unknown_item_count": unknown_items,
        "voice_span_source": {"method": source_spans["method"],
                               "provenance": source_spans["provenance"],
                               "artifact_sha256": _sha256(spans_path),
                               "source_condition": source_spans["source_condition"]},
        "audio_alignment": alignment,
        "visual_evidence": {"verified_transition_count": len(verified),
                             "transition_count": len(transitions),
                             "unverified_transition_count": len(transitions) - len(verified),
                             "screenshot_sha256": [item["sha256"] for item in screenshots]},
        "start_error_seconds": {"p95_upper_bound": p95_start,
                                 "max_absolute_upper_bound": max_start},
        "end_error_seconds": {"p95_upper_bound": p95_end,
                               "max_absolute_upper_bound": max_end},
        "coverage": coverage, "thresholds": {
            "minimum_items": SYNC_SAMPLE_MINIMUM,
            "minimum_measured_items": SYNC_SAMPLE_MINIMUM,
            "p95_seconds": P95_LIMIT_SECONDS,
            "maximum_seconds": MAXIMUM_LIMIT_SECONDS, "voiced_coverage": COVERAGE_MINIMUM},
        "items": mappings,
    }
    return _apply_calibration_label(result, calibration, passes)


def _unknown_result(language, reference, source_hash, source_spans, reason,
                    item_count, alignment=None):
    return {"schema_version": 1, "language": language,
            "candidate_revision": None, "reference_sha256": reference.get("sha256"),
            "input_sha256": source_hash, "sync_status": "unknown",
            "sync_gate_passed": None, "release_gate_passed": False,
            "acoustic_sync_assessed": False,
            "measurement_scope": "browser_audio_context_render_output",
            "physical_output_latency": "unknown", "samples": item_count,
            "measured_item_count": 0, "missing_item_count": 0,
            "unknown_item_count": item_count,
            "voice_span_source": {"method": source_spans.get("method"),
                                  "provenance": source_spans.get("provenance"),
                                  "source_condition": source_spans.get("source_condition")},
            "audio_alignment": alignment or {"status": "not_assessed"},
            "visual_evidence": {"verified_transition_count": 0},
            "coverage": {"voiced_coverage": None, "unknown_reason": reason},
            "start_error_seconds": {"p95_upper_bound": None, "max_absolute_upper_bound": None},
            "end_error_seconds": {"p95_upper_bound": None, "max_absolute_upper_bound": None},
            "thresholds": {"minimum_items": SYNC_SAMPLE_MINIMUM,
                           "p95_seconds": P95_LIMIT_SECONDS,
                           "maximum_seconds": MAXIMUM_LIMIT_SECONDS,
                           "voiced_coverage": COVERAGE_MINIMUM}}


def _read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvidenceError("evidence_json_unreadable") from exc


def _failure_metadata(reference_path, source_path):
    try:
        reference = _read_json(reference_path)
    except EvidenceError:
        reference = {}
    if not isinstance(reference, dict):
        reference = {}
    try:
        source_hash = _sha256(source_path)
    except OSError:
        source_hash = None
    return reference.get("sha256"), source_hash


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True,
                        help="Fixed reference manifest with source input hash and item intervals")
    parser.add_argument("--source", type=Path, required=True,
                        help="The exact full WAV input named by the reference manifest")
    parser.add_argument("--voice-spans", type=Path, required=True,
                        help="Hash-bound automated voice-span estimates with method and uncertainty")
    parser.add_argument("--recording", type=Path, required=True,
                        help="Existing real browser collector recording JSON")
    parser.add_argument("--report", type=Path, required=True,
                        help="Browser collector report with playback-audio and screenshot artifacts")
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration", action="store_true",
                        help="Score independent synthetic calibration evidence without assessing acceptance")
    parser.add_argument("--ocr-executable", type=Path,
                        help="Explicit Tesseract-compatible OCR executable for saved screenshot pixels")
    parser.add_argument("--tessdata-dir", type=Path,
                        help="Explicit OCR language-data directory")
    args = parser.parse_args(argv)
    try:
        result = score(args.reference, args.source, args.voice_spans, args.recording,
                       args.report, args.language, calibration=args.calibration,
                       ocr_executable=args.ocr_executable, tessdata_dir=args.tessdata_dir)
        successful = (result.get("calibration_thresholds_satisfied") is True
                      if args.calibration else result["sync_gate_passed"] is True)
        exit_code = 0 if successful else 1
    except (EvidenceError, OSError, ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
        reason = str(exc) if isinstance(exc, EvidenceError) else "evidence_invalid"
        reference_hash, source_hash = _failure_metadata(args.reference, args.source)
        result = {"schema_version": 1, "language": args.language,
                  "sync_status": "measurement_invalid", "sync_gate_passed": None,
                  "release_gate_passed": False, "acoustic_sync_assessed": False,
                  "input_sha256": source_hash, "reference_sha256": reference_hash,
                  "invalid_reason": reason,
                  "measurement_scope": "browser_audio_context_render_output",
                  "physical_output_latency": "unknown"}
        if args.calibration:
            result.update({"acceptance_status": "not_assessed",
                           "calibration_thresholds_satisfied": False})
        exit_code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = redact_recording(result)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result.items()
                      if key not in ("items", "voice_span_source")}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
