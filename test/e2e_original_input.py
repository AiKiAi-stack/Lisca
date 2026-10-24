"""Real configuration/upload/preflight journey with the offline ASR fixture."""
import json
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import launch_browser, BASE, ROOT, configure, request, snapshot, stop_if_active


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
        finally:
            stop_if_active(page)
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors}
            (directory / "input-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
