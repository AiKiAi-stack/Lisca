# Playback and caption design

The product is a Windows browser player for people watching foreign-language live streams and local videos. The primary job is reading translated speech while watching.

Palette: canvas #10151c, player #000000, surface #1b2430, text #edf2f7, muted #a5b1c2, action #84b5ff. Typography: Segoe UI Variable / Microsoft YaHei UI for controls, Segoe UI / Microsoft YaHei for readable captions.

Layout: one compact source row above one large player, one quiet status row below it. Settings, transcript, and channels use mutually exclusive native dialogs. The empty player directs users to the source input. During playback the video and stable caption are the visual focus. All controls are keyboard accessible, have visible focus, and work without remote fonts or icon downloads.

Brief review: a dashboard/card grid would compete with the picture. Removed persistent history, draft translation frame, configuration form, engineering selectors, and repeated toolbar actions from the default view. Dark playback surfaces are deliberate for video; restrained blue controls are subordinate to the content. No token animation, caption fade, or decorative transitions.

Caption contract: complete committed phrases only; at most two target-language lines per page, with optional single source line. Hold 2.4–8 seconds according to reading load. Pause freezes reading time. Late captions still appear inside the video; measured lag is visible. Overload never accelerates reading: pending pages beyond a bounded 14-second budget may be skipped, with a visible notice and complete transcript retained. This does not claim zero translation latency.

Skills: [frontend-design](https://github.com/anthropics/skills/tree/main/skills/frontend-design), [UI UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill). Installed outside the repository on D:.

UI UX Pro Max search matched the entertainment dark-player direction. Its hero/marketing layout and display-font suggestions do not fit a working player, so they were not adopted. Two UX searches for progressive disclosure returned unrelated results; the grouping and dialog decisions use the skill's explicit Forms/Navigation defaults and the user's request, not a claimed database match. Stack detected: plain HTML, CSS and vanilla JavaScript; no framework was introduced.
