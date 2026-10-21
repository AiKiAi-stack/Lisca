# Playback and caption design

The product is a Windows browser player for people watching foreign-language live streams and local videos. The primary job is reading translated speech while watching.

Palette: canvas #10151c, player #000000, surface #1b2430, text #edf2f7, muted #a5b1c2, action #84b5ff. Typography: Segoe UI Variable / Microsoft YaHei UI for controls, Segoe UI / Microsoft YaHei for readable captions.

Layout: one compact source row above one large player, one quiet status row below it. Settings, transcript, and channels use mutually exclusive native dialogs. The empty player directs users to the source input. During playback the video and stable caption are the visual focus. All controls are keyboard accessible, have visible focus, and work without remote fonts or icon downloads.

Brief review: a dashboard/card grid would compete with the picture. Removed persistent history, draft translation frame, configuration form, engineering selectors, and repeated toolbar actions from the default view. Dark playback surfaces are deliberate for video; restrained blue controls are subordinate to the content. No token animation, caption fade, or decorative transitions.

Caption contract: complete committed phrases only; at most two target-language lines per page, with optional single source line. Hold 2.4–8 seconds according to reading load. Pause freezes reading time. Late captions still appear inside the video; measured lag is visible. Overload never accelerates reading: pending pages beyond a bounded 14-second budget may be skipped, with a visible notice and complete transcript retained. This does not claim zero translation latency.

Skills: [frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design), [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill). Installed outside the repository on D:.

UI UX Pro Max search matched the entertainment dark-player direction. Its hero/marketing layout and display-font suggestions do not fit a working player, so they were not adopted. Two UX searches for progressive disclosure returned unrelated results; the grouping and dialog decisions use the skill's explicit Forms/Navigation defaults and the user's request, not a claimed database match. Stack detected: plain HTML, CSS and vanilla JavaScript; no framework was introduced.

Local-file playback preserves every pending completed page, including pages read after EOF. Only live HLS applies the bounded catch-up policy. A later stable source correction creates a complete caption revision; it never token-rewrites the current page. Translation request backlog retains its separate safety stop with an explicit error, rather than continuing unbounded billed work.

Local-file playback preserves every pending completed page, including pages read after EOF. Only live HLS applies the bounded catch-up policy. A later stable source correction creates a complete caption revision in source order; it never token-rewrites the current page. Deletions retract obsolete captions and records. Translation request backlog retains its separate explicit safety stop, preventing unbounded billed work.

## Local validation, 2026-10-21/22

- The original actual-page regression reproduced all three failures before repair.
- Real cloud English-to-Chinese CGTN playback and Chinese-to-English local video both showed complete captions inside the video. Fullscreen includes the caption and an explicit exit button.
- During local pause, two browser observations kept the media clock at 3.93238 seconds and the same complete caption. A long pause also exposed provider disconnect; the UI now preserves the paused caption and reconnects at that position when playback resumes, with no automatic retry loop.
- Local screenshot/evidence files are retained under the ignored logs/translation-acceptance directory: caption-fix-live.png, caption-fix-fullscreen.png, caption-fix-local.png and caption-fix-evidence.json.
- Full mobile-browser rendering, a 30-minute stability run, zero provider latency and public-release readiness are not claimed.

Final checks: 121 Python tests passed; 20 actual-page/presenter Node regressions passed; repository-wide Pylint errors-only passed with the CI path/extension settings. Real local pause lasted 35.288 seconds at media time 4.264288, preserving the complete caption; resume generated new translations at 5.0–8.3 and 8.7–10.8 seconds and ended without error. Evidence is in caption-fix-pause-resume.json.
