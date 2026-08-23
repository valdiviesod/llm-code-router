# Testing

```bash
.venv/bin/pytest              # unit + contract tests, no network, no CLIs
.venv/bin/pytest -m provider  # real CLIs; costs real quota
```

`addopts = -m 'not provider'` keeps live tests out of the default run, so the
suite never depends on a real account.

## Layers

| File | Covers |
|---|---|
| `test_config.py` | loading, aliases, rejection of unknown keys |
| `test_classifier.py` | task type, complexity, risk escalation |
| `test_router.py` | capability filtering, overrides, quota veto, weights |
| `test_usage.py` | windows, provenance, forecasting, conservation |
| `test_orchestrator.py` | end-to-end flow, escalation, handoff size, graphs |
| `test_adapter_contract.py` | the contract every adapter must satisfy |
| `test_context.py` | selection, fingerprint stability, summarisation |
| `test_storage.py` | SQL aggregation, truncation signalling, audit |
| `test_validation.py` | stack detection, git diff |
| `test_worktree.py` | isolation and cleanup |
| `test_security.py` | SAFE/ASK/BLOCK, redaction |
| `test_tui.py` | app boots, panels render, bindings work |
| `test_cli.py` | argument dispatch |
| `test_live_providers.py` | real CLIs, `-m provider` only |

## Fakes

`tests/conftest.py` provides `FakeAdapter` (no subprocess) and `FakeRegistry`.
Anything needing a real CLI belongs in `test_live_providers.py`.

## Adding an adapter

Add the class to `ADAPTERS` and a real captured payload to `NATIVE_PAYLOADS` in
`test_adapter_contract.py`. Everything else is inherited.
