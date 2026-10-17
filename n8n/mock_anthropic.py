"""Stand-in for api.anthropic.com, for testing the n8n Claude path without an API key.

    python n8n/mock_anthropic.py 8099      then set OEA_ANTHROPIC_URL=http://127.0.0.1:8099 for n8n

Checks the request has the shape n8n must send (key header, forced submit_resolution tool) and answers
with a fixed SC-1 diagnosis whose cost is deliberately wrong (₹50), so the cost check is exercised too.
"""
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

SC1 = {
    "root_cause": "Mock Claude: WH-A's system count for SKU-HDPH (5) is phantom; the shelf scan found 0.",
    "confidence": 0.9,
    "evidence": [{"source": "check_inventory", "fact": "WH-A system 5, scan 0; WH-B 11 verified free"}],
    "options": [{"title": "Ship from Mumbai Hub", "cost": 50, "risk": "low", "rationale": "Verified stock in WH-B",
                 "steps": [{"type": "reallocate_stock", "params": {"order_id": "ORD-1001", "from": "WH-A", "to": "WH-B"}}]}],
}


class H(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        problems = []
        if not self.headers.get("x-api-key"):
            problems.append("missing x-api-key")
        if body.get("tool_choice", {}).get("name") != "submit_resolution":
            problems.append("tool_choice not forced")
        if "Evidence gathered by the workflow" not in body["messages"][0]["content"]:
            problems.append("no evidence in prompt")
        if problems:
            self._send(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "; ".join(problems)}})
            return
        self._send(200, {"id": "msg_mock", "type": "message", "role": "assistant", "model": body["model"],
                         "content": [{"type": "text", "text": "The scan proves WH-A's stock is phantom."},
                                     {"type": "tool_use", "id": "tu_1", "name": "submit_resolution", "input": SC1}],
                         "stop_reason": "tool_use"})

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", int(sys.argv[1]) if len(sys.argv) > 1 else 8099), H).serve_forever()
