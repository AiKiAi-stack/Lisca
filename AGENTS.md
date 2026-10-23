## Agent skills

### Local WebUI

Use the existing WebUI at `http://127.0.0.1:8002/` for local checks. After updating
WebUI code, restart that service and verify its HTTP response before reporting
the change as deployed. Keep one WebUI instance on port 8002; inspect the owning
process before stopping it. Do not start a replacement WebUI on another port.

### Issue tracker

Issues and specs are tracked in this repository's GitHub Issues using `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

Use the default labels: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repo. Read root `GLOSSARY.md` and relevant `docs/adr/` files. See `docs/agents/domain.md`.

## 模型分工

以下约定适用于本仓库及其子目录。

- 主任务和主协调代理（Coordinator Agent）使用 **GPT-6.1-Sol**，模型 ID 为 `gpt-6.1-sol`，thinking effort 为 **Max**（`max`）。
- 被分发任务的所有子代理（包括 Execution Agent、审查代理和继续分发的子代理）使用 **GPT-6-Luna**，模型 ID 为 `gpt-6-luna`，thinking effort 为 **Max**（`max`）。
- 项目默认模型配置位于 `.codex/config.toml`，调整模型分工时同步更新该配置和本文件。

### 创建子代理

- 创建子代理时显式指定 `model="gpt-6-luna"` 和 `reasoning_effort="max"`。
- 使用当前宿主的 `spawn_agent` 时，设置 `fork_turns="none"`，以便指定独立的模型和思考强度；在任务消息中提供目标、工作目录、相关约束和完成标准。
- 所需模型或 Max 思考强度不可用时，如实说明限制，由用户决定替代配置。
