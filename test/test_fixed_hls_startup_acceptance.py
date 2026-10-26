"""Synthetic-only tests for the fixed-input live-HLS startup method."""
import hashlib
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.parse import urljoin
from urllib.request import Request, urlopen
import wave

import numpy as np
import pytest

import fixed_hls_startup_acceptance as hls
import fixed_realtime_startup_acceptance as startup


@pytest.fixture
def synthetic_hls_samples(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "ROOT", tmp_path)
    specs = {}
    for language, frequency in (("zh", 440), ("en", 660)):
        source_path = tmp_path / f"{language}-source.wav"
        samples = (0.12 * np.sin(2 * np.pi * frequency * np.arange(48_000) / 16_000))
        pcm = (samples * 32767).astype("<i2").tobytes()
        with wave.Wave_write(str(source_path)) as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(startup.PCM_RATE)
            writer.writeframes(pcm)
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        reference_path = tmp_path / f"{language}-reference.json"
        reference_path.write_text(json.dumps({"asset_sha256": source_sha}), encoding="utf-8")
        specs[language] = {
            "source": source_path,
            "reference": reference_path,
            "source_sha256": source_sha,
            "end_frame": 48_000,
            "reference_items": 1,
        }
    monkeypatch.setattr(startup, "SAMPLES", specs)
    return specs


def test_generated_hls_retains_wav_lineage_and_marks_aac_as_lossy(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)

    assert provenance["source_wav_sha256"] == startup._sha256(
        startup.SAMPLES["zh"]["source"])
    assert provenance["fixed_wav_clip_sha256"] == startup._sha256(
        provenance["fixed_wav_clip_path"])
    assert provenance["encoded_hls_playlist_sha256"] == startup._sha256(
        provenance["encoded_hls_playlist_path"])
    assert provenance["encoded_segments"]
    for segment in provenance["encoded_segments"]:
        assert segment["sha256"] == startup._sha256(
            Path(provenance["sample_directory"]) / segment["filename"])
        assert segment["duration_seconds"] > 0
    assert len(provenance["decoded_pcm_sha256"]) == 64
    assert provenance["pyav_version"]
    assert "libavcodec" in provenance["libav_versions"]
    assert provenance["aac_lossy"] is True
    assert provenance["time_stretch_applied"] is False
    assert provenance["transcription_accuracy_claim"] is False
    assert abs(provenance["decoded_pcm_duration_seconds"]
               - provenance["source_interval_seconds"]["end"]) < 0.25


def test_full_hls_demux_preserves_short_tail_and_rejects_payload_mismatch(
        tmp_path, synthetic_hls_samples, monkeypatch):
    spec = synthetic_hls_samples["en"]
    source_samples = 48_800  # 3.05 s forces a final partial AAC access unit.
    signal = (0.12 * np.sin(2 * np.pi * 660 * np.arange(source_samples) / 16_000))
    pcm = (signal * 32767).astype("<i2").tobytes()
    with wave.Wave_write(str(spec["source"])) as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(16_000)
        writer.writeframes(pcm)
    source_sha = hashlib.sha256(spec["source"].read_bytes()).hexdigest()
    spec["source_sha256"] = source_sha
    spec["end_frame"] = source_samples
    spec["reference"].write_text(json.dumps({"asset_sha256": source_sha}), encoding="utf-8")

    provenance = hls.prepare_hls_sample("en", tmp_path)
    final_segment = provenance["encoded_segments"][-1]
    decoded_hash, decoded_count, decoded_duration, decoded_first_pts = hls._decode_pcm_provenance(
        provenance["encoded_hls_playlist_path"])

    # Auto-probing this short TS tail can select MP2 and fabricate its bytes.
    import av
    with av.open(str(Path(provenance["sample_directory"]) / final_segment["filename"]),
                 format="mpegts") as tail:
        assert tail.streams.audio[0].codec_context.name == "aac"
        assert sum(packet.size > 0 for packet in tail.demux(audio=0)) > 0
    assert final_segment["audio_end_seconds"] > final_segment["playlist_end_seconds"]
    assert provenance["fixed_wav_pcm_sample_count"] == source_samples
    assert provenance["decoded_from_playlist_sha256"] == provenance[
        "encoded_hls_playlist_sha256"]
    assert provenance["first_aac_packet_pts_seconds"] == provenance[
        "encoded_segments"][0]["audio_start_seconds"]
    assert provenance["first_decoded_aac_frame_pts_seconds"] == round(decoded_first_pts, 6)
    assert provenance["decoder_initial_offset_seconds"] == round(
        decoded_first_pts - provenance["first_aac_packet_pts_seconds"], 6)
    assert (decoded_hash, decoded_count, decoded_duration) == (
        provenance["decoded_pcm_sha256"], provenance["decoded_pcm_sample_count"],
        provenance["decoded_pcm_duration_seconds"])

    original_packet_hash = hls._packet_hash
    observed_packets = set()

    def changed_payload_on_second_read(packet):
        identity = (packet.pts, packet.size)
        if identity in observed_packets:
            return "0" * 64
        observed_packets.add(identity)
        return original_packet_hash(packet)

    monkeypatch.setattr(hls, "_packet_hash", changed_payload_on_second_read)
    with pytest.raises(ValueError, match="not bound to the full HLS stream"):
        hls.prepare_hls_sample("en", tmp_path)


def test_live_playlist_exposes_only_source_clock_segments_and_never_endlist(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    first_segment = provenance["encoded_segments"][0]
    before_first_complete = max(0.0, first_segment["end_seconds"] - 0.02)

    initial = hls.render_live_playlist(provenance, "zh-startup-01", 0.0)
    almost_first_complete = hls.render_live_playlist(
        provenance, "zh-startup-01", before_first_complete)
    first_complete = hls.render_live_playlist(
        provenance, "zh-startup-01", first_segment["end_seconds"])
    second_segment = provenance["encoded_segments"][1]
    second_complete = hls.render_live_playlist(
        provenance, "zh-startup-01", second_segment["end_seconds"])

    assert "#EXT-X-ENDLIST" not in initial
    assert "#EXT-X-ENDLIST" not in almost_first_complete
    assert "#EXT-X-ENDLIST" not in first_complete
    assert "#EXT-X-ENDLIST" not in second_complete
    assert "seg000.ts?startup_case=zh-startup-01" not in initial
    assert "seg000.ts?startup_case=zh-startup-01" not in almost_first_complete
    assert "seg000.ts?startup_case=zh-startup-01" in first_complete
    assert "seg001.ts?startup_case=zh-startup-01" not in first_complete
    assert "seg001.ts?startup_case=zh-startup-01" in second_complete


def test_browser_precheck_does_not_wait_for_or_start_application_clock(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    log_path = tmp_path / "origin-requests.jsonl"
    server = hls.PacedHLSOrigin(("127.0.0.1", 0), {"zh": provenance}, log_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    case = "zh-startup-01"
    url = f"http://127.0.0.1:{server.server_port}/zh.m3u8?startup_case={case}"
    try:
        with urlopen(Request(url, headers={"User-Agent": startup.APP_UA,
                     "Referer": "https://www.bilibili.com"}), timeout=1) as response:
            body = response.read().decode("utf-8")
        assert "#EXT-X-TARGETDURATION" in body
        assert "#EXTINF" not in body and "#EXT-X-ENDLIST" not in body
        assert server.case_start(case) is None
        with urlopen(Request(url, headers={"User-Agent": startup.APP_UA}),
                     timeout=2) as response:
            assert "#EXTINF" in response.read().decode("utf-8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    rows = startup._read_jsonl(log_path)
    probe = next(row for row in rows if row.get("pre_application_manifest_probe"))
    assert hls._valid_manifest_observation(probe, provenance, case)
    assert probe["case_source_clock_started_monotonic_ns"] is None
    assert probe["visible_segment_indices"] == []
    changed = dict(probe, visible_segment_indices=[0])
    assert not hls._valid_manifest_observation(changed, provenance, case)


def test_origin_paces_real_mpegts_body_and_records_app_vs_browser_proxy(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    log_path = tmp_path / "origin-requests.jsonl"
    server = hls.PacedHLSOrigin(("127.0.0.1", 0), {"zh": provenance}, log_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    app_url = f"http://127.0.0.1:{server.server_port}/zh.m3u8?startup_case=zh-startup-01"
    try:
        origin_started = time.monotonic()
        with urlopen(Request(app_url, headers={"User-Agent": startup.APP_UA}), timeout=3) as response:
            playlist = response.read().decode("utf-8")
        segment_url = next(line for line in playlist.splitlines()
                           if line.endswith(".ts?startup_case=zh-startup-01"))
        segment_url = urljoin(app_url, segment_url)
        with urlopen(Request(segment_url, headers={"User-Agent": startup.APP_UA}), timeout=4) as response:
            segment_bytes = response.read()
        first_audio_available_elapsed = time.monotonic() - origin_started

        browser_url = app_url
        with urlopen(Request(browser_url, headers={
                "User-Agent": startup.APP_UA, "Referer": "https://www.bilibili.com"}),
                timeout=3) as response:
            browser_playlist = response.read().decode("utf-8")
        browser_segment_line = next(line for line in browser_playlist.splitlines()
                                    if line.endswith(".ts?startup_case=zh-startup-01"))
        browser_segment_url = urljoin(browser_url, browser_segment_line)
        with urlopen(Request(browser_segment_url, headers={
                "User-Agent": startup.APP_UA, "Referer": "https://www.bilibili.com"}),
                timeout=4) as response:
            browser_segment_bytes = response.read()

        future_segment = provenance["encoded_segments"][1]
        future_url = (f"http://127.0.0.1:{server.server_port}/zh/"
                      f"{future_segment['filename']}?startup_case=zh-startup-01")
        future_requested = time.monotonic()
        with urlopen(Request(future_url, headers={"User-Agent": startup.APP_UA}), timeout=4) as response:
            future_segment_bytes = response.read()
        future_segment_elapsed = time.monotonic() - future_requested
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    rows = startup._read_jsonl(log_path)
    app_rows = [row for row in rows if row["requester_role"] == "application_stream_source"]
    browser_rows = [row for row in rows if row["requester_role"] == "webui_browser_media_proxy"]
    app_segment = next(row for row in app_rows if row["resource_kind"] == "audio_video_segment")
    browser_segment = next(row for row in browser_rows
                           if row["resource_kind"] == "audio_video_segment")
    assert segment_bytes == browser_segment_bytes
    assert future_segment_bytes
    assert first_audio_available_elapsed >= provenance["encoded_segments"][0]["end_seconds"] - 0.08
    assert future_segment_elapsed >= max(
        0.0,
        provenance["encoded_segments"][1]["end_seconds"]
        - provenance["encoded_segments"][0]["end_seconds"] - 0.08,
    )
    assert app_segment["referer_present"] is False
    assert app_segment["segment_index"] == 0
    assert browser_segment["referer_present"] is True
    assert app_segment["case_query_present"] is True
    assert app_segment["first_audio_body_write_started_monotonic_ns"] > 0
    assert app_segment["body_sha256"] == provenance["encoded_segments"][0]["sha256"]
    assert (app_segment["first_audio_body_write_started_monotonic_ns"]
            >= app_segment["case_source_clock_started_monotonic_ns"]
            + int(provenance["encoded_segments"][0]["end_seconds"] * 1e9) - 10_000_000)
    future_body = next(row for row in app_rows if row.get("segment_index") == 1)
    assert (future_body["first_audio_body_write_started_monotonic_ns"]
            >= future_body["case_source_clock_started_monotonic_ns"]
            + int(provenance["encoded_segments"][1]["end_seconds"] * 1e9) - 10_000_000)
    assert any(row["resource_kind"] == "playlist" for row in app_rows)
    assert any(row["resource_kind"] == "playlist" for row in browser_rows)
    assert all(row["playlist_has_endlist"] is False for row in rows
               if row["resource_kind"] == "playlist")


def test_generated_live_hls_decodes_through_real_stream_source_without_eof_prefetch(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    log_path = tmp_path / "origin-requests.jsonl"
    server = hls.PacedHLSOrigin(("127.0.0.1", 0), {"zh": provenance}, log_path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/zh.m3u8?startup_case=zh-startup-01"
    source = None
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        from stream_source import StreamSource

        source = StreamSource(url, chunk_sec=0.1)
        assert source.container.format.name == "hls"
        chunks = iter(source.iter_chunks())
        first_pcm = next(chunks)
        assert first_pcm
        assert len(first_pcm) == 3200
        assert source.first_pts_sec is not None
        chunks.close()
    finally:
        if source is not None:
            source.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    rows = startup._read_jsonl(log_path)
    assert any(row["requester_role"] == "application_stream_source"
               and row["resource_kind"] == "playlist" for row in rows)
    assert any(row["requester_role"] == "application_stream_source"
               and row["resource_kind"] == "audio_video_segment" for row in rows)
    app_segments = [row for row in rows if row["requester_role"] == "application_stream_source"
                    and row["resource_kind"] == "audio_video_segment"]
    assert app_segments[0]["segment_index"] == 0
    assert all(row["resource_kind"] != "playlist" or row["playlist_has_endlist"] is False
               for row in rows)


def test_hls_analyzer_rejects_ambiguous_first_audio_body_and_missing_browser_branch(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    run = _write_one_valid_hls_case(tmp_path, provenance)
    args = SimpleNamespace(language="zh", output=tmp_path, origin_log=None, decoded_log=None)

    valid_evidence = hls.analyze(args)
    assert valid_evidence["hls_evidence_validated"] is True
    assert valid_evidence["acceptance_gate_passed"] is False  # one run cannot satisfy 20.
    assert valid_evidence["cases"][0]["case_valid"] is True
    assert valid_evidence["hls_cases"][0]["first_app_audio_segment_index"] == 0
    assert valid_evidence["hls_live_pipeline_observed_for_all_starts"] is True
    assert "requires exactly 20 valid non-simulated independent starts" in (
        valid_evidence["gate_blockers"])
    assert not any("requires observed HLS" in blocker
                   for blocker in valid_evidence["gate_blockers"])

    origin_path = tmp_path / "origin-requests.jsonl"
    rows = startup._read_jsonl(origin_path)
    app_segment = next(row for row in rows if row["requester_role"] == "application_stream_source"
                       and row["resource_kind"] == "audio_video_segment")
    skipped_segment = provenance["encoded_segments"][1]
    app_segment.update({
        "segment_index": skipped_segment["index"],
        "segment_start_seconds": skipped_segment["start_seconds"],
        "segment_duration_seconds": skipped_segment["duration_seconds"],
        "expected_body_sha256": skipped_segment["sha256"],
        "body_sha256": skipped_segment["sha256"],
        "first_audio_body_write_started_monotonic_ns": (
            app_segment["case_source_clock_started_monotonic_ns"]
            + int(skipped_segment["end_seconds"] * 1e9)),
    })
    origin_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n",
                          encoding="utf-8")
    skipped_prefix = hls.analyze(args)
    assert skipped_prefix["hls_evidence_validated"] is False
    assert any("skipped the fixed audio prefix" in blocker
               for blocker in skipped_prefix["hls_gate_blockers"])

    rows = startup._read_jsonl(origin_path)
    app_segment = next(row for row in rows if row["requester_role"] == "application_stream_source"
                       and row["resource_kind"] == "audio_video_segment")
    origin_path.write_text("\n".join(json.dumps(row) for row in [*rows, app_segment]) + "\n",
                          encoding="utf-8")
    ambiguous = hls.analyze(args)
    assert ambiguous["hls_evidence_validated"] is False
    assert ambiguous["acceptance_gate_passed"] is False
    assert any("ambiguous" in blocker for blocker in ambiguous["hls_gate_blockers"])

    rows = [row for row in rows if row["requester_role"] != "webui_browser_media_proxy"]
    origin_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    no_browser = hls.analyze(args)
    assert no_browser["hls_evidence_validated"] is False
    assert no_browser["acceptance_gate_passed"] is False


def test_hls_gate_rejects_missing_provenance_and_non_hls_decode_metadata(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    _write_one_valid_hls_case(tmp_path, provenance)
    args = SimpleNamespace(language="zh", output=tmp_path, origin_log=None, decoded_log=None)

    sidecar_path = tmp_path / "hls-runs" / "zh-startup-01.json"
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    sidecar["decoded_pcm_sha256"] = "0" * 64
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    missing_provenance = hls.analyze(args)
    assert missing_provenance["hls_evidence_validated"] is False
    assert missing_provenance["acceptance_gate_passed"] is False
    assert any("provenance is missing or mismatched" in blocker
               for blocker in missing_provenance["hls_gate_blockers"])

    sidecar["decoded_pcm_sha256"] = provenance["decoded_pcm_sha256"]
    sidecar_path.write_text(json.dumps(sidecar), encoding="utf-8")
    decode_path = tmp_path / "decoded-arrivals.jsonl"
    decode = json.loads(decode_path.read_text(encoding="utf-8"))
    decode["is_live_hls"] = False
    decode_path.write_text(json.dumps(decode) + "\n", encoding="utf-8")
    non_hls = hls.analyze(args)
    assert non_hls["hls_evidence_validated"] is False
    assert non_hls["acceptance_gate_passed"] is False
    assert non_hls["hls_live_pipeline_observed_for_all_starts"] is False
    assert any("not uniquely correlated" in blocker
               for blocker in non_hls["hls_gate_blockers"])


def test_existing_hls_sample_rejects_different_decode_runtime(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    path = Path(provenance["sample_directory"]) / "provenance.json"
    previous = json.loads(path.read_text(encoding="utf-8"))
    previous["pyav_version"] = "different-runtime"
    path.write_text(json.dumps(previous), encoding="utf-8")

    with pytest.raises(ValueError, match="different PyAV/libav runtime"):
        hls.prepare_hls_sample("zh", tmp_path)


def test_shared_startup_gate_rejects_simulated_gaps_and_errors_for_hls(
        tmp_path, synthetic_hls_samples):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    _write_one_valid_hls_case(tmp_path, provenance)
    args = SimpleNamespace(language="zh", output=tmp_path, origin_log=None, decoded_log=None)
    report_path = tmp_path / "collector" / "report.json"
    original = json.loads(report_path.read_text(encoding="utf-8"))

    for mutation in (
            {"simulated_recognition": True},
            {"sampling": {"status": "measurement_invalid", "sample_count": 3}},
            {"errors": [{"type": "source_gap"}]},
    ):
        report_path.write_text(json.dumps({**original, **mutation}), encoding="utf-8")
        result = hls.analyze(args)
        assert result["hls_evidence_validated"] is True
        assert result["acceptance_gate_passed"] is False
        assert result["cases"][0]["case_valid"] is False


def test_collect_refuses_real_recognition_without_explicit_acknowledgement(tmp_path):
    args = SimpleNamespace(allow_real_recognition=False, language="zh", output=tmp_path)
    with pytest.raises(ValueError, match="pass --allow-real-recognition explicitly"):
        hls.collect_one(args)
    assert not (tmp_path / "runs").exists()
    assert not (tmp_path / "samples").exists()


def _write_one_valid_hls_case(output, provenance):
    label = "zh-startup-01"
    base_ns = time.monotonic_ns()
    sample_directory = Path(provenance["sample_directory"])
    run_directory = output / "runs"
    hls_run_directory = output / "hls-runs"
    report_directory = output / "collector"
    run_directory.mkdir(exist_ok=True)
    hls_run_directory.mkdir(exist_ok=True)
    report_directory.mkdir(exist_ok=True)
    report_path = report_directory / "report.json"
    recording_path = report_directory / "recording.json"
    report_path.write_text(json.dumps({
        "simulated_recognition": False,
        "recording_status": "cancelled",
        "operator_stop": {"trigger": "operator_signal", "delete_status": 200},
        "session_post_start_count": 1,
        "sampling": {"status": "valid", "sample_count": 3},
        "clock_calibration": {
            "method": "bracketed_same_host_monotonic_to_browser_performance_clock",
            "sampler_origin_monotonic_ns": base_ns,
            "uncertainty_ms": 1,
        },
        "preparation": {"first_ready_after_ms": 300, "first_playing_after_ms": 400},
        "timeline_samples": [
            {"observed_after_ms": 500, "visible": True, "visible_text": "Synthetic sentence.",
             "paused": False, "ended": False, "buffering": False, "media_time": 0.1},
            {"observed_after_ms": 550, "visible": True, "visible_text": "Synthetic sentence.",
             "paused": False, "ended": False, "buffering": False, "media_time": 0.15},
            {"observed_after_ms": 600, "visible": True, "visible_text": "Synthetic sentence.",
             "paused": False, "ended": False, "buffering": False, "media_time": 0.2},
        ],
        "errors": [],
    }), encoding="utf-8")
    recording_path.write_text(json.dumps({"events": [{"type": "ready", "delay": 0}]}),
                              encoding="utf-8")
    (run_directory / f"{label}.json").write_text(json.dumps({
        "label": label,
        "language": "zh",
        "startup_class": "cold",
        "cold_first_session_confirmed": True,
        "service_pid": 123,
        "service_process_start_ticks": 456,
        "source_sha256": provenance["source_wav_sha256"],
        "clip_sha256": provenance["fixed_wav_clip_sha256"],
        "source_interval_seconds": provenance["source_interval_seconds"],
        "input_pipeline": "live_hls",
        "media_suffix": ".m3u8",
        "recognition_started": True,
        "simulated_recognition": False,
        "report_path": str(report_path),
        "recording_path": str(recording_path),
    }), encoding="utf-8")
    (hls_run_directory / f"{label}.json").write_text(json.dumps({
        "label": label,
        "language": "zh",
        "source_wav_sha256": provenance["source_wav_sha256"],
        "fixed_wav_clip_sha256": provenance["fixed_wav_clip_sha256"],
        "fixed_wav_pcm_sha256": provenance["fixed_wav_pcm_sha256"],
        "fixed_wav_pcm_sample_count": provenance["fixed_wav_pcm_sample_count"],
        "hls_provenance_sha256": provenance["provenance_sha256"],
        "encoded_hls_playlist_sha256": provenance["encoded_hls_playlist_sha256"],
        "encoded_segments": provenance["encoded_segments"],
        "decoded_from_playlist_sha256": provenance["decoded_from_playlist_sha256"],
        "pyav_version": provenance["pyav_version"],
        "libav_versions": provenance["libav_versions"],
        "decoded_pcm_sha256": provenance["decoded_pcm_sha256"],
        "decoded_pcm_sample_count": provenance["decoded_pcm_sample_count"],
        "decoded_pcm_duration_seconds": provenance["decoded_pcm_duration_seconds"],
        "first_aac_packet_pts_seconds": provenance["first_aac_packet_pts_seconds"],
        "first_decoded_aac_frame_pts_seconds": provenance[
            "first_decoded_aac_frame_pts_seconds"],
        "decoder_initial_offset_seconds": provenance["decoder_initial_offset_seconds"],
        "aac_lossy": True,
        "time_stretch_applied": False,
        "transcription_accuracy_claim": False,
    }), encoding="utf-8")
    segment = provenance["encoded_segments"][0]
    source_clock_ns = base_ns - 1_000_000_000
    first_body_ns = source_clock_ns + int(segment["end_seconds"] * 1e9)
    playlist_sha = hashlib.sha256(hls.render_live_playlist(
        provenance, label, segment["end_seconds"]).encode("utf-8")).hexdigest()
    requests = []
    for role, referer in (("application_stream_source", False),
                          ("webui_browser_media_proxy", True)):
        requests.append({
            "case_id": label, "method": "GET", "requester_role": role,
            "referer_present": referer, "resource_kind": "playlist",
            "request_started_monotonic_ns": source_clock_ns,
            "case_source_clock_started_monotonic_ns": source_clock_ns,
            "case_query_present": True, "playlist_has_endlist": False,
        "visible_segment_indices": [0], "source_elapsed_seconds": segment["end_seconds"],
            "playlist_sha256": playlist_sha,
            "response_status": 200, "response_complete": True,
        })
        requests.append({
            "case_id": label, "method": "GET", "requester_role": role,
            "referer_present": referer, "resource_kind": "audio_video_segment",
            "segment_index": 0, "segment_start_seconds": segment["start_seconds"],
            "segment_duration_seconds": segment["duration_seconds"],
            "segment_end_seconds": segment["end_seconds"],
            "request_started_monotonic_ns": first_body_ns,
            "case_source_clock_started_monotonic_ns": source_clock_ns,
            "case_query_present": True,
            "first_audio_body_write_started_monotonic_ns": first_body_ns,
            "first_audio_body_write_completed_monotonic_ns": first_body_ns + 500_000_000,
            "first_app_audio_body": role == "application_stream_source",
            "expected_body_sha256": segment["sha256"],
            "body_sha256": segment["sha256"], "complete_response": True,
            "response_status": "complete",
        })
    (output / "origin-requests.jsonl").write_text(
        "\n".join(json.dumps(row) for row in requests) + "\n", encoding="utf-8")
    (output / "decoded-arrivals.jsonl").write_text(json.dumps({
        "event": "stream_source_pcm_summary", "case_id": label,
        "process_id": 123, "is_live_hls": True, "stream_status": "closed",
        "pyav_version": provenance["pyav_version"],
        "libav_versions": provenance["libav_versions"],
        "first_decoded_pcm_monotonic_ns": first_body_ns + 600_000_000,
    }) + "\n", encoding="utf-8")
    return provenance


@pytest.mark.parametrize("problem", [
    "server_pyav_version", "server_libav_versions", "server_runtime_missing",
    "manifest_incomplete", "manifest_completion_missing",
])
def test_hls_audit_rejects_wrong_server_runtime_or_incomplete_manifest(
        tmp_path, synthetic_hls_samples, problem):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    _write_one_valid_hls_case(tmp_path, provenance)
    args = SimpleNamespace(language="zh", output=tmp_path, origin_log=None, decoded_log=None)
    if problem.startswith("server_"):
        path = tmp_path / "decoded-arrivals.jsonl"
        row = json.loads(path.read_text())
        if problem == "server_pyav_version":
            row["pyav_version"] = "different-runtime"
        elif problem == "server_libav_versions":
            row["libav_versions"]["libavcodec"] = [0, 0, 0]
        else:
            row.pop("pyav_version")
            row.pop("libav_versions")
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    else:
        path = tmp_path / "origin-requests.jsonl"
        rows = startup._read_jsonl(path)
        row = next(item for item in rows if item["resource_kind"] == "playlist")
        if problem == "manifest_incomplete":
            row["response_complete"] = False
        else:
            row.pop("response_complete")
        path.write_text("\n".join(json.dumps(item) for item in rows) + "\n", encoding="utf-8")
    result = hls.analyze(args)
    assert result["hls_evidence_validated"] is False
    assert result["acceptance_gate_passed"] is False
    assert any(("runtime" if problem.startswith("server_") else "manifest") in blocker
               for blocker in result["hls_gate_blockers"])


@pytest.mark.parametrize("problem", ["none", "only_probe", "after_app", "future_segment"])
def test_manifest_precheck_cannot_replace_actual_browser_hls_evidence(
        tmp_path, synthetic_hls_samples, problem):
    del synthetic_hls_samples
    provenance = hls.prepare_hls_sample("zh", tmp_path)
    _write_one_valid_hls_case(tmp_path, provenance)
    path = tmp_path / "origin-requests.jsonl"
    rows = startup._read_jsonl(path)
    browser = next(row for row in rows if row["resource_kind"] == "playlist"
                   and row["requester_role"] == "webui_browser_media_proxy")
    probe = dict(browser, pre_application_manifest_probe=True,
                 case_source_clock_started_monotonic_ns=None,
                 request_started_monotonic_ns=browser["request_started_monotonic_ns"] - 1,
                 visible_segment_indices=[], source_elapsed_seconds=0,
                 playlist_sha256=hashlib.sha256(hls.render_live_playlist(
                     provenance, "zh-startup-01", 0).encode()).hexdigest())
    if problem == "only_probe":
        rows.remove(browser)
    elif problem == "after_app":
        probe["request_started_monotonic_ns"] = browser["request_started_monotonic_ns"] + 1
    elif problem == "future_segment":
        probe["visible_segment_indices"] = [0]
    rows.append(probe)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    result = hls.analyze(SimpleNamespace(language="zh", output=tmp_path,
                                       origin_log=None, decoded_log=None))
    assert result["hls_evidence_validated"] is (problem == "none")
    assert result["acceptance_gate_passed"] is False  # A single case is never 20 starts.
