# PR89 验收缺口复测：启动 / 同步 / 长时（2026-11-04）

> 本文是 `notes/pr89-acceptance-20261104/REPORT.md` 的仓库副本，原始报告与截图/音频仍在本机证据目录。

## 结论（先看）

- **启动延迟：仍不通过，且不是本地显示层可单独修复的问题。** 当前候选 `027b3254cc56cc7ea58b1e2066958e86481cc6a0` 用固定 HLS、真实 ASR、每语 20 次独立启动测得：中文 P95 **5013.9 ms**，英文 P95 **4470.3 ms**；门槛是 **3000 ms**。20/20 次测量均有效，冷/热、HLS 分片/PCM/进程相关性和浏览器采样均通过。相比旧 40 次的 10.5135 / 5.9663 秒已明显改善，但两语仍未达标。
- **音画字幕同步：当前候选的真实浏览器采集已完成，但评分器返回 unknown，不能记通过。** 采集本身干净（1 次 POST、0 错误、真实音频与 175 张转场截图、真实转写准确率通过）；评分器因播放期间 9 次短暂停顿把音频切成多个 epoch，短 epoch 锚点不足，返回 `audio_alignment.status = unmatched`，未得到 50 句有效同步样本。
- **长时观看（中英各 30 分钟）完成，应用时间轴代理没有增长。** 中文 1800.26 秒 / 36007 采样、英文 1800.37 秒 / 36009 采样，最大采样间隔 68/56ms，0 公开错误、各 1 次会话创建、0 来源缓冲；前后 5 分钟延迟代理增长 **-0.044 秒（中）/ -0.228 秒（英）**（均 ≤1 秒）。但这是应用 ASR 词时间代理，不是独立声学真值；2 秒以上无可见字幕区间很多（新闻自然停顿/切句），英文代理绝对值为负、不可跨语种比较。不能据此宣称有声覆盖或声学同步通过。
- **音画字幕同步的本地预缓冲候选也已实测失败：** 新增 5 秒本地预缓冲的 `23f9292` 在 480 秒内 `local_timeout`，未完成参考文件，无法评分；不作为修复提交。
- 因此 **PR89 的正式产品验收仍不能通过**；把当前结果写成通过会违反原门槛。需要用户在“接受约 5 秒启动门槛 / 更换更低延迟的识别服务 / 继续保留缺口”之间做决定。

> 本轮没有修改 main 的产品代码；实验分支未合并。所有结果只适用于上述候选、固定输入和本机环境。历史失败、unknown 和原门槛保留。

## 候选、环境与输入

| 项目 | 值 |
| --- | --- |
| 候选 | `027b3254cc56cc7ea58b1e2066958e86481cc6a0`（main，PR89 已 squash 合入） |
| 工作目录 | `/home/wbb/.local/share/stream-with-asr/checkouts/p0-live-source`，工作区 clean |
| 服务 | 8002，`preview-install-venv`，Python 3.12，DashScope 2.0.3；Key 仅内存 `configured=true/saved=false` |
| 启动固定输入 | `uploads/acceptance-media/{zh,en}-single-speaker-reference.wav`，SHA-256 `d40bb3a…` / `4b7d551…`，16.18925 / 16.55 秒哈希固定前缀 |
| 启动 HLS | `tests/acceptance/fixed_hls_startup_acceptance.py` 生成 MPEG-TS/H.264+AAC，按源时钟释放已完成分片；应用与浏览器代理实际读取 |
| 启动采集器 | `tests/acceptance/fixed_hls_startup_acceptance.py collect/analyze` + `startup-observer.py`（仅外部可撤销计时，不记录 Key/正文） |
| 同步采集 | `tests/acceptance/run_real_browser_acceptance.py --capture-acoustic-sync`，真实 Chrome、真实音频输出、截图/OCR |
| 同步评分 | `tests/acceptance/score_acoustic_acceptance.py`，独立 VAD 声区间 + 浏览器渲染音频对齐 |
| 长时 | `tests/acceptance/local_live_replay.py` 把固定中文/英文长新闻按 `-re` 转滚动 HLS，普通 8002 产品链路 + 真实 ASR |

## 1. 启动延迟：20+20 次当前候选实测

命令形态（每语独立冷启动一次新服务，随后 19 次热启动）：

```bash
python tests/acceptance/fixed_hls_startup_acceptance.py prepare --output <dir>
python tests/acceptance/fixed_hls_startup_acceptance.py serve --port 8766 --output <dir>
python tests/acceptance/fixed_hls_startup_acceptance.py collect --language zh \
  --label zh-startup-01 --startup-class cold --service-pid <pid> \
  --cold-first-session-confirmed --output <dir> --allow-real-recognition
# ... 02..20 warm ...
python tests/acceptance/fixed_hls_startup_acceptance.py analyze --language zh \
  --output <dir> --origin-log <dir>/origin-requests.jsonl \
  --decoded-log <dir>/decoded-arrivals-zh.jsonl
```

| 指标 | 中文 | 英文 |
| --- | ---: | ---: |
| 有效启动 / 总启动 | 20 / 20 | 20 / 20 |
| 冷 / 热 | 1 / 19 | 1 / 19 |
| P50 | 4920.8 ms | 4414.0 ms |
| **P95（nearest rank）** | **5013.9 ms** | **4470.3 ms** |
| 最大 | 5014.5 ms | 4473.5 ms |
| 3 秒门槛 | **失败** | **失败** |
| 运行错误 | 0 | 0 |
| HLS 证据校验 | 通过 | 通过 |
| HLS 分片可用延迟 P95 | 578.6 ms（不计入门槛） | 见分析 JSON |

证据：
- 分析：`startup-run/zh-startup-analysis.json`（SHA-256 `0fbb89d1…`）、`en-startup-analysis.json`（`33908c85…`）
- 原始请求/解码：`startup-run/origin-requests.jsonl`（`5b93cc79…`）、`decoded-arrivals-zh.jsonl`（`7559afff…`）、`decoded-arrivals-en.jsonl`（`76d6b2ef…`）
- 每例报告/录制：`startup-run/collector/`

### 首条稳定字幕实际是什么

- 中文首条可见：`广州女大学生登山`，浏览器采样约 5.45 秒（起点为 origin 首音频体写入前），源范围约 0.6–4.12 秒；识别过程依次出现 `Good`（0.6–0.72）、`广州女大学生`（0.72–）、`广州女大学生登`、`广州女大学生登山`。
- 英文首条可见：`He began a confused complaint against the wizard,`，约 4.95 秒（报告采样）。
- 独立复核（只读，未改代码/未重跑 ASR）把两例拆到 provider 交付层：中文首个完整短语 `广州女大学生登山` 在 origin 后 **+4274.3 ms** 才出现，英文完整主句 `He began a confused complaint against the wizard,` 在 **+3272.2 ms** 出现。应用在 provider 交付后的显示只有 **4 ms（中）/26 ms（英）**；应用自身的稳定策略（公共前缀重复 + 2.5 秒词组分页/标点分句）额外等待 530 ms / 1012 ms。即使把本地稳定策略和 100ms 端点都假设为零，也仍有 **4375.8 ms（中）/3373.6 ms（英）**，超过 3 秒。
- 能塞进 3 秒的候选文本都是未完成草稿：中文 `广州女大学生`（+2685.4 ms，名词短语，后来被扩展）、英文 `He began a confused complaint against the`（+2688.5 ms，缺宾语）、英文 `He began`（+1178.5 ms）。显示它们会违反“完整稳定短句 / 不显示未完成草稿”的要求。
- 因此本地显示层没有安全修复空间；把首词组提前到 3 秒内只能发布更短残片，或更换识别服务/门槛。

### 结论

3 秒门槛在当前固定输入 + 当前 DashScope FunASR realtime 上不可达；本地显示/分页不是主因。旧 10.5/5.97 秒的失败保留，本轮 5.01/4.47 秒是当前候选的新证据，但仍失败。

## 2. 音画字幕同步：采集真实完成，评分 unknown

- 采集：`sync/zh-sync-pr89-01.*`，316.619 秒中文固定参考；真实 Chrome、真实音频输出、6412 个 50ms 采样、175 张转场截图、1 次 POST、0 错误、转写准确率组件通过。
- 评分输入完整：参考 manifest、独立 VAD 声区间、浏览器渲染音频、截图/OCR 均可用。
- 评分结果：`sync_status = unknown`，`measured_item_count = 0`，`unknown_item_count = 59`。
- 关键原因：`audio_alignment.status = unmatched`，8 个 epoch 中 0/4/5 无足够声学锚点。实测播放期间共有 **9 次停顿，总 3.73 秒，最长 2.1 秒**（应用“等待当前片段的原文字幕”），短停顿把音频对齐切成无法各自锚定的 epoch；评分器要求每个 epoch 至少 2 个锚点。
- 这不是“同步已失败”：没有产生可用的起止偏差样本，所以是 **unknown**，不能记通过，也不能记失败。

可复核文件：
- `sync/zh-sync-pr89-01.report.json`、`.recording.json`、`.playback.webm`、`.screens/`
- 评分：`sync/zh-sync-pr89-01.acoustic-score.json`
- 转写分数：`sync/zh-sync-pr89-01.score.json`

### 需要决定的事项

1. 是否允许在评分中合并“小于 X 秒的短暂停顿”并按覆盖缺口计账（不改变 0.5/1.0 秒偏差门槛）；或
2. 是否给本地文件增加更长但仍有界的起始缓冲，以减少播放中途等待字幕的停顿（会影响首次等待，需产品确认）；或
3. 保持 unknown，继续保留同步缺口。

本报告不擅自放宽评分器或改变产品行为。

## 3. 同步修复候选（仅本地文件）——已试，未成功

在确认“同步 unknown”主要由本地播放中途等待字幕造成后，另建独立工作树候选：

- 分支：`codex/pr89-acceptance-local-buffer-v2`；提交 `23f9292`（基于 `027b325`，只改 `tools/webui.py` + 回归）。
- 行为：已知时长足够长的本地原文字幕文件首次播放使用 5 秒有界预缓冲；确认静音仍按 1.5 秒规则释放；直播 HLS 仍 0.1 秒；短文件/未知时长保持 0.1 秒。
- 回归：新增本地预缓冲测试；相关 39 项通过；完整 Python **541 passed / 1 skipped**（7:46）。
- **真实复测失败：** `sync-fix/zh-sync-fix-01.*` 在 480 秒上限内以 `local_timeout` 结束（1 次 POST、9602 个采样、采样有效、0 个公开错误），没有完成 316.6 秒参考文件，无法评分。首次 ready 3614ms、首可见 4400ms。该候选没有解决中途停顿，不能作为同步修复；补丁保留在 `local-buffer-v2-failed.patch`，不合并。
- 结论：本地文件预缓冲方向未能得到可接受的真实结果；同步缺口和评分 unknown 继续保留。

## 4. 长时观看（30 分钟）

中文（`long/zh-long-pr89.*`，本地公视新闻 52:07 转滚动 HLS，真实 ASR，30 分钟上限观察）：

| 指标 | 结果 |
| --- | ---: |
| 观察时长 | 1800.26 秒（cap） |
| 50ms 采样 / 最大间隔 | 36007 / 68 ms（有效） |
| 可见字幕采样比例（DOM） | 87.77% |
| 暂停采样 / 缓冲采样 | 50 / 0 |
| 会话创建 / 公开错误 | 1 / 0 |
| 前 5 分钟延迟代理中位数 | 4.699 秒 |
| 后 5 分钟延迟代理中位数 | 4.655 秒 |
| **前后增长（代理）** | **-0.044 秒（≤1 秒）** |
| 结束方式 | 观察上限取消（非正常 EOF） |

- 延迟代理使用应用自己的 ASR 词/句末时间，**不是独立声学真值**；只能说明应用时间轴前后没有增长，不能单独证明声学同步。
- 2 秒以上的“无可见字幕”区间很多（新闻中自然停顿/切句），已逐段保留在 `long/zh-long-pr89.summary.json`；不能把 87.77% DOM 可见率当作有声覆盖率。

英文（`long/en-long-pr89.*`，PBS 新闻 56:45 转滚动 HLS，真实 ASR，30 分钟上限观察）：

| 指标 | 结果 |
| --- | ---: |
| 观察时长 | 1800.37 秒（cap） |
| 50ms 采样 / 最大间隔 | 36009 / 56 ms（有效） |
| 可见字幕采样比例（DOM） | 83.39% |
| 暂停采样 / 缓冲采样 | 49 / 0 |
| 会话创建 / 公开错误 | 1 / 0 |
| 前 5 分钟延迟代理中位数 | -4.185 秒 |
| 后 5 分钟延迟代理中位数 | -4.413 秒 |
| **前后增长（代理）** | **-0.228 秒（≤1 秒）** |

- 英文代理绝对值为负，说明它只适合做前后趋势比较，不能当作实际字幕延迟；英文源 PTS/词时间与中文不同，绝对值不可跨语种比较。
- 两语 30 分钟都完成、无公开错误、采样有效、无来源缓冲；但都没有独立声学 95% 覆盖或同步真值。长时稳定性是正面证据，**不是** #63 同步或正式发布通过。
- 结束方式均为观察上限取消，不是正常 EOF；未把 cap 记作完成。

## 证据目录

`/home/wbb/.local/share/stream-with-asr/notes/pr89-acceptance-20261104/`

- `startup-run/`：40 次启动报告、分析、HLS 源/解码日志
- `sync/`：中文声学同步采集与评分
- `long/`：30 分钟长时观察
- 旧失败/旧 unknown 保留在 `notes/acceptance-execution-20261027/`、`notes/recovery-20261102/`，未被覆盖。

## 边界与未证明

- 启动延迟只覆盖固定哈希 HLS 前缀和本机环境；不是公网 30 分钟直播或所有来源。
- 同步评分只覆盖浏览器 AudioContext 渲染输出，不是物理扬声器/房间声学延迟；未得到有效测量。
- 长时观察的来源是本机滚动 HLS，不是公网 HLS；不能证明公网来源的固有缓冲/网络。
- 没有重新执行 Windows、第二设备或正式发布验收；这些仍按用户既有决定排除。
