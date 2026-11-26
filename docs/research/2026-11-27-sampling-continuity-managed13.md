# 2026-11-27 采样缺口复算：zh-local-managed13

对象：`v0.2.0` 候选 `59e9e4b` 的 1802 秒本地长片真实浏览器报告。原报告保留在 `/media/wbb/KEKE/LiscaAcceptance/caption-rd-20261121/zh-local-managed13/zh-local-managed13.report.json`，SHA-256 `1199469b66629f586bd8808860b628ddc79d5f1aa66b1ad482750e24538b6b5b`。本文只复算原报告，不修改原文件、不新增识别调用。

## 原报告事实

- `recording_status=completed`，`completion_reason=completed`，errors 0；媒体 EOF `1802.033567` 秒。
- 1 个识别会话；完成后四次缓存跳转（5.1/600/1700/1790 秒）新增会话 0。
- 首次 `ready/playing` 为 `14.595/14.701` 秒；首条可见字幕媒体时间 `16.608764` 秒。
- 36,373 个 DOM 样本，请求间隔 50 ms；最大观测间隔 109 ms，原 `sampling.status=measurement_invalid`。

## 复算方法与结果

命令：

```sh
python tests/acceptance/sampling_continuity.py \
  /media/wbb/KEKE/LiscaAcceptance/caption-rd-20261121/zh-local-managed13/zh-local-managed13.report.json
```

结果：

```json
{
  "sample_count": 36373,
  "observed_span_ms": 1818609,
  "max_observed_gap_ms": 109,
  "gap_count": 1,
  "bounded_gap_count": 1,
  "unbounded_gap_count": 0,
  "max_transition_bound_ms": 109,
  "uncertain_span_ms": 0,
  "status": "bounded",
  "declared_sampling_status": "measurement_invalid"
}
```

唯一超过 100 ms 的缺口在 `observed_after_ms=1038751–1038860`（109 ms），两端字幕由：

```text
要全民普发现金1万元，但仍待立法院审议。同时在
立院新会期开议前，
```

变为空。`text_changed=true`、`visible_changed=true`，说明至少一次“可见 → 消失”转场发生在该 109 ms 内；缺口不是端点完全不变的观测空洞。

## 对原门槛的影响

- **同步时间**：该转场的实际时刻被界定在 109 ms 内（最坏 ±54.5 ms，保守按 109 ms 计）。相对 P95 ≤0.5 秒、最大 ≤1 秒的门槛，109 ms 的边界不足以把结论从通过翻成不通过；但仍应按保守上界重算，而不是把中点当精确值。
- **覆盖**：若保守地把缺口整段视为“字幕可能不可见”，对 1801.9 秒观察的影响约 `109 / 1,818,609 = 0.006%`，低于 95% 覆盖门槛的余量。若缺口内字幕实际可见，则影响更小。
- **原状态不变**：原报告的 `sampling.status` 仍是 `measurement_invalid`。`bounded` 是补充证据，不是原 100 ms 门槛通过，也不替代 #70 的正式声学 gate。
- **仍不能排除的情况**：两端变化只能证明至少有一次转场；同一 109 ms 内若还有一次快速 off/on，没有应用内转场日志就无法排除。后续采集应在页面内记录每次字幕转场的 `performance.now()` 与媒体时间，才能把“至少一次”升级为“完整转场序列”。

## 建议

1. 本地长片的**功能结论**（正常 EOF、四次缓存跳转零新增会话、0 错误、无裁切、两行稳定）可以按“带 109 ms 转场边界”收口，不再为单次 109 ms 缺口重跑 30 分钟。
2. 严格 50 ms 请求 / 100 ms 最大间隔继续作为 #105 的工具指标；是否把 `bounded` 缺口纳入产品门槛，需要维护者明确决定，本文不自行改门槛。
3. 下一次真实长片采集加入字幕转场日志（页面内 `MutationObserver` 记录时间、文本、媒体时间），把缺口从“至少一次、109 ms 边界”升级为可复核的完整转场序列；该改动只增加证据，不改播放行为。
4. 40.215 秒“有声区间未见字幕”的三阈值检测候选仍待独立词对齐；它不属于本次采样缺口复算，不因本文被写成覆盖通过。
