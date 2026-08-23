# Security

## Threat model

coderouter launches coding agents that can edit files and run shell commands in your
project. The orchestrator is therefore treated as a privileged component, and the
agents' output is treated as untrusted text.

## Controls

| Control | Where | What it does |
|---|---|---|
| Command policy | `security/policy.py` | Classifies commands SAFE / ASK / BLOCK. BLOCK is absolute. |
| Secret redaction | `security/policy.py`, `logging.py` | Strips API keys and tokens from output before it reaches logs, the database or another agent. |
| Audit log | `storage/db.py` (`audit_log`) | Every execution and escalation, with the routing reason. |
| Worktree isolation | `git/worktree.py` | Parallel agents never share a working tree. |
| Validation gating | `validation/engine.py` | Only runs tools the project actually declares. |

## Destructive operations

Patterns such as `rm -rf /`, `DROP DATABASE` and `mkfs` are blocked outright and
cannot be enabled through configuration alone. Operations that are risky but
legitimate (`git push`, `sudo`, `docker`) require explicit approval.

## Reporting

Report vulnerabilities privately to the maintainers rather than opening a public
issue.
