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
