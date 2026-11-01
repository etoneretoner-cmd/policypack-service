"""Demo: a shopping agent that MUST ask PolicyPack before every payment.
It tries to go on a spree. Watch where the policy stops it.
Run against the REST API:  python3 examples/agent_demo.py [base_url]"""
import json, sys, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8791"

def post(path, payload):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())

# The agent's owner wrote these rules the naive way (like everyone does):
policy = {"cap": 60, "budget": 192, "allowlist": [3, 5, 11],
          "escalation_threshold": 45, "freeze_window_steps": 2, "word_bits": 8}

print("1) Lint BEFORE the policy touches money:")
print("  ", json.dumps(post("/v1/lint", {"policy": policy})["naive_verdict"]),
      "- conflicts:", len(post("/v1/lint", {"policy": policy})["conflicts"]),
      "| dead rules:", len(post("/v1/lint", {"policy": policy})["dead_rules"]))
print("   -> naive reading is contradictory; the service enforces the "
      "integrated gate instead (single ordered decision).")

pid = post("/v1/policies", {"policy": policy})["policy_id"]
print(f"2) Policy registered: {pid}")

def buy(amount, payee, label):
    d = post("/v1/authorize", {"policy_id": pid,
             "transaction": {"amount": amount, "payee": payee}})
    print(f"   {label}: buy {amount} from vendor {payee} -> "
          f"{d['decision']} ({d['reason']})")
    return d["decision"]

print("3) The agent shops:")
buy(50, 5, "normal purchase ")
buy(30, 4, "unlisted vendor ")
buy(70, 5, "over the cap    ")
print("4) The agent loops (runaway spree, 60 x 30 = 1800 wanted):")
paid = 0
for i in range(60):
    d = post("/v1/authorize", {"policy_id": pid,
             "transaction": {"amount": 30, "payee": 3}})
    if d["decision"].startswith("PAY"):
        paid += 30
    else:
        print(f"   stopped at attempt {i + 1}: {d['decision']} ({d['reason']})")
        break
import urllib.request as u
st = json.loads(u.urlopen(BASE + f"/v1/status?policy_id={pid}").read())
print(f"   total actually spent (incl. first purchase): {st['spent_total']} "
      f"of a 192 budget - the spree died at the policy, not at the wallet.")
print("5) A dispute lands -> freeze latched:")
post("/v1/freeze", {"policy_id": pid, "frozen": True})
buy(10, 5, "during freeze   ")
