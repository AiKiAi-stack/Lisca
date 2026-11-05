"""Exercise live HLS clock handling in Chrome with a page-local fake ASR socket.

The WebUI stays on its existing 8002 fixture process. This test serves only a
rolling local media playlist on an ephemeral port and never calls a provider.
"""
import json
import re
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error, sync_playwright

from e2e_original_release import BASE, ROOT, launch_browser, stop_if_active


HLS_DIR = ROOT / "logs" / "original-release-fixture" / "hls"


def fixture_state():
    from e2e_original_release import request
    return request("/__fixture/state")


def start_rolling_source():
    manifest = (HLS_DIR / "all.m3u8").read_text(encoding="utf-8")
    entries = re.findall(r"(#EXTINF:[^\n]+)\n([^\n]+)", manifest)
    assert entries, f"Missing local HLS media segments in {HLS_DIR}"
    started = time.monotonic()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(HLS_DIR), **kwargs)

        def do_GET(self):
            if urlsplit(self.path).path != "/live.m3u8":
                return super().do_GET()
            elapsed = max(0, time.monotonic() - started)
            end = min(len(entries), 3 + int(elapsed / 2))
            begin = max(0, end - 6)
            body = ("#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n"
                    f"#EXT-X-MEDIA-SEQUENCE:{begin}\n#EXT-X-INDEPENDENT-SEGMENTS\n")
            body += "".join(f"{duration}\n{name}\n" for duration, name in entries[begin:end])
            if end == len(entries):
                body += "#EXT-X-ENDLIST\n"
            payload = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.apple.mpegurl")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}/live.m3u8"


def attach_trace(page):
    page.evaluate("""() => {
      const video = document.querySelector('video');
      const status = document.querySelector('#status');
      const buffering = document.querySelector('#sourceBufferStatus');
      window.__clockSamples = [];
      window.__clockEvents = [];
      for (const name of ['pause','playing','waiting','canplay','seeking','seeked']) {
        video.addEventListener(name, () => window.__clockEvents.push({name,
          at:performance.now(), time:video.currentTime, status:status.innerText}));
      }
      window.__clockTimer = setInterval(() => window.__clockSamples.push({
        at:performance.now(), time:video.currentTime, paused:video.paused,
        status:status.innerText, sourceBuffering:!buffering.hidden,
        readyState:video.readyState, seeking:video.seeking
      }), 25);
    }""")


def install_page_isolation(page, sockets, client_messages):
    def mock_cloud_session(route):
        payload = {"session": "offline-live-clock-browser"} if route.request.method == "POST" else {}
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(payload))

    page.route("**/api/cloud/sessions*", mock_cloud_session)
    page.route("**/channels*", lambda route: route.fulfill(
        status=422, content_type="application/json", body=json.dumps({"detail": "单路 HLS"})))
    page.route("**/share/start", lambda route: route.fulfill(
        status=503, content_type="application/json", body=json.dumps({"detail": "offline test"})))
    page.route("**/share/stop/**", lambda route: route.fulfill(
        status=200, content_type="application/json", body="{}"))

    def mock_socket(socket):
        sockets.append(socket)
        socket.on_message(lambda message: client_messages.append(message))

    page.route_web_socket(re.compile(r"ws://127\.0\.0\.1:8002/ws\?.*"), mock_socket)
    page.goto(BASE, wait_until="domcontentloaded")
    attach_trace(page)


def open_player(browser, media_url, errors, before_start=None):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.on("pageerror", lambda error: errors.append(str(error)))
    sockets, client_messages = [], []
    install_page_isolation(page, sockets, client_messages)
    if before_start:
        before_start(page)
    page.locator("#url").fill(media_url)
    page.locator("#start").click()
    deadline = time.monotonic() + 12
    while not sockets and time.monotonic() < deadline:
        page.wait_for_timeout(50)
    assert sockets, "The page-local WebSocket route did not intercept the player connection"
    return page, sockets[0], client_messages


def send(socket, message):
    socket.send(json.dumps(message))


def player_state(page):
    return page.locator("video").evaluate("""video=>({time:video.currentTime,paused:video.paused,
      readyState:video.readyState,seeking:video.seeking})""")


def status_text(page):
    return page.locator("#status").inner_text()


def trace(page):
    return page.evaluate("() => ({samples:window.__clockSamples,events:window.__clockEvents})")


def guard_wait_periods(samples):
    periods, start, last = [], None, None
    for sample in samples:
        waiting = "等待当前片段的原文字幕" in sample["status"]
        if waiting and start is None:
            start = sample["at"]
        if not waiting and start is not None:
            periods.append((start, sample["at"]))
            start = None
        last = sample["at"]
    if start is not None and last is not None:
        periods.append((start, last))
    return [(end - begin) / 1000 for begin, end in periods]


def wait_for_socket_ready(page, socket, start_at=0):
    send(socket, {"type": "ready", "delay": 0, "start_at": start_at,
                  "original": True, "buffered": True})


def send_recovery_captions(page, socket):
    current = player_state(page)["time"]
    send(socket, {"type": "buffer", "play_at": current + 8})
    send(socket, {"type": "subtitle", "sentence_id": "offline-a", "revision": 1,
                  "beg": current - .2, "end": current + 1.2,
                  "text": "Offline recovery phrase one.", "is_final": True,
                  "translated": False})
    send(socket, {"type": "subtitle", "sentence_id": "offline-b", "revision": 1,
                  "beg": current + 1.2, "end": current + 3.2,
                  "text": "Offline recovery phrase two.", "is_final": True,
                  "translated": False})
    page.wait_for_function("() => document.querySelector('#status').innerText.includes('追上')",
                           timeout=5000)


def stop_page(page):
    try:
        stop_if_active(page)
    finally:
        page.close()


def run_live_late_ready(browser, live_url, errors):
    page, socket, _messages = open_player(browser, live_url, errors)
    try:
        page.wait_for_function("() => document.querySelector('video').readyState >= 2", timeout=15000)
        initial = player_state(page)
        send(socket, {"type": "buffer", "play_at": initial["time"] + 40})
        page.wait_for_function("() => document.querySelector('#status').innerText.includes('等待当前片段')",
                               timeout=5000)
        page.wait_for_function("() => document.querySelector('#status').innerText.includes('部分缺失')",
                               timeout=10000)
        page.wait_for_function("t => {const v=document.querySelector('video'); return !v.paused && v.currentTime >= t + 3;}",
                               arg=initial["time"], timeout=12000)

        before_ready = player_state(page)
        assert before_ready["time"] >= initial["time"] + 2.5 and not before_ready["paused"], before_ready
        target = max(0, before_ready["time"] - 1.5)
        marker = page.evaluate("() => performance.now()")
        wait_for_socket_ready(page, socket, target)
        page.wait_for_function("() => document.querySelector('#status').innerText.includes('字幕已就绪')",
                               timeout=3000)
        page.wait_for_timeout(600)
        after_ready = trace(page)
        after_samples = [sample for sample in after_ready["samples"]
                         if marker <= sample["at"] <= marker + 700]
        assert after_samples, "No Chrome media samples after the late ready message"
        minimum = min(sample["time"] for sample in after_samples)
        latest = max(sample["time"] for sample in after_samples)
        assert minimum >= before_ready["time"] - .15, {
            "before_ready": before_ready, "target": target, "minimum_after_ready": minimum}
        assert latest >= before_ready["time"] + .25, {
            "before_ready": before_ready, "latest_after_ready": latest}

        send_recovery_captions(page, socket)
        fault_start = player_state(page)["time"]
        send(socket, {"type": "buffer", "play_at": fault_start})
        page.wait_for_function("() => document.querySelector('#status').innerText.includes('部分缺失')",
                               timeout=3000)
        page.wait_for_timeout(400)
        final_state = player_state(page)
        sampled = trace(page)
        waits = guard_wait_periods(sampled["samples"])
        assert len(waits) == 1, {"guard_wait_periods_seconds": waits}
        assert sum(waits) <= 2.25, {"guard_wait_periods_seconds": waits}
        assert not final_state["paused"] and final_state["time"] >= fault_start + .25, final_state
        assert not errors, errors
        return {"late_ready_no_rewind": True, "media_before_ready": round(before_ready["time"], 3),
                "requested_start_at": round(target, 3), "minimum_after_ready": round(minimum, 3),
                "second_fault_added_no_hold": True, "guard_hold_periods_seconds": waits}
    finally:
        stop_page(page)


def run_external_pauses_and_recovery(browser, vod_url, errors):
    pending_fragments = []

    def block_fourth_fragment(page):
        page.route(re.compile(r"/video\?url=.*seg03\.ts(?:&|$)"),
                   lambda route: pending_fragments.append(route))

    page, socket, client_messages = open_player(browser, vod_url, errors, block_fourth_fragment)
    try:
        page.wait_for_function("() => document.querySelector('video').readyState >= 2", timeout=15000)
        wait_for_socket_ready(page, socket, 0)
        page.wait_for_function("() => !document.querySelector('video').paused && document.querySelector('video').currentTime > .5",
                               timeout=10000)
        page.wait_for_function("() => document.querySelector('#status').innerText.includes('字幕已就绪')",
                               timeout=3000)
        current = player_state(page)["time"]
        send(socket, {"type": "buffer", "play_at": current + 30})

        page.locator("video").evaluate("video=>video.pause()")
        page.wait_for_timeout(100)
        assert "pause" in client_messages, client_messages
        paused_at = player_state(page)["time"]
        page.wait_for_timeout(16000)
        paused_after = player_state(page)
        assert paused_after["paused"] and abs(paused_after["time"] - paused_at) < .08, paused_after
        page.locator("video").evaluate("video=>video.play()")
        page.wait_for_function("t => {const v=document.querySelector('video'); return !v.paused && v.currentTime > t + .4;}",
                               arg=paused_at, timeout=5000)

        page.wait_for_function("""() => {const v=document.querySelector('video');
          return v.currentTime>5 && v.readyState<3 && !v.paused &&
            !document.querySelector('#sourceBufferStatus').hidden;}""", timeout=15000)
        assert pending_fragments, "No local HLS fragment was held"
        blocked_requests = len(pending_fragments)
        before_buffer = player_state(page)["time"]
        send(socket, {"type": "buffer", "play_at": before_buffer})
        page.wait_for_timeout(1000)
        during_buffer = player_state(page)
        assert not during_buffer["paused"] and abs(during_buffer["time"] - before_buffer) < .15, during_buffer
        assert not ("等待当前片段" in status_text(page)), status_text(page)
        assert not page.locator("#sourceBufferStatus").evaluate("element=>element.hidden")

        for route in pending_fragments:
            route.continue_()
        pending_fragments.clear()
        page.wait_for_function("() => document.querySelector('#sourceBufferStatus').hidden", timeout=5000)
        page.wait_for_function("() => document.querySelector('#status').innerText.includes('等待当前片段')",
                               timeout=2000)
        page.wait_for_function("() => document.querySelector('#status').innerText.includes('部分缺失')",
                               timeout=5000)
        waits = guard_wait_periods(trace(page)["samples"])
        assert len(waits) == 1 and 1.75 <= waits[0] <= 2.25, {
            "guard_wait_periods_seconds": waits}

        send_recovery_captions(page, socket)
        assert "15 秒没有进展" not in status_text(page), status_text(page)
        assert not errors, errors
        source_samples = trace(page)["samples"]
        buffering_seconds = sum((following["at"] - current["at"]) / 1000
                                for current, following in zip(source_samples, source_samples[1:])
                                if current["sourceBuffering"])
        return {"user_pause_seconds": 16, "blocked_fragment_requests": blocked_requests,
                "media_advanced_during_source_buffer_event": round(
                    during_buffer["time"] - before_buffer, 3),
                "observed_source_buffering_seconds": round(buffering_seconds, 3),
                "first_guard_wait_after_external_pauses_seconds": round(waits[0], 3),
                "recovered_without_timeout": True}
    finally:
        for route in pending_fragments:
            try:
                route.continue_()
            except Error:
                pass
        stop_page(page)


def run_unplayed_hls_alignment(browser, vod_url, errors):
    page, socket, _messages = open_player(browser, vod_url, errors)
    try:
        page.wait_for_function("() => {const v=document.querySelector('video'); return v.readyState >= 2 && v.paused && v.buffered.length && v.buffered.end(0) > 3;}",
                               timeout=15000)
        before = player_state(page)
        assert before["time"] < .5 and before["paused"], before
        marker = page.evaluate("() => {window.__readyMarker=performance.now(); return window.__readyMarker;}")
        wait_for_socket_ready(page, socket, 2.5)
        page.wait_for_function("() => window.__clockEvents.some(e=>e.name==='seeked' && e.at >= window.__readyMarker)",
                               timeout=5000)
        # The marker is set immediately before dispatch so the event proves the
        # initial HLS target was applied before playback, not reached by waiting.
        page.wait_for_function("t => document.querySelector('video').currentTime >= t - .2",
                               arg=2.5, timeout=5000)
        state = player_state(page)
        events = trace(page)["events"]
        seeks = [event for event in events if event["name"] == "seeked" and event["at"] >= marker]
        assert seeks and abs(seeks[0]["time"] - 2.5) < .5, {"before": before, "seeked": seeks[:3]}
        assert not errors, errors
        return {"unplayed_hls_aligned_to_start_at": True, "before_time": round(before["time"], 3),
                "start_at": 2.5, "seeked_time": round(seeks[0]["time"], 3)}
    finally:
        stop_page(page)


def main():
    fixture = fixture_state()
    assert fixture.get("simulated_recognition") is True, "The local offline fixture is required"
    assert HLS_DIR.joinpath("all.m3u8").is_file(), f"Missing offline HLS fixture: {HLS_DIR}"
    media_server, live_url = start_rolling_source()
    vod_url = f"http://127.0.0.1:{media_server.server_port}/all.m3u8"
    errors = []
    results = {}
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        try:
            results["live_late_ready_and_repeated_faults"] = run_live_late_ready(
                browser, live_url, errors)
            results["user_pause_source_buffering_and_recovery"] = run_external_pauses_and_recovery(
                browser, vod_url, errors)
            results["unplayed_hls_start_alignment"] = run_unplayed_hls_alignment(
                browser, vod_url, errors)
            assert not errors, errors
        finally:
            browser.close()
            media_server.shutdown()
            media_server.server_close()
    print(json.dumps({"simulated_recognition": True, "real_accuracy_tested": False,
                      "provider_calls": 0, "page_errors": errors,
                      "results": results}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
