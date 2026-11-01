"""PolicyPack REST API (stdlib only). Run: python3 -m policypack.api [port]
Endpoints:
  POST /v1/lint        {policy}                      -> coherence diagnostics
  POST /v1/policies    {policy}                      -> {policy_id}
  POST /v1/authorize   {policy_id, transaction}      -> decision
  POST /v1/freeze      {policy_id, frozen: bool}     -> dispute freeze latch
  GET  /v1/status      {policy_id}                   -> spent, count, frozen
"""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from .core import STORE, lint, authorize

def handle(method, path, body):
    if method == "POST" and path == "/v1/lint":
        return 200, lint(body["policy"])
    if method == "POST" and path == "/v1/policies":
        return 200, {"policy_id": STORE.new_policy(body["policy"])}
    if method == "POST" and path == "/v1/authorize":
        return 200, authorize(STORE, body["policy_id"], body["transaction"])
    if method == "POST" and path == "/v1/freeze":
        STORE.frozen[body["policy_id"]] = bool(body["frozen"])
        return 200, {"policy_id": body["policy_id"], "frozen": STORE.frozen[body["policy_id"]]}
    if method == "GET" and (path == "/" or path == ""):
        return 200, {"service": "PolicyPack", "version": "0.1.0",
                     "what": "The check other agents call before they pay: "
                             "policy coherence lint + payment authorization.",
                     "endpoints": ["POST /v1/lint", "POST /v1/policies",
                                   "POST /v1/authorize", "POST /v1/freeze",
                                   "GET /v1/status?policy_id=..."]}
    if method == "GET" and path.startswith("/v1/status"):
        pid = path.split("policy_id=")[-1]
        return 200, {"policy_id": pid,
                     "spent_total": STORE.spent_in_window(pid, None),
                     "payments": STORE.count_in_window(pid, None),
                     "frozen": STORE.frozen.get(pid, False)}
    return 404, {"error": "not found"}

class H(BaseHTTPRequestHandler):
    def _go(self, method):
        body = {}
        if method == "POST":
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        try:
            code, out = handle(method, self.path, body)
        except KeyError as e:
            code, out = 400, {"error": f"missing field {e}"}
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self): self._go("POST")
    def do_GET(self): self._go("GET")
    def log_message(self, *a): pass

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT") or (sys.argv[1] if len(sys.argv) > 1 else 8791))
    HTTPServer(("0.0.0.0", port), H).serve_forever()
