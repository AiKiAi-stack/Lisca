# 07：把评分报告绑定到正确的识别输入

**Parent:** [本地实现规格](../../../docs/plans/2026-10-23-original-caption-release-spec.md)。

**GitHub:** https://github.com/AiKiAi-stack/stream-with-asr/issues/41

**What to build:** 维护者对录音和参考集评分时能确认识别了哪个输入，不会把不完整、失败、翻译或错配的记录当作合格原文证据。

**Blocked by:** None (can start immediately).

**Status:** completed

**Approval:** 2026-10-23，用户已确认实现规格、测试边界、12 项粒度及依赖。此状态表示任务定义可执行，不代表阻塞项或外部前提已满足。

**Requirements:** 第 7.2–7.4 节；A01/A02。来自 L01。

**Verification:** 从记录工具的输入及参考 manifest 到最终报告，覆盖公开 CLI 行为；识别录音可使用离线事件样本。

- [x] 录音保存实际本地输入 SHA-256，与参考集声明分别表达；评分明确报告是否匹配。证据：`/home/wbb/.local/share/stream-with-asr/notes/evidence.md`；`test_recording_and_scoring_clis_bind_a_local_input_to_the_report`。
- [x] 输入错配、缺少来源证明、录音期间文件替换或旧格式缺失身份不能报告为已核验来源。证据：同一 evidence 文件；`test_scoring_cli_names_a_reference_hash_mismatch_without_erasing_accuracy`、`test_scoring_cli_keeps_legacy_recordings_unverified`、`test_recording_cli_detects_local_input_replacement_before_scoring`。
- [x] 远端直播按来源与采样区间保留证据，不把 URL 当作不可变文件哈希。证据：同一 evidence 文件；`test_recording_and_scoring_clis_preserve_remote_sample_identity_without_hashing_url`。
- [x] CER/WER 使用预先固定的归一化，修订和撤回不重复计数，无 ID 字幕正确保留与撤回。证据：同一 evidence 文件；`test_scoring_cli_uses_fixed_normalization_with_revisions_retractions_and_anonymous_captions`、`test_scoring_cli_reports_chinese_character_error_rate`。
- [x] 缺少正常完成证据、存在错误或翻译事件时不通过；取消与传输结束不被混同为成功。证据：同一 evidence 文件；`test_recording_and_scoring_clis_require_explicit_normal_completion` 覆盖 completed/cancelled/error/missing reason，另有公共 HTTP/WS DELETE 与识别错误测试。
- [x] 报告区分转写准确率、数据来源证明和完整发布门槛，缺少画面/时间指标不能宣称同步通过。证据：同一 evidence 文件；报告保持 `transcription_accuracy_passed`、`source_verified`、`accuracy_gate_passed` 独立，`sync_gate_passed: null` 且 `release_gate_passed: false`。
- [x] 录音 JSON 和 CLI 输出递归隐藏远端 URL 用户信息、query/session token、敏感字段与异常消息。证据：同一 evidence 文件；`test_recording_cli_recursively_redacts_credentials_from_events_and_output`、`test_recording_cli_recursively_redacts_signed_url_from_exception_text` 与带凭据远端源 CLI 用例。

**External prerequisites:** 固定参考文字与离线录音足以开发；真实数据集执行在 09/10。
