#!/usr/bin/env python3
"""PolicyPack frozen regression suite (stdlib only).

Run:  python3 tests/run_regression.py     (exit code 0 = all green)

WHAT IS FROZEN HERE, AND FROM WHICH SOURCE OF TRUTH
---------------------------------------------------
1. Lint verdicts for the six RuleLab golden packs (3 naive, 3 integrated),
   translated to service policy parameters. The expected conflicts and dead
   rules are those of the Tau-verified pack reports
   (rulelab/packs/*.pack.json, scenario matrix 19/19):
     personnel naive : conflicts cap/allowlist + cap/budget + cap/freeze,
                       escalation DEAD (threshold 80 >= cap 60)
     achats naive    : conflicts cap/allowlist + cap/budget,
                       double approval ALIVE (80 < cap 100), no freeze rule
     devops naive    : conflicts cap/allowlist + cap/budget + cap/freeze,
                       escalation ALIVE (35 < cap 40)
   The service lint() evaluates the NAIVE reading of flat parameters, so
   integrated packs also report naive_verdict=INCOHERENT plus
   integrated_verdict="COHERENT (single ordered gate)"; what distinguishes
   them in this suite is dead_rules == [] and the authorize gate below.
2. The authorize sequence of examples/agent_demo.py (the published demo is
   the source of truth): PAY_ESCALATE 50@5, REFUSE 30@4 (unlisted),
   REFUSE 70@5 (cap), spree 30@3 paid 4x then REFUSE on budget,
   total spent 170 of 192, remaining 22, freeze REFUSE.
3. Shared pool budget, freeze latch, velocity limit - engine semantics.

RULE: re-run this suite after ANY change to the engine (policypack/core.py)
or the API/MCP layers. If a case fails, the change broke a frozen verdict:
fix the engine, never edit the expectation to match a regression.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from policypack.core import Store, lint, authorize  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not cond else ""))


# --- Golden pack parameters (translated from rulelab/packs/*.pack.json) -----
PACKS = {
    "personnel-naif":    {"cap": 60, "budget": 192, "allowlist": [3, 5, 11],
                          "escalation_threshold": 80, "freeze_window_steps": 2,
                          "word_bits": 8},
    "personnel-integre": {"cap": 60, "budget": 192, "allowlist": [3, 5, 11],
                          "escalation_threshold": 45, "freeze_window_steps": 2,
                          "word_bits": 8},
    "achats-naif":       {"cap": 100, "budget": 150, "allowlist": [2, 4, 6, 8],
                          "escalation_threshold": 80, "word_bits": 8},
    "achats-integre":    {"cap": 100, "budget": 150, "allowlist": [2, 4, 6, 8],
                          "escalation_threshold": 80, "word_bits": 8},
    "devops-naif":       {"cap": 40, "budget": 160, "allowlist": [1, 2],
                          "escalation_threshold": 35, "freeze_window_steps": 3,
                          "word_bits": 8},
    "devops-integre":    {"cap": 40, "budget": 160, "allowlist": [1, 2],
                          "escalation_threshold": 35, "freeze_window_steps": 3,
                          "word_bits": 8},
}
CF = ("cap", "freeze")
EXPECTED_LINT = {  # conflicts as sorted tuples of rule names, dead rule names
    "personnel-naif":    ([("cap", "allowlist"), ("cap", "budget"), CF], ["escalation"]),
    "personnel-integre": ([("cap", "allowlist"), ("cap", "budget"), CF], []),
    "achats-naif":       ([("cap", "allowlist"), ("cap", "budget")], []),
    "achats-integre":    ([("cap", "allowlist"), ("cap", "budget")], []),
    "devops-naif":       ([("cap", "allowlist"), ("cap", "budget"), CF], []),
    "devops-integre":    ([("cap", "allowlist"), ("cap", "budget"), CF], []),
}
DEMO_POLICY = dict(PACKS["personnel-integre"])  # exactly the demo's policy
T0 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=timezone.utc).timestamp()


def engine_lint_cases():
    print("== ENGINE: lint of the 6 golden packs ==")
    for name, pol in PACKS.items():
        r = lint(pol)
        exp_conf, exp_dead = EXPECTED_LINT[name]
        got_conf = sorted(tuple(c["rules"]) for c in r["conflicts"])
        got_dead = [d["rule"] for d in r["dead_rules"]]
        check(f"lint {name}: naive_verdict INCOHERENT",
              r["naive_verdict"] == "INCOHERENT", r["naive_verdict"])
        check(f"lint {name}: conflicts match Tau pack report",
              got_conf == sorted(exp_conf), str(got_conf))
        check(f"lint {name}: dead rules match Tau pack report",
              got_dead == exp_dead, str(got_dead))
        check(f"lint {name}: integrated gate verdict COHERENT",
              r["integrated_verdict"].startswith("COHERENT"),
              r["integrated_verdict"])
        check(f"lint {name}: no warnings", r["warnings"] == [], str(r["warnings"]))


def demo_sequence(store, pid, now0=T0):
    """The published demo sequence; returns the list of decisions."""
    out = []
    out.append(authorize(store, pid, {"amount": 50, "payee": 5}, now0))
    out.append(authorize(store, pid, {"amount": 30, "payee": 4}, now0 + 60))
    out.append(authorize(store, pid, {"amount": 70, "payee": 5}, now0 + 120))
    for i in range(5):  # spree: 4 pay, 5th refused on budget
        out.append(authorize(store, pid, {"amount": 30, "payee": 3}, now0 + 180 + 60 * i))
    return out


def check_demo_decisions(tag, out):
    exp = ["PAY_ESCALATE", "REFUSE", "REFUSE", "PAY", "PAY", "PAY", "PAY", "REFUSE"]
    got = [d["decision"] for d in out]
    check(f"{tag}: demo decisions sequence", got == exp, str(got))
    check(f"{tag}: 50@5 escalates (threshold 45)",
          "escalation threshold 45" in out[0]["reason"], out[0]["reason"])
    check(f"{tag}: 30@4 refused, payee not allowlisted",
          "not on the approved list" in out[1]["reason"], out[1]["reason"])
    check(f"{tag}: 70@5 refused, over cap 60",
          "over per-transaction cap 60" in out[2]["reason"], out[2]["reason"])
    check(f"{tag}: spree stops on budget with remaining 22",
          "would exceed the cumulative budget" in out[7]["reason"]
          and out[7].get("remaining") == 22, out[7]["reason"])


def engine_authorize_cases():
    print("== ENGINE: authorize - published demo sequence ==")
    store = Store()
    pid = store.new_policy(dict(DEMO_POLICY))
    out = demo_sequence(store, pid)
    check_demo_decisions("engine", out)
    check("engine: demo total spent = 170", store.spent_in_window(pid, None, T0 + 9999) == 170,
          str(store.spent_in_window(pid, None, T0 + 9999)))
    check("engine: demo payments count = 5", store.count_in_window(pid, None, T0 + 9999) == 5)

    print("== ENGINE: authorize - shared pool budget ==")
    store = Store()
    pa = dict(DEMO_POLICY, pool_id="regression-pool")
    a = store.new_policy(pa)
    b = store.new_policy(dict(pa))
    t = T0
    for i in range(4):
        d = authorize(store, a, {"amount": 40, "payee": 3}, t + 60 * i)
        check(f"pool: agent A payment {i + 1} of 40 PAY", d["decision"] == "PAY", str(d))
    d = authorize(store, b, {"amount": 40, "payee": 3}, t + 300)
    check("pool: agent B (spent 0 itself) refused - budget is shared",
          d["decision"] == "REFUSE" and d.get("remaining") == 32, str(d))
    d = authorize(store, b, {"amount": 32, "payee": 3}, t + 360)
    check("pool: agent B pays the last 32", d["decision"] == "PAY" and d.get("remaining") == 0, str(d))
    d = authorize(store, a, {"amount": 1, "payee": 3}, t + 420)
    check("pool: pool exhausted at 192, next payment refused",
          d["decision"] == "REFUSE" and d.get("remaining") == 0, str(d))

    print("== ENGINE: authorize - freeze latch ==")
    store = Store()
    pid = store.new_policy(dict(DEMO_POLICY))
    store.frozen[pid] = True
    d = authorize(store, pid, {"amount": 10, "payee": 5}, T0)
    check("freeze: latched freeze refuses", d["decision"] == "REFUSE"
          and "freeze is active" in d["reason"], str(d))
    store.frozen[pid] = False
    d = authorize(store, pid, {"amount": 10, "payee": 5}, T0 + 60)
    check("freeze: unlatched freeze pays again", d["decision"] == "PAY", str(d))

    print("== ENGINE: authorize - velocity limit ==")
    store = Store()
    pol = dict(DEMO_POLICY, velocity_max_tx=3, velocity_window_min=1)
    pid = store.new_policy(pol)
    for i, dt in enumerate((0, 10, 20)):
        d = authorize(store, pid, {"amount": 5, "payee": 3}, T0 + dt)
        check(f"velocity: payment {i + 1} within the minute PAY",
              d["decision"] == "PAY", str(d))
    d = authorize(store, pid, {"amount": 5, "payee": 3}, T0 + 30)
    check("velocity: 4th payment within the minute refused",
          d["decision"] == "REFUSE" and "velocity limit" in d["reason"], str(d))
    d = authorize(store, pid, {"amount": 5, "payee": 3}, T0 + 61)
    check("velocity: payment after the window slides PAY",
          d["decision"] == "PAY", str(d))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(method, base, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read())


def rest_cases():
    print("== REST API (local server subprocess) ==")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen([sys.executable, "-m", "policypack.api", str(port)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        up = False
        for _ in range(50):
            try:
                code, _ = http("GET", base, "/")
                up = code == 200
                break
            except Exception:
                time.sleep(0.2)
        check("REST: server comes up", up)
        if not up:
            return
        code, root = http("GET", base, "/")
        check("REST: GET / is 200 PolicyPack",
              code == 200 and root.get("service") == "PolicyPack", str(root))
        code, r = http("POST", base, "/v1/lint", {"policy": PACKS["personnel-naif"]})
        check("REST: /v1/lint personnel-naif INCOHERENT with dead escalation",
              code == 200 and r["naive_verdict"] == "INCOHERENT"
              and [d["rule"] for d in r["dead_rules"]] == ["escalation"], str(r))
        code, r = http("POST", base, "/v1/policies", {"policy": DEMO_POLICY})
        pid = r["policy_id"]
        check("REST: policy registered", code == 200 and pid.startswith("pol_"), str(r))
        seq = [(50, 5), (30, 4), (70, 5)] + [(30, 3)] * 5
        decisions = []
        last = None
        for amt, payee in seq:
            code, last = http("POST", base, "/v1/authorize",
                              {"policy_id": pid, "transaction": {"amount": amt, "payee": payee}})
            decisions.append(last["decision"])
        check("REST: demo decisions sequence",
              decisions == ["PAY_ESCALATE", "REFUSE", "REFUSE", "PAY", "PAY", "PAY", "PAY", "REFUSE"],
              str(decisions))
        check("REST: demo budget refusal carries remaining 22",
              last.get("remaining") == 22, str(last))
        code, st = http("GET", base, f"/v1/status?policy_id={pid}")
        check("REST: /v1/status 200, spent_total 170, 5 payments, not frozen",
              code == 200 and st["spent_total"] == 170 and st["payments"] == 5
              and st["frozen"] is False, str(st))
        http("POST", base, "/v1/freeze", {"policy_id": pid, "frozen": True})
        code, d = http("POST", base, "/v1/authorize",
                       {"policy_id": pid, "transaction": {"amount": 10, "payee": 5}})
        check("REST: frozen policy refuses (demo step 5)",
              d["decision"] == "REFUSE" and "freeze is active" in d["reason"], str(d))
    finally:
        proc.terminate()
        proc.wait(timeout=10)


def mcp_cases():
    print("== MCP stdio smoke test ==")
    proc = subprocess.Popen([sys.executable, "-m", "policypack.mcp_server"],
                            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True)
    reqs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    out, _ = proc.communicate("\n".join(json.dumps(r) for r in reqs) + "\n", timeout=30)
    tools = []
    for line in out.splitlines():
        msg = json.loads(line)
        if msg.get("id") == 2:
            tools = msg["result"]["tools"]
    names = sorted(t["name"] for t in tools)
    check("MCP: tools/list returns exactly the 4 tools",
          names == ["policypack_authorize", "policypack_lint_policy",
                    "policypack_register_policy", "policypack_set_freeze"], str(names))


def main():
    engine_lint_cases()
    engine_authorize_cases()
    rest_cases()
    mcp_cases()
    passed = sum(1 for _, ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\nREGRESSION: {passed}/{total} cases green")
    if passed != total:
        print("FAILED CASES:")
        for name, ok, detail in RESULTS:
            if not ok:
                print(f"  - {name} {detail}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
