# 操作记录与可追溯日志

- 日期：2026-11-24（Asia/Shanghai）。
- 状态：用户已确认（2026-11-24 会话逐项确认）。
- 来源：用户 2026-11-24 反馈「我的操作在后台没有任何可复现、可追溯的记录，也没有后端操作记录」。
- GitHub：[#111 操作记录与可追溯日志](https://github.com/AiKiAi-stack/Lisca/issues/111)。
- 依赖：[WebUI 使用问题修复规格](2026-11-24-webui-usage-fixes-spec.md) 的动作埋点；本规格独立实现与验收。

## Problem Statement

用户点「批量检测」「播放」「取消」之后，没有任何地方能把「我点了什么 → 后端收到什么 → 结果如何」按顺序还原：

- 前端只有内存环形缓冲 500 条（`lisca/web/static/index.html:255-275`），刷新页面即丢；只能在「诊断日志」里手动看和复制。
- 后端 `app.log` 记录服务端事件，但没有用户动作、没有请求 ID、没有会话关联；`_ServerLogHandler` 只把服务端行经 WS 推给前端（`lisca/web/app.py:401-409`）。
- 扫描、批量检测、取消都没有可关联的标识（扫描纯前端、无扫描 ID），失败后无法判断对应哪次操作与哪个输入。

目标是最小可追溯：用户报问题时给出会话 ID 与时间，维护者能从本机文件还原动作序列、对应请求、结果与关键输入，用于复现；不做合规级审计平台。

## Solution

### 会话与请求标识

- 页面加载生成 `session_id = crypto.randomUUID()`，存 `sessionStorage`；同标签页内刷新保持不变。
- 每次由用户动作发起的 fetch 生成 `req_id`，通过 `X-Lisca-Request-Id` / `X-Lisca-Session-Id` 请求头发送；前端统一封装 fetch，避免逐处补。
- 前端 `logEvent(type, detail)` 升级为 `logEvent(type, detail, { reqId, params })`，在写入内存缓冲的同时进入上报队列。

### 事件与落盘

- `POST /ops` 追加事件行，`GET /api/ops?session=&limit=` 读取本机记录供面板恢复。
- 后端中间件记录每个 HTTP 请求：`ts`、`session_id`、`req_id`、`method`、`path`（不含 query）、`status`、`dur_ms`、`outcome`。
- 显式生命周期事件：`session_start` / `session_end` / `session_error`；扫描聚合事件：`scan_start` / `scan_cancel` / `scan_complete`（含成功数、失败数、取消数、耗时）。不落逐频道 probe 行。
- 写入 `logs/ops-YYYYMMDD.jsonl`（`O_APPEND`，每行一个 JSON，文件权限 0600）；启动与写入时清理超过 7 天或总量超过 50MB 的 `ops-*.jsonl`，不触碰其他日志。
- 保留：写入失败（磁盘满/权限）不得影响主流程，只在前端状态处提示一次。

### 脱敏与导出

- 磁盘上行保留完整 URL（含 query），因为 `logs/` 是本机数据且已被忽略；文件权限 0600。
- 展示、复制、默认导出都脱敏：复用前端 `redactDiagnosticDetail` 与后端 `_redact_url`，隐藏 userinfo、query 值与片段。
- 导出提供「包含完整地址」显式选项，勾选时给出提示；导出 JSONL 行与磁盘一致时才包含原值。

### 查看界面

- 「诊断日志」改为「操作记录」：合并前后端时间线（用户动作 / HTTP 请求 / 会话生命周期 / 扫描聚合），按类型筛选，显示当前 `session_id`，提供复制与导出 JSONL。
- 刷新后从 `GET /api/ops?session=` 恢复当前会话时间线；跨会话记录只能导出文件查看，不默认全量加载。
- 不把整条 Python 日志流塞进 JSONL；会话级生命周期事件单独记录。

## Testing Decisions

1. **中间件**：任意请求落一行，含 method/path/status/dur_ms 与 `X-Lisca-Request-Id`、`X-Lisca-Session-Id`；无请求头时字段为空而不报错。
2. **`POST /ops`**：追加一行且内容与入参一致；缺少 session 时仍可落盘并标记 `session: null`；写入失败返回可控错误但不影响其他路由。
3. **读取与脱敏**：`GET /api/ops` 按 session 过滤、按时间排序；默认返回脱敏值；导出接口默认脱敏，`full=1` 时才返回原值。
4. **保留策略**：构造超龄与超量文件，验证只删除 `ops-*` 且保留 7 天内、50MB 内最新记录。
5. **前端**：`logEvent` 触发批量上报（≤200ms 或 ≤20 条合并）；面板时间线合并顺序正确；筛选、复制、导出默认脱敏；刷新后时间线从后端恢复。
6. **真实旅程**：在 `127.0.0.1:8002` 走「加载频道 → 批量检测 → 取消 → 播放 → 停止」，导出的 JSONL 能用同一 `session_id` 串起全部动作与 HTTP 行，并定位到输入（脱敏值与完整值各验证一次）。

## Out of Scope

合规审计、远程上报、日志加密、跨机器汇总、逐频道 probe 明细落盘、把 Python 日志全量转成结构化事件、操作回放自动化。

## Acceptance

- 自动回归覆盖第 3 节；真实旅程导出文件可用 `session_id` 还原动作序列。
- 用户能按「会话 ID + 时间」定位到对应操作与请求；无敏感值出现在默认视图与默认导出中。
- 未实现前不宣称可追溯；磁盘保留原值这一取舍写进导出提示与本文档。
