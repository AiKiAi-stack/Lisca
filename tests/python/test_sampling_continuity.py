import json

import sampling_continuity as continuity


def sample(at, text="", visible=None):
    return {
        "observed_after_ms": at,
        "visible_text": text,
        "visible": bool(text.strip()) if visible is None else visible,
    }


def test_even_samples_are_valid():
    result = continuity.analyze([sample(0, "a"), sample(50, "a"), sample(100, "a")])
    assert result["status"] == "valid"
    assert result["gap_count"] == 0
    assert result["max_observed_gap_ms"] == 50
    assert result["sample_count"] == 3


def test_gap_with_caption_change_is_bounded():
    result = continuity.analyze([sample(0, "hello"), sample(150, "")])
    assert result["status"] == "bounded"
    assert result["bounded_gap_count"] == 1
    assert result["unbounded_gap_count"] == 0
    assert result["max_transition_bound_ms"] == 150
    assert result["gaps"][0]["text_changed"] is True


def test_gap_with_visibility_change_is_bounded():
    result = continuity.analyze([
        sample(0, "hello", visible=True),
        sample(120, "hello", visible=False),
    ])
    assert result["status"] == "bounded"
    assert result["gaps"][0]["visible_changed"] is True


def test_gap_without_endpoint_change_is_unbounded():
    result = continuity.analyze([sample(0, "hello"), sample(160, "hello")])
    assert result["status"] == "unbounded"
    assert result["bounded_gap_count"] == 0
    assert result["unbounded_gap_count"] == 1
    assert result["uncertain_span_ms"] == 160


def test_mixed_gaps_are_unbounded_if_any_hole_remains():
    result = continuity.analyze([
        sample(0, "a"), sample(150, "b"), sample(320, "b"),
    ])
    assert result["status"] == "unbounded"
    assert result["bounded_gap_count"] == 1
    assert result["unbounded_gap_count"] == 1
    assert result["max_transition_bound_ms"] == 150


def test_tolerance_is_inclusive():
    result = continuity.analyze([sample(0, "a"), sample(100, "b")])
    assert result["status"] == "valid"
    assert result["gap_count"] == 0


def test_insufficient_samples_are_reported_not_passed():
    assert continuity.analyze([])["status"] == "insufficient_samples"
    assert continuity.analyze([sample(0)])["status"] == "insufficient_samples"


def test_invalid_timestamps_are_ignored():
    result = continuity.analyze([
        sample(0, "a"),
        {"observed_after_ms": "bad", "visible_text": "b"},
        sample(50, "a"),
    ])
    assert result["sample_count"] == 2
    assert result["status"] == "valid"


def test_report_analysis_keeps_declared_status_separate(tmp_path):
    report = {
        "label": "case",
        "sampling": {"status": "measurement_invalid"},
        "timeline_samples": [sample(0, "a"), sample(140, "b")],
    }
    path = tmp_path / "report.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))

    result = continuity.analyze_report(loaded)

    assert result["status"] == "bounded"
    assert result["declared_sampling_status"] == "measurement_invalid"
    assert result["report_label"] == "case"
