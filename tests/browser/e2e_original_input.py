"""Real configuration/upload/preflight journey with the offline ASR fixture."""
import json
import sys
import time
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Error as PlaywrightError, sync_playwright

from e2e_original_release import launch_browser, BASE, ROOT, configure, request, snapshot, stop_if_active


def run_channel_selection_check(page):
    """Check channel selection without forwarding a session request upstream."""
    channel_list_url = "https://channels.test/iptv.m3u"
    channel_url = "https://media.test/live.m3u8?case=channel-picker"
    pending_sessions = []
    session_posts = []
    html = (ROOT / "tools" / "webui_static" / "index.html").read_text(encoding="utf-8")

    def serve_worktree_html(route):
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

    def intercept_channel_lookup(route):
        target = parse_qs(urlparse(route.request.url).query).get("url", [""])[0]
        if target == channel_list_url:
            route.fulfill(json=[{"group": "测试", "name": "央视测试", "url": channel_url}])
        elif target == channel_url:
            route.fulfill(status=422, json={"detail": "这是单路 HLS 播放流，不是频道列表；请直接播放该地址。"})
        else:
            route.fulfill(status=400, json={"detail": "Unexpected channel lookup"})

    def hold_cloud_session(route):
        session_posts.append(json.loads(route.request.post_data or "{}"))
        pending_sessions.append(route)
        page.evaluate("payload => { window.__channelPickerHeldSession = payload }", session_posts[-1])

    page.route(BASE + "/", serve_worktree_html)
    page.route(BASE + "/channels?*", intercept_channel_lookup)
    page.route(BASE + "/api/cloud/sessions", hold_cloud_session)
    try:
        page.goto(BASE + "/", wait_until="domcontentloaded")
        page.locator("#url").fill(channel_list_url)
        with page.expect_response(lambda response: "/channels?" in response.url):
            page.locator("#start").click()
        page.locator(".ch-row").first.wait_for()
        assert session_posts == [], session_posts

        with page.expect_request(lambda request: request.method == "POST"
                                 and request.url == BASE + "/api/cloud/sessions") as session_request:
            page.locator(".ch-row").first.click()
        page.wait_for_function("window.__channelPickerHeldSession !== undefined")
        post = json.loads(session_request.value.post_data or "{}")
        assert len(session_posts) == 1, session_posts
        assert post["url"] == channel_url, post
        assert page.locator("#url").input_value() == channel_url
        assert page.locator(".ch-row.active").count() == 1
        dialog_closed_before_reply = not page.locator("#channelsDialog").evaluate(
            "dialog => dialog.open")
        assert dialog_closed_before_reply, "Selecting a channel leaves the modal covering the player"
        assert len(pending_sessions) == 1, "The session request must remain held during the check"

        page.locator("#settingsBtn").click()
        page.get_by_role("tab", name="更多", exact=True).click()
        page.locator("#chBtn").click()
        assert page.locator("#channelsDialog[open]").count() == 1
        assert page.locator(".ch-name").all_text_contents() == ["央视测试"]
        return {"case": "channel-selection-closes-modal-before-session-reply", "passed": True,
                "intercepted_posts": len(session_posts), "selected_url": post["url"],
                "modal_closed_before_reply": dialog_closed_before_reply,
                "channel_list_reopened": True, "provider_calls": 0}
    finally:
        for route in pending_sessions:
            try:
                route.abort()
            except PlaywrightError:
                pass


def channel_picker_main():
    """Run only the focused no-provider browser check against the local WebUI."""
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        try:
            result = run_channel_selection_check(page)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        finally:
            page.close()
            browser.close()


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True, "Offline fixture required"
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    results = []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        try:
            assert page.request.delete(BASE + "/api/cloud").ok
            before = request("/__fixture/state")["started"]
            page.goto(BASE)
            page.locator("#url").fill(fixture["media"]["normal"])
            page.locator("#start").click()
            page.locator("#settingsDialog[open]").wait_for()
            assert "配置" in page.locator("#status").inner_text()
            assert page.locator("#apiKey").get_attribute("type") == "password"
            assert not page.locator("#saveKey").is_checked()
            assert request("/__fixture/state")["started"] == before
            results.append({"case": "unconfigured-no-recognition", "passed": True})

            configure(page)
            assert not page.locator("#apiKey").input_value()
            config = request("/api/cloud")
            assert config["configured"] and not config["saved"]
            persisted = page.evaluate("JSON.stringify({local:{...localStorage},session:{...sessionStorage}})")
            assert "offline-fixture-key" not in persisted
            assert "offline-fixture-key" not in json.dumps(config)

            for name in ("invalid", "video-only"):
                configure(page)
                page.locator("#url").fill(fixture["media"][name])
                page.locator("#start").click()
                expected = "音频轨" if name == "video-only" else "失败|无法|不支持"
                page.wait_for_function("pattern => new RegExp(pattern).test(document.querySelector('#status').innerText)",
                                       arg=expected)
                failure = snapshot(page)
                assert request("/__fixture/state")["started"] == before, failure
                assert any(word in failure["status"] for word in ("失败", "无法", "音轨", "不支持")), failure
                results.append({"case": "preflight-" + name, "passed": True,
                                "message": failure["status"], "new_recognition_requests": 0})
                stop_if_active(page)

            request("/__fixture/scenario", {"mode": "normal"})
            page.locator("#file").set_input_files(fixture["media"]["normal"])
            page.wait_for_function("""() => document.querySelector('video').currentTime>.3 &&
                document.querySelector('#current').innerText.includes('第一句')""", timeout=15000)
            playing = snapshot(page)
            assert request("/__fixture/state")["started"] == before + 1
            page.locator("#settingsBtn").click()
            page.get_by_role("tab", name="更多", exact=True).click()
            page.locator("#logBtn").click()
            diagnostics = page.locator("#logText").input_value()
            assert "connect" in diagnostics
            assert "offline-fixture-key" not in diagnostics
            page.get_by_role("button", name="关闭诊断日志", exact=True).click()
            results.append({"case": "configure-upload-visible-caption", "passed": True,
                            "snapshot": playing, "configuration_saved": False})
            page.screenshot(path=str(directory / "configure-upload.png"))
            assert not errors, errors

            picker_page = browser.new_page()
            try:
                results.append(run_channel_selection_check(picker_page))
            finally:
                picker_page.close()
        finally:
            stop_if_active(page)
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors}
            (directory / "input-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    if sys.argv[1:] == ["--channel-picker-only"]:
        channel_picker_main()
    else:
        main()
