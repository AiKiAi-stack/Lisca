# 03：本地提前播放、静音放行与无进展退出

**Parent:** [本地实现规格](../../../docs/plans/2026-10-23-original-caption-release-spec.md)。

**What to build:** 长本地视频无需整段处理完即可开始；静音可播放，识别无进展时用户能退出等待，而音频投送始终有界。

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

**Approval:** 2026-10-23，用户已确认实现规格、测试边界、12 项粒度及依赖。此状态表示任务定义可执行，不代表阻塞项或外部前提已满足。

**Requirements:** R06/R09、第 4 节；A07/A08。来自 L02/L03。

**Verification:** 公开 WS 驱动可控音频源与识别器，真实播放器观察准备、等待、静音与继续；可控时钟用于超时和节奏测试。

- [ ] 覆盖当前位置的字幕就绪后开始播放，长文件未结束时即可产生可观看结果；不得退回等整段完成。
- [ ] 每个观察点的本地音频提前量符合当前 2 秒节奏限制；暂停后没有积累出的突发额度，停止能中断节流等待。
- [ ] 全静音及静音转语音均可前进，状态正确更新，不捏造字幕；无音轨与静音分开处理。
- [ ] 只有心跳、重复状态或发送音频时，连续 15 秒无真实识别进展会停止新投送，并提供重试、仅播放、停止。
- [ ] 用户暂停不耗尽故障等待预算；有效进展前进可更新预算，迟到旧会话事件不算当前进展。
- [ ] 至少 30 分钟媒体检查准备、取消和容量边界；加速时钟逻辑测试与持续运行资源实测分别记账。

**External prerequisites:** 自动化不需要真实识别；真实长文件体验纳入 09 的环境验收。

**Implementation evidence (2026-10-24):** Public `POST /api/cloud/sessions` + WebSocket tests now cover server-side 15-second no-progress termination, pause-excluded budget including a pause while source iteration is blocked, forward subtitle range versus repeated range, pacing cancellation, and an accelerated 30-minute silent input. The accelerated run observed ready before EOF, a 2-second maximum audio lead, and at most one unprocessed chunk. After merging integration tip `666f162`, focused Python tests passed 21/21 and the caption-browser plus playback-guard Node suites passed 61/61. Details and commands: `/home/wbb/.local/share/stream-with-asr/notes/preparation.md`.

**Still pending:** The 30-minute run is accelerated and synthetic; no continuous 30-minute wall-clock/resource measurement is claimed. The real 8002 browser recheck, including silence-to-speech status with the accepted-subtitle callback, remains with the coordinator. Checkboxes remain open until those acceptance observations are recorded.
