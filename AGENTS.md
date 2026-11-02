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

## 执行与验收

- 开始接续时核对父 Issue 和全部未完成产品指标；独立就绪的工作并行推进，只按真实依赖安排阻塞。
- 每次实验先写明原验收项、待区分的假设和结果将改变的决定。同一问题最多尝试 3 次，跨代理、上下文累计；没有新增决策证据时提前停止该路径。
- 歧义保留为 unknown，失败与原始证据另存；保持原门槛和用户已排除的范围，不把局部计量困难升级为全项目前置。
- 分别报告代码、CI、运行时和产品验收，注明候选版本、输入身份及证据日期；历史结果只支持其原有范围。
- 优先交付能独立审查的小切片，及时收束主题与上下文；工具修复和自动化绿灯不能代替尚未通过的观看体验。
- 按用户授权的范围决定是否带已知问题发布，先同步支持清单和限制；明确正式版或 prerelease、未通过项与剩余风险，保持发布门槛的真实结论。

## 模型偏好

- 主任务和协调优先 **GPT-6.1-Sol / Max**（`gpt-6.1-sol` / `max`）；写作和电脑操作优先 **GPT-6.1-Terra / Max**（`gpt-6.1-terra` / `max`）。
- 允许委派时，其他子代理优先 **GPT-6-Luna / Max**（`gpt-6-luna` / `max`）。通过 `spawn_agent` 显式指定模型与强度，并设置 `fork_turns="none"`，提供目标、目录、约束和完成标准。
- 默认配置位于 `.codex/config.toml`。这些是偏好；如不可用，如实说明实际模型、强度和影响，继续已授权的可行工作。
