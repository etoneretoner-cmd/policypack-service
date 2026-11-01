"""PolicyPack core: policy lint (coherence) + authorize (integrated gate).

A policy is a set of spending rules for an agent wallet:
  cap                   max amount per transaction
  budget                max cumulative spend (total, or per rolling window)
  budget_window_hours   if set, the budget applies per rolling window
  allowlist             payee/vendor ids allowed (None = everyone)
  freeze_window_steps   while a dispute freeze is active, nothing is paid
  escalation_threshold  payments above this are flagged PAY_ESCALATE
  velocity_max_tx       max number of paid transactions per velocity window
  velocity_window_min   velocity window length in minutes
  category_caps         {category: cap} extra per-category caps
  pool_id               policies sharing a pool_id share budget + velocity
  word_bits             if set (e.g. 8), check the overflow envelope

lint() checks the NAIVE reading (each rule enforced independently, the way
people actually write them): structural conflicts, dead rules, warnings.
authorize() runs the INTEGRATED gate (one ordered decision procedure) over
live state. Same parameters, two readings - exactly the RuleLab distinction.
"""
import time

class Store:
    """In-memory state: policies, ledgers, freeze flags. Ledger entries are
    (timestamp, amount) so rolling windows and velocity can be computed.
    A pool's ledger is shared by every policy with the same pool_id."""
    def __init__(self):
        self.policies = {}          # policy_id -> policy dict
        self.ledgers = {}           # ledger_key -> [(ts, amount)]
        self.frozen = {}            # policy_id -> bool
        self._seq = 0

    def new_policy(self, policy):
        self._seq += 1
        pid = f"pol_{self._seq:04d}"
        self.policies[pid] = policy
        self.frozen[pid] = False
        return pid

    def ledger_key(self, policy):
        return "pool:" + policy["pool_id"] if policy.get("pool_id") else None

    def ledger(self, pid):
        pol = self.policies[pid]
        key = self.ledger_key(pol) or ("policy:" + pid)
        return self.ledgers.setdefault(key, [])

    def spent_in_window(self, pid, window_seconds, now=None):
        now = now or time.time()
        return sum(a for ts, a in self.ledger(pid)
                   if window_seconds is None or ts > now - window_seconds)

    def count_in_window(self, pid, window_seconds, now=None):
        now = now or time.time()
        return sum(1 for ts, a in self.ledger(pid)
                   if window_seconds is None or ts > now - window_seconds)

STORE = Store()

def lint(policy):
    """Structural coherence check of the naive rule set. Exact for this
    rule family (same diagnostics as the Tau-verified linter)."""
    conflicts, dead, warnings = [], [], []
    cap = policy["cap"]; budget = policy.get("budget")
    allow = policy.get("allowlist"); esc = policy.get("escalation_threshold")
    freeze = policy.get("freeze_window_steps") or 0
    if allow is not None:
        conflicts.append({
            "rules": ["cap", "allowlist"],
            "why": "The cap rule orders paying any amount <= cap; the allowlist "
                   "orders paying 0 to unlisted payees. For an unlisted payee "
                   "the two rules contradict each other."})
    if budget is not None:
        conflicts.append({
            "rules": ["cap", "budget"],
            "why": "Repeated maximum payments forced by the cap rule always "
                   "breach a hard cumulative budget eventually; the two rules "
                   "cannot both hold forever."})
    if freeze > 0:
        conflicts.append({
            "rules": ["cap", "freeze"],
            "why": "During a dispute freeze the freeze rule orders 0 while "
                   "the cap rule still orders paying small amounts."})
    if esc is not None and esc >= cap:
        dead.append({
            "rule": "escalation",
            "why": f"Escalation threshold {esc} >= cap {cap}: payments never "
                   f"exceed the cap, so escalation can never trigger."})
    wb = policy.get("word_bits")
    if wb and budget is not None and cap + budget > (2 ** wb) - 1:
        warnings.append({
            "kind": "overflow_envelope",
            "why": f"cap + budget = {cap + budget} > {(2 ** wb) - 1} "
                   f"(word_bits={wb}): the budget check can wrap around and "
                   f"let overspend through."})
    for cat, ccap in (policy.get("category_caps") or {}).items():
        if ccap > cap:
            warnings.append({
                "kind": "category_cap_above_cap",
                "why": f"Category cap for '{cat}' is {ccap} > global cap "
                       f"{cap}; the part above {cap} is unreachable."})
    if budget is not None and budget < cap:
        warnings.append({
            "kind": "budget_below_cap",
            "why": f"Budget {budget} < cap {cap}: a maximum-size payment "
                   f"only fits at the very start of a window."})
    return {
        "naive_verdict": "INCOHERENT" if conflicts else "COHERENT",
        "conflicts": conflicts,
        "dead_rules": dead,
        "warnings": warnings,
        "integrated_verdict": "COHERENT (single ordered gate)",
    }

def authorize(store, pid, tx, now=None):
    """The integrated gate. Ordered checks; state changes only on PAY.
    tx: {amount, payee, category?, dispute?}  dispute=True simulates the
    freeze signal; a latched freeze via set_freeze() also blocks."""
    now = now or time.time()
    pol = store.policies[pid]
    amount = tx["amount"]
    def refuse(reason, **extra):
        return {"decision": "REFUSE", "reason": reason, **extra}
    if store.frozen.get(pid) or tx.get("dispute"):
        return refuse("dispute freeze is active")
    if amount > pol["cap"]:
        return refuse(f"amount {amount} over per-transaction cap {pol['cap']}")
    allow = pol.get("allowlist")
    if allow is not None and tx["payee"] not in allow:
        return refuse(f"payee {tx['payee']} not on the approved list")
    ccaps = pol.get("category_caps") or {}
    cat = tx.get("category")
    if cat in ccaps and amount > ccaps[cat]:
        return refuse(f"amount {amount} over category cap {ccaps[cat]} for '{cat}'")
    vmax = pol.get("velocity_max_tx"); vwin = pol.get("velocity_window_min")
    if vmax is not None and vwin:
        n = store.count_in_window(pid, vwin * 60, now)
        if n + 1 > vmax:
            return refuse(f"velocity limit: {n} payments in the last {vwin} min "
                          f"(max {vmax})")
    budget = pol.get("budget")
    if budget is not None:
        wh = pol.get("budget_window_hours")
        spent = store.spent_in_window(pid, wh * 3600 if wh else None, now)
        if spent + amount > budget:
            return refuse(f"would exceed the cumulative budget "
                          f"(remaining: {budget - spent})",
                          remaining=budget - spent)
        remaining = budget - spent - amount
    else:
        remaining = None
    store.ledger(pid).append((now, amount))
    esc = pol.get("escalation_threshold")
    if esc is not None and amount > esc:
        return {"decision": "PAY_ESCALATE",
                "reason": f"amount {amount} above escalation threshold {esc}: "
                          f"pay only with dual approval",
                "remaining": remaining}
    return {"decision": "PAY", "reason": "within policy", "remaining": remaining}
