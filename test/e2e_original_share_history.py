"""Check the truncated-history notice and ongoing subtitles in a second page.

Requires the local offline fixture on the sole WebUI port, 8002. Recognition is
scripted and simulated; this is not the separate-device LAN acceptance.
"""
import json
import re
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import BASE, ROOT, launch_browser, open_scenario, request, stop_if_active


def wait_for_fixture(predicate, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = request("/__fixture/state")
        if predicate(state):
            return state
        time.sleep(.1)
    raise AssertionError("offline fixture did not emit the expected caption count")


def redact_capability(value):
    return re.sub(r"(/share/)[^/?\s]+", r"\1[redacted]", str(value))


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True
    directory = ROOT / "logs" / "original-release-browser"
    directory.mkdir(parents=True, exist_ok=True)
    results, page_errors, cleanup_errors = [], [], []
    browser = context = owner = viewer = None
    failure = None
    try:
        with sync_playwright() as playwright:
            try:
                browser = launch_browser(playwright)
                context = browser.new_context(viewport={"width": 1280, "height": 800})
                context.grant_permissions(["clipboard-read", "clipboard-write"], origin=BASE)
                owner = context.new_page()
                owner.on("pageerror", lambda error: page_errors.append(redact_capability(error)))
                open_scenario(owner, "share-history", fixture["live_url"])
                emitted = wait_for_fixture(lambda state: state["share_history_captions"] >= 300)
                assert emitted["active"] == 1
                owner.wait_for_function("document.querySelectorAll('#history > div').length === 200",
                                        timeout=10000)

                # Exercise the actual copy control. Keep the capability only in
                # this ephemeral browser context and never put it in the report.
                owner.locator("#settingsBtn").click()
                owner.get_by_role("tab", name="更多").click()
                owner.locator("#shareCopy").wait_for(state="visible", timeout=15000)
                owner.locator("#shareCopy").click()
                owner.wait_for_function("document.querySelector('#status').innerText.includes('观众链接已复制')")
                viewer_url = owner.evaluate("navigator.clipboard.readText()")
                assert viewer_url.startswith(BASE + "/share/")
                owner.get_by_role("button", name="关闭设置", exact=True).click()
                viewer = context.new_page()
                viewer.on("pageerror", lambda error: page_errors.append(redact_capability(error)))
                viewer.goto(viewer_url, wait_until="domcontentloaded", timeout=15000)

                viewer.wait_for_function("""() => {
                  const notice = document.querySelector('#shareHistoryNotice');
                  return notice && !notice.hidden &&
                    notice.textContent.includes('较早字幕记录未保留，当前直播继续');
                }""", timeout=15000)
                viewer.wait_for_function("""() => {
                  const video = document.querySelector('video');
                  return video && video.currentTime > .3 && !video.paused;
                }""", timeout=15000)
                request("/__fixture/release-share-history-future", {})
                viewer.wait_for_function("""() => {
                  const current = document.querySelector('#current');
                  const history = document.querySelector('#history');
                  return current && current.innerText.includes('Caption after history snapshot') &&
                    history && history.innerText.includes('Caption after history snapshot');
                }""", timeout=15000)

                before = viewer.locator("video").evaluate("video => video.currentTime")
                owner_before = owner.locator("video").evaluate("video => video.currentTime")
                viewer.wait_for_timeout(1000)
                after = viewer.locator("video").evaluate("video => video.currentTime")
                owner_after = owner.locator("video").evaluate("video => video.currentTime")
                assert after - before >= .5, {"before": before, "after": after}
                assert owner_after - owner_before >= .5, {"before": owner_before, "after": owner_after}
                assert not viewer.locator("#current").evaluate("element => element.classList.contains('err')")
                assert not page_errors, page_errors
                state = request("/__fixture/state")
                assert state["share_history_captions"] == 300
                assert state["active"] == 1
                results.append({"case": "history-truncation-is-nonterminal-and-live-continues",
                                "passed": True, "simulated_recognition": True,
                                "historical_caption_events": 300,
                                "warning": "较早字幕记录未保留，当前直播继续",
                                "same_browser_context": True,
                                "separate_lan_device_tested": False,
                                "owner_video_advanced_seconds": owner_after - owner_before,
                                "viewer_video_advanced_seconds": after - before,
                                "future_caption_visible": True})
            except Exception as error:
                # Browser/Playwright errors may include the copied bearer path.
                failure = redact_capability(error)
            finally:
                if owner:
                    try:
                        stop_if_active(owner)
                    except Exception as error:
                        cleanup_errors.append("owner stop: " + redact_capability(error))
                    try:
                        owner.evaluate("navigator.clipboard.writeText('')")
                    except Exception as error:
                        cleanup_errors.append("clipboard clear: " + redact_capability(error))
                for label, resource in (("viewer close", viewer), ("context close", context),
                                        ("browser close", browser)):
                    if resource:
                        try:
                            resource.close()
                        except Exception as error:
                            cleanup_errors.append(label + ": " + redact_capability(error))
    except Exception as error:
        # Also sanitize errors raised while starting/stopping Playwright itself.
        if failure is None:
            failure = redact_capability(error)

    report = {"simulated_recognition": True, "real_accuracy_tested": False,
              "results": results, "page_errors": page_errors,
              "cleanup_errors": cleanup_errors}
    (directory / "share-history-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    if failure:
        raise AssertionError(failure) from None
    if cleanup_errors:
        raise AssertionError("browser cleanup failed: " + "; ".join(cleanup_errors)) from None


if __name__ == "__main__":
    main()
