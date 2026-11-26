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

## 边界与用途

- **原状态不变**：原报告的 `sampling.status` 仍是 `measurement_invalid`；50 ms 请求、100 ms 最大间隔和保守退出语义保留。`bounded` 只是补充分类，不是原门槛通过，也不把整次采集改判为有效。
- **不证明同步或覆盖**：缺口内至少有一次“可见 → 消失”转场，但连续性、阅读、同步等时间敏感指标仍按原规则保持 `unknown`。任何同步结论都必须使用独立声音/可见字幕证据，不能由本复算补出。
- **支持逐指标报告**：109 ms 缺口不应抹掉已经直接观察到的媒体 EOF、缓存跳转和会话计数事实。按 #103 的逐指标证据方案，这些独立行为可以分别标记为 observed，而全局采样状态、退出码和时间敏感指标保持原样。
- **仍不能排除的情况**：两端变化只能证明至少有一次转场；同一 109 ms 内若还有一次快速 off/on，没有应用内转场日志就无法排除。后续采集应在页面内记录每次字幕转场的 `performance.now()` 与媒体时间，才能把“至少一次”升级为“完整转场序列”。

## 建议

1. 保留原 `measurement_invalid` 和所有 unknown，不用本复算重跑或重判 1802 秒采集；把 109 ms 缺口作为逐指标报告中的补充说明。
2. 严格 50 ms 请求 / 100 ms 最大间隔继续按 #63 的工程边界执行；`bounded` 不自行升级为产品门槛。
3. 下一次真实长片采集加入字幕转场日志（页面内 `MutationObserver` 记录时间、文本、媒体时间），把缺口从“至少一次、109 ms 边界”升级为可复核的完整转场序列；该改动只增加证据，不改播放行为。
4. 40.215 秒“有声区间未见字幕”的三阈值检测候选仍待独立词对齐；它不属于本次采样缺口复算，不因本文被写成覆盖通过。
