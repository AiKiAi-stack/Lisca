"""Measure local PyAV open/decode timing against the paced WAV origin.

This is a bounded offline source probe: it sends no PCM to any provider.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from fixed_realtime_startup_acceptance import (
    DEFAULT_OUTPUT, PacingOrigin, _read_jsonl, _wav_data_offset, prepare_sample,
)
from startup_timing_probe import install_stream_source_probe
from lisca.stream_source import StreamSource  # pylint: disable=import-error
import lisca.stream_source as stream_source_module  # pylint: disable=import-error


def run(language, output=DEFAULT_OUTPUT):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = prepare_sample(language, output)
    origin_log = output / f"offline-pyav-{language}-origin.jsonl"
    decode_log = output / f"offline-pyav-{language}-decode.jsonl"
    for path in (origin_log, decode_log):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite probe evidence: {path}")
    wav_data = Path(manifest["clip_path"]).read_bytes()
    sample = {"bytes": wav_data, "data_offset": _wav_data_offset(wav_data)}
    server = PacingOrigin(("127.0.0.1", 0), {language: sample}, origin_log, 0.1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    class WebUIStub:
        StreamSource = StreamSource

    install_stream_source_probe(WebUIStub, decode_log)
    case_id = f"{language}-startup-probe"
    url = f"http://127.0.0.1:{server.server_port}/{language}.wav?startup_case={case_id}"
    opened_ns = time.monotonic_ns()
    source = None
    iterator = None
    dummy_sink_bytes = 0
    try:
        source = StreamSource(url)
        open_done_ns = time.monotonic_ns()
        iterator = iter(source.iter_chunks())
        chunk = next(iterator)
        dummy_sink_bytes += len(chunk)
        yielded_ns = time.monotonic_ns()
    finally:
        if iterator is not None:
            iterator.close()
        if source is not None:
            source.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    origin_rows = [row for row in _read_jsonl(origin_log)
                   if row.get("case_id") == case_id
                   and row.get("requester_role") == "application_stream_source"]
    decoded_rows = [row for row in _read_jsonl(decode_log) if row.get("case_id") == case_id]
    if len(origin_rows) != 1 or len(decoded_rows) != 1:
        raise AssertionError("probe expected one correlated application GET and decoded stream")
    origin, decoded = origin_rows[0], decoded_rows[0]
    origin_ns = origin["origin_first_pcm_write_started_monotonic_ns"]
    decoded_ns = decoded["first_decoded_pcm_monotonic_ns"]
    if decoded_ns < origin_ns:
        raise AssertionError("decoded PCM timestamp precedes its source body-write bound")
    result = {
        "probe_only": True,
        "provider_calls": 0,
        "source": f"fixed {language} reference prefix over local paced HTTP",
        "source_sha256": manifest["source_sha256"],
        "clip_sha256": manifest["clip_sha256"],
        "source_interval_seconds": manifest["source_interval_seconds"],
        "source_duration_seconds": manifest["clip_duration_seconds"],
        "av_open_elapsed_ms": round((open_done_ns - opened_ns) / 1e6, 1),
        "first_pcm_yield_after_open_ms": round((decoded_ns - open_done_ns) / 1e6, 1),
        "origin_first_pcm_send_to_decoded_yield_ms": round((decoded_ns - origin_ns) / 1e6, 1),
        "first_chunk_bytes": decoded["first_chunk_bytes"],
        "dummy_sink_bytes": dummy_sink_bytes,
        "application_get_count": len(origin_rows),
        "referer_present": origin.get("referer_present"),
        "stream_status": decoded["stream_status"],
        "acceptance_gate_evidence": False,
    }
    target = output / f"offline-pyav-source-probe-{language}.json"
    target.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


def compare_open_options(language, output=DEFAULT_OUTPUT):
    """Compare one FFmpeg open option at a time against the unchanged baseline.

    The diagnostic baseline removes the product's WAV analysis limit before
    applying each one-variable variant. The same fixed prefix is delivered over
    the paced local HTTP origin. Every
    run fully decodes the stream, so byte count and SHA-256 detect dropped or
    changed PCM. This is diagnostic only and makes no provider calls.
    """
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    manifest = prepare_sample(language, output)
    origin_log = output / f"offline-pyav-options-{language}-origin.jsonl"
    result_path = output / f"offline-pyav-options-{language}.json"
    if origin_log.exists() or result_path.exists():
        raise FileExistsError("refusing to overwrite prior PyAV option probe evidence")
    wav_data = Path(manifest["clip_path"]).read_bytes()
    sample = {"bytes": wav_data, "data_offset": _wav_data_offset(wav_data)}
    server = PacingOrigin(("127.0.0.1", 0), {language: sample}, origin_log, 0.1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_open = stream_source_module.av.open
    variants = (
        ("baseline", None),
        ("analyzeduration_100000", ("analyzeduration", "100000")),
        ("probesize_32768", ("probesize", "32768")),
    )
    rows = []
    try:
        for name, option in variants:
            case_id = f"{language}-startup-probe-{name}"
            open_timing = {}

            def measured_open(*args, _option=option, **kwargs):
                options = dict(kwargs.get("options") or {})
                options.pop("analyzeduration", None)
                if _option is not None:
                    options[_option[0]] = _option[1]
                kwargs["options"] = options
                open_timing["analysis_options"] = {
                    key: options[key] for key in ("analyzeduration", "probesize") if key in options}
                started_ns = time.monotonic_ns()
                try:
                    return original_open(*args, **kwargs)
                finally:
                    open_timing["elapsed_ms"] = round(
                        (time.monotonic_ns() - started_ns) / 1e6, 1)

            stream_source_module.av.open = measured_open
            url = (f"http://127.0.0.1:{server.server_port}/{language}.wav"
                   f"?startup_case={case_id}")
            started_ns = time.monotonic_ns()
            source = None
            iterator = None
            digest = hashlib.sha256()
            pcm_bytes = 0
            first_yield_ns = None
            try:
                source = StreamSource(url)
                constructor_done_ns = time.monotonic_ns()
                iterator = iter(source.iter_chunks())
                for chunk in iterator:
                    if first_yield_ns is None:
                        first_yield_ns = time.monotonic_ns()
                    pcm_bytes += len(chunk)
                    digest.update(chunk)
            finally:
                if iterator is not None:
                    iterator.close()
                if source is not None:
                    source.close()
                stream_source_module.av.open = original_open
            rows.append({
                "variant": name,
                "analysis_options": open_timing["analysis_options"],
                "provider_calls": 0,
                "av_open_elapsed_ms": open_timing.get("elapsed_ms"),
                "constructor_elapsed_ms": round((constructor_done_ns - started_ns) / 1e6, 1),
                "first_pcm_yield_after_constructor_ms": round(
                    (first_yield_ns - constructor_done_ns) / 1e6, 1),
                "first_pcm_yield_after_start_ms": round(
                    (first_yield_ns - started_ns) / 1e6, 1),
                "first_pcm_yield_monotonic_ns": first_yield_ns,
                "decoded_pcm_bytes": pcm_bytes,
                "decoded_pcm_sha256": digest.hexdigest(),
                "expected_pcm_bytes": manifest["clip_frames"] * 2,
                "full_pcm_length_match": pcm_bytes == manifest["clip_frames"] * 2,
                "case_id": case_id,
            })
    finally:
        stream_source_module.av.open = original_open
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    baseline = rows[0]
    assert [row["analysis_options"] for row in rows] == [
        {}, {"analyzeduration": "100000"}, {"probesize": "32768"}], "probe variants are not isolated"
    origin_rows = _read_jsonl(origin_log)
    origins_by_case = {
        row.get("case_id"): row for row in origin_rows
        if row.get("method") == "GET"
        and row.get("requester_role") == "application_stream_source"
        and row.get("referer_present") is False
    }
    for row in rows:
        row["matches_baseline_pcm"] = (
            row["decoded_pcm_bytes"] == baseline["decoded_pcm_bytes"]
            and row["decoded_pcm_sha256"] == baseline["decoded_pcm_sha256"])
        origin = origins_by_case.get(row["case_id"], {})
        first_write_ns = origin.get("origin_first_pcm_write_started_monotonic_ns")
        first_yield_ns = row.pop("first_pcm_yield_monotonic_ns")
        row["origin_first_pcm_write_to_first_yield_ms"] = (
            round((first_yield_ns - first_write_ns) / 1e6, 1)
            if isinstance(first_write_ns, int) and isinstance(first_yield_ns, int) else None)
    origin_counts = {
        row["case_id"]: sum(1 for item in origin_rows
                            if item.get("case_id") == row["case_id"]
                            and item.get("method") == "GET"
                            and item.get("requester_role") == "application_stream_source"
                            and item.get("referer_present") is False)
        for row in rows
    }
    result = {
        "probe_only": True,
        "baseline": "counterfactual default analysis with the product WAV limit removed",
        "provider_calls": 0,
        "language": language,
        "source_sha256": manifest["source_sha256"],
        "clip_sha256": manifest["clip_sha256"],
        "source_interval_seconds": manifest["source_interval_seconds"],
        "pacing_seconds_per_pcm_chunk": 0.1,
        "application_get_counts_by_case": origin_counts,
        "all_variants_have_one_correlated_no_referer_get": all(
            count == 1 for count in origin_counts.values()),
        "all_variants_match_baseline_pcm": all(row["matches_baseline_pcm"] for row in rows),
        "all_variants_match_expected_pcm_length": all(row["full_pcm_length_match"] for row in rows),
        "acceptance_gate_evidence": False,
        "runs": rows,
    }
    result_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--language", choices=("zh", "en"), required=True)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--compare-open-options", action="store_true",
                        help="compare analyzeduration=100000 and probesize=32768 separately against baseline")
    args = parser.parse_args()
    if args.compare_open_options:
        compare_open_options(args.language, args.output)
    else:
        run(args.language, args.output)
