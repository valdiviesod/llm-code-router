# Adding a model

Models belong to adapters, not to the core.

1. Return the model from your adapter's `get_models()`, ideally by asking the CLI
   (`agy models`) rather than hardcoding a list.
2. Make sure `_argv()` passes it through (`--model <id>`).
3. Let users pin it with `agents.<id>.default_model` in the config.

`RoutingEngine._model_for()` returns the configured model, or `None` to defer to
the CLI's own default. It deliberately does not guess a model id that may not
exist for the user's account.

To make the router *choose* between models, promote the choice into
`Candidate.model` and score it the same way agents are scored — that is a routing
strategy change, see [adding-router-strategy.md](adding-router-strategy.md).
