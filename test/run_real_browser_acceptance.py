"""Collect bounded real-player evidence through the configured WebUI.

The collector never configures recognition credentials or alters service routes.
Local files run to both video EOF and the normal recognition end event. HTTP(S)
sources stop at ``--seconds`` and are recorded as cancelled by that cap.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from urllib.parse import urlsplit

from e2e_original_release import launch_browser, snapshot, stop_if_active
from run_original_acceptance import (
    file_identity,
    redact_recording,
    redact_text,
    sha256_file,
    write_recording,
)
from score_original_acceptance import score
from playwright.sync_api import sync_playwright


BASE = "http://127.0.0.1:8002"
ROOT = Path(__file__).resolve().parents[1]
EVENT_FIELDS = {
    "subtitle": ("beg", "end", "text", "words", "is_final", "sentence_id",
                 "revision", "translated"),
    "caption_remove": ("beg", "sentence_id", "revision"),
    "preview": ("beg", "end", "text", "sentence_id"),
    "timeline": ("audio_pts",),
    "buffer": ("play_at", "local_silence_from", "local_silence_through"),
    "ready": ("delay", "start_at", "original", "buffered", "silent", "prepared",
              "progressive"),
    "error": ("failure_category", "retryable"),
    "end": ("reason",),
}
URL_PATTERN = re.compile(r"(?i)\b(?:https?|wss?)://[^\s<>\"']+")
BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/-]+=*")
PROVIDER_KEY_PATTERN = re.compile(r"(?i)\bsk-[a-z0-9_-]{16,}\b")
SENSITIVE_NAMES = {
    "api_key", "access_token", "refresh_token", "token", "session", "session_id",
    "session_token", "password", "passwd", "secret", "credential", "authorization",
    "cookie", "signature", "sig", "auth", "url", "source_url", "websocket_url",
}


def safe_remote_source(value):
    """Keep only the remote origin; paths and query strings can be capabilities."""
    try:
        parts = urlsplit(str(value))
        host = parts.hostname
        port = parts.port
    except (TypeError, ValueError):
        return "[redacted-media-url]"
    if parts.scheme.lower() not in ("http", "https") or not host:
        return "[redacted-media-url]"
    host = f"[{host}]" if ":" in host else host
    authority = f"{host}:{port}" if port else host
    return f"{parts.scheme.lower()}://{authority}/[redacted-media-url]"


def sanitize_text(value):
    def replace_url(match):
        candidate = match.group(0)
        suffix = ""
        while candidate and candidate[-1] in ".,;:!?)}]":
            suffix = candidate[-1] + suffix
            candidate = candidate[:-1]
        return safe_remote_source(candidate) + suffix

    text = URL_PATTERN.sub(replace_url, str(value))
    text = redact_text(text)
    text = BEARER_PATTERN.sub("Bearer [redacted]", text)
    return PROVIDER_KEY_PATTERN.sub("[redacted]", text)


def _safe_value(value):
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return [_safe_value(item) for item in value]
    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            name = str(key)
            normalized = name.lower().replace("-", "_")
            if (normalized in SENSITIVE_NAMES
                    or normalized.endswith(("_token", "_secret", "_password", "_credential",
                                            "_authorization", "_cookie", "_signature", "_auth"))):
                safe[name] = "[redacted]"
            else:
                safe[sanitize_text(name)] = _safe_value(item)
        return safe
    return value


def sanitize_ws_event(event, observed_after_ms):
    """Keep scorer-relevant public events while dropping transport capabilities."""
    if not isinstance(event, dict):
        return None
    event_type = event.get("type")
    if event_type not in EVENT_FIELDS:
        return None
    safe = {"type": event_type}
    for name in EVENT_FIELDS[event_type]:
        if name in event:
            safe[name] = _safe_value(event[name])
    if event_type == "error":
        # Provider error text can echo account data; retain its category and presence only.
        safe["message_present"] = bool(event.get("message")) or event.get("message_present") is True
    safe["observed_after_ms"] = max(0, round(float(observed_after_ms)))
    return safe


def _endpoint_category(method, path):
    method = str(method).upper()
    if path == "/share/start" and method == "POST":
        return "share_start"
    elif path == "/api/cloud/sessions" and method == "POST":
        return "session_start"
    elif re.fullmatch(r"/api/cloud/sessions/[^/]+", path) and method == "DELETE":
        return "session_stop"
    return "other"


def classify_http_response(method, path, status):
    category = _endpoint_category(method, path)
    diagnostic = ({"type": "http_response_error", "endpoint_category": category,
                   "status": int(status)} if int(status) >= 400 else None)
    failure = ({"type": "session_start_http_error", "status": int(status)}
               if category == "session_start" and not 200 <= int(status) < 300 else None)
    return diagnostic, failure


def classify_runtime_error(source, message=""):
    if source == "console":
        return "diagnostic", {"type": "console_error", "message": sanitize_text(message)}
    if source == "page":
        return "failure", {"type": "page_error", "message": sanitize_text(message)}
    return "failure", {"type": "websocket_transport_error"}


def fatal_player_sample(sample):
    code = sample.get("media_error_code") or 0
    if code:
        return {"type": "media_error", "code": int(code)}
    if sample.get("status_is_error"):
        return {"type": "ui_fatal"}
    return None


SAMPLER_START = r"""() => {
  if (window.__realAcceptanceSampler) return;
  const state = {samples: [], started: performance.now(), timer: null};
  const sample = () => {
    const video = document.querySelector('#player');
    const caption = document.querySelector('#current');
    const status = document.querySelector('#status');
    if (!video || !caption || !status) return;
    const css = getComputedStyle(caption);
    const lineHeight = parseFloat(css.lineHeight) || parseFloat(css.fontSize) * 1.3;
    const padding = parseFloat(css.paddingTop) + parseFloat(css.paddingBottom);
    let bufferedAhead = 0;
    for (let i = 0; i < video.buffered.length; i++) {
      if (video.buffered.start(i) <= video.currentTime && video.buffered.end(i) >= video.currentTime) {
        bufferedAhead = video.buffered.end(i) - video.currentTime;
        break;
      }
    }
    const text = caption.innerText || '';
    state.samples.push({
      observed_after_ms: Math.round(performance.now() - state.started),
      media_time: Number.isFinite(video.currentTime) ? video.currentTime : null,
      duration: Number.isFinite(video.duration) ? video.duration : null,
      ended: video.ended,
      paused: video.paused,
      seeking: video.seeking,
      ready_state: video.readyState,
      network_state: video.networkState,
      media_error_code: video.error ? video.error.code : 0,
      buffering: !video.paused && !video.ended && video.readyState < HTMLMediaElement.HAVE_FUTURE_DATA,
      buffered_ahead_seconds: bufferedAhead,
      visible_text: text,
      visible: !!text.trim() && !/(正在准备字幕|跳转中|正在跳转)/.test(text.trim())
        && !/^(⏳|⚠)/.test(text.trim())
        && caption.getClientRects().length > 0,
      status: status.innerText || '',
      status_is_error: status.classList.contains('err') || caption.classList.contains('err'),
      line_count: text.trim() ? (caption.scrollHeight - padding) / lineHeight : 0,
      clipped: caption.scrollWidth > caption.clientWidth + 1,
      height_clipped: caption.scrollHeight > caption.clientHeight + 1
    });
  };
  state.drain = () => state.samples.splice(0, state.samples.length);
  state.stop = () => { if (state.timer) clearInterval(state.timer); state.timer = null; sample(); };
  window.__realAcceptanceSampler = state;
  sample();
  state.timer = setInterval(sample, 50);
}"""


def drain_samples(page):
    try:
        samples = page.evaluate("() => window.__realAcceptanceSampler?.drain() || []")
    except Exception:
        return []
    return [_safe_value(sample) for sample in samples]


def write_browser_recording(media_path, output_path, events, status, completion_signal,
                            completion_reason, errors, started_at, started, metadata,
                            source_before=None):
    safe_events = [sanitize_ws_event(event, event.get("observed_after_ms", 0))
                   for event in events if isinstance(event, dict)]
    safe_events = [event for event in safe_events if event is not None]
    result = write_recording(
        media_path, output_path, safe_events, status, completion_signal, completion_reason,
        [_safe_value(error) for error in errors], started_at, started,
        metadata=_safe_value(metadata), source_before=source_before,
    )
    result.update({
        "simulated_recognition": False,
        "real_accuracy_tested": False,
        "acoustic_sync_assessed": False,
        "user_audio_heard": False,
        "human_reference_reviewed": False,
    })
    result = redact_recording(result)
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def _write_json(path, value):
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    safe = redact_recording(value)
    destination.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding="utf-8")
    return safe


def parse_checkpoints(value):
    if not value:
        return []
    try:
        result = [float(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("checkpoints must be comma-separated seconds") from exc
    if any(not math.isfinite(item) or item < 0 for item in result):
        raise argparse.ArgumentTypeError("checkpoints must be finite non-negative seconds")
    return sorted(set(result))


def _take_checkpoint(page, seconds, started, screenshot_path):
    video = page.locator("#player")
    duration = video.evaluate("video => Number.isFinite(video.duration) ? video.duration : null")
    if duration is None or seconds > duration:
        return {"target_seconds": seconds, "status": "outside_media_duration", "duration": duration}
    video.evaluate("(video, time) => { video.pause(); video.currentTime = time; }", seconds)
    try:
        page.wait_for_function(
            "time => { const v=document.querySelector('#player'); return !v.seeking && Math.abs(v.currentTime-time)<=0.1; }",
            arg=seconds, timeout=5000,
        )
    except Exception:
        pass
    page.wait_for_timeout(100)
    page.evaluate("() => new Promise(requestAnimationFrame)")
    visible = snapshot(page)
    media_time = visible.get("time")
    page.screenshot(path=str(screenshot_path), mask=[page.locator("#url")])
    return {
        "target_seconds": seconds,
        "observed_after_ms": round((time.monotonic() - started) * 1000),
        "media_time": media_time,
        "sampling_tolerance_seconds": 0.1,
        "within_sampling_tolerance": (media_time is not None and abs(media_time - seconds) <= 0.1),
        "paused": visible.get("paused"),
        "visible_text": sanitize_text(visible.get("text", "")),
        "status": sanitize_text(visible.get("status", "")),
        "line_count": visible.get("lines"),
        "clipped": visible.get("clipped"),
        "screenshot_path": str(screenshot_path),
    }

def _result_paths(output_dir, label):
    destination = Path(output_dir).expanduser()
    return (destination / f"{label}.recording.json",
            destination / f"{label}.report.json",
            destination / f"{label}.score.json")


def collect(args):
    output_dir = args.output or ROOT / "logs" / "real-browser-acceptance"
    recording_path, report_path, score_path = _result_paths(output_dir, args.label)
    remote = args.media.startswith(("http://", "https://"))
    media_path = safe_remote_source(args.media) if remote else str(Path(args.media).resolve())
    started_at = None
    started = None
    events, errors, samples, checkpoints, page_errors = [], [], [], [], []
    diagnostics, websocket_errors, session_post_failures = [], [], []
    posts = {"count": 0, "started": None}
    session_post_statuses = []
    delete_responses = []
    stop_state = {"requested": False, "trigger": None, "delete_status": None}
    source_before = None
    if not remote:
        try:
            source_before = {"identity": file_identity(media_path), "sha256": sha256_file(media_path)}
        except OSError:
            errors.append({"type": "local_input_unavailable"})
    live_cap, video_eof = False, False
    checkpoint_post_delta = None
    browser = page = None
    sampler_started = False
    next_progress_at = 0.0
    progress_path = Path(output_dir).expanduser() / f"{args.label}.progress.json"

    def write_progress():
        nonlocal next_progress_at
        now = time.monotonic()
        if started is None or now < next_progress_at:
            return
        next_progress_at = now + 2
        latest = samples[-1] if samples else {}
        last_subtitle = next((event for event in reversed(events)
                              if event["type"] == "subtitle"), {})
        word_ends = [word[1] for word in last_subtitle.get("words") or []
                     if len(word) > 1 and isinstance(word[1], (int, float))]
        through = [last_subtitle["end"]] if isinstance(last_subtitle.get("end"), (int, float)) else []
        _write_json(progress_path, {
            "label": args.label, "running": True,
            "elapsed_seconds": round(now - started, 3),
            "session_post_start_count": posts["count"],
            "sample_count": len(samples),
            "media_time": latest.get("media_time"),
            "paused": latest.get("paused"), "buffering": latest.get("buffering"),
            "caption_visible": latest.get("visible"),
            "latest_subtitle_observed_after_ms": last_subtitle.get("observed_after_ms"),
            "latest_subtitle_end": last_subtitle.get("end"),
            "latest_speech_through": max(through + word_ends, default=None),
            "error_count": len(errors) + len(page_errors) + len(websocket_errors),
        })

    def drain():
        nonlocal video_eof
        if page is not None and sampler_started:
            batch = drain_samples(page)
            samples.extend(batch)
            video_eof |= any(sample.get("ended") for sample in batch)

    def on_frame(payload):
        if isinstance(payload, bytes):
            try:
                payload = payload.decode("utf-8")
            except UnicodeDecodeError:
                return
        try:
            raw = json.loads(payload)
        except (TypeError, json.JSONDecodeError):
            return
        event = sanitize_ws_event(raw, (time.monotonic() - started) * 1000)
        if event:
            events.append(event)
            if event["type"] == "error":
                errors.append({"type": "recognition_error",
                               "failure_category": event.get("failure_category"),
                               "retryable": event.get("retryable")})

    def count_session_post(route):
        if route.request.method.upper() == "POST":
            posts["count"] += 1
            posts["started"] = posts["started"] or time.monotonic()
        route.continue_()

    def observe_response(response):
        request = response.request
        path = urlsplit(response.url).path
        diagnostic, failure = classify_http_response(request.method, path, response.status)
        if diagnostic:
            diagnostics.append(diagnostic)
        if failure:
            session_post_failures.append(failure)
        if request.method.upper() == "POST" and path == "/api/cloud/sessions":
            session_post_statuses.append(response.status)
        elif request.method.upper() == "DELETE" and re.fullmatch(
                r"/api/cloud/sessions/[^/]+", path):
            # Record only the status; the request URL contains the opaque capability.
            delete_responses.append(response.status)

    def on_request_failed(request):
        path = urlsplit(request.url).path
        category = _endpoint_category(request.method, path)
        if category != "other":
            diagnostics.append({"type": "request_failed", "endpoint_category": category})
        if request.method.upper() == "POST" and path == "/api/cloud/sessions":
            session_post_failures.append({"type": "session_start_request_failed"})

    def on_websocket(socket):
        socket.on("framereceived", on_frame)
        socket.on("socketerror", lambda message: websocket_errors.append(
            classify_runtime_error("websocket", message)[1]))

    def on_console(message):
        if message.type == "error":
            diagnostics.append(classify_runtime_error("console", message.text)[1])

    def on_page_error(error):
        page_errors.append(classify_runtime_error("page", str(error))[1])

    def stop_active(trigger):
        if page is None:
            return False
        try:
            active = page.locator("#stop").is_visible()
            response_count_before = len(delete_responses)
            stop_if_active(page)
        except Exception as exc:
            errors.append({"type": "cleanup_stop_error", "message": sanitize_text(str(exc))})
            return False
        if not active:
            return False
        stop_state["requested"] = True
        stop_state["action"] = "operator_cancelled"
        stop_state["trigger"] = trigger
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and len(delete_responses) <= response_count_before:
            try:
                page.wait_for_timeout(100)
            except Exception:
                break
        if len(delete_responses) > response_count_before:
            stop_state["delete_status"] = delete_responses[-1]
        return True

    def close_browser():
        if page is not None:
            if not stop_state["requested"]:
                stop_active("collector_cleanup")
            # WebSocket is expected to close before DELETE returns. Drain available public
            # events without treating the absence of an end event as a cleanup failure.
            drain()
            try:
                page.evaluate("() => window.__realAcceptanceSampler?.stop()")
                drain()
            except Exception:
                pass
        if browser is not None:
            browser.close()

    try:
        if not errors:
            with sync_playwright() as playwright:
                try:
                    browser = launch_browser(playwright)
                    page = browser.new_page(viewport={"width": 1280, "height": 800})
                    page.set_default_timeout(10_000)
                    page.route("**/api/cloud/sessions", count_session_post)
                    page.on("response", observe_response)
                    page.on("requestfailed", on_request_failed)
                    page.on("pageerror", on_page_error)
                    page.on("console", on_console)
                    page.on("websocket", on_websocket)
                    page.goto(BASE, wait_until="domcontentloaded", timeout=20_000)
                    page.locator("#url").fill(args.media)
                    started_at = datetime.now(timezone.utc).isoformat()
                    started = time.monotonic()
                    page.evaluate(SAMPLER_START)
                    sampler_started = True
                    page.locator("#start").click()

                    no_start_deadline = started + 15
                    while True:
                        drain()
                        write_progress()
                        if args.stop_file and args.stop_file.exists():
                            stop_active("operator_signal")
                            break
                        end = next((e for e in reversed(events) if e["type"] == "end"), None)
                        player_failure = next((fatal_player_sample(sample)
                                               for sample in reversed(samples[-5:])
                                               if fatal_player_sample(sample)), None)
                        if player_failure:
                            errors.append(player_failure)
                        if (page_errors or websocket_errors or player_failure
                                or session_post_failures
                                or any(e["type"] == "error" for e in events)):
                            errors.extend(session_post_failures)
                            break
                        if not posts["count"]:
                            if time.monotonic() >= no_start_deadline or any(
                                    s.get("status_is_error") for s in samples[-5:]):
                                errors.append({"type": "session_start_missing"})
                                break
                        elif not session_post_statuses:
                            if time.monotonic() - posts["started"] >= 15:
                                errors.append({"type": "session_start_response_missing"})
                                break
                        elif not 200 <= session_post_statuses[-1] < 300:
                            errors.append({"type": "session_start_http_error",
                                           "status": session_post_statuses[-1]})
                            break
                        elif remote:
                            if time.monotonic() - posts["started"] >= args.seconds:
                                live_cap = True
                                stop_active("observation_cap")
                                break
                            if end:
                                break
                        elif end:
                            if end.get("reason") != "completed" or video_eof:
                                break
                        if not remote and time.monotonic() - started >= args.timeout:
                            errors.append({"type": "local_timeout"})
                            break
                        if remote and time.monotonic() - started >= args.seconds + 15:
                            errors.append({"type": "live_start_timeout"})
                            break
                        page.wait_for_timeout(200)

                    drain()
                    last_reason = next((e.get("reason") for e in reversed(events)
                                        if e.get("type") == "end"), None)
                    if not remote and last_reason == "completed" and video_eof and not errors and not page_errors:
                        before = posts["count"]
                        checkpoint_dir = Path(output_dir).expanduser() / args.label
                        if args.checkpoints:
                            checkpoint_dir.mkdir(parents=True, exist_ok=True)
                        for point in args.checkpoints:
                            screenshot_path = checkpoint_dir / f"{point:g}s.png"
                            checkpoints.append(_take_checkpoint(page, point, started, screenshot_path))
                        checkpoint_post_delta = posts["count"] - before
                        if checkpoint_post_delta:
                            errors.append({"type": "recognition_post_after_completed_seek",
                                           "count": checkpoint_post_delta})
                finally:
                    # Stop and drain while Playwright's event loop is still alive.
                    close_browser()
    except Exception as exc:
        errors.append({"type": "collector_error", "message": sanitize_text(str(exc))})
    if started is None:
        started_at, started = datetime.now(timezone.utc).isoformat(), time.monotonic()
    errors.extend(page_errors)
    errors.extend(websocket_errors)
    terminal_reasons = [e.get("reason") for e in events if e.get("type") == "end"]
    last_reason = terminal_reasons[-1] if terminal_reasons else None
    terminal_error = last_reason in ("error", "failed")
    if terminal_error and not any(error.get("type") == "terminal_error" for error in errors):
        errors.append({"type": "terminal_error"})
    hard_error = bool(errors)
    if (stop_state["requested"]
            and stop_state["trigger"] in ("observation_cap", "operator_signal")
            and stop_state["delete_status"] == 200 and posts["count"]
            and not terminal_error and not hard_error):
        status, signal, reason = "cancelled", "cancelled", "cancelled"
    elif last_reason == "completed" and (remote or video_eof) and not hard_error:
        status, signal, reason = "completed", "end", "completed"
    else:
        status = "failed"
        signal = "end" if last_reason else ("cancelled" if live_cap else None)
        reason = last_reason or ("cancelled" if live_cap else "completion_unverified")
        if not remote and (last_reason != "completed" or not video_eof) and not hard_error:
            errors.append({"type": "local_eof_or_normal_end_missing"})
        if remote and live_cap and stop_state["delete_status"] != 200 and not hard_error:
            errors.append({"type": "live_stop_delete_unconfirmed"})
        if remote and not live_cap and last_reason not in (None, "completed") and not hard_error:
            errors.append({"type": "unexpected_remote_terminal_reason"})

    gaps = [b["observed_after_ms"] - a["observed_after_ms"] for a, b in zip(samples, samples[1:])]
    max_gap = max(gaps, default=0)
    measurement_invalid = len(samples) < 2 or max_gap > 100 or any(
        item.get("within_sampling_tolerance") is False for item in checkpoints)
    metadata = {"language": args.language, "media_kind": "remote_stream" if remote else "local_file",
                "sample_interval_ms": 50, "sampling_tolerance_seconds": 0.1,
                "session_post_start_count": posts["count"], "video_eof_observed": video_eof,
                "live_seconds_cap_reached": live_cap,
                "operator_stop": stop_state.copy()}
    recording = write_browser_recording(media_path, recording_path, events, status, signal, reason,
                                        errors, started_at, started, metadata, source_before)
    errors = recording.get("errors", errors)
    score_result = None
    if args.reference and args.language and recording["recording_status"] == "completed":
        try:
            manifest = json.loads(Path(args.reference).read_text(encoding="utf-8"))
            score_result = _write_json(score_path, score(manifest, recording, args.language))
            recording["real_accuracy_tested"] = True
            _write_json(recording_path, recording)
        except Exception as exc:
            errors.append({"type": "scoring_error", "message": sanitize_text(str(exc))})
            recording["real_accuracy_tested"] = False
            recording["errors"] = _safe_value(errors)
            _write_json(recording_path, recording)

    actual_captions = [s for s in samples if s.get("visible")]
    first_caption = actual_captions[0] if actual_captions else None
    first_ready = next((e for e in events if e.get("type") == "ready"), None)
    first_playing = next((s for s in samples if not s.get("paused") and (s.get("media_time") or 0) > 0), None)
    overflow = [s for s in actual_captions if s.get("line_count", 0) > 2.1
                or s.get("clipped") or s.get("height_clipped")]
    report = {
        "label": args.label, "media_kind": metadata["media_kind"],
        "recording_status": recording["recording_status"],
        "completion_signal": recording.get("completion_signal"),
        "completion_reason": recording.get("completion_reason"),
        "terminal_reasons": terminal_reasons,
        "simulated_recognition": False,
        "real_accuracy_tested": recording["real_accuracy_tested"],
        "acoustic_sync_assessed": False, "user_audio_heard": False,
        "human_reference_reviewed": False,
        "source_verified": score_result.get("source_verified") if score_result else None,
        "source_verified_from": "score_original_acceptance.score" if score_result else None,
        "score_path": str(score_path) if score_result else None,
        "session_post_start_count": posts["count"], "operator_stop": stop_state.copy(),
        "session_post_starts_during_completed_seek": checkpoint_post_delta,
        "video_eof_observed": video_eof, "live_seconds_cap_reached": live_cap,
        "sampling": {"requested_interval_ms": 50, "tolerance_ms": 100,
                     "sample_count": len(samples), "max_observed_gap_ms": round(max_gap, 1),
                     "status": "measurement_invalid" if measurement_invalid else "valid"},
        "preparation": {
            "first_ready_after_ms": first_ready.get("observed_after_ms") if first_ready else None,
            "first_playing_after_ms": first_playing.get("observed_after_ms") if first_playing else None,
            "first_visible_caption": ({"after_ms": first_caption["observed_after_ms"],
                                       "media_time": first_caption["media_time"],
                                       "text": first_caption["visible_text"]} if first_caption else None),
        },
        "timeline_samples": samples, "visible_caption_overflow_samples": overflow,
        "checkpoints": checkpoints, "errors": errors, "diagnostics": diagnostics,
        "recording_path": str(recording_path),
    }
    _write_json(report_path, report)
    _write_json(progress_path, {
        "label": args.label, "running": False,
        "recording_status": recording["recording_status"],
        "live_seconds_cap_reached": live_cap,
        "operator_stop_trigger": stop_state.get("trigger"),
        "report_path": str(report_path),
    })
    success = (recording["recording_status"] == "completed"
               or (remote and live_cap and recording["recording_status"] == "cancelled"))
    return {"label": args.label, "recording_status": recording["recording_status"],
            "session_post_start_count": posts["count"], "timeline_sample_count": len(samples),
            "simulated_recognition": False, "real_accuracy_tested": recording["real_accuracy_tested"],
            "measurement_status": "measurement_invalid" if measurement_invalid else "valid",
            "exit_ok": success and not errors and not measurement_invalid,
            "recording_path": str(recording_path), "report_path": str(report_path)}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media", required=True, help="Local media path or HTTP(S) stream URL")
    parser.add_argument("--label", required=True, help="Short artifact label")
    parser.add_argument("--output", type=Path, default=None, help="Artifact output directory")
    parser.add_argument("--seconds", type=float, default=300,
                        help="Maximum observation seconds for HTTP(S) sources")
    parser.add_argument("--timeout", type=float, default=420,
                        help="Maximum wall seconds for local-file recognition and playback")
    parser.add_argument("--reference", type=Path, help="Optional fixed reference manifest")
    parser.add_argument("--language", choices=("zh", "en"), help="Language for optional scoring")
    parser.add_argument("--checkpoints", type=parse_checkpoints, default=[],
                        help="Optional comma-separated local seek checkpoints, such as 5.1,23.8")
    parser.add_argument("--stop-file", type=Path,
                        help="Cancel only this observation if the file appears; preserve its report")
    args = parser.parse_args(argv)
    if args.stop_file and args.stop_file.exists():
        parser.error("--stop-file already exists; no observation was started")
    if not args.media.startswith(("http://", "https://")):
        args.media = str(Path(args.media).expanduser().resolve())
    if not math.isfinite(args.seconds) or args.seconds <= 0:
        parser.error("--seconds must be a finite positive duration")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be a finite positive duration")
    if args.reference and not args.language:
        parser.error("--language is required with --reference")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.label):
        parser.error("--label must contain 1-80 letters, numbers, dots, underscores, or hyphens")
    return args


def main(argv=None):
    args = parse_args(argv)
    result = collect(args)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["exit_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
