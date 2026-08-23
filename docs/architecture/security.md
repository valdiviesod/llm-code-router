# Security (`coderouter/security/`)

See [SECURITY.md](../../SECURITY.md) for the threat model.

**Why** Agents run shell commands. An orchestrator that runs them unattended
without a policy is a liability.

**Responsibility** Classify commands, redact secrets, and keep an audit trail.

**Categories**

| Category | Behaviour |
|---|---|
| `SAFE` | Runs unattended. |
| `ASK` | Requires human approval (`git push`, `sudo`, `docker`, …). |
| `BLOCK` | Never runs (`rm -rf /`, `DROP DATABASE`, `mkfs`, fork bombs). |

Patterns are configurable under `security:` in the config, but the block list
ships with destructive defaults.

**Redaction** `redact_secrets()` runs over agent output before it is persisted,
logged or handed to another agent. The JSON log formatter independently redacts
any field whose name contains token/key/secret/password/authorization.

**Must not** Log a secret. Persist a raw credential. Let a BLOCK pattern be
bypassed by configuration alone.
