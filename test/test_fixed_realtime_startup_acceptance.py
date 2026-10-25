import json
import hashlib
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from urllib.request import Request, urlopen
import wave

import pytest

import fixed_realtime_startup_acceptance as startup
import startup_timing_probe as probe


@pytest.fixture(autouse=True)
def uploads_unavailable_to_tests(tmp_path, monkeypatch):
    for language, spec in startup.SAMPLES.items():
        monkeypatch.setitem(
            startup.SAMPLES, language,
            {**spec, "source": tmp_path / f"{language}-uploaded-asset-must-not-be-used.wav"})


def test_fixed_prefix_prepare_contract_uses_synthetic_reference_wavs(tmp_path, monkeypatch):
    monkeypatch.setattr(startup, "ROOT", tmp_path)
    for language, end_frame in (("zh", 64), ("en", 80)):
        source_path = tmp_path / f"{language}-source.wav"
        source_pcm = b"".join((sample - 80).to_bytes(2, "little", signed=True)
                               for sample in range(160))
        with wave.Wave_write(str(source_path)) as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(startup.PCM_RATE)
            writer.writeframes(source_pcm)
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        reference_path = tmp_path / f"{language}-reference.json"
        reference_path.write_text(json.dumps({"asset_sha256": source_sha}), encoding="utf-8")
        spec = {
            "source": source_path, "reference": reference_path,
            "source_sha256": source_sha, "end_frame": end_frame,
            "reference_items": 1,
        }
        monkeypatch.setitem(startup.SAMPLES, language, spec)

        manifest = startup.prepare_sample(language, tmp_path)
        assert manifest["source_sha256"] == source_sha
        assert manifest["clip_frames"] == end_frame
        assert manifest["source_interval_seconds"] == {
            "start": 0.0, "end": end_frame / startup.PCM_RATE,
        }
        assert manifest["clip_sha256"] == hashlib.sha256(
            Path(manifest["clip_path"]).read_bytes()).hexdigest()
        with wave.open(manifest["clip_path"], "rb") as reader:
            assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(),
                    reader.getnframes()) == (1, 2, startup.PCM_RATE, end_frame)
            assert reader.readframes(end_frame) == source_pcm[:end_frame * 2]

        monkeypatch.setitem(startup.SAMPLES, language,
                            {**spec, "source_sha256": "0" * 64})
        with pytest.raises(ValueError, match=f"fixed {language} source hash changed"):
            startup.prepare_sample(language, tmp_path)
        monkeypatch.setitem(startup.SAMPLES, language, spec)

        reference_path.write_text(json.dumps({"asset_sha256": "0" * 64}), encoding="utf-8")
        with pytest.raises(ValueError, match="reference manifest does not bind"):
            startup.prepare_sample(language, tmp_path)
        reference_path.write_text(json.dumps({"asset_sha256": source_sha}), encoding="utf-8")

        if language == "zh":
            clip_path = manifest["clip_path"]
            with wave.open(clip_path, "rb") as reader:
                clip_pcm = reader.readframes(reader.getnframes())
            with wave.Wave_write(str(clip_path)) as writer:
                writer.setnchannels(1)
                writer.setsampwidth(2)
                writer.setframerate(8000)  # Same bytes, altered playback speed.
                writer.writeframes(clip_pcm)
            with pytest.raises(ValueError, match="existing clip WAV format differs"):
                startup.prepare_sample(language, tmp_path)


def test_stable_caption_requires_visible_identical_text_across_100ms():
    samples = [
        {"observed_after_ms": 100, "visible": True, "visible_text": "Hello."},
        {"observed_after_ms": 150, "visible": True, "visible_text": "Hello."},
        {"observed_after_ms": 200, "visible": True, "visible_text": "Hello."},
    ]
    assert startup.stable_caption_sample(samples) is samples[-1]
    assert startup.stable_caption_sample(samples[:2]) is None
    assert startup.stable_caption_sample([
        samples[0], {**samples[1], "visible_text": "Hello world."}, samples[2]
    ]) is None
    assert startup.stable_caption_sample([
        samples[0], {**samples[1], "observed_after_ms": 260}, samples[2]
    ]) is None


def test_stream_source_probe_records_numeric_first_decoded_pcm_without_url(tmp_path):
    class FakeStreamSource:
        def __init__(self, url_or_path):
            self.url_or_path = url_or_path

        def iter_chunks(self):
            yield b"\x01\x02" * 1600
            yield b"\x03\x04" * 1600

    fake_webui = SimpleNamespace(StreamSource=FakeStreamSource)
    log = tmp_path / "decoded.jsonl"
    probe.install_stream_source_probe(fake_webui, log)
    secret_url = "http://127.0.0.1:8765/zh.wav?startup_case=zh-startup-01"
    source = fake_webui.StreamSource(secret_url)
    chunks = list(source.iter_chunks())
    row = json.loads(log.read_text(encoding="utf-8"))
    assert len(chunks) == 2
    assert row["case_id"] == "zh-startup-01"
    assert row["first_decoded_pcm_monotonic_ns"] > 0
    assert row["first_chunk_bytes"] == 3200
    assert row["first_chunk_duration_ms"] == 100.0
    assert row["decoded_pcm_duration_ms"] == 200.0
    assert secret_url not in log.read_text(encoding="utf-8")
    assert not {"url", "transcript", "pcm_payload"}.intersection(row)


def test_browser_clock_calibration_maps_sampler_origin_to_host_monotonic():
    class FakePage:
        def evaluate(self, expression, *_args, **_kwargs):
            if expression == "sampler":
                return None
            return {"now": 1500.0, "start": 1250.0}

    fake_collector = SimpleNamespace(SAMPLER_START="sampler")
    state = {}
    page = startup._CalibratedPage(FakePage(), fake_collector, state)
    page.evaluate("sampler")
    calibration = state["clock_calibration"]
    assert calibration["method"] == "bracketed_same_host_monotonic_to_browser_performance_clock"
    assert calibration["sampler_origin_monotonic_ns"] > 0
    assert calibration["uncertainty_ms"] < 50


def test_origin_paces_audio_and_separates_application_ua_from_browser(tmp_path):
    import wave

    clip = tmp_path / "short.wav"
    with wave.Wave_write(str(clip)) as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(startup.PCM_RATE)
        writer.writeframes(b"\x00\x00" * 2400)
    data = clip.read_bytes()
    sample = {"bytes": data, "data_offset": startup._wav_data_offset(data)}
    log = tmp_path / "origin.jsonl"
    server = startup.PacingOrigin(("127.0.0.1", 0), {"zh": sample, "en": sample},
                                  log, 0.1)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        started = time.monotonic()
        request = Request(
            f"http://127.0.0.1:{server.server_port}/zh.wav?startup_case=zh-startup-01",
            headers={"User-Agent": startup.APP_UA},
        )
        with urlopen(request, timeout=3) as response:
            received = response.read()
        elapsed = time.monotonic() - started
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    row = json.loads(log.read_text(encoding="utf-8"))
    assert received == data
    assert elapsed >= 0.08
    assert row["requester_role"] == "application_stream_source"
    assert row["referer_present"] is False
    assert startup._requester_role(startup.APP_UA, "https://www.bilibili.com") == "webui_browser_media_proxy"
    assert row["origin_first_pcm_write_started_monotonic_ns"] > 0
    assert row["complete_response"] is True


def test_twenty_dummy_cases_measure_delay_but_never_pass_acceptance(tmp_path, monkeypatch):
    output = tmp_path
    fixed = {"source_sha256": "source", "clip_sha256": "clip",
             "clip_duration_seconds": 1.0, "source_interval_seconds": {"start": 0, "end": 1}}
    monkeypatch.setattr(startup, "prepare_sample", lambda _language, _output: fixed)
    runs = output / "runs"
    runs.mkdir()
    origin_rows, decode_rows = [], []
    for index in range(1, 21):
        label = f"zh-startup-{index:02d}"
        base_ns = 1_000_000_000 + index * 1_000_000_000
        report = {
            "simulated_recognition": True, "recording_status": "completed",
            "session_post_start_count": 1, "sampling": {"status": "valid", "sample_count": 3},
            "clock_calibration": {
                "method": "bracketed_same_host_monotonic_to_browser_performance_clock",
                "sampler_origin_monotonic_ns": base_ns, "uncertainty_ms": 1,
            },
            "preparation": {"first_ready_after_ms": 280, "first_playing_after_ms": 300},
            "timeline_samples": [
                {"observed_after_ms": 200, "visible": True, "visible_text": "Hello",
                 "paused": False, "ended": False, "buffering": False, "media_time": 0.05},
                {"observed_after_ms": 250, "visible": True, "visible_text": "Hello",
                 "paused": False, "ended": False, "buffering": False, "media_time": 0.10},
                {"observed_after_ms": 300, "visible": True, "visible_text": "Hello",
                 "paused": False, "ended": False, "buffering": False, "media_time": 0.15},
            ],
            "errors": [],
        }
        recording = {"events": [{"type": "ready", "delay": 0}]}
        report_path = output / f"{label}.report.json"
        recording_path = output / f"{label}.recording.json"
        report_path.write_text(json.dumps(report), encoding="utf-8")
        recording_path.write_text(json.dumps(recording), encoding="utf-8")
        (runs / f"{label}.json").write_text(json.dumps({
            "label": label, "language": "zh",
            "startup_class": "cold" if index == 1 else "warm",
            "cold_first_session_confirmed": index == 1,
            "service_pid": 9, "service_process_start_ticks": 42,
            "source_sha256": "source", "clip_sha256": "clip",
            "recognition_started": True, "simulated_recognition": False,
            "report_path": str(report_path), "recording_path": str(recording_path),
        }), encoding="utf-8")
        origin_rows.append({
            "case_id": label, "requester_role": "application_stream_source",
            "method": "GET", "referer_present": False,
            "origin_first_pcm_write_started_monotonic_ns": base_ns - 100_000_000,
        })
        decode_rows.append({
            "case_id": label, "event": "stream_source_pcm_summary",
            "stream_status": "completed", "process_id": 9,
            "first_decoded_pcm_monotonic_ns": base_ns + 100_000_000,
        })
    origin_log = output / "origin.jsonl"
    origin_log.write_text("\n".join(json.dumps(row) for row in origin_rows) + "\n",
                          encoding="utf-8")
    decode_log = output / "decoded.jsonl"
    decode_log.write_text("\n".join(json.dumps(row) for row in decode_rows) + "\n",
                          encoding="utf-8")

    args = SimpleNamespace(language="zh", output=output, origin_log=origin_log,
                           decoded_log=decode_log)
    result = startup.analyze(args)
    assert result["cases"][0]["acceptance_latency_upper_bound_from_origin_first_PCM_write_ms"] == 400.0
    assert result["cases"][0]["decoded_PCM_yield_to_visible_ms_diagnostic"] == 200.0
    assert result["acceptance_gate_passed"] is False
    assert result["attempted_starts"] == 20
    assert result["cold_starts"] == 1 and result["warm_starts"] == 19
    assert result["valid_correlated_upper_bound_starts"] == 0
    assert any("requires exactly 20 valid" in blocker for blocker in result["gate_blockers"])


def test_analyzer_rejects_missing_or_ambiguous_application_get(tmp_path, monkeypatch, capsys):
    fixed = {"source_sha256": "source", "clip_sha256": "clip",
             "clip_duration_seconds": 1.0, "source_interval_seconds": {"start": 0, "end": 1}}
    monkeypatch.setattr(startup, "prepare_sample", lambda _language, _output: fixed)
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    base_ns = 1_000_000_000
    report = {
        "simulated_recognition": False, "recording_status": "completed",
        "session_post_start_count": 1,
        "sampling": {"status": "valid", "sample_count": 3},
        "clock_calibration": {
            "method": "bracketed_same_host_monotonic_to_browser_performance_clock",
            "sampler_origin_monotonic_ns": base_ns, "uncertainty_ms": 1,
        },
        "preparation": {"first_ready_after_ms": 20, "first_playing_after_ms": 100},
        "timeline_samples": [
            {"observed_after_ms": 200, "visible": True, "visible_text": "Hi.",
             "paused": False, "ended": False, "buffering": False, "media_time": 0.05},
            {"observed_after_ms": 250, "visible": True, "visible_text": "Hi.",
             "paused": False, "ended": False, "buffering": False, "media_time": 0.10},
            {"observed_after_ms": 300, "visible": True, "visible_text": "Hi.",
             "paused": False, "ended": False, "buffering": False, "media_time": 0.15},
        ],
        "errors": [],
    }
    report_path = tmp_path / "one.report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    recording_path = tmp_path / "one.recording.json"
    recording_path.write_text(json.dumps({"events": [{"type": "ready", "delay": 0}]}),
                              encoding="utf-8")
    label = "zh-startup-01"
    run_path = runs_dir / f"{label}.json"
    run_path.write_text(json.dumps({
        "label": label, "language": "zh", "startup_class": "cold",
        "cold_first_session_confirmed": True, "service_pid": 9,
        "service_process_start_ticks": 42, "source_sha256": "source",
        "clip_sha256": "clip", "recognition_started": True,
        "simulated_recognition": False, "report_path": str(report_path),
        "recording_path": str(recording_path),
    }), encoding="utf-8")
    origin_log = tmp_path / "origin.jsonl"
    app_get = {
        "case_id": label, "requester_role": "application_stream_source",
        "method": "GET", "referer_present": False,
        "origin_first_pcm_write_started_monotonic_ns": base_ns - 100_000_000,
    }
    origin_log.write_text(json.dumps(app_get) + "\n", encoding="utf-8")
    decode_log = tmp_path / "decoded.jsonl"
    decode_row = {
        "case_id": label, "event": "stream_source_pcm_summary",
        "stream_status": "completed", "process_id": 9,
        "first_decoded_pcm_monotonic_ns": base_ns + 100_000_000,
    }
    decode_log.write_text(json.dumps(decode_row) + "\n", encoding="utf-8")
    args = SimpleNamespace(language="zh", output=tmp_path, origin_log=origin_log,
                           decoded_log=decode_log)

    result = startup.analyze(args)
    assert result["cases"][0]["case_valid"] is True
    assert result["cases"][0]["decoder_process_matches_service_pid"] is True
    assert result["acceptance_gate_passed"] is False  # one case is never the 20-start gate

    decode_log.write_text(json.dumps({**decode_row, "process_id": 10}) + "\n",
                          encoding="utf-8")
    foreign_decoder = startup.analyze(args)
    assert foreign_decoder["cases"][0]["case_valid"] is False
    assert foreign_decoder["cases"][0]["decoder_process_matches_service_pid"] is False

    decode_log.write_text(json.dumps(decode_row) + "\n", encoding="utf-8")
    run_meta = json.loads(run_path.read_text(encoding="utf-8"))
    run_meta["service_pid"] = 0
    run_path.write_text(json.dumps(run_meta), encoding="utf-8")
    invalid_service_pid = startup.analyze(args)
    assert invalid_service_pid["cases"][0]["case_valid"] is False
    assert invalid_service_pid["cases"][0]["decoder_process_matches_service_pid"] is False

    run_meta["service_pid"] = 9
    run_path.write_text(json.dumps(run_meta), encoding="utf-8")
    origin_log.write_text("", encoding="utf-8")
    missing = startup.analyze(args)
    assert missing["cases"][0]["case_valid"] is False
    assert missing["cases"][0]["application_arrival_boundary"] == "unobserved or uncorrelated"
    assert startup.main(["analyze", "--language", "zh", "--output", str(tmp_path)]) == 1
    capsys.readouterr()

    origin_log.write_text(json.dumps(app_get) + "\n" + json.dumps(app_get) + "\n",
                          encoding="utf-8")
    ambiguous = startup.analyze(args)
    assert ambiguous["cases"][0]["case_valid"] is False
    assert ambiguous["cases"][0]["application_arrival_boundary"] == "unobserved or uncorrelated"
