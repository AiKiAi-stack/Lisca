"""Browser regression for complete sentence overlays and revised ASR timestamps.

Requires the WebUI server and Playwright Chromium. Run with:
    python test/e2e_subtitle_overlay.py http://127.0.0.1:8002/
"""
import os
import sys

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", r"D:\codex_tmp\playwright-browsers")

from playwright.sync_api import sync_playwright

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8002/"


def main():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        result = page.evaluate("""async () => {
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>24.3});
          Object.defineProperty(player, 'paused', {configurable:true, get:()=>false});
          onSubtitle({beg:22.0, end:24.5, text:'In 2017, Chen Guangwei turned attention',
            words:[[24.1,24.5,'attention']], is_final:false});
          const partialHidden = currentEl.textContent === '';
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>21.5});
          syncSubtitle();
          const beforeSentence = currentEl.textContent;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>24.3});
          syncSubtitle();
          const first = currentEl.textContent;
          onSubtitle({beg:22.8, end:26.0, text:'In 2017, Chen Guangwei turned his attention to the sea and nearby islands to collect trash, no matter the weather.',
            words:[[22.8,23.0,'In'],[23.0,23.4,'2017'],[23.4,23.8,'Chen'],
                   [23.8,24.4,'Guangwei'],[24.4,24.8,'turned'],[24.8,25.0,'his'],
                   [25.0,25.5,'attention'],[25.5,25.8,'to'],[25.8,26.0,'the sea']], is_final:true});
          const updated = currentEl.textContent;
          // Real-world regression: one finalized API result can contain many
          // sentences. The overlay must reveal only the phrase timed at t.
          const longText = 'Your eyes were blinded. There was no path. You slipped on the ice and fell. You tried to get up and you found you had no strength. You had not thought of dying. You did not realize that at the altitude of 14,000 or 15,000 feet, there was little oxygen.';
          subs = [];
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>96.0});
          onSubtitle({beg:94.1, end:112.1, text:longText, words:[], is_final:true});
          const longOverlayPhrase = currentEl.textContent;
          const longOverlayBounded = longOverlayPhrase.length <= 60;
          subs = [];
          historyItems = [];
          historyEl.innerHTML = '';
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>102.3});
          const splitWordText = 'Here is the wind singing through steel as it whistles its way across the plain.';
          const splitWordTokens = ['Here','is','the','wind','singing','through','steel','as','it','wh','istles','its','way','across','the','plain.'];
          const splitWordTimes = splitWordTokens.map((token, i) => {
            const beg = 100 + i * 0.2;
            return [beg, beg + 0.3, token];
          });
          splitWordTimes[9] = [101.8,102.5,'wh'];
          splitWordTimes[10] = [102.2,102.7,'istles'];
          splitWordTimes[11] = [102.4,102.8,'its'];
          onSubtitle({beg:100, end:106, text:splitWordText, words:splitWordTimes, is_final:true});
          const splitWordOverlay = currentEl.textContent;
          const splitWordHistory = historyEl.lastElementChild?.textContent || '';
          const chineseText = '俺也不知道算不算，她对俺挺好的，俺也有点喜欢她。';
          let chineseIndex = 0;
          const chineseWords = [...chineseText].filter(char => !/[，。]/.test(char)).map(char => {
            const beg = 27.8 + chineseIndex++ * 0.18;
            return [beg, beg + 0.18, char];
          });
          const chineseChunks = captionSegments({beg:27.8, end:32.3,
            text:chineseText, words:chineseWords});
          const chineseWithoutWords = captionSegments({beg:40, end:50,
            text:'这一段中文转写没有字级时间戳但是播报员仍然持续说话所以字幕不能一次堆满整个画面需要拆成几段完整而可读的文字',
            words:[]});
          subs = [];
          historyItems = [];
          historyEl.innerHTML = '';
          const playerRect = playerWrap.getBoundingClientRect();
          const captionRect = currentEl.getBoundingClientRect();
          const fitsPlayer = captionRect.top >= playerRect.top && captionRect.bottom <= playerRect.bottom;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>26.5});
          syncSubtitle();
          const afterSentence = currentEl.textContent;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>31.5});
          onSubtitle({beg:31.0, end:33.0, text:'A separate sentence in the next playback window.', words:[]});
          const nextFrame = currentEl.textContent;
          const playbackHistoryCount = historyEl.children.length;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>60.5});
          onSubtitle({beg:60.0, end:null, text:'A partial without an end time stays on screen during speech.', words:null});
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>62.5});
          syncSubtitle();
          const openEndedCaption = currentEl.textContent;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>63.2});
          onSubtitle({beg:63.0, end:64.0, text:'The following sentence replaces the open-ended caption.', words:[]});
          const afterOpenEndedCaption = currentEl.textContent;
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
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>600.5});
          onSubtitle({beg:600.0, end:null, text:'A disconnected partial has a safety limit.', words:null});
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>620.0});
          syncSubtitle();
          const safetyTimeoutCaption = currentEl.textContent;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>660.0});
          syncSubtitle();
          const afterSafetyTimeout = currentEl.textContent;
          urlEl.value = 'https://example.test/live.m3u8';
          let mediaPauseCalls = 0, mediaPlayCalls = 0, simulatedTime = 9.99;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>simulatedTime});
          Object.defineProperty(player, 'paused', {configurable:true, get:()=>false});
          player.pause = () => { mediaPauseCalls++; };
          player.play = () => { mediaPlayCalls++; return Promise.resolve(); };
          playStarted = true;
          playbackLimit = 10;
          syncPlaybackToBuffer();
          const heldAtDelayLimit = mediaPauseCalls === 1 && holdingForBuffer;
          Object.defineProperty(player, 'paused', {configurable:true, get:()=>true});
          playbackLimit = 10.3;
          syncPlaybackToBuffer();
          const holdsForUsefulBuffer = holdingForBuffer && mediaPlayCalls === 0;
          playbackLimit = 11.3;
          syncPlaybackToBuffer();
          const resumedWithBufferedMedia = mediaPlayCalls === 1 && !holdingForBuffer;
          urlEl.value = 'https://gh.bravetimes.cn/https://raw.githubusercontent.com/best-fan/iptv-sources/master/cn_all.m3u8';
          await chBtn.onclick();
          const channelListLoaded = !chPanel.hidden && chRows.size > 0;
          // Seeking into known transcript history should render that caption
          // instead of leaving the loading overlay on screen.
          subs = [];
          uiPaused = true;
          onSubtitle({beg:67.5, end:81.7, text:'Known caption covering the seek target.', is_final:true, words:[]});
          const finalKeptWhilePaused = subs.length === 1;
          uiPaused = false;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>73.0});
          seekPending = true;
          syncSubtitle();
          const knownCaptionAfterSeek = currentEl.textContent;
          const knownCaptionClearedSeek = !seekPending;
          seekPending = true;
          Object.defineProperty(player, 'currentTime', {configurable:true, get:()=>90.0});
          syncSubtitle();
          const unknownSeekStillWaits = currentEl.textContent.includes('跳转中');
          // A direct HLS media playlist must not be rendered as a channel list.
          if (!chPanel.hidden) await chBtn.onclick();
          urlEl.value = 'https://example.test/live/index.m3u8';
          const originalFetch = window.fetch;
          window.fetch = async (input) => String(input).includes('/channels?')
            ? new Response(JSON.stringify({detail:'这是单路 HLS 播放流，不是频道列表；请直接播放该地址。'}),
                {status:422, headers:{'Content-Type':'application/json'}})
            : originalFetch(input);
          await chBtn.onclick();
          window.fetch = originalFetch;
          const hlsRejectedAsChannelList = chPanel.hidden && chRows.size === 0
            && document.getElementById('status').textContent.includes('单路 HLS 播放流');
          return {beforeSentence, first, updated, afterSentence, nextFrame, historyCount:playbackHistoryCount, fitsPlayer, duplicateCleanup,
            longOverlayPhrase, longOverlayBounded, splitWordOverlay, splitWordHistory,
            chineseChunks, chineseWithoutWords,
            openEndedCaption, afterOpenEndedCaption,
            safetyTimeoutCaption, afterSafetyTimeout,
            cappedHistoryCount, latestHistoryRow,
            delayOptions:[...delayEl.options].map(option=>option.value), partialHidden,
            heldAtDelayLimit, resumedWithBufferedMedia, channelListLoaded, selectedDelay:delayEl.value,
            knownCaptionAfterSeek, knownCaptionClearedSeek, unknownSeekStillWaits, hlsRejectedAsChannelList,
            holdsForUsefulBuffer, finalKeptWhilePaused};
        }""")
        browser.close()

    assert result["beforeSentence"] == "", result
    assert result["partialHidden"], result
    assert result["heldAtDelayLimit"], result
    assert result["resumedWithBufferedMedia"], result
    assert result["channelListLoaded"], result
    assert result["holdsForUsefulBuffer"], result
    assert result["finalKeptWhilePaused"], result
    assert result["knownCaptionAfterSeek"] == "Known caption covering the seek target.", result
    assert result["knownCaptionClearedSeek"], result
    assert result["unknownSeekStillWaits"], result
    assert result["hlsRejectedAsChannelList"], result
    assert result["selectedDelay"] == "5", result
    assert result["first"] == "", result
    assert len(result["updated"]) <= 48, result
    assert result["longOverlayBounded"], result
    assert "whistles" in result["splitWordHistory"], result
    assert "whistles" in result["splitWordOverlay"], result
    assert "wh istles" not in result["splitWordOverlay"], result
    assert any(chunk["text"] == "她对俺挺好的，" for chunk in result["chineseChunks"]), result
    assert all(not chunk["text"].endswith("她对") for chunk in result["chineseChunks"]), result
    assert all(len(chunk["text"]) <= 26 for chunk in result["chineseWithoutWords"]), result
    assert result["nextFrame"] == "A separate sentence in the next playback window.", result
    assert result["afterSentence"] == "", result
    assert result["historyCount"] == 1, result
    assert result["openEndedCaption"] == "A partial without an end time stays on screen during speech.", result
    assert len(result["afterOpenEndedCaption"]) <= 48, result
    assert result["safetyTimeoutCaption"] == "A disconnected partial has a safety limit.", result
    assert result["afterSafetyTimeout"] == "", result
    assert result["fitsPlayer"], result
    assert result["duplicateCleanup"], result
    assert result["cappedHistoryCount"] == 200, result
    assert "Corrected latest history item" in result["latestHistoryRow"], result
    assert result["delayOptions"] == ["0.1", "1", "5"], result
    print("PASS: captions align, seek uses known history, single HLS streams are rejected as channel lists, and .m3u8 channel lists load")


if __name__ == "__main__":
    main()
