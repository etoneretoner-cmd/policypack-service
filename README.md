# PolicyPack Service — the check other agents call before they pay

Two questions, asked by software, inside the buying process:

1. **Is this policy even coherent?** `lint` — contradictory rules, dead rules
   and overflow envelopes, caught BEFORE the policy touches money. No other
   agent platform checks this (verified across ~35 platforms, Nov 2026).
2. **May I pay this?** `authorize` — one ordered gate over live state:
   freeze → cap → allowlist → category caps → velocity → budget.
   Answers `PAY`, `REFUSE` (with the reason), or `PAY_ESCALATE`
   (pay only with dual/human approval).

## Use it from an agent framework (MCP)

Any MCP-capable agent (Claude Agent SDK, OpenAI Agents SDK, LangGraph, ...)
adds this to its MCP config — no code changes to the agent:

```json
{ "mcpServers": { "policypack": {
    "command": "python3",
    "args": ["-m", "policypack.mcp_server"],
    "cwd": "/path/to/policypack-service" } } }
```

The agent then has 4 tools: `policypack_lint_policy`,
`policypack_register_policy`, `policypack_authorize`,
`policypack_set_freeze`. Rule for the agent's system prompt: *"Before every
payment, call policypack_authorize. On REFUSE, do not pay. On PAY_ESCALATE,
ask the human."*

## Use it from anything else (REST)

```
POST /v1/lint       {"policy": {...}}                          -> diagnostics
POST /v1/policies   {"policy": {...}}                          -> {"policy_id"}
POST /v1/authorize  {"policy_id": "...", "transaction":
                     {"amount": 50, "payee": 5, "category": "api"}}
                    -> {"decision": "PAY"|"REFUSE"|"PAY_ESCALATE", ...}
POST /v1/freeze     {"policy_id": "...", "frozen": true}
GET  /v1/status?policy_id=...
```

Run: `python3 -m policypack.api 8791` (stdlib only, no dependencies).

## Policy fields

`cap`, `budget`, `budget_window_hours` (rolling window), `allowlist`,
`freeze_window_steps`, `escalation_threshold`, `velocity_max_tx` +
`velocity_window_min`, `category_caps`, `pool_id` (several agents, ONE
shared budget), `word_bits` (overflow-envelope check, e.g. 8).

## Demo

`python3 -m policypack.api 8791 &` then `python3 examples/agent_demo.py` —
a shopping agent lints its (contradictory) rules, shops, then tries a
1 800-unit runaway spree and gets stopped by the budget at the policy layer.

## Regression tests

A frozen regression suite lives in `tests/run_regression.py` (stdlib only):

```
python3 tests/run_regression.py
```

It freezes the golden verdicts from the RuleLab packs (lint conflicts and
dead rules for the 3 naive + 3 integrated packs), the published demo's
authorize sequence (spree stopped at 170 of a 192 budget, remaining 22),
plus shared-pool budget, freeze latch and velocity cases — against the
engine directly, the REST API, and an MCP stdio smoke test (4 tools).
Exit code is non-zero if any case fails. **Re-run it after any change to
the engine or the API/MCP layers; if a case fails, fix the engine — never
edit an expectation to match a regression.**

Formal-proof note: the lint diagnostics for the base rule family are the
same ones verified against Tau Language 0.8.0 in RuleLab (three packs,
scenario matrix 19/19). The service computes them structurally, in real
time, for any parameters.
