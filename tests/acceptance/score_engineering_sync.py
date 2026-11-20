"""Estimate output-voice/caption timing from saved PCM, raw API clocks and pixels.

This is the ADR-0011 engineering estimate. It never qualifies the formal gate.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re

import av
import numpy as np

from score_acoustic_acceptance import (
    EvidenceError, _bounds, _check_source_energy, _finite, _ocr_caption, _percentile95,
    _read_audio, _resolve_ocr, _safe_artifact, _screen_artifacts, _sha256,
    _validate_approved_artifacts, _validate_voice_spans,
)
from score_original_acceptance import tokens


SCOPE = "browser_output_timestamp_estimate"
MATCH_CORRELATION = 0.99
SILENT_RUN_SECONDS = 0.02
MAX_CLOCK_PAIR_GAP_SECONDS = 0.25
MAX_PIXEL_OBSERVATION_GAP_SECONDS = 0.25


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceError("evidence_json_unreadable") from exc
    if not isinstance(value, dict):
        raise EvidenceError("evidence_json_object_required")
    return value


def _integer(value, minimum=0):
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def _base(language):
    return {"measurement_scope": SCOPE, "language": language, "status": "unknown",
            "analysis_version": "engineering_sync_v1", "analysis_script_sha256": _sha256(__file__),
            "formal_gate_passed": None, "sync_gate_passed": None,
            "release_gate_passed": False, "acceptance_assessed": False,
            "physical_output_latency": "unknown", "output_clock_error_bound_ms": None,
            "clock_semantics": "raw_browser_api_estimate_with_unbounded_physical_error",
            "source_progress": {"status": "unknown",
                                "reason": "output_pcm_and_pixels_do_not_identify_quiet_source_progress"},
            "unknown_reasons": []}


def _capture(report_path, report):
    capture = report.get("engineering_sync")
    if (not isinstance(capture, dict) or capture.get("measurement_scope") != SCOPE
            or capture.get("mode") != "browser_audio_worklet_pcm"):
        raise EvidenceError("engineering_pcm_capture_missing")
    revision = capture.get("candidate_revision", report.get("candidate_revision"))
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise EvidenceError("candidate_revision_missing")
    if report.get("candidate_revision", revision) != revision:
        raise EvidenceError("candidate_revision_mismatch")
    installation, audio = capture.get("installation"), capture.get("audio")
    if not isinstance(installation, dict) or not isinstance(audio, dict):
        raise EvidenceError("engineering_capture_metadata_missing")
    for record in (installation, audio):
        if (record.get("routing") != "media_element_audio_worklet_destination"
                or record.get("transparent_pcm") is not True
                or record.get("media_element_muted") is not False
                or not _finite(record.get("media_element_volume"))
                or not 0 < record["media_element_volume"] <= 1
                or record.get("media_element_id") not in ("audioEl", "player")
                or record.get("output_clock_error_bound_ms") is not None):
            raise EvidenceError("captured_output_path_unverified")
    if (installation.get("status") != "installed" or audio.get("status") != "captured"
            or audio.get("integrity_errors") != [] or audio.get("worklet_failed") is not False
            or audio.get("format") != "f32le" or audio.get("layout") != "interleaved"):
        raise EvidenceError("capture_integrity_not_clean")
    context = audio.get("context_id")
    rate, channels = audio.get("sample_rate_hz"), audio.get("channel_count")
    origin = audio.get("sampler_performance_origin_ms")
    if (not isinstance(context, str) or not context or not _integer(rate, 1)
            or not _integer(channels, 1) or not _finite(origin)
            or installation.get("context_id") != context
            or installation.get("sample_rate_hz") != rate
            or installation.get("sampler_performance_origin_ms") != origin):
        raise EvidenceError("capture_context_identity_invalid")
    pcm_path = _safe_artifact(report_path, audio.get("path", ""))
    ledger_path = _safe_artifact(report_path, audio.get("metadata_path", ""))
    if _sha256(pcm_path) != audio.get("sha256"):
        raise EvidenceError("capture_pcm_hash_mismatch")
    if _sha256(ledger_path) != audio.get("metadata_sha256"):
        raise EvidenceError("capture_ledger_hash_mismatch")
    clocks, barriers, pixels = [], [], []
    sequence = offset = quantum_count = 0
    first_frame = end_frame = None
    summary = None
    previous_clock = {}
    last_segment = -1
    closed_segments = set()
    with pcm_path.open("rb") as pcm, ledger_path.open(encoding="utf-8") as ledger:
        for line in ledger:
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise EvidenceError("capture_ledger_json_invalid") from exc
            if not isinstance(row, dict):
                raise EvidenceError("capture_ledger_record_invalid")
            kind = row.get("kind")
            if kind == "pixel_observation":
                pixels.append(row)
                continue
            if row.get("context_id") != context:
                raise EvidenceError("capture_ledger_context_mismatch")
            if kind == "pcm_batch":
                if summary is not None:
                    raise EvidenceError("capture_pcm_after_summary")
                if (row.get("sequence") != sequence or not _integer(row.get("sequence"))
                        or not _integer(row.get("sample_rate_hz"), 1)
                        or not _integer(row.get("channel_count"), 1)
                        or not _integer(row.get("byte_offset"))
                        or row.get("sample_rate_hz") != rate
                        or row.get("channel_count") != channels
                        or row.get("byte_offset") != offset):
                    raise EvidenceError("capture_batch_sequence_or_format_invalid")
                quanta = row.get("quanta")
                if not isinstance(quanta, list) or not quanta:
                    raise EvidenceError("capture_quanta_missing")
                batch_frames = 0
                for quantum in quanta:
                    frame, count = quantum.get("current_frame"), quantum.get("frame_count")
                    if not _integer(frame) or not _integer(count, 1):
                        raise EvidenceError("capture_quantum_invalid")
                    if end_frame is not None and frame != end_frame:
                        raise EvidenceError("capture_frame_gap_overlap_or_reorder")
                    if first_frame is None:
                        first_frame = frame
                    end_frame = frame + count
                    batch_frames += count
                    quantum_count += 1
                byte_count = batch_frames * channels * 4
                if row.get("byte_count") != byte_count:
                    raise EvidenceError("capture_batch_byte_count_invalid")
                block = pcm.read(byte_count)
                if len(block) != byte_count or hashlib.sha256(block).hexdigest() != row.get("sha256"):
                    raise EvidenceError("capture_batch_hash_or_length_mismatch")
                if not np.isfinite(np.frombuffer(block, dtype="<f4")).all():
                    raise EvidenceError("capture_pcm_nonfinite")
                offset += byte_count
                sequence += 1
            elif kind == "capture_summary":
                if summary is not None:
                    raise EvidenceError("capture_summary_repeated")
                summary = row
            elif kind in ("context_state", "device_change") or row.get("mapping_barrier") is True:
                if row.get("mapping_barrier") is not True or not _integer(row.get("clock_segment")):
                    raise EvidenceError("capture_clock_barrier_invalid")
                segment = row["clock_segment"]
                if segment < last_segment or (kind != "clock_sample" and segment == last_segment):
                    raise EvidenceError("capture_clock_segment_not_advancing")
                last_segment = segment
                barriers.append(row)
                if kind == "clock_sample" and row.get("usable") is True:
                    raise EvidenceError("capture_barrier_clock_marked_usable")
                if kind == "clock_sample":
                    closed_segments.add(segment)
            elif kind == "clock_sample":
                if not isinstance(row.get("usable"), bool):
                    raise EvidenceError("capture_clock_usable_flag_missing")
                if row["usable"]:
                    fields = ("context_time_seconds", "performance_time_ms",
                              "performance_time_after_ms", "call_before_after_ms", "call_after_after_ms")
                    if (not all(_finite(row.get(key)) for key in fields)
                            or row.get("state") != "running"
                            or not _integer(row.get("clock_segment"))
                            or row["context_time_seconds"] < 0
                            or row["call_after_after_ms"] < row["call_before_after_ms"]
                            or abs(row["performance_time_ms"] - origin
                                   - row["performance_time_after_ms"]) > 0.000001):
                        raise EvidenceError("capture_clock_pair_invalid")
                    if row["clock_segment"] < last_segment or row["clock_segment"] in closed_segments:
                        raise EvidenceError("capture_usable_clock_crosses_barrier")
                    last_segment = row["clock_segment"]
                    previous = previous_clock.get(row["clock_segment"])
                    if previous is not None and (
                            row["context_time_seconds"] <= previous["context_time_seconds"]
                            or row["performance_time_after_ms"] <= previous["performance_time_after_ms"]):
                        raise EvidenceError("capture_usable_clock_not_advancing")
                    previous_clock[row["clock_segment"]] = row
                    clocks.append(row)
            elif kind == "capture_error":
                raise EvidenceError("capture_error_in_ledger")
            elif kind != "media_state":
                raise EvidenceError("capture_ledger_kind_unknown")
        if pcm.read(1):
            raise EvidenceError("capture_pcm_unaccounted_tail")
    if summary is None or first_frame is None:
        raise EvidenceError("capture_final_summary_or_pcm_missing")
    counts = {"batch_count": sequence, "frame_count": end_frame - first_frame,
              "first_frame": first_frame, "end_frame": end_frame,
              "sample_rate_hz": rate, "channel_count": channels}
    for key, value in counts.items():
        if (not _integer(audio.get(key)) or audio[key] != value
                or not _integer(summary.get(key)) or summary[key] != value):
            raise EvidenceError("capture_final_counter_mismatch")
    if (summary.get("worklet_failed") is not False or summary.get("status") != "captured"
            or audio.get("byte_count") != offset
            or pcm_path.stat().st_size != offset):
        raise EvidenceError("capture_final_status_or_length_mismatch")
    return capture, audio, pcm_path, clocks, barriers, pixels, {
        "status": "validated", **counts, "byte_count": offset, "quantum_count": quantum_count,
        "pcm_sha256": audio["sha256"], "ledger_sha256": audio["metadata_sha256"],
        "usable_clock_sample_count": len(clocks), "mapping_barrier_count": len(barriers),
        "variable_quantum_lengths_allowed": True,
    }


def _reference(reference_path, source_path, spans_path, language, fixture):
    if not fixture:
        _validate_approved_artifacts(reference_path, spans_path, language)
    reference, spans = _read_json(reference_path), _read_json(spans_path)
    source_hash = _sha256(source_path)
    if reference.get("sha256") != source_hash or spans.get("source_sha256") != source_hash:
        raise EvidenceError("frozen_reference_source_hash_mismatch")
    if reference.get("language") not in (None, language) or spans.get("schema_version") != 1:
        raise EvidenceError("frozen_reference_language_or_schema_invalid")
    if (not spans.get("provenance") or not isinstance(spans.get("method"), dict)
            or not spans["method"].get("name") or not spans["method"].get("parameters")
            or spans.get("source_condition") not in (
                ("known_clean_single_speaker", "synthetic_tone_fixture") if fixture
                else ("known_clean_single_speaker",))):
        raise EvidenceError("frozen_voice_span_method_invalid")
    if not fixture and spans["provenance"] != "automated_estimate_not_human":
        raise EvidenceError("frozen_voice_span_provenance_invalid")
    rate = reference.get("asset_sample_rate_hz")
    if not _integer(rate, 1):
        raise EvidenceError("reference_sample_rate_invalid")
    items = _validate_voice_spans(reference, spans)
    source = _read_audio(source_path, rate)
    if any(item["reference_interval"][1] * rate > len(source) + 1 for item in items):
        raise EvidenceError("reference_intervals_exceed_audio")
    _check_source_energy(source, rate, items)
    return items, source, rate, {
        "source_sha256": source_hash, "manifest_sha256": _sha256(reference_path),
        "voice_spans_sha256": _sha256(spans_path), "item_count": len(items),
        "approved_product_reference": not fixture, "method": spans["method"],
        "provenance": spans["provenance"],
    }


def _ocr_identity(executable, tessdata_dir, language):
    command, data = _resolve_ocr(executable, tessdata_dir)
    models = ("chi_sim", "eng") if language == "zh" else ("eng",)
    return {"status": "available" if command else "unavailable", "executable": command,
            "executable_sha256": _sha256(command) if command else None,
            "tessdata_dir": str(data) if data else None, "languages": list(models),
            "traineddata_sha256": {
                name: (_sha256(data / (name + ".traineddata"))
                       if data and (data / (name + ".traineddata")).is_file() else None)
                for name in models}}


def _pixels(report_path, capture, raw_pixels, language, ocr_executable, tessdata_dir):
    descriptor = capture.get("pixel_capture")
    if not isinstance(descriptor, dict):
        # Older transition-only screenshots are usable, with their full observation gaps.
        return _screen_artifacts(report_path, capture, language, ocr_executable, tessdata_dir)
    audio = capture["audio"]
    if (descriptor.get("metadata_path") != audio["metadata_path"]
            or descriptor.get("metadata_sha256") != audio["metadata_sha256"]
            or descriptor.get("ledger_kind") != "pixel_observation"
            or not _integer(descriptor.get("observation_count"))
            or descriptor.get("observation_count") != len(raw_pixels)):
        raise EvidenceError("pixel_ledger_identity_mismatch")
    executable, data = _resolve_ocr(ocr_executable, tessdata_dir)
    cache, verified = {}, []
    previous_after = -math.inf
    for index, row in enumerate(raw_pixels, 1):
        if (not _integer(row.get("observation_index"), 1) or row["observation_index"] != index
                or row.get("context_id") != audio["context_id"]):
            raise EvidenceError("pixel_observation_identity_invalid")
        viewport, after_viewport = row.get("viewport_before"), row.get("viewport_after")
        if (not isinstance(viewport, dict) or viewport != after_viewport
                or not all(_integer(viewport.get(key), 2) for key in ("width", "height"))
                or row.get("device_scale_factor_before") != 1
                or row.get("device_scale_factor_after") != 1):
            raise EvidenceError("pixel_viewport_invalid")
        roi = row.get("roi_bounds")
        _bounds(roi, viewport, "pixel_roi_invalid")
        if roi != descriptor.get("fixed_roi"):
            raise EvidenceError("pixel_roi_changed")
        if (not all(_integer(roi[key]) for key in ("x", "y", "width", "height"))
                or row.get("ocr_crop_source") != "fixed_caption_roi"
                or row.get("ocr_crop_bounds") != {"x": 0, "y": 0,
                                                   "width": roi["width"], "height": roi["height"]}):
            raise EvidenceError("pixel_crop_invalid")
        margin = row.get("shadow_margin_css_px")
        if not _finite(margin) or margin < 0 or margin != descriptor.get("shadow_margin_css_px"):
            raise EvidenceError("pixel_shadow_margin_invalid")
        for bounds in (row.get("caption_bounds_before"), row.get("caption_bounds_after")):
            if bounds is not None:
                _bounds(bounds, viewport, "pixel_caption_bounds_invalid")
                if (bounds["x"] - margin < roi["x"] or bounds["y"] - margin < roi["y"]
                        or bounds["x"] + bounds["width"] + margin > roi["x"] + roi["width"]
                        or bounds["y"] + bounds["height"] + margin > roi["y"] + roi["height"]):
                    raise EvidenceError("pixel_caption_outside_roi")
        before, after = row.get("before_after_ms"), row.get("after_after_ms")
        if (not all(_finite(value) for value in (before, after, row.get("observed_after_ms"),
                                                 row.get("uncertainty_ms")))
                or before > after or before < previous_after
                or abs(row["observed_after_ms"] - (before + after) / 2) > 0.000001
                or row["uncertainty_ms"] < (after - before) / 2
                or not isinstance(row.get("dom_state_stable"), bool)):
            raise EvidenceError("pixel_observation_bracket_invalid")
        previous_after = after
        path = _safe_artifact(report_path, row.get("path", ""))
        if _sha256(path) != row.get("sha256") or path.stat().st_size != row.get("byte_count"):
            raise EvidenceError("screenshot_hash_mismatch")
        with av.open(str(path), format="png_pipe") as image:
            frame = next(image.decode(video=0), None)
            if (frame is None or frame.width != roi["width"] or frame.height != roi["height"]
                    or row.get("screenshot_width_px") != frame.width
                    or row.get("screenshot_height_px") != frame.height):
                raise EvidenceError("pixel_dimensions_invalid")
            if row["sha256"] not in cache:
                cache[row["sha256"]] = (_ocr_caption(
                    frame.to_ndarray(format="rgb24"), (0, 0, frame.width, frame.height),
                    executable, data, language) if executable else None)
        verified.append({**row, "_ocr_text": cache[row["sha256"]]})
    return verified


def _pages(screens, language):
    runs = []
    for screen_index, screen in enumerate(screens):
        text = screen.get("_ocr_text")
        key = None if text is None or screen.get("dom_state_stable") is False else tuple(tokens(text, language))
        moment, uncertainty = screen["observed_after_ms"] / 1000, screen["uncertainty_ms"] / 1000
        observation = [moment - uncertainty, moment + uncertainty]
        if screen_index and moment - screens[screen_index - 1]["observed_after_ms"] / 1000 > MAX_PIXEL_OBSERVATION_GAP_SECONDS:
            runs.append({"tokens": None, "first_observation": runs[-1]["last_observation"],
                         "last_observation": observation, "observation_count": 0,
                         "first_observation_index": screen_index, "last_observation_index": screen_index})
        if runs and runs[-1]["tokens"] == key:
            runs[-1]["last_observation"] = observation
            runs[-1]["observation_count"] += 1
            runs[-1]["last_observation_index"] = screen_index
        else:
            runs.append({"tokens": key, "first_observation": observation,
                         "last_observation": observation, "observation_count": 1,
                         "first_observation_index": screen_index, "last_observation_index": screen_index})
    pages = []
    for index, run in enumerate(runs):
        if not run["tokens"]:
            continue
        previous = runs[index - 1] if index else None
        following = runs[index + 1] if index + 1 < len(runs) else None
        pages.append({**run, "index": len(pages),
                      "unknown_gap_before": previous is not None and previous["tokens"] is None,
                      "start_seconds": ([previous["last_observation"][0], run["first_observation"][1]]
                                        if previous is not None and previous["tokens"] is not None else None),
                      "end_seconds": ([run["last_observation"][0], following["first_observation"][1]]
                                      if following is not None and following["tokens"] is not None else None)})
    return pages


def _caption_occurrences(reference_text, pages, language):
    expected = tokens(reference_text, language)
    occurrences = []
    if not expected:
        return occurrences
    for begin in range(len(pages)):
        observed = []
        for end in range(begin, len(pages)):
            if end > begin and pages[end]["unknown_gap_before"]:
                break
            observed.extend(pages[end]["tokens"])
            if observed != expected[:len(observed)]:
                break
            if observed == expected:
                occurrences.append({"page_indices": list(range(begin, end + 1)),
                                    "start_seconds": pages[begin]["start_seconds"],
                                    "end_seconds": pages[end]["end_seconds"]})
                break
    return occurrences


def _compact(samples, rate):
    """Drop only exact-zero runs for matching and retain every original sample index."""
    silent = samples == 0
    changes = np.flatnonzero(np.diff(np.r_[False, silent, False].astype(np.int8)))
    keep = np.ones(len(samples), dtype=bool)
    for begin, end in zip(changes[::2], changes[1::2]):
        if end - begin >= math.ceil(SILENT_RUN_SECONDS * rate):
            keep[begin:end] = False
    indices = np.flatnonzero(keep)
    return samples[indices], indices


def _captured_samples(path, audio, rate):
    raw = np.fromfile(path, dtype="<f4").reshape(-1, audio["channel_count"])
    mono = raw.mean(axis=1, dtype=np.float32)
    if audio["sample_rate_hz"] != rate:
        frame = av.AudioFrame.from_ndarray(mono.reshape(1, -1), format="fltp", layout="mono")
        frame.sample_rate = audio["sample_rate_hz"]
        resampler = av.AudioResampler(format="fltp", layout="mono", rate=rate)
        frames = resampler.resample(frame) + resampler.resample(None)
        mono = np.concatenate([part.to_ndarray().reshape(-1) for part in frames])
    compact, indices = _compact(mono, rate)
    return compact, indices / rate + audio["first_frame"] / audio["sample_rate_hz"]


class _WaveformScan:
    """Full-output normalized correlation; all compatible offsets are retained."""

    def __init__(self, captured):
        self.captured = captured.astype(np.float64)
        self.sums = np.r_[0.0, np.cumsum(self.captured)]
        self.squares = np.r_[0.0, np.cumsum(self.captured ** 2)]
        self.transforms = {}

    def matches(self, template):
        length, total = len(template), len(self.captured)
        if length < 16 or total < length:
            return []
        centered = template.astype(np.float64) - float(np.mean(template))
        energy = float(centered @ centered)
        if energy <= 1e-12:
            return []
        size = 1 << (total + length - 2).bit_length()
        if size not in self.transforms:
            self.transforms[size] = np.fft.rfft(self.captured, n=size)
        convolution = np.fft.irfft(self.transforms[size] * np.fft.rfft(centered[::-1], n=size), n=size)
        numerator = convolution[length - 1:total]
        variance = (self.squares[length:] - self.squares[:-length]
                    - (self.sums[length:] - self.sums[:-length]) ** 2 / length)
        denominator = np.sqrt(np.maximum(variance * energy, 1e-30))
        compatible = numerator / denominator >= MATCH_CORRELATION
        changes = np.flatnonzero(np.diff(np.r_[False, compatible, False].astype(np.int8)))
        return [{"offset_samples": [int(begin), int(end - 1)],
                 "minimum_correlation": float(np.min(numerator[begin:end] / denominator[begin:end]))}
                for begin, end in zip(changes[::2], changes[1::2])]


def _clock_values(contexts, clocks):
    contexts = np.asarray(contexts, dtype=np.float64)
    values, counts = np.full(contexts.shape, np.nan), np.zeros(contexts.shape, dtype=np.uint32)
    segments = {}
    for row in clocks:
        segments.setdefault(row["clock_segment"], []).append(row)
    for rows in segments.values():
        if len(rows) < 2:
            continue
        context = np.array([row["context_time_seconds"] for row in rows])
        wall = np.array([row["performance_time_after_ms"] / 1000 for row in rows])
        gaps = np.diff(context)
        slopes = np.diff(wall) / gaps
        pair = np.clip(np.searchsorted(context, contexts, side="right") - 1, 0, len(rows) - 2)
        mask = ((contexts >= context[0]) & (contexts <= context[-1])
                & (gaps[pair] <= MAX_CLOCK_PAIR_GAP_SECONDS)
                & (slopes[pair] >= 0.9) & (slopes[pair] <= 1.1))
        values[mask] = wall[pair[mask]] + (contexts[mask] - context[pair[mask]]) * slopes[pair[mask]]
        counts[mask] += 1
    values[counts != 1] = np.nan
    return values


def _wall_interval(context_interval, clocks):
    segments = {}
    for row in clocks:
        segments.setdefault(row["clock_segment"], []).append(row)
    candidates = []
    for rows in segments.values():
        probes = list(context_interval) + [
            (left["context_time_seconds"] + right["context_time_seconds"]) / 2
            for left, right in zip(rows, rows[1:])
            if right["context_time_seconds"] > context_interval[0]
            and left["context_time_seconds"] < context_interval[1]]
        values = _clock_values(probes, rows)
        if np.isfinite(values).all() and values[1] >= values[0]:
            candidates.append(values[:2])
    if len(candidates) != 1:
        return None
    return candidates[0].tolist()


def _span_occurrences(span, source, rate, scan, context_indices, clocks):
    begin, finish = round(span["start"] * rate), round(span["end"] * rate)
    template, indices = _compact(source[begin:finish], rate)
    if not len(template):
        return []
    occurrences = scan.matches(template)
    for occurrence in occurrences:
        low, high = occurrence["offset_samples"]
        edges = []
        for edge, uncertainty in ((0, span["start_uncertainty"]),
                                  (finish - begin, span["end_uncertainty"])):
            bounds = []
            for source_index, offset, side in ((edge - uncertainty * rate, low, "left"),
                                               (edge + uncertainty * rate, high, "right")):
                position = int(np.searchsorted(indices, source_index))
                if position == 0 or position == len(indices):
                    anchor = 0 if position == 0 else len(indices) - 1
                    mapped = context_indices[offset + anchor] + (source_index - indices[anchor]) / rate
                elif indices[position] == source_index:
                    mapped = context_indices[offset + position]
                else:
                    # A removed source-silence gap has no unique interior correspondence.
                    mapped = context_indices[offset + position - (1 if side == "left" else 0)]
                bounds.append(mapped + (-1 if side == "left" else 1) / rate)
            edges.append(_wall_interval(bounds, clocks))
        occurrence.update({"start_seconds": edges[0], "end_seconds": edges[1],
                           "context_start_seconds": float(context_indices[low]),
                           "context_end_seconds": float(context_indices[high + len(template) - 1] + 1 / rate),
                           "source_retained_samples": len(indices),
                           "source_total_samples": finish - begin})
    return occurrences


def _difference(visible, audible):
    return [visible[0] - audible[1], visible[1] - audible[0]]


def _statistics(intervals):
    lower = [0 if begin <= 0 <= end else min(abs(begin), abs(end)) for begin, end in intervals]
    upper = [max(abs(begin), abs(end)) for begin, end in intervals]
    return {"count": len(intervals), "nearest_rank_percentile": 95,
            "p95_signed_interval": ([_percentile95([row[0] for row in intervals]),
                                      _percentile95([row[1] for row in intervals])] if intervals else None),
            "max_signed_interval": ([max(row[0] for row in intervals), max(row[1] for row in intervals)]
                                    if intervals else None),
            "p95_absolute_lower_bound": _percentile95(lower),
            "p95_upper_bound": _percentile95(upper),
            "max_absolute_lower_bound": max(lower, default=None),
            "max_absolute_upper_bound": max(upper, default=None)}


def _measure(items, source, rate, captured, context_indices, clocks, pages, language):
    scan = _WaveformScan(captured)
    rows = []
    for item in items:
        audio_occurrences = [_span_occurrences(span, source, rate, scan, context_indices, clocks)
                             for span in item["spans"]]
        captions = _caption_occurrences(item["reference"], pages, language)
        row = {"item": item["item"], "reference": item["reference"], "status": "unknown",
               "reference_voice_status": item["energy_check"], "audio_occurrences": audio_occurrences,
               "caption_occurrences": captions, "unknown_reasons": []}
        if item["energy_check"] != "supported":
            row["unknown_reasons"].append("reference_voice_" + item["energy_check"])
        if not audio_occurrences or any(len(occurrences) != 1 for occurrences in audio_occurrences):
            row["unknown_reasons"].append("output_waveform_unmatched_or_ambiguous")
        if len(captions) != 1:
            row["unknown_reasons"].append("visible_text_unmatched_or_ambiguous")
        if not row["unknown_reasons"]:
            spans = [occurrences[0] for occurrences in audio_occurrences]
            if (any(span["start_seconds"] is None or span["end_seconds"] is None for span in spans)
                    or any(left["context_end_seconds"] > right["context_start_seconds"]
                           for left, right in zip(spans, spans[1:]))):
                row["unknown_reasons"].append("audio_clock_mapping_missing_or_order_ambiguous")
            if captions[0]["start_seconds"] is None or captions[0]["end_seconds"] is None:
                row["unknown_reasons"].append("pixel_transition_unbounded")
            if not row["unknown_reasons"]:
                row.update({"status": "measured", "audio_spans": spans,
                            "start_error_seconds": _difference(captions[0]["start_seconds"], spans[0]["start_seconds"]),
                            "end_error_seconds": _difference(captions[0]["end_seconds"], spans[-1]["end_seconds"])})
        rows.append(row)
    # Shared reference identities cannot earn credit from the same output or pixels.
    for index, row in enumerate(rows):
        for other in rows[index + 1:]:
            shared_pages = any(set(first["page_indices"]) & set(second["page_indices"])
                               for first in row["caption_occurrences"] for second in other["caption_occurrences"])
            shared_audio = any(first["context_start_seconds"] < second["context_end_seconds"]
                               and second["context_start_seconds"] < first["context_end_seconds"]
                               for occurrences in row["audio_occurrences"] for first in occurrences
                               for others in other["audio_occurrences"] for second in others)
            if shared_pages or shared_audio:
                for affected in (row, other):
                    affected["status"] = "unknown"
                    if "output_identity_shared" not in affected["unknown_reasons"]:
                        affected["unknown_reasons"].append("output_identity_shared")
    return rows


def _coverage(items, rows, source, rate, contexts, clocks, pages):
    denominator = covered = possible = 0.0
    wall = _clock_values(contexts, clocks)
    for item, row in zip(items, rows):
        amount = (sum(span["end"] - span["start"] for span in item["spans"])
                  if item["energy_check"] == "supported" and item["spans"]
                  else item["reference_interval"][1] - item["reference_interval"][0])
        denominator += amount
        if row["status"] != "measured":
            possible += amount
            row["coverage_lower_seconds"], row["coverage_upper_seconds"] = 0.0, amount
            row["sampled_coverage_lower_seconds"], row["sampled_coverage_upper_seconds"] = 0.0, amount
            continue
        guaranteed = np.zeros(len(contexts), dtype=bool)
        potential = ~np.isfinite(wall)
        for page_index in row["caption_occurrences"][0]["page_indices"]:
            page = pages[page_index]
            guaranteed |= (wall >= page["start_seconds"][1]) & (wall <= page["end_seconds"][0])
            potential |= (wall >= page["start_seconds"][0]) & (wall <= page["end_seconds"][1])
        minimum, maximum = 0.0, 0.0
        for span, occurrence in zip(item["spans"], row["audio_spans"]):
            begin, end = round(span["start"] * rate), round(span["end"] * rate)
            _template, indices = _compact(source[begin:end], rate)
            first, last = occurrence["offset_samples"]
            good = (indices >= math.ceil(span["start_uncertainty"] * rate)) & (
                indices < end - begin - math.ceil(span["end_uncertainty"] * rate))
            certain = good.copy()
            maybe = np.zeros(len(indices), dtype=bool)
            for offset in range(first, last + 1):
                certain &= guaranteed[offset:offset + len(indices)]
                maybe |= potential[offset:offset + len(indices)]
            minimum += int(certain.sum()) / rate
            maximum += int(maybe.sum()) / rate + (end - begin - len(indices)) / rate
        minimum, maximum = min(amount, minimum), min(amount, maximum)
        row["sampled_coverage_lower_seconds"], row["sampled_coverage_upper_seconds"] = minimum, maximum
        row["coverage_lower_seconds"], row["coverage_upper_seconds"] = 0.0, amount
        covered += minimum
        possible += maximum
    return {"status": "sample_based_engineering_estimate", "reference_voiced_seconds": denominator,
            "unconditional": {"status": "unknown", "reason": "pixels_between_observations_unobserved",
                              "covered_reference_seconds_lower": 0.0,
                              "covered_reference_seconds_upper": denominator,
                              "voiced_coverage_lower_bound": 0.0 if denominator else None,
                              "voiced_coverage_upper_bound": 1.0 if denominator else None},
            "sampled_covered_reference_seconds_lower_estimate": covered,
            "sampled_covered_reference_seconds_upper_estimate": possible,
            "sampled_voiced_coverage_lower_estimate": covered / denominator if denominator else None,
            "sampled_voiced_coverage_upper_estimate": possible / denominator if denominator else None,
            "sampled_estimate_assumption": "same_observed_page_persists_between_adjacent_compatible_images",
            "denominator": "all_frozen_reference_items_with_original_unknown_fallback",
            "replay_credit": "at_most_once_per_reference_sample"}


def score(report_path, language, *, reference_path=None, source_path=None, spans_path=None,
          fixture=False, ocr_executable=None, tessdata_dir=None):
    """Read a closed capture; never use delivery, app or ASR timestamps as truth."""
    report = _read_json(report_path)
    capture, audio, pcm, clocks, _barriers, pixels, integrity = _capture(report_path, report)
    result = _base(language)
    result.update({"candidate_revision": capture.get("candidate_revision", report.get("candidate_revision")),
                   "candidate_worktree_dirty": capture.get("candidate_worktree_dirty",
                                                            report.get("candidate_worktree_dirty")),
                   "fixture_only": fixture, "capture_integrity": integrity,
                   "original_dom_sampling": report.get("sampling", {"status": "not_reported"})})
    screens = _pixels(report_path, capture, pixels, language, ocr_executable, tessdata_dir)
    pages = _pages(screens, language)
    result["visual_evidence"] = {
        "observation_count": len(screens), "visible_page_count": len(pages),
        "unknown_ocr_observation_count": sum(screen.get("_ocr_text") is None for screen in screens),
        "pixel_sha256": [screen["sha256"] for screen in screens], "pages": pages,
        "sampling_limit": "transitions_between_pixel_observations_remain_unobserved",
        "target_interval_ms": capture.get("pixel_capture", {}).get("target_interval_ms"),
        "maximum_observed_gap_seconds": max((
            (second["observed_after_ms"] - first["observed_after_ms"]) / 1000
            for first, second in zip(screens, screens[1:])), default=None),
        "maximum_credited_observation_gap_seconds": MAX_PIXEL_OBSERVATION_GAP_SECONDS,
        "ocr": _ocr_identity(ocr_executable, tessdata_dir, language),
    }
    if not all((reference_path, source_path, spans_path)):
        result["unknown_reasons"] = ["frozen_reference_missing"]
        return {"schema_version": 1, "engineering_sync": result}
    items, source, rate, identity = _reference(reference_path, source_path, spans_path, language, fixture)
    captured, context_indices = _captured_samples(pcm, audio, rate)
    rows = _measure(items, source, rate, captured, context_indices, clocks, pages, language)
    coverage = _coverage(items, rows, source, rate, context_indices, clocks, pages)
    measured = [row for row in rows if row["status"] == "measured"]
    start_stats = _statistics([row["start_error_seconds"] for row in measured])
    end_stats = _statistics([row["end_error_seconds"] for row in measured])
    if len(measured) != len(items):
        result["unknown_reasons"].append("unmeasured_reference_items")
    if len(measured) < 50:
        result["unknown_reasons"].append("minimum_measured_items_not_met")
    if not screens or any(screen.get("_ocr_text") is None for screen in screens):
        result["unknown_reasons"].append("pixel_ocr_incomplete")
    if capture.get("pixel_capture", {}).get("status", "captured") != "captured":
        result["unknown_reasons"].append("pixel_capture_incomplete")
    sampling = report.get("sampling")
    if (not isinstance(sampling, dict) or sampling.get("status") != "valid"
            or not _integer(sampling.get("requested_interval_ms"), 1)
            or sampling["requested_interval_ms"] != 50
            or not _finite(sampling.get("max_observed_gap_ms"))
            or sampling["max_observed_gap_ms"] > 100):
        result["unknown_reasons"].append("original_dom_sampling_invalid_or_missing")
    within = (len(measured) >= 50 and not result["unknown_reasons"]
              and all(stats["p95_upper_bound"] <= 0.5 and stats["max_absolute_upper_bound"] <= 1
                      for stats in (start_stats, end_stats))
              and coverage["sampled_voiced_coverage_lower_estimate"] >= 0.95)
    result.update({
        "status": ("unknown" if result["unknown_reasons"] else
                   "estimate_within_thresholds" if within else "estimate_exceeds_thresholds"),
        "reference_identity": identity, "samples": len(items), "measured_item_count": len(measured),
        "unknown_item_count": len(items) - len(measured),
        "missing_item_count": 0,
        "unmatched_caption_item_count": sum(not row["caption_occurrences"] for row in rows),
        "unmatched_audio_item_count": sum(not row["audio_occurrences"] or any(not group for group in row["audio_occurrences"])
                                           for row in rows),
        "item_accounting": {"measured": len(measured), "missing_confirmed": 0,
                            "unknown": len(items) - len(measured), "total": len(items)},
        "replay_or_ambiguous_audio_item_count": sum(any(len(group) > 1 for group in row["audio_occurrences"])
                                                    for row in rows),
        "reference_voice_unknown_items": [item["item"] for item in items if item["energy_check"] != "supported"],
        "retained_vad_start_unknown_items": [item["item"] for item in items
                                              if item["spans"] and item["spans"][0]["start_uncertainty"] > 0.5],
        "waveform_matching": {"method": "full_voiced_span_global_normalized_waveform_correlation",
                              "minimum_correlation": MATCH_CORRELATION,
                              "all_compatible_offsets_retained": True,
                              "exact_zero_run_compaction_seconds": SILENT_RUN_SECONDS,
                              "original_context_index_map_preserved": True,
                              "matching_rate_hz": rate,
                              "matching_limit": "changed_or_unresolved_waveforms_remain_unknown"},
        "clock_mapping": {"method": "raw_same_segment_bracketing_api_pairs_no_extrapolation",
                          "maximum_pair_gap_seconds": MAX_CLOCK_PAIR_GAP_SECONDS,
                          "local_slope_range": [0.9, 1.1],
                          "physical_error_bound_seconds": None},
        "start_error_seconds": start_stats, "end_error_seconds": end_stats, "coverage": coverage,
        "sampled_engineering_thresholds_satisfied": bool(within),
        "thresholds": {"minimum_items": 50, "p95_seconds": 0.5, "maximum_seconds": 1.0,
                       "reference_voiced_coverage": 0.95}, "items": rows,
    })
    return {"schema_version": 1, "engineering_sync": result}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--voice-spans", type=Path)
    parser.add_argument("--fixture", action="store_true", help="Synthetic control only; never product acceptance")
    parser.add_argument("--ocr-executable", type=Path)
    parser.add_argument("--tessdata-dir", type=Path)
    args = parser.parse_args(argv)
    if any((args.reference, args.source, args.voice_spans)) and not all(
            (args.reference, args.source, args.voice_spans)):
        parser.error("--reference, --source and --voice-spans must be supplied together")
    try:
        result = score(args.report, args.language, reference_path=args.reference,
                       source_path=args.source, spans_path=args.voice_spans, fixture=args.fixture,
                       ocr_executable=args.ocr_executable, tessdata_dir=args.tessdata_dir)
        code = 1 if result["engineering_sync"]["status"] == "unknown" else 0
    except (EvidenceError, OSError, ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
        result = {"schema_version": 1, "engineering_sync": {
            **_base(args.language), "status": "measurement_invalid",
            "invalid_reason": str(exc) if isinstance(exc, EvidenceError) else "evidence_invalid"}}
        code = 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in result["engineering_sync"].items()
                      if key != "items"}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
