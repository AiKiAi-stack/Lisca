# WebUI 使用问题修复：批量检测、字幕标点、样式面板

- 日期：2026-11-24（Asia/Shanghai）。
- 状态：用户已确认（2026-11-24 会话逐项确认；grill 结论见下）。
- 来源：用户 2026-11-24 反馈「上次的频道合辑扫描等待过长无法中断，读第二个片源合辑时无法扫描；字幕不要加句号；字幕调节按钮应外置并实时看到变化」。
- GitHub：[父 issue #113](https://github.com/AiKiAi-stack/Lisca/issues/113)；子任务 [#108 扫描取消](https://github.com/AiKiAi-stack/Lisca/issues/108)、[#109 句末标点](https://github.com/AiKiAi-stack/Lisca/issues/109)、[#110 样式面板](https://github.com/AiKiAi-stack/Lisca/issues/110)。

## Problem Statement

用户加载 M3U 频道列表（839 个频道）后启动「批量检测」，在 342/11473 处长时间等待，没有任何中止入口；随后加载第二个列表，批量按钮永久不可点，实际上无法再扫描。当前字幕在句末显示「。」，不符合用户对直播字幕的阅读要求。字幕字号/位置/字体调节位于模态「设置」弹窗内，打开时画面被 backdrop 压暗并被居中面板遮挡，无法边调边看。

已核实的事实（HEAD `974f6cf`）：

- 批量检测是**纯前端** 20 并发循环（`lisca/web/static/index.html:490-508`）；`chProbeBtn.disabled = true` 只在 `done >= total` 分支复位（`:493,505`）；全仓无 `AbortController`；`fetch('/probe')` 无 timeout；关闭弹窗不取消。
- 扫描中 `loadChannels` 重新赋值全局 `channels`（`:392,403`），运行中循环读 `channels[idx].url`（`:501`）→ `TypeError`，20 个 worker 全灭，按钮永不恢复。截图「共 839 个频道 / 批量检测 342/11473」即换列表后旧批次仍在跑。
- 浏览器同源 HTTP/1.1 通常只有 6 条连接，20 个 `/probe` 会把新的 `/channels`、`/probe` 堵在网络层。
- 后端 `/probe` 是异步请求而非串行队列，没有扫描锁；`httpx.Timeout(8.0, connect=3.0)` 只是每次 socket read，1MB 分片在慢源上可跑数分钟（`lisca/web/app.py:631-655`），整批没有 deadline；`/probe`、`/channels` 都不接收 `Request`，感知不到客户端断开。
- 句号来自 DashScope FunASR 的 final `sentence["text"]`（`lisca/asr/asr_client.py:87` 原样透传；`app.py:1070` 只删除「纯标点无语音」行）；Lisca 没有任何补标点代码。历史区与当前字幕同源（`index.html:581,638,1052-1053`）。
- 样式面板在 `settingsDialog`（`showModal()`，`:2168`；backdrop `player.css:148-149`）的「字幕样式」页签内（`:36,47-61`）；`styleBtn`（`:88`）是 `hidden` 死代码；`applyStyle` 直接写 `#current` 内联样式（`:338-353`）；全屏 `#playerWrap:fullscreen #current` 的 `clamp()` 会覆盖它（`player.css:142`）。

## Solution

### 1. 批量检测可取消、可替换

- 前端引入扫描代次 `scanGeneration` 与 `activeScanController`。每次批量开始或列表加载递增代次；旧代次的 worker 在每次 `await` 后检查代次并退出。
- 新批量开始、或 `loadChannels` 开始前，先 `abort()` 旧 controller 并复位按钮；`/probe`、`/channels` 的 fetch 都带 `signal` 与单请求上限（`AbortSignal.timeout(15000)`，不支持时用 setTimeout + abort 回退）。
- 批量启动时对频道列表取快照（`const list = channels.slice()`）与 `total`，worker 只读快照，杜绝换列表越界。
- 运行中「批量检测」按钮变成「取消检测」（`aria-pressed`）；点击即 abort、代次递增、按钮恢复、`chInfo` 显示「已取消，已测 N/M」，已测行的结果保留。
- 关闭 `channelsDialog` **不取消**扫描；进度与结果在重开时仍可见，取消只由按钮触发。
- 所有结束路径走统一 `finally`：恢复按钮、清除「检测中…」。任何异常都不能让按钮停在 disabled。
- 后端 `/probe` 增加 `request: Request`：在两段 `aiter_bytes()` 之间检查 `await request.is_disconnected()`；用 `asyncio.wait_for(..., timeout=15)` 包住整个探测（项目支持 Python 3.10，不能用 `asyncio.timeout()`）。取消/超时不写入 `_PROBE_CACHE`，返回 `{ok: false, timed_out: true}`；前端把 `AbortError` 显示为「已取消」，不标 ❌ 失效。
- `/channels` 同样接收 `Request` 并加总超时（30s）；本地文件读取移出事件循环（`asyncio.to_thread`）。

### 2. 当前字幕不显示句末标点

- 新增显示层函数 `stripTrailingSentencePunctuation(text)`：去掉末尾连续的 `。！？.!?;；` 及其后的空白与收尾引号/括号（`”’）)]】`），不触碰句中的逗号、顿号、冒号与省略号。
- 仅作用于 `#current` 的渲染（final 与分页后的每一页）；`#history` 保留原文，便于追溯与复制。断句、分页、提交、服务端纯标点行清理继续使用 `source_text`，不改变识别行为。
- 分页中间页本来不以句末标点结尾，函数对它们无副作用；整句最后一页去掉句末标点。

### 3. 字幕样式外置并实时预览

- 把 `#stylePanel` 移出 `settingsDialog`，成为页面级面板：`position: fixed; right: 16px; bottom: 16px; z-index: 20; max-height: 60dvh`；`≤760px` 改为底部 sheet。按钮 `#styleBtn` 移入 `.player-tools` 并可见，删除「字幕样式」设置页签。
- 样式改为 CSS 变量（`:root` 上的 `--caption-size`、`--caption-bottom`、`--caption-font`、`--caption-color`、`--caption-bg`），`#current` 使用 `var()`；`applyStyle` 改为 `setProperty`，删除内联样式写入，保留 localStorage 持久化与 `fitCaptionWidth` 的 computed style 读取。
- 全屏继续沿用用户设定字号：移除 `#playerWrap:fullscreen #current` 的 `clamp()` 覆盖。**v1 全屏内不可调**（外部面板不在全屏渲染树），退出全屏再调；不在此规格内做全屏克隆面板。
- 面板为非模态（不用 `<dialog>`，不复用 `showModal`），支持 Esc 关闭、`aria-expanded`/`aria-controls`；拖动时立即生效，无需防抖。

## Testing Decisions

1. **边界**：前端用 `tests/frontend/*.test.cjs` 的既有方式（`vm` 切片执行 `index.html` 片段）断言行为；后端用 `tests/python/`，以本地可控的慢上游替身驱动 `/probe`。
2. **扫描可取消**：取消后不再发起新 `/probe`，已测行保留，按钮恢复可点；第二次扫描能立即开始；扫描中调用 `loadChannels` 不抛 `TypeError`、旧批次停止、新列表正常渲染；关闭并重开弹窗进度仍在。
3. **扫描上界**：慢上游下 `/probe` 在 15s 内返回 `timed_out` 且不写缓存；断开客户端后，后端在下一次 chunk 前停止（用可控慢响应验证）。
4. **标点**：「警方找到疑似女尸。」渲染后无句号；英文 `on the left.` 无句点；句中「，」保留；`……` 保留；纯标点行仍被清空；`#history` 仍保留原文。
5. **样式**：面板不在任何 `dialog` 内；打开时页面无 backdrop；拖动字号后 `#current` computed `font-size` 立即变化；刷新后恢复；`设置` 弹窗不再有「字幕样式」页签；全屏字号等于用户设定值。
6. **真实浏览器旅程**：在 `127.0.0.1:8002` 用真实 M3U 完成「加载 → 批量检测 → 取消 → 再检测 → 扫描中换列表 → 选台播放 → 调样式 → 观察无句号」，留截图/时间线；不以 Node 断言或 HTTP 200 代替。

## Out of Scope

全屏内调节样式；操作记录落盘（见[操作记录规格](2026-11-24-operation-record-spec.md)）；整体交互重构（另立审计任务）；把批量扫描搬到后端作业队列；频道列表持久化与「合辑」数据模型；去标点改变断句/分页/识别行为。

## Acceptance

- 自动回归：新增前端与后端用例按第 3 节逐条通过，既有回归不回退。
- 产品验收：真实浏览器旅程完成且留证；`logs/` 中保留该次运行的诊断日志。
- 未通过项按原样记录，不用「界面看起来好了」代替验收；实现与验收都只在 `127.0.0.1:8002` 上操作。
