# 操作记录与后端日志（供 Agent 与用户排查）

本页说明本机的两份可追溯记录，以及如何用命令行/`jq` 还原「用户点了什么 → 后端做了什么」。

## 文件

| 文件 | 内容 | 保留 |
| --- | --- | --- |
| `logs/ops-YYYYMMDD.jsonl` | 结构化操作记录：用户动作、HTTP 摘要、会话生命周期、系统动作 | 7 天 / 50MB，按天轮转，只清理 `ops-*` |
| `logs/webui-YYYYMMDD.log` | WebUI 运行日志（`lisca.web.app` 等 INFO/WARNING/ERROR） | 5MB × 5 轮转 |
| `logs/webui-windows.log` | 便携版启动器的 stdout（如使用 Windows 启动器） | 由启动器管理 |

`logs/`、`uploads/` 属于本机数据，不进入 Git。

## JSONL 行格式

每行一个 JSON 对象，公共字段：

| 字段 | 含义 |
| --- | --- |
| `ts` | UTC ISO 时间（毫秒） |
| `kind` | `user`（前端动作）、`http`（请求摘要）、`session`（会话起止）、`system`（后端动作） |
| `session` | 页面会话 ID（`sessionStorage` 的 `lisca-ops-session`）；缺失时为 `null` |
| `req_id` | 请求 ID，来自 `X-Lisca-Request-Id` |

### kind=user

```json
{"kind":"user","session":"0810...","action":"scan_start","detail":"60 个频道","client_ts":"2026-11-24T14:50:07.860Z"}
```

`action` 是前端事件名（`channels_load`、`scan_start`、`scan_cancel`、`connect`、`play`、`style_panel`、`sub_toggle` 等）；`detail` 已做显示层脱敏。

### kind=http

```json
{"kind":"http","session":"0810...","req_id":"0810...-4","method":"GET","path":"/channels","status":200,"dur_ms":12.4}
```

只记路径不记 query；`/probe` 逐条不落盘，批量结果由 `scan_start/scan_cancel/scan_complete` 聚合。

### kind=session

```json
{"kind":"session","session":"0810...","event":"start","url":"https://…/live.m3u8","engine":"aliyun-funasr"}
```

### kind=system

```json
{"kind":"system","session":"0810...","action":"channels_loaded","source":"url","count":839,"dur_ms":420.1}
{"kind":"system","session":"0810...","action":"session_ready","engine":"aliyun-funasr","local_media":false}
{"kind":"system","session":"0810...","action":"session_end","reason":"completed","captions":42,"duration_sec":613.2}
```

已记录的系统动作：`channels_loaded`、`channels_failed`、`upload`、`share_start`、`share_stop`、`cloud_config`、`session_ready`、`session_end`。

## 命令行读取

```sh
# 最近 200 条（默认脱敏）
python -m lisca.ops

# 某个会话的完整链路
python -m lisca.ops --session <session-id> --limit 500

# 只看后端系统动作或用户动作
python -m lisca.ops --kind system
python -m lisca.ops --kind user --kind session

# 本机调试需要完整 URL 时（不脱敏，注意屏幕共享）
python -m lisca.ops --full --session <session-id>
```

## jq 常用查询

```sh
# 最近一次运行的会话 ID
jq -r 'select(.kind=="session" and .event=="start") | .session' logs/ops-*.jsonl | tail -1

# 某会话的时间线（按时间）
jq -c 'select(.session=="<id>")' logs/ops-*.jsonl | sort

# 失败的后端动作
jq -c 'select(.kind=="system" and (.action|endswith("_failed")))' logs/ops-*.jsonl

# 慢请求
jq -c 'select(.kind=="http" and .dur_ms>2000)' logs/ops-*.jsonl
```

## 边界

- 默认视图与默认导出脱敏（隐藏 URL 凭据与 query 值）；磁盘 JSONL 保留原值以便复现，文件权限 0600。
- 不记录识别文本正文；字幕内容在字幕记录与验收录制中，避免把节目内容复制进操作日志。
- 操作记录写入失败不影响主流程，只在服务日志中留一条警告。
- 这套记录用于排查与复现，不是合规审计，也不远程上报。
