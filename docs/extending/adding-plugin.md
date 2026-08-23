# Adding a plugin

Out-of-tree code plugs in through Python entry points; nothing needs to be
vendored into this repository.

```toml
# your package's pyproject.toml
[project.entry-points."v4ld1.agents"]
my-agent = "my_package.adapter:MyAdapter"
```

`AgentRegistry` loads the `v4ld1.agents` group at startup, after importing the
built-in adapters. Plugin loading is best-effort: a broken plugin is skipped
rather than taking down the orchestrator, so check `v4ld1 agents` to confirm
yours was picked up.

Your adapter must satisfy the same contract as a built-in one — copy
`tests/test_adapter_contract.py` into your package and parametrise it over your
class.

Config for your agent lives under its id, exactly like a built-in:

```yaml
agents:
  my-agent:
    enabled: true
    command: my-cli
```
