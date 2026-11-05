# 05：直播字幕故障有界等待并恢复当前时间轴

**Parent:** [本地实现规格](../../../docs/plans/2026-10-23-original-caption-release-spec.md)。

**GitHub:** https://github.com/AiKiAi-stack/stream-with-asr/issues/39

**What to build:** 直播字幕落后时媒体最多累计等待 2 秒，随后继续并提示缺失；字幕恢复或来源重连后重新对应当前节目。

**Blocked by:** None (can start immediately).

**Status:** completed

**Approval:** 2026-10-23，用户已确认实现规格、测试边界、12 项粒度及依赖。此状态表示任务定义可执行，不代表阻塞项或外部前提已满足。

**Requirements:** R06/R08、第 4 节；A03/A04/A08。来自 L03/L04。

**Verification:** 本地可控 HLS 来源与真实浏览器播放器，注入字幕迟到、来源缓冲、时间戳变化和重连；识别使用替身。

- [x] 同一次字幕故障累计等待不超过 2 秒，到上限媒体继续且字幕缺失提示持续可见。
- [x] 一串迟到结果不能反复暂停或重置故障预算；恢复后不补播过期短句。
- [x] 只有字幕追上当前媒体并持续推进，或用户显式重启，才结束本次故障；把这一条件写成可重复测试。
- [x] 媒体断流与字幕等待分开呈现，来源自身缓冲独立计量。
- [x] 重连后重新关联解码/播放器时间轴；旧 HLS 实例回调不能影响新实例或新来源。
- [x] 媒体时间、可见字幕、应用暂停与来源缓冲都有记录，不以拖慢播放换取表面同步。

**External prerequisites:** 自动化使用本地 HLS，不代表公网可播或 30 分钟真实识别通过；这些由 10 验证。

**集成验证（2026-10-24）:** `tests/browser/e2e_original_live.py`正常/迟到链，`tests/browser/e2e_original_live_failures.py`3项及source_buffer真实播放器模拟回归通过；2秒逻辑预算不被迟到链重置，缺失提示持续到实际追上，来源缓冲独立。纯识别恢复保留媒体及3代同ID字幕历史；晚到PTS与正常结束队列由公共浏览器消息链测试覆盖。 所有浏览器识别均为离线替身，不代表PRD真实质量或发布门槛通过。最终代码、复审和日志索引见[实施状态](../../../docs/plans/2026-10-23-original-caption-implementation-status.md)。
