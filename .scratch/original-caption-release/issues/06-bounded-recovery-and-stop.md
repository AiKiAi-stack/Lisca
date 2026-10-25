# 06：统一故障恢复预算并证明停止后没有新请求

**Parent:** [本地实现规格](../../../docs/plans/2026-10-23-original-caption-release-spec.md)。

**GitHub:** https://github.com/AiKiAi-stack/stream-with-asr/issues/40

**What to build:** 用户能区分服务错误并看懂恢复过程；同次故障最多恢复 2 次，停止、仅播放和删除配置都能阻断后续处理与费用。

**Blocked by:** None (can start immediately).

**Status:** completed

**Approval:** 2026-10-23，用户已确认实现规格、测试边界、12 项粒度及依赖。此状态表示任务定义可执行，不代表阻塞项或外部前提已满足。

**Requirements:** R02/R09、第 4 节；A08。来自 L03/L05。

**Verification:** 公开会话与真实浏览器，服务/媒体故障注入，外部请求及音频投送计数；覆盖请求开始、响应中与退避等待的竞态。

- [x] 认证、权限、额度、限流、网络、服务暂不可用和未知错误按可用证据提示；无服务商依据时不编造分类。证据：`/home/wbb/.local/share/stream-with-asr/notes/recovery.md`；`test_cloud_ws_classifies_failures_only_from_provider_evidence`（公开 POST/WS）、`test_callback_preserves_structured_sdk_error_without_raw_message`。
- [x] 同一临时故障总计恢复最多 2 次；媒体和识别层不能叠加隐式重试，失败本身不重置计数。
- [x] 当前次数与等待可观察；采用已确认的 1 秒、2 秒初始退避，取消可打断等待；认证/权限/已知额度错误等待用户操作。
- [x] 混合网络/媒体/识别故障直接测试共用预算，而非只重复同一种错误。
- [x] 停止、仅播放、换内容、删除配置、拥有者断开后新投送和新识别请求为 0；已经开始的请求单独记录。
- [x] 媒体缓冲、事件积压、终稿记录和后台任务的容量策略可观察；超限不继续伪装识别正常，结束后释放会话资源。证据：`/home/wbb/.local/share/stream-with-asr/notes/recovery.md` 最终集成状态；公开 HTTP/WS 容量测试、最终90项浏览器逻辑测试，以及 `/home/wbb/.local/share/stream-with-asr/notes/browser-capacity-stop-green.log`。浏览器用模拟识别，不证明真实识别准确度或连续运行 30 分钟。

**External prerequisites:** 故障回归使用替身，不通过真实无效 Key、限流或付费重试制造测试。

**集成验证（2026-10-24）:** `test/e2e_original_recovery.py`7项通过：实测退避约1.01/2.01秒、最多2次，顺序/同时媒体与识别故障共用预算，认证不自动重试，停止/仅播放/删除配置零新请求与音频。同时故障场景另延迟成功分片1.2秒，媒体及当前字幕恢复。公开WS与容量测试覆盖拥有者断开、切换、worker和队列上限及释放。 所有浏览器识别均为离线替身，不代表PRD真实质量或发布门槛通过。最终代码、复审和日志索引见[实施状态](../../../docs/plans/2026-10-23-original-caption-implementation-status.md)。
