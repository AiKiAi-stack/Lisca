# 原文字幕：本地接续记录

日期：2026-10-23（Asia/Shanghai）。当前分支 `feat/cloud-translation`，本轮接续起点为 `5a4dc434c86e8665999192f07cde1b13949fb1dd`。用户暂时无法登录 GitHub，本轮只做本地检查与提交；未推送、未创建线上 Issue。

## 当前接续入口（2026-10-23 后续确认）

- 用户已接受[首版实现规格 / TR](2026-10-23-original-caption-release-spec.md)的测试边界、1 秒/2 秒初始退避，以及[12 项任务和依赖](2026-10-23-original-caption-ticket-proposal.md)。
- 正式逐项任务位于 `.scratch/original-caption-release/issues/`，这些任务 Markdown 已在忽略规则中单独放行，可随 Git 保存；其他临时数据继续忽略。
- 当前 12 项均未完成。01–07 无技术阻塞项，可从公开会话到真实播放器等已确认测试边界着手；08–12 需遵循任务依赖和实际环境前提。
- 原 L01–L06 仅作来源，不维护第二套执行状态。以下运行与测试记录属于 `98b798b` 实现基线；本次规格和任务确认没有新增功能通过证明。

## 需求入口

- `SPEC.md` 是历史规格，当前范围以 [原文字幕 PRD v1.0](2026-10-21-live-translation-prd.md) 和 [ADR-0008](../adr/0008-original-language-captions.md) 为准。
- 当前优先中文语音显示中文、英文显示英文；默认页面与检查连接不启用翻译。旧显式翻译接口的兼容行为保留。
- 产品仍未完成公开发布验收；本地提交和测试通过不等于 PRD 全部交付。
- 待办现以[正式任务索引](2026-10-23-original-caption-ticket-proposal.md)跟进；[本地 Issue 草稿](2026-10-23-local-issues.md)保留来源。字幕显示/同步、控制与恢复是优先方向，评分来源绑定可独立推进；真实环境前提见逐项任务。

## 本次保留并验证的改动

- 本地原文音频按实际 PCM 时长投送，识别器最多提前约 2 秒；用户暂停时间不计入投送时钟，避免长文件一次性突发发送。
- 检测到连续静音时传递时间进展，允许静音播放，不虚构字幕；字幕恢复后按媒体时间继续处理。
- 连接新内容时销毁旧 HLS 实例；迟到回调检查实例身份。网络/媒体恢复共用最多 2 次预算，显示当前次数，并禁用底层重复自动重试。
- 原文验收录音工具接受 HTTP(S) 媒体输入；新增固定归一化 CER/WER 评分工具和回归。
- 评分要求存在结束事件且没有错误；按时间区分没有句子 ID 的终稿，实际翻译事件不能被缺省或错误汇总隐藏。来源 SHA 绑定仍待 L01，评分结果不能独立证明字幕显示、时序、直播延迟或完整发布门槛。
- 将 Windows 换行差异恢复为仓库原有 LF，保留所有实际功能修改；原有 Windows `.venv` 未改动。
- 按用户最新约定同步 `AGENTS.md` 和 `.codex/config.toml`：主代理 `gpt-6.1-sol` / `max`，子代理 `gpt-6-luna` / `max`。设置键依据 [OpenAI 官方配置参考](https://learn.chatgpt.com/docs/config-file/config-reference)。这里记录项目默认值，当前运行会话的模型由宿主控制。

## 验证与运行环境

- Linux 测试环境：`.scratch/linux-venv`，Python 3.12.14；Node.js 24.22.1 使用宿主自带运行时。`.scratch/` 在忽略清单，不随提交分发。
- 最终完整 Python 回归：136 通过、1 项 Windows DPAPI 专用测试因 Linux 跳过；耗时 41.77 秒。另有 1 条 Starlette/httpx 弃用告警，不影响本轮通过。
- 前端行为回归：31 通过。Pylint 按 CI 的 errors-only 范围检查，并包含新增评分脚本；检查通过。
- 沙箱内 HTTP TestClient 卡在本地通信，已终止该次测试；同一用例及完整回归在允许本地通信的环境运行通过。
- 启动前已检查 8002 无占用；当前只运行一个绑定 `127.0.0.1:8002` 的 WebUI。页面、`/playback_guard.js`、`/caption_presenter.js`、`/hls.min.js`、`/player.css`、`/api/cloud` 均确认 HTTP 200。
- 本轮没有调用真实云端识别，也没有重跑历史付费验收。`logs/` 和 `uploads/` 的历史本机证据未随提交分发，未将其视为本轮通过证明。

Linux 启动命令（重启前仍须检查 8002 端口所属进程）：

```bash
.scratch/linux-venv/bin/python -m lisca --host 127.0.0.1 --port 8002
```

离线回归命令：

```bash
.scratch/linux-venv/bin/python -m pytest test/ -q -o cache_dir=.scratch/pytest-cache
node tests/frontend/caption_browser.test.cjs
```

`node` 不在当前 shell PATH 时，使用宿主提供的 Node.js 绝对路径。现有 Windows 环境仍使用 `.venv/Scripts/`，不可把 Linux 测试路径当作 Windows 安装说明。

## 最终检查记录

- Python 完整回归、31 项前端回归和 CI 范围 Pylint errors-only 均通过；评分修复后重新执行了完整 Python 回归及评分脚本静态检查。
- 项目 TOML 可解析，主/子代理模型与思考强度断言通过。
- `git diff --check` 通过。提交包含当前功能改动、必要回归、模型约定、本记录和本地 Issue 草稿，不包含 Key、日志、媒体或隔离测试环境。
