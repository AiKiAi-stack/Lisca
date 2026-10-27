import json
import time

import pytest

import run_real_browser_acceptance as acceptance


def test_capped_recording_keeps_cancelled_truth_and_redacts_transport_secrets(tmp_path):
    media = tmp_path / "sample.mp4"
    media.write_bytes(b"fixed local test input")
    output = tmp_path / "recording.json"
    source_before = {"identity": acceptance.file_identity(media),
                     "sha256": acceptance.sha256_file(media)}
    secret = "bearer-value-that-must-not-escape"
    events = [
        acceptance.sanitize_ws_event({
            "type": "subtitle", "beg": 0, "end": 1, "text": "Keep these words",
            "is_final": True, "sentence_id": "1", "authorization": f"Bearer {secret}",
            "session_url": f"wss://media.example/ws?session={secret}",
        }, 25),
    ]

    recording = acceptance.write_browser_recording(
        media_path=str(media), output_path=output, events=events, status="cancelled",
        completion_signal="cancelled", completion_reason="cancelled", errors=[],
        started_at="2026-10-25T00:00:00+00:00", started=time.monotonic() - 5,
        metadata={"session_post_start_count": 1}, source_before=source_before,
    )

    saved = json.loads(output.read_text(encoding="utf-8"))
    assert recording["recording_status"] == saved["recording_status"] == "cancelled"
    assert recording["completion_reason"] == "cancelled"
    assert not any(event["type"] == "end" for event in saved["events"])
    assert recording["simulated_recognition"] is False
    assert recording["real_accuracy_tested"] is False
    assert recording["acoustic_sync_assessed"] is False
    assert recording["user_audio_heard"] is False
    assert recording["input_unchanged"] is True
    assert saved["final_cues"][0]["text"] == "Keep these words"
    assert "source_verified" not in saved
    artifact_text = output.read_text(encoding="utf-8")
    assert secret not in artifact_text
    assert "Bearer " not in artifact_text
    assert "session_url" not in artifact_text


def test_optional_share_http_failure_is_diagnostic_but_session_start_is_fatal():
    share_diagnostic, share_failure = acceptance.classify_http_response(
        "POST", "/share/start", 500)
    session_diagnostic, session_failure = acceptance.classify_http_response(
        "POST", "/api/cloud/sessions", 500)
    stop_path = "/api/cloud/sessions/private-capability"
    stop_diagnostic, stop_failure = acceptance.classify_http_response(
        "DELETE", stop_path, 500)

    assert share_diagnostic == {
        "type": "http_response_error", "endpoint_category": "share_start", "status": 500}
    assert share_failure is None
    assert session_diagnostic == {
        "type": "http_response_error", "endpoint_category": "session_start", "status": 500}
    assert session_failure == {"type": "session_start_http_error", "status": 500}
    assert stop_diagnostic == {
        "type": "http_response_error", "endpoint_category": "session_stop", "status": 500}
    assert stop_failure is None
    assert "private-capability" not in str((session_diagnostic, session_failure,
                                            stop_diagnostic, stop_failure))


def test_player_failure_classification_is_separate_from_console_diagnostics():
    console_kind, console = acceptance.classify_runtime_error(
        "console", "Failed to load resource: HTTP 500 https://host.invalid/private?token=secret")
    page_kind, _page_error = acceptance.classify_runtime_error("page", "uncaught callback")
    websocket_kind, websocket_error = acceptance.classify_runtime_error("websocket")

    assert console_kind == "diagnostic"
    assert console["type"] == "console_error"
    assert "secret" not in console["message"]
    assert page_kind == "failure"
    assert websocket_kind == "failure"
    assert websocket_error == {"type": "websocket_transport_error"}
    assert acceptance.fatal_player_sample({"status_is_error": False,
                                            "media_error_code": 0}) is None
    assert acceptance.fatal_player_sample({"status_is_error": True,
                                            "media_error_code": 0}) == {"type": "ui_fatal"}
    assert acceptance.fatal_player_sample({"status_is_error": False,
                                            "media_error_code": 4}) == {
        "type": "media_error", "code": 4}


def test_browser_launch_failure_cannot_report_valid_sampling(tmp_path, monkeypatch):
    media = tmp_path / "sample.wav"
    media.write_bytes(b"source identity only; browser launch must fail first")

    def fail_launch(_playwright):
        raise RuntimeError("browser dependency unavailable")

    monkeypatch.setattr(acceptance, "launch_browser", fail_launch)
    args = acceptance.parse_args(["--media", str(media), "--label", "no-browser",
                                  "--output", str(tmp_path)])
    result = acceptance.collect(args)
    report = json.loads((tmp_path / "no-browser.report.json").read_text())

    assert result["session_post_start_count"] == 0
    assert result["exit_ok"] is False
    assert report["sampling"]["sample_count"] == 0
    assert result["measurement_status"] == "measurement_invalid"
    assert report["sampling"]["status"] == "measurement_invalid"


def test_acoustic_capture_requires_a_local_reference_bound_input(tmp_path):
    reference = tmp_path / "reference.json"
    media = tmp_path / "sample.wav"
    reference.write_text("{}", encoding="utf-8")
    media.write_bytes(b"input identity only")

    with pytest.raises(SystemExit):
        acceptance.parse_args(["--media", str(media), "--label", "missing-ref",
                               "--capture-acoustic-sync"])
    with pytest.raises(SystemExit):
        acceptance.parse_args(["--media", "https://media.test/live.m3u8",
                               "--label", "remote-capture", "--reference", str(reference),
                               "--language", "en", "--capture-acoustic-sync"])

    args = acceptance.parse_args(["--media", str(media), "--label", "local-capture",
                                  "--reference", str(reference), "--language", "en",
                                  "--capture-acoustic-sync"])

    assert args.capture_acoustic_sync is True


def test_error_message_presence_survives_recording_sanitization_without_raw_text(tmp_path):
    event = acceptance.sanitize_ws_event({"type": "error",
        "message": "private provider details must not be saved"}, 30)
    recording = acceptance.write_browser_recording(
        media_path="https://media.test/live.m3u8", output_path=tmp_path / "error.json",
        events=[event], status="failed", completion_signal=None,
        completion_reason="completion_unverified", errors=[],
        started_at="2026-10-25T00:00:00+00:00", started=time.monotonic(), metadata={})

    assert recording["events"][0]["message_present"] is True
    assert "private provider details" not in (tmp_path / "error.json").read_text()


def _run_collect_with_fake_browser(monkeypatch, tmp_path, *, label, seconds, on_start,
                                   observe_recovery=True):
    clock = {"now": 100.0}

    class Request:
        def __init__(self, method, url):
            self.method = method
            self.url = url

    class Response:
        def __init__(self, request, status):
            self.request = request
            self.url = request.url
            self.status = status

    class Socket:
        def __init__(self, url="ws://127.0.0.1:8002/ws?session=TEST_ONLY_SOCKET_CAPABILITY"):
            self.url = url
            self.listeners = {}

        def on(self, name, callback):
            self.listeners[name] = callback

        def send(self, event):
            self.listeners["framereceived"](json.dumps(event))

    class Locator:
        def __init__(self, page, selector):
            self.page = page
            self.selector = selector

        def fill(self, _value):
            pass

        def click(self):
            if self.selector == "#start":
                self.page.start()
            elif self.selector == "#stop":
                self.page.stop()

        def is_visible(self):
            return self.selector == "#stop" and self.page.active

    class Page:
        def __init__(self):
            self.handlers = {}
            self.routes = []
            self.scheduled = []
            self.sample_buffer = []
            self.next_sample_at = None
            self.sampler_started_at = None
            self.active = False
            self.status = "正在识别音频…"
            self.status_is_error = False
            self.retry_visible = False
            self.caption = ""

        def on(self, name, callback):
            self.handlers.setdefault(name, []).append(callback)

        def route(self, _pattern, callback):
            self.routes.append(callback)

        def set_default_timeout(self, _milliseconds):
            pass

        def goto(self, *_args, **_kwargs):
            pass

        def locator(self, selector):
            return Locator(self, selector)

        def evaluate(self, script):
            if script == acceptance.SAMPLER_START:
                self.sampler_started_at = clock["now"]
                self.next_sample_at = clock["now"]
                self._sample()
                return None
            if "__realAcceptanceSampler?.drain()" in script:
                batch, self.sample_buffer = self.sample_buffer, []
                return batch
            if "__realAcceptanceSampler?.stop()" in script:
                self._sample()
                return None
            raise AssertionError(f"Unexpected collector page evaluation: {script}")

        def _sample(self):
            elapsed = max(0.0, clock["now"] - self.sampler_started_at)
            self.sample_buffer.append({
                "observed_after_ms": round(elapsed * 1000),
                "media_time": elapsed,
                "duration": None,
                "ended": False,
                "paused": False,
                "seeking": False,
                "ready_state": 4,
                "network_state": 1,
                "media_error_code": 0,
                "buffering": False,
                "buffered_ahead_seconds": 1.0,
                "visible_text": self.caption,
                "visible": bool(self.caption),
                "status": self.status,
                "status_is_error": self.status_is_error,
                "retry_visible": self.retry_visible,
                "stop_visible": self.active,
                "line_count": 1 if self.caption else 0,
                "clipped": False,
                "height_clipped": False,
            })

        def _post(self, method):
            url = (acceptance.BASE + "/api/cloud/sessions" if method == "POST" else
                   acceptance.BASE + "/api/cloud/sessions/TEST_ONLY_SESSION_CAPABILITY")
            request = Request(method, url)
            if method == "POST":
                for callback in self.routes:
                    callback(type("Route", (), {"request": request,
                                                    "continue_": lambda _self: None})())
            for callback in self.handlers.get("response", []):
                callback(Response(request, 200))

        def open_session(self):
            self._post("POST")
            return self.open_socket()

        def open_socket(self, url="ws://127.0.0.1:8002/ws?session=TEST_ONLY_SOCKET_CAPABILITY"):
            socket = Socket(url)
            for callback in self.handlers.get("websocket", []):
                callback(socket)
            return socket

        def start(self):
            self.active = True
            on_start(self)

        def stop(self):
            self.active = False
            self._post("DELETE")

        def schedule(self, after_seconds, callback):
            self.scheduled.append((clock["now"] + after_seconds, callback))
            self.scheduled.sort(key=lambda event: event[0])

        def wait_for_timeout(self, milliseconds):
            target = clock["now"] + milliseconds / 1000
            while self.scheduled and self.scheduled[0][0] <= target:
                at, callback = self.scheduled.pop(0)
                clock["now"] = at
                callback()
            clock["now"] = target
            while self.next_sample_at is not None and self.next_sample_at <= target:
                self._sample()
                self.next_sample_at += 0.05

    page = Page()

    class Browser:
        def new_page(self, **_kwargs):
            return page

        def close(self):
            pass

    class Playwright:
        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            pass

    monkeypatch.setattr(acceptance.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(acceptance, "sync_playwright", Playwright)
    monkeypatch.setattr(acceptance, "launch_browser", lambda _playwright: Browser())

    def stop_if_active(active_page):
        if active_page.locator("#stop").is_visible():
            active_page.locator("#stop").click()

    monkeypatch.setattr(acceptance, "stop_if_active", stop_if_active)
    argv = ["--media", "https://media.test/live.m3u8?token=TEST_ONLY_MEDIA_CAPABILITY",
            "--label", label, "--output", str(tmp_path), "--seconds", str(seconds)]
    if observe_recovery:
        argv.append("--observe-recovery")
    args = acceptance.parse_args(argv)
    result = acceptance.collect(args)
    report = json.loads((tmp_path / f"{label}.report.json").read_text(encoding="utf-8"))
    recording = json.loads((tmp_path / f"{label}.recording.json").read_text(encoding="utf-8"))
    return result, report, recording, page, clock


def _retry_error(socket):
    socket.send({"type": "error", "failure_category": "network", "retryable": True})


def test_opt_in_observation_tracks_recovery_generation_and_keeps_original_cap(
        tmp_path, monkeypatch):
    def on_start(page):
        page.open_socket(
            "ws://127.0.0.1:8002/share/TEST_ONLY_SHARE_CAPABILITY/ws?viewer_id=private")
        first = page.open_session()
        first.send({"type": "subtitle", "beg": 0, "end": 1, "text": "First cue",
                    "is_final": True, "sentence_id": "same-id", "revision": 1})
        _retry_error(first)
        page.retry_visible = True
        page.schedule(1.1, lambda: reconnect(page))

        def reconnect(target):
            second = target.open_session()
            target.retry_visible = False
            target.caption = "Recovered cue"
            second.send({"type": "subtitle", "beg": 1, "end": 2, "text": "Recovered cue",
                         "is_final": True, "sentence_id": "same-id", "revision": 1})
            # A delayed end from the old socket must not terminate the new generation.
            target.schedule(0.2, lambda: first.send({"type": "end", "reason": "error"}))

    result, report, recording, _page, clock = _run_collect_with_fake_browser(
        monkeypatch, tmp_path, label="recovery-cap", seconds=1.5, on_start=on_start)

    assert report["session_post_start_count"] == 2
    assert report["live_seconds_cap_reached"] is True
    assert report["operator_stop"]["trigger"] == "observation_cap"
    assert result["recording_status"] == "failed"
    assert result["exit_ok"] is False
    assert result["real_accuracy_tested"] is False
    assert recording["metadata"]["observe_recovery"] is True
    caption_events = [event for event in recording["events"]
                      if event["type"] == "subtitle" and event.get("provider_sentence_id")]
    assert [(event["connection_generation"], event["provider_sentence_id"], event["sentence_id"])
            for event in caption_events] == [
                (1, "same-id", "generation-1:same-id"),
                (2, "same-id", "generation-2:same-id"),
            ]
    assert any(error.get("type") == "recognition_error"
               and error.get("connection_generation") == 1 for error in recording["errors"])
    assert recording["completion_reason"] == "cancelled"
    assert clock["now"] - 100.0 <= 1.7
    artifact_text = (tmp_path / "recovery-cap.recording.json").read_text(encoding="utf-8")
    assert "TEST_ONLY" not in artifact_text
    assert "TEST_ONLY_MEDIA_CAPABILITY" not in artifact_text


@pytest.mark.parametrize("retryable", [False, None])
def test_opt_in_observation_stops_on_nonretryable_or_missing_retryability(
        tmp_path, monkeypatch, retryable):
    def on_start(page):
        socket = page.open_session()
        event = {"type": "error", "failure_category": "authentication"}
        if retryable is not None:
            event["retryable"] = retryable
        socket.send(event)

    result, report, recording, _page, clock = _run_collect_with_fake_browser(
        monkeypatch, tmp_path, label=f"fatal-{retryable}", seconds=10,
        on_start=on_start)

    assert report["session_post_start_count"] == 1
    assert report["live_seconds_cap_reached"] is False
    assert result["recording_status"] == "failed"
    assert result["exit_ok"] is False
    assert recording["errors"][0]["retryable"] is retryable
    assert clock["now"] - 100.0 < 0.3


def test_opt_in_observation_stops_when_manual_retry_ui_persists_after_recovery_budget(
        tmp_path, monkeypatch):
    def on_start(page):
        first = page.open_session()
        _retry_error(first)
        first.send({"type": "end", "reason": "error"})
        page.retry_visible = True
        page.schedule(1.0, lambda: failed_retry(page, 2))

    def failed_retry(page, generation):
        socket = page.open_session()
        _retry_error(socket)
        page.retry_visible = True
        if generation == 2:
            socket.send({"type": "end", "reason": "error"})
            page.retry_visible = True
            page.schedule(2.0, lambda: failed_retry(page, 3))
        else:
            # The real live UI leaves the retry button visible after its budget is exhausted.
            page.status = "recognition recovery exhausted"

    result, report, recording, _page, clock = _run_collect_with_fake_browser(
        monkeypatch, tmp_path, label="recovery-budget", seconds=10, on_start=on_start)

    assert report["session_post_start_count"] == 3
    assert report["live_seconds_cap_reached"] is False
    assert result["recording_status"] == "failed"
    assert result["exit_ok"] is False
    assert len([error for error in recording["errors"]
                if error.get("type") == "recognition_error"]) == 3
    assert clock["now"] - 100.0 < 7.0


def test_opt_in_observation_stops_on_media_only_ui_exhaustion_and_keeps_delete_evidence(
        tmp_path, monkeypatch):
    def on_start(page):
        page.open_session()
        page.schedule(0.2, lambda: media_recovery_exhausted(page))

    def media_recovery_exhausted(page):
        # showCaptionRecovery cancels the session, awaits its DELETE, then hides Stop.
        page._post("DELETE")
        page.active = False
        page.retry_visible = True
        page.status = "media recovery exhausted"

    result, report, recording, _page, clock = _run_collect_with_fake_browser(
        monkeypatch, tmp_path, label="media-recovery-exhausted", seconds=5,
        on_start=on_start)

    assert report["live_seconds_cap_reached"] is False
    assert report["operator_stop"]["requested"] is False
    assert report["operator_stop"]["trigger"] is None
    assert report["session_delete_statuses"] == [200]
    assert recording["metadata"]["session_delete_statuses"] == [200]
    assert recording["metadata"]["operator_stop"]["requested"] is False
    assert any(error["type"] == "ui_recovery_exhausted"
               for error in recording["errors"])
    assert not any(error["type"] == "live_stop_delete_unconfirmed"
                   for error in recording["errors"])
    assert result["recording_status"] == "failed"
    assert result["exit_ok"] is False
    assert clock["now"] - 100.0 < 1.0


def test_observe_recovery_option_is_remote_only(tmp_path):
    remote = acceptance.parse_args([
        "--media", "https://media.test/live.m3u8", "--label", "remote",
        "--observe-recovery",
    ])
    assert remote.observe_recovery is True

    local = tmp_path / "sample.wav"
    local.write_bytes(b"synthetic parser-only input")
    with pytest.raises(SystemExit):
        acceptance.parse_args([
            "--media", str(local), "--label", "local", "--observe-recovery",
        ])


def test_default_collection_still_stops_on_retryable_error(tmp_path, monkeypatch):
    def on_start(page):
        socket = page.open_session()
        _retry_error(socket)
        page.schedule(1.0, lambda: page.open_session())

    result, report, recording, _page, clock = _run_collect_with_fake_browser(
        monkeypatch, tmp_path, label="default-stop", seconds=10,
        on_start=on_start, observe_recovery=False)

    assert report["session_post_start_count"] == 1
    assert report["live_seconds_cap_reached"] is False
    assert result["recording_status"] == "failed"
    assert result["exit_ok"] is False
    assert "observe_recovery" not in recording["metadata"]
    assert "connection_generation" not in recording["events"][0]
    assert clock["now"] - 100.0 < 0.3
