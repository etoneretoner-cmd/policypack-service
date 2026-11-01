"""PolicyPack MCP server (stdio, JSON-RPC 2.0, no dependencies).
Any MCP-capable agent (Claude, OpenAI Agents SDK, LangGraph, ...) connects
and gets four tools to call inside its buying process:
  policypack_lint_policy    check a policy for contradictions before use
  policypack_register_policy  register a policy, get a policy_id
  policypack_authorize      ask BEFORE paying: PAY / REFUSE / PAY_ESCALATE
  policypack_set_freeze     latch/unlatch the dispute freeze
Run: python3 -m policypack.mcp_server
"""
import json, sys
from .core import STORE, lint, authorize

TOOLS = [
    {"name": "policypack_lint_policy",
     "description": "Check an agent payment policy for internal contradictions "
                    "(conflicting rules, dead rules) BEFORE it is used. A policy "
                    "that reads fine can be impossible to satisfy.",
     "inputSchema": {"type": "object", "properties": {
         "policy": {"type": "object"}}, "required": ["policy"]}},
    {"name": "policypack_register_policy",
     "description": "Register a payment policy and get its policy_id. The "
                    "response includes the lint diagnostics.",
     "inputSchema": {"type": "object", "properties": {
         "policy": {"type": "object"}}, "required": ["policy"]}},
    {"name": "policypack_authorize",
     "description": "Ask PolicyPack BEFORE making a payment. Returns PAY, "
                    "REFUSE (with the reason), or PAY_ESCALATE (pay only with "
                    "dual/human approval). Call this before every transaction.",
     "inputSchema": {"type": "object", "properties": {
         "policy_id": {"type": "string"},
         "transaction": {"type": "object", "properties": {
             "amount": {"type": "number"}, "payee": {},
             "category": {"type": "string"}, "dispute": {"type": "boolean"}},
             "required": ["amount", "payee"]}},
         "required": ["policy_id", "transaction"]}},
    {"name": "policypack_set_freeze",
     "description": "Latch or release the dispute freeze: while frozen, every "
                    "payment is refused.",
     "inputSchema": {"type": "object", "properties": {
         "policy_id": {"type": "string"}, "frozen": {"type": "boolean"}},
         "required": ["policy_id", "frozen"]}},
]

def call_tool(name, args):
    if name == "policypack_lint_policy":
        return lint(args["policy"])
    if name == "policypack_register_policy":
        pid = STORE.new_policy(args["policy"])
        return {"policy_id": pid, "lint": lint(args["policy"])}
    if name == "policypack_authorize":
        return authorize(STORE, args["policy_id"], args["transaction"])
    if name == "policypack_set_freeze":
        STORE.frozen[args["policy_id"]] = bool(args["frozen"])
        return {"policy_id": args["policy_id"], "frozen": STORE.frozen[args["policy_id"]]}
    raise ValueError(f"unknown tool {name}")

def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        req = json.loads(line)
        method, rid = req.get("method"), req.get("id")
        if method == "initialize":
            out = {"protocolVersion": "2024-11-05",
                   "capabilities": {"tools": {}},
                   "serverInfo": {"name": "policypack", "version": "0.1.0"}}
        elif method == "tools/list":
            out = {"tools": TOOLS}
        elif method == "tools/call":
            p = req["params"]
            try:
                res = call_tool(p["name"], p.get("arguments", {}))
                out = {"content": [{"type": "text", "text": json.dumps(res)}]}
            except Exception as e:
                out = {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True}
        elif rid is None:
            continue
        else:
            out = {"error": "unknown method"}
        if rid is not None:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": rid, "result": out}) + "\n")
            sys.stdout.flush()

if __name__ == "__main__":
    main()
