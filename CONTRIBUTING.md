# Contributing

## Definition of Done
- Security checks passed
- Hooks/scheduler methods valid
- SQL safe and parameterized
- Idempotency for external calls
- Structured logging
- Tests or smoke checks passed

## Agent skills
Frappe agent skills live in `.claude/skills/` and are versioned with the code.
Scope, update commands and limits: [docs/agent-skills.md](docs/agent-skills.md).

## PR Checklist
- [ ] No secrets in code
- [ ] Migration-safe changes
- [ ] Scheduler path valid
- [ ] Error handling for external APIs
