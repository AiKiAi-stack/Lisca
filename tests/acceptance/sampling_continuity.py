"""Analyze browser acceptance sampling gaps without weakening the gate.

The browser collector requests a DOM sample every 50 ms and marks a run
``measurement_invalid`` when any two consecutive observations are more than
100 ms apart.  For each such gap this module reports whether the endpoints
differ:

* ``bounded`` means at least one endpoint state change was observed and some
  change therefore happened inside the gap.  The gap width bounds the time of
  that observed change, but it does not show there was nothing else in the
  interval: other changes may have happened during the gap without being
  observed.
* ``unbounded`` means the endpoints agree; a short visible/empty transition
  could have happened entirely inside the gap.

A ``bounded`` gap is supplementary description only.  It does not make the
original sampling valid, and it must not be used to pass time-sensitive
metrics.  This module does not change the collector's ``measurement_invalid``
status or any acceptance threshold; callers decide whether a bounded gap is
acceptable for their measurement.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _visible_text(sample: dict[str, Any]) -> str:
    text = sample.get("visible_text")
    return text if isinstance(text, str) else ""


def analyze(samples: Iterable[dict[str, Any]], tolerance_ms: float = 100) -> dict[str, Any]:
    """Classify every inter-sample gap against a fixed tolerance.

    A gap larger than ``tolerance_ms`` is ``bounded`` when the visible text or
    visibility flag differs between its endpoints.  At least one endpoint
    change was then observed inside the gap and its time is bounded by the gap
    width, but other changes inside the interval cannot be excluded.
    A gap whose endpoints agree is ``unbounded``: a full transition could have
    occurred and reverted inside it without being observed.
    """
    ordered = [sample for sample in samples if isinstance(sample, dict)]
    points = [(sample, _number(sample.get("observed_after_ms"))) for sample in ordered]
    points = [(sample, at) for sample, at in points if at is not None]
    if len(points) < 2:
        return {
            "tolerance_ms": tolerance_ms,
            "sample_count": len(points),
            "observed_span_ms": 0,
            "max_observed_gap_ms": 0,
            "gap_count": 0,
            "bounded_gap_count": 0,
            "unbounded_gap_count": 0,
            "max_transition_bound_ms": 0,
            "uncertain_span_ms": 0,
            "status": "insufficient_samples",
            "gaps": [],
        }

    gaps: list[dict[str, Any]] = []
    max_gap = 0
    for (before, begin), (after, end) in zip(points, points[1:]):
        width = end - begin
        max_gap = max(max_gap, width)
        if width <= tolerance_ms:
            continue
        text_changed = _visible_text(before) != _visible_text(after)
        visible_changed = bool(before.get("visible")) != bool(after.get("visible"))
        gaps.append({
            "start_ms": begin,
            "end_ms": end,
            "width_ms": width,
            "text_changed": text_changed,
            "visible_changed": visible_changed,
            "bounded": text_changed or visible_changed,
            "before_text": _visible_text(before),
            "after_text": _visible_text(after),
            "before_visible": bool(before.get("visible")),
            "after_visible": bool(after.get("visible")),
        })

    bounded = [gap for gap in gaps if gap["bounded"]]
    unbounded = [gap for gap in gaps if not gap["bounded"]]
    status = "valid" if not gaps else ("bounded" if not unbounded else "unbounded")
    return {
        "tolerance_ms": tolerance_ms,
        "sample_count": len(points),
        "observed_span_ms": points[-1][1] - points[0][1],
        "max_observed_gap_ms": max_gap,
        "gap_count": len(gaps),
        "bounded_gap_count": len(bounded),
        "unbounded_gap_count": len(unbounded),
        "max_transition_bound_ms": max((gap["width_ms"] for gap in bounded), default=0),
        "uncertain_span_ms": sum(gap["width_ms"] for gap in unbounded),
        "status": status,
        "gaps": gaps,
    }


def analyze_report(report: dict[str, Any], tolerance_ms: float = 100) -> dict[str, Any]:
    """Analyze the ``timeline_samples`` of a real-browser acceptance report."""
    samples = report.get("timeline_samples")
    if not isinstance(samples, list):
        samples = []
    result = analyze(samples, tolerance_ms)
    declared = (report.get("sampling") or {}).get("status")
    result["declared_sampling_status"] = declared
    result["report_label"] = report.get("label")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="real-browser acceptance report JSON")
    parser.add_argument("--tolerance-ms", type=float, default=100)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    print(json.dumps(analyze_report(report, args.tolerance_ms), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
