"""Browser regression for complete sentence overlays and revised ASR timestamps.

Requires the WebUI server and Playwright Chromium. Run with:
    python test/e2e_subtitle_overlay.py http://127.0.0.1:8001/
"""
import os
import sys

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\codex_tmp\playwright-browsers")

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/"


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        result = page.evaluate("""() => {
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>24.5});
          Object.defineProperty(player, 'paused', {configurable:true, get:()=>false});
          onSubtitle({beg:22.0, end:24.0, text:'In 2017, Chen Guangwei turned attention',
            words:[[24.1,24.5,'attention']],});
          const first = currentEl.textContent;
          onSubtitle({beg:22.8, end:26.0, text:'In 2017, Chen Guangwei turned his attention to the sea and nearby islands to collect trash, no matter the weather.',
            words:[[22.8,23.0,'In'],[23.0,23.4,'2017'],[23.4,23.8,'Chen'],
                   [23.8,24.4,'Guangwei'],[24.4,24.8,'turned'],[24.8,25.0,'his'],
                   [25.0,25.5,'attention'],[25.5,25.8,'to'],[25.8,26.0,'the sea']]});
          const updated = currentEl.textContent;
          const playerRect = playerWrap.getBoundingClientRect();
          const captionRect = currentEl.getBoundingClientRect();
          const fitsPlayer = captionRect.top >= playerRect.top && captionRect.bottom <= playerRect.bottom;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>31.5});
          onSubtitle({beg:31.0, end:33.0, text:'A separate sentence in the next playback window.', words:[]});
          const nextFrame = currentEl.textContent;
          const playbackHistoryCount = historyEl.children.length;
          const duplicateA = {beg:40.0, end:42.0, text:'older duplicate', words:[],
            historyItem:appendHistory(40.0, 42.0, 'older duplicate')};
          const duplicateB = {beg:40.1, end:42.0, text:'second duplicate', words:[],
            historyItem:appendHistory(40.1, 42.0, 'second duplicate')};
          subs.push(duplicateA, duplicateB);
          onSubtitle({beg:40.2, end:42.0, text:'corrected duplicate', words:[]});
          const duplicateCleanup = historyEl.innerText.includes('corrected duplicate')
            && !historyEl.innerText.includes('second duplicate');
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>1000});
          for (let i = 0; i < 205; i++) {
            const beg = 100 + i * 2;
            onSubtitle({beg, end:beg + 1, text:`History item ${i}`, words:[]});
          }
          onSubtitle({beg:508.2, end:509, text:'Corrected latest history item', words:[]});
          const cappedHistoryCount = historyEl.children.length;
          const latestHistoryRow = historyEl.lastElementChild.textContent;
          return {first, updated, nextFrame, historyCount:playbackHistoryCount, fitsPlayer, duplicateCleanup,
            cappedHistoryCount, latestHistoryRow};
        }""")
        browser.close()

    assert result["first"] == "In 2017, Chen Guangwei turned attention", result
    assert result["updated"] == "In 2017, Chen Guangwei turned his attention to the sea and nearby islands to collect trash, no matter the weather.", result
    assert result["nextFrame"] == "A separate sentence in the next playback window.", result
    assert result["historyCount"] == 2, result
    assert result["fitsPlayer"], result
    assert result["duplicateCleanup"], result
    assert result["cappedHistoryCount"] == 200, result
    assert "Corrected latest history item" in result["latestHistoryRow"], result
    print("PASS: long caption fits; corrected partial merges; next playback window updates independently")


if __name__ == "__main__":
    main()
