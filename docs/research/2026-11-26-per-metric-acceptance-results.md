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

## 2026-11-27 审核修正：本地输入身份绑定

Codex 的 Spec 审核发现 `collect()` 在 `write_browser_recording()` 产生
`input_unchanged` 之前就构造了 `metrics`。用原采集替身复现：`on_start` 在 1.3 秒
替换本地 `sample.wav`、1.5 秒发送 `completed`、1.6 秒结束，写出的
`recording.input_unchanged=false`，但 `metrics.media_eof.status=observed`、
`errors=[]`。这违反 Testing Decisions 中“输入身份不符不能给出相应成功状态”。

修正先读取真实写出的 recording 身份结果，再决定依赖固定输入的指标：

- 本地文件 `input_unchanged=true` 时保持原行为。
- 文件在采集中改变时，`media_eof`、`cache_jump`、`continuity` 均为 `failed`，
  原因写明本地输入改变，`evidence` 保留修改前后哈希与文件身份。
- 文件在采集后无法读取或身份无法确认时，这三项为 `unknown`，原因分别写明未记录
  修改前身份或修改后无法读取。
- `session_count` 继续按独立请求账本记为 `observed`；`cancellation` 继续按 DELETE
  回执判定，不因输入变化一起抹掉。
- 远端仍按 `source_kind=remote_stream` 单列，不强加本地文件恒定哈希。
- 删除可生成 `observed` 的同步分支；`sync` 固定为 `unknown`，并指向独立评分器
  `tests/acceptance/score_acoustic_acceptance.py`。合并 `#116` 后保留仅作兼容的
  `acoustic_sync_assessed` 形参，`collect()` 固定传 `False`，该形参只写入证据，
  不改变 `sync=unknown`。

本切片未重写已返回 `failed` 的 DELETE 500 路径，未改 100 ms 全局采样门槛、
`measurement_invalid` 或 `exit_ok` 语义。

RED（修正前，新增测试经 `collect`/写出的报告）：

```sh
set -o pipefail
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pytest \
  tests/python/test_real_browser_acceptance.py \
  -k 'input_identity_mismatch' -q
```

- 退出码：`1`
- 计数：`2 failed, 60 deselected`
- 失败原因：替换和删除输入两种场景下，`media_eof` 仍为 `observed`，而不是
  预期的 `failed`/`unknown`；`cache_jump` 和 `continuity` 同样尚未读取身份结果。

GREEN（修正后）：

```sh
set -o pipefail
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pytest \
  tests/python/test_real_browser_acceptance.py \
  -k 'input_identity_mismatch' -q
```

- 退出码：`0`
- 计数：`2 passed, 60 deselected`
- 覆盖：替换输入使三项为 `failed`，删除/无法读取使三项为 `unknown`，原因含
  `input`；同一报告 `session_count` 仍为 `observed` 且计数为 1。

完整采集回归：

```sh
set -o pipefail
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pytest \
  tests/python/test_real_browser_acceptance.py -q
```

- 退出码：`0`
- 计数：`62 passed, 1 warning`（警告仅为 pytest 缓存目录只读）

相邻定向回归与静态检查：

```sh
set -o pipefail
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pytest \
  tests/python/test_real_browser_acceptance.py \
  tests/python/test_long_acceptance_probe.py -q
```

- 退出码：`0`
- 计数：`98 passed, 1 warning`

```sh
set -o pipefail
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pylint --errors-only \
  --init-hook="import sys; sys.path[:0] = ['tests/acceptance', 'tests/browser', 'tests/python']" \
  tests/acceptance/run_real_browser_acceptance.py \
  tests/python/test_real_browser_acceptance.py
git diff --check
```

- Pylint 退出码：`0`（无 error）
- `git diff --check` 退出码：`0`

## 2026-11-27 集成：合并 #116 并发分支

把远端 `origin/codex/issue-triage-20261126`（Draft PR #116，基点 `04785bb`）
以 `git merge --no-ff` 合入本分支的 `dcfdd30`。双方共同起源于 `508fe37`；
本候选的父提交即 `dcfdd30` 与 `04785bb`，没有改写任一侧历史。

保留并合并的行为：

- 并发分支：报告 `sampling_continuity` 字段、
  `tests/acceptance/sampling_continuity.py` 工具及
  `tests/python/test_sampling_continuity.py`、`docs/README.md` 与 `tests/README.md`
  导航、109 ms 缺口复算文档，以及 `checkpoint_post_delta` 非零时把最后检查点
  之后新增识别会话判为 `cache_jump=failed` 的检查。
- 本分支：`source_binding` 对本地输入身份变化的 `failed`/`unknown` 绑定、
  `collect()` 先写 recording 再生成 `metrics`、`sync` 固定 `unknown` 并指向独立
  评分器、采集指南与本文结果文档。
- 冲突仅在两份修改测试与 `build_metric_evidence()`：合并为单次、在
  `write_browser_recording()` 之后调用；函数同时接受 `recording` 与并发分支的
  `remote`/`acoustic_sync_assessed`/`checkpoint_post_delta`。传入 `recording`
  时仍以 recording 的 `source_kind` 和输入身份为准，直接单元调用可只传 `remote`。
  检查点证据仍保留 `visible_text`，新增会话/截图/容差判定不合并成未知。

集成后一次性回归：

```sh
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pytest \
  tests/python/test_real_browser_acceptance.py \
  tests/python/test_long_acceptance_probe.py \
  tests/python/test_sampling_continuity.py -q \
  -o cache_dir=/tmp/lisca-103-integration-pytest
```

- 退出码：`0`
- 计数：`108 passed`
- 覆盖双方回归：输入身份绑定、先 recording 后 metrics、采样缺口分类、逐指标
  证据、直播取消、长片探针；原 50 ms/100 ms 门槛、`measurement_invalid`、
  `exit_ok` 语义未改。

错误级 Pylint（含测试导入路径，覆盖 collector、连续性工具及两份修改测试）：

```sh
/home/wbb/.local/share/stream-with-asr/dev-venv/bin/python -m pylint --errors-only \
  --init-hook="import sys; sys.path[:0] = ['tests/acceptance', 'tests/browser', 'tests/python']" \
  --ignored-modules=dashscope.audio.asr --disable=E0611,E1101,E0110 \
  tests/acceptance/run_real_browser_acceptance.py \
  tests/acceptance/sampling_continuity.py \
  tests/python/test_real_browser_acceptance.py \
  tests/python/test_sampling_continuity.py
git diff --check
```

- Pylint 退出码：`0`（无 error）
- `git diff --check` 退出码：`0`

本轮未运行真实 ASR、真实浏览器采集或新的长片；构建由 Codex 负责。集成提交的
准确 SHA 在本文件随附提交完成后由交付报告给出，父提交范围即
`dcfdd30` + `04785bb`。

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
