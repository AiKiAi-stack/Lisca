"""Caption style controls change the real visible overlay and survive reload."""
import json
import time

from playwright.sync_api import sync_playwright

from e2e_original_release import ROOT, launch_browser, open_scenario, request, snapshot, stop_if_active, wait_complete


def colors(page):
    return page.locator("#current").evaluate("""element => {
      const css = getComputedStyle(element);
      return {color:css.color,background:css.backgroundColor,bottom:parseFloat(css.bottom)};
    }""")


def main():
    fixture = request("/__fixture/state")
    assert fixture.get("simulated_recognition") is True
    results, errors = [], []
    with sync_playwright() as playwright:
        browser = launch_browser(playwright)
        page = browser.new_page()
        page.on("pageerror", lambda error: errors.append(error.stack))
        try:
            open_scenario(page, "normal", fixture["media"]["normal"])
            wait_complete(page)
            page.locator("video").evaluate("video=>{video.pause();video.currentTime=2.4}")
            page.wait_for_function("document.querySelector('#current').innerText.includes('Second')")
            original = colors(page)
            page.locator("#settingsBtn").click()
            page.get_by_role("tab", name="字幕", exact=True).click()
            page.locator("#contrastSel").wait_for(state="visible", timeout=1000)
            page.locator("#contrastSel").select_option("high")
            page.locator("#posRange").focus()
            page.locator("#posRange").press("End")
            page.get_by_role("button", name="关闭设置", exact=True).click()
            high = colors(page)
            assert high["background"] == "rgb(0, 0, 0)", high
            assert high["bottom"] > original["bottom"], (original, high)
            page.locator("#subToggle").click()
            assert page.locator("#subToggle").get_attribute("aria-pressed") == "false"
            assert not snapshot(page)["text"]
            page.locator("#subToggle").click()
            assert "Second" in snapshot(page)["text"]
            results.append({"case": "contrast-position-and-subtitle-toggle", "passed": True,
                            "original": original, "high": high})
            stop_if_active(page)
            page.reload(wait_until="domcontentloaded")
            page.locator("#settingsBtn").click()
            page.get_by_role("tab", name="字幕", exact=True).click()
            assert page.locator("#contrastSel").input_value() == "high"
            assert page.locator("#posRange").input_value() == "30"
            page.locator("#styleReset").click()
            assert page.locator("#contrastSel").input_value() == "default"
            assert page.locator("#posRange").input_value() == "12"
            results.append({"case": "style-preferences-and-reset", "passed": True})
            assert not errors, errors
        finally:
            stop_if_active(page)
            report = {"simulated_recognition": True, "real_accuracy_tested": False,
                      "at_unix_seconds": time.time(), "results": results, "page_errors": errors}
            directory = ROOT / "logs" / "original-release-browser"
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "style-report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False), flush=True)
            browser.close()


if __name__ == "__main__":
    main()
