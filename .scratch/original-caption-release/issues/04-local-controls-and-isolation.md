# 04：本地暂停跳转复用字幕并隔离旧会话

**Parent:** [本地实现规格](../../../docs/plans/2026-10-23-original-caption-release-spec.md)。

**GitHub:** https://github.com/AiKiAi-stack/stream-with-asr/issues/38

**What to build:** 用户暂停、继续、前后跳转和切换内容后，字幕跟随当前媒体；已经完成识别的内容不会再次发起收费识别。

**Blocked by:** None (can start immediately).

**Status:** completed

**Approval:** 2026-10-23，用户已确认实现规格、测试边界、12 项粒度及依赖。此状态表示任务定义可执行，不代表阻塞项或外部前提已满足。

**Requirements:** R06/R07/R09；A07。来自 L02。

**Verification:** 真实播放器经公开会话操作，来源与识别替身记录外部请求和投送；主动让旧请求、旧解析和旧媒体回调迟到。

- [x] 已完成识别后暂停 3 秒、30 秒、继续及任意跳转均复用字幕，新增识别请求为 0。
- [x] 未完成时跳到已准备区间直接复用；区间外先提示可能再次调用 API，从新位置处理并停止旧位置投送。
- [x] 长暂停导致连接失效后从恢复位置继续，不无提示重跑整段。
- [x] 连续跳转、停止重开、切换文件后旧字幕、旧解析结果和旧媒体事件污染为 0。
- [x] EOF 清空过期叠层且没有持续 API 请求；同次会话回看已完成区间仍保留字幕。

**External prerequisites:** 自动化使用模拟识别；真实计费调用的验证前提在 09 单独说明。

**集成验证（2026-10-24）:** `tests/browser/e2e_original_controls.py`8项、`tests/browser/e2e_original_lifecycle.py`2项通过；已准备/完成字幕复用，区间外单次确认与取消、从目标重建、3/30秒暂停、长暂停、EOF和迟到创建响应均留证。`tests/browser/e2e_original_source_buffer.py`另实际释放换文件前延迟的旧HLS分片，未污染新文件与字幕。 所有浏览器识别均为离线替身，不代表PRD真实质量或发布门槛通过。最终代码、复审和日志索引见[实施状态](../../../docs/plans/2026-10-23-original-caption-implementation-status.md)。
