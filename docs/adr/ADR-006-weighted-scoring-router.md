# ADR-006 — Weighted scoring instead of machine learning

**Status** Accepted — 2026-08-22

## Context

The router must pick an agent from quota pressure, capability fit, historical
success and cost. A learned ranker is the obvious "smart" answer, and the wrong
one for v0.1: there is no training data on day one, the cold-start behaviour
would be arbitrary, and — worst — the user could not be told *why* their quota
was spent on a particular agent.

## Decision

A weighted linear score over four named factors (quota, history, fit, cost) with
per-mode weights that sum to 1.0. Historical success uses a neutral prior until
there are at least five samples. Every factor contributes a human-readable reason
string that surfaces in the Routing view.

Hard constraints sit outside the score: capability filtering runs first, the
quota forecast can veto a candidate outright, and a user override always wins.

## Decision on learning

Statistics are recorded from run one (`agent_stats`), so the `history` factor
improves with use. That is the whole learning loop for now — no model, no
training step.

## Consequences

- Cold start is sane and explainable.
- Every decision can justify itself, which is the difference between a tool
  people trust with their quota and one they fight.
- Weights are hand-tuned, so they can be wrong; they are in one dict, with a test
  enforcing that each mode sums to 1.0.
- An ML ranker can replace the scorer later behind the same `decide()` signature.
  The vetoes and the decision shape are safety properties and stay put.
