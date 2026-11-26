# 2026-11-26：#103 浏览器采集逐指标证据结果

## 范围

- 起始提交：`df2b9a0`。
- 仅修改真实浏览器采集器、对应 Python 回归、采集指南和本结果文档。
- 未启动 8002 服务、未调用真实 ASR、未使用凭据、未放宽原采样门槛。
- 全局 `sampling.status`、50 ms 目标、100 ms 最大间隔和 `exit_ok` 语义不变。

## 实现行为

`run_real_browser_acceptance.collect()` 的 `.report.json` 新增 `metrics`，每项包含
`status`（`observed` / `unknown` / `failed`）、`reason` 和 `evidence`：

- `media_eof`：直接以采样中的媒体 `ended` 样本为证据；没有该样本时本地记为
  `failed`，远端或未开始观测记为 `unknown`。
- `cache_jump`：每个检查点必须同时具有目标容差内的媒体时间、已写出的截图，以及
  该检查点前后零新增识别会话；目标不匹配或新增会话为 `failed`，缺证据为 `unknown`，
  未请求检查点为 `unknown`。
- `session_count`：直接使用请求账本；浏览器未启动时为 `unknown`。
- `cancellation`：仅在取消请求（排除正常完成后的 collector cleanup）且收到 DELETE
  回执时记为 `observed`；非 2xx 为 `failed`，无回执为 `failed`。
- `continuity`：采样有效且有播放样本时记为 `observed`；采样超限或不足时为
  `unknown`，不补插值。
- `readability`、`sync`、`coverage`：缺少独立可读性、声学同步或声音参考证据时
  继续 `unknown`。
- `resources`：没有独立资源记录输入时继续 `unknown`。

109 ms 间隔仍使 `sampling.status=measurement_invalid` 且 `exit_ok=false`，但不会
抹去同一报告中直接观察到的 EOF、缓存跳转和会话计数。检查点证据失败只影响
`cache_jump` 及原有全局采样判定，不把其他未知指标改成通过。

## RED / GREEN

RED（实现前，本地媒体和替身浏览器经现有 `collect`/报告出口）：

```sh
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pytest \
  tests/python/test_real_browser_acceptance.py -q
```

- 退出码：`1`
- 计数：`6 failed, 54 passed`
- 失败原因：报告没有 `metrics`，且目标失配/新增会话/浏览器未启动场景无法区分状态。

GREEN（实现后）：

```sh
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pytest \
  tests/python/test_real_browser_acceptance.py -q
```

- 退出码：`0`
- 计数：`60 passed, 1 warning`（警告仅为 pytest 缓存目录只读）
- 覆盖：109 ms 间隔下 EOF/跳转/会话计数仍 `observed`；浏览器未启动时全部
  `unknown`；目标失配和新增会话使跳转 `failed`；截图缺失为 `unknown`；干净有效
  采样保持 `exit_ok=true`，可读性/同步/覆盖/资源仍 `unknown`；现有直播取消入口
  补充断言 DELETE 回执为 `observed`。

相邻回归：

```sh
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pytest \
  tests/python/test_real_browser_acceptance.py \
  tests/python/test_long_acceptance_probe.py -q
```

- 退出码：`0`
- 计数：`96 passed, 1 warning`

静态检查：

```sh
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pylint --errors-only \
  --init-hook="import sys; sys.path[:0] = ['tests/acceptance', 'tests/browser', 'tests/python']" \
  tests/acceptance/run_real_browser_acceptance.py \
  tests/python/test_real_browser_acceptance.py
git diff --check
```

- Pylint 退出码：`0`（无 error）
- `git diff --check` 退出码：`0`

## 未运行与剩余产品项

- 未重跑真实 1802 秒长片和真实 109 ms 间隔采集。本轮用现有采集器替身经
  `collect`/报告出口验证，不把历史 `zh-local-managed10/13` 重写成合格。
- 真实本地完整 EOF、四次缓存跳转、零新增会话仍需一次新的真实观察；即使逐指标
  为 `observed`，只要全局采样仍超限，按本轮规则整体 `exit_ok` 继续为失败。
- 正式有声覆盖、独立声学同步、可读性和资源记录没有独立输入，继续 `unknown`。
- 中英文 30 分钟直播连续性、资源增长/释放和频道旅程属于 #43/#44/#63 的剩余项。
- 未运行前端回归：本次没有前端或应用代码变更。

报告实现入口：`tests/acceptance/run_real_browser_acceptance.py`；回归入口：
`tests/python/test_real_browser_acceptance.py`；字段说明：
`docs/guides/real-browser-collector.md`。
