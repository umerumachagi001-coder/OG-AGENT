"""Generate the n8n workflow files in n8n/workflows/ (importable JSON).

    python n8n/build_workflows.py

Edit this file, not the JSON. Two workflows:
  OEA 1 · Detect exceptions        every 10 s (and on demand via webhook oea-scan): run detector rules,
                                   start one investigation per new exception
  OEA 2 · Investigate and resolve  claim → check the right systems → Claude (or rule playbook) →
                                   approval policy → hold if needed → auto-fix, or ping + wait for a person
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "workflows"
API = "{{ $env.OEA_API_URL || 'http://127.0.0.1:8001' }}"
DETECT_ID = "oeaDetect0000001"
INVESTIGATE_ID = "oeaInvestigate01"


def uid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"oea/{name}"))


class WF:
    def __init__(self, wid: str, name: str, notes: str):
        self.wid, self.name, self.notes = wid, name, notes
        self.nodes: list[dict] = []
        self.conn: dict = {}

    def node(self, name, type_, version, params, pos, **extra):
        n = {"id": uid(f"{self.wid}/{name}"), "name": name, "type": type_, "typeVersion": version,
             "position": list(pos), "parameters": params, **extra}
        self.nodes.append(n)
        return name

    def link(self, a, b, out=0):
        outs = self.conn.setdefault(a, {"main": []})["main"]
        while len(outs) <= out:
            outs.append([])
        outs[out].append({"node": b, "type": "main", "index": 0})

    def chain(self, *names):
        for a, b in zip(names, names[1:]):
            self.link(a, b)

    def json(self):
        return {"id": self.wid, "name": self.name, "active": False, "nodes": self.nodes, "connections": self.conn,
                "settings": {"executionOrder": "v1", "saveManualExecutions": True, "callerPolicy": "workflowsFromSameOwner"},
                "pinData": {}, "meta": {"templateCredsSetupCompleted": True},
                "tags": [], "versionId": uid(self.wid + "/v")}

    # ---- node helpers ----
    def post(self, name, path, body_expr, pos, **extra):
        """POST to the orchestrator/bridge. body_expr is a JS expression (without {{ }})."""
        return self.node(name, "n8n-nodes-base.httpRequest", 4.2, {
            "method": "POST", "url": f"={API}{path}", "sendBody": True, "specifyBody": "json",
            "jsonBody": "={{ JSON.stringify(" + body_expr + ") }}", "options": {"timeout": 30000}}, pos, **extra)

    def tool(self, name, tool, input_expr, pos, **extra):
        return self.post(name, "/n8n/exceptions/{{ $('Claim exception').first().json.id }}/tool",
                         "{ tool: '" + tool + "', input: " + input_expr + " }", pos, **extra)

    def code(self, name, js, pos, **extra):
        return self.node(name, "n8n-nodes-base.code", 2, {"jsCode": js}, pos, **extra)

    def if_(self, name, left_expr, op, pos, right=""):
        """op: 'true' | 'notEmpty' | 'equals'"""
        operator = {"true": {"type": "boolean", "operation": "true", "singleValue": True},
                    "notEmpty": {"type": "string", "operation": "notEmpty", "singleValue": True},
                    "equals": {"type": "string", "operation": "equals"}}[op]
        cond = {"id": uid(name + "/c"), "leftValue": "={{ " + left_expr + " }}", "rightValue": right, "operator": operator}
        return self.node(name, "n8n-nodes-base.if", 2.2, {
            "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "loose", "version": 2},
                           "conditions": [cond], "combinator": "and"},
            "looseTypeValidation": True, "options": {}}, pos)

    def sticky(self, name, text, pos, w=340, h=160, color=7):
        return self.node(name, "n8n-nodes-base.stickyNote", 1, {"content": text, "width": w, "height": h, "color": color}, pos)


# ───────────────────────── OEA 1 · Detect exceptions ─────────────────────────

def detect() -> WF:
    w = WF(DETECT_ID, "OEA 1 · Detect exceptions", "")
    w.sticky("About", "## Detect exceptions\nEvery 10 s (or when the dashboard's **Trigger a failure** calls the "
             "`oea-scan` webhook) run the detector rules over all open orders.\nEach new exception starts "
             "**OEA 2 · Investigate and resolve** without waiting for it.", (-60, -260), w=520, h=170)
    w.node("Every 10 seconds", "n8n-nodes-base.scheduleTrigger", 1.2,
           {"rule": {"interval": [{"field": "seconds", "secondsInterval": 10}]}}, (0, 0))
    w.node("Scan now (webhook)", "n8n-nodes-base.webhook", 2,
           {"httpMethod": "POST", "path": "oea-scan", "responseMode": "responseNode", "options": {}}, (0, 200),
           webhookId=uid("webhook/oea-scan"))
    w.post("Run detector rules", "/n8n/scan", "{}", (240, 100))
    w.node("Reply to caller", "n8n-nodes-base.respondToWebhook", 1.1,
           {"respondWith": "firstIncomingItem", "options": {}}, (480, 200))
    w.if_("Anything new?", "$('Run detector rules').first().json.count > 0", "true", (480, 0))
    w.node("One item per exception", "n8n-nodes-base.splitOut", 1,
           {"fieldToSplitOut": "created", "options": {}}, (720, -20))
    w.node("Start investigation", "n8n-nodes-base.executeWorkflow", 1.2, {
        "workflowId": {"__rl": True, "value": INVESTIGATE_ID, "mode": "id"},
        "mode": "each", "options": {"waitForSubWorkflow": False}}, (960, -20))
    w.link("Every 10 seconds", "Run detector rules")
    w.link("Scan now (webhook)", "Run detector rules")
    w.link("Run detector rules", "Anything new?")
    w.link("Run detector rules", "Reply to caller")
    w.link("Anything new?", "One item per exception", 0)
    w.link("One item per exception", "Start investigation")
    return w


# ───────────────────────── OEA 2 · Investigate and resolve ─────────────────────────

READ_CLAUDE = r"""// Pull the structured diagnosis out of Claude's reply (forced submit_resolution tool call).
const r = $input.first().json;
const block = (r.content || []).find((b) => b.type === 'tool_use' && b.name === 'submit_resolution');
const thoughts = (r.content || []).filter((b) => b.type === 'text').map((b) => b.text);
if (!block) {
  const why = r.error ? (r.error.message || JSON.stringify(r.error)) : 'Claude returned no diagnosis';
  return [{ json: { ok: false, reason: `Claude unavailable: ${why}`.slice(0, 300) } }];
}
return [{ json: { ok: true, source: 'llm', resolution: { ...block.input, thoughts } } }];
"""

BACK_TO_DECISION = "return [{ json: $('Apply approval policy').first().json }];"

NORMALISE = "const b = $input.first().json.body || {};\nreturn [{ json: { id: Number(b.id) } }];"


def investigate() -> WF:
    w = WF(INVESTIGATE_ID, "OEA 2 · Investigate and resolve", "")
    X = lambda i: i * 240  # noqa: E731
    w.sticky("About", "## Investigate and resolve one exception\n1. Claim it\n2. Check the systems that matter "
             "for this kind of problem\n3. Ask Claude for the root cause and 1–3 ranked fixes "
             "(rule playbook if Claude is off or fails)\n4. Apply the approval policy (`agent/policy.json`)\n"
             "5. Hold the order if it must not move\n6. Fix it automatically, **or** ping a person and wait for "
             "the dashboard's Approve / Reject", (-60, -420), w=560, h=260)

    # triggers
    w.node("From detector", "n8n-nodes-base.executeWorkflowTrigger", 1.1, {"inputSource": "passthrough"}, (0, 0))
    w.node("Re-investigate (webhook)", "n8n-nodes-base.webhook", 2,
           {"httpMethod": "POST", "path": "oea-investigate", "responseMode": "onReceived", "options": {}}, (0, 200),
           webhookId=uid("webhook/oea-investigate"))
    w.code("Read exception id", NORMALISE, (X(1), 200))
    w.post("Claim exception", "/n8n/exceptions/{{ $json.id }}/start", "{}", (X(2), 0))
    w.if_("Not already handled?", "$json.claimed", "true", (X(3), 0))
    w.link("From detector", "Claim exception")
    w.chain("Re-investigate (webhook)", "Read exception id", "Claim exception")
    w.link("Claim exception", "Not already handled?")

    # route to the right systems
    w.node("Which systems to check?", "n8n-nodes-base.switch", 3.2, {
        "mode": "expression", "numberOutputs": 4,
        "output": "={{ ({inventory: 0, supplier: 1, deadline: 1, payment: 2, pricing: 3})[$json.domain] ?? 1 }}",
        "options": {}}, (X(4), 0))
    w.link("Not already handled?", "Which systems to check?", 0)
    oid = "$('Claim exception').first().json.order_id"
    # 0 inventory
    w.node("Each SKU", "n8n-nodes-base.splitOut", 1, {"fieldToSplitOut": "skus", "destinationFieldName": "sku",
                                                       "options": {}}, (X(5), -300))
    w.tool("Check stock", "check_inventory", "{ sku: $json.sku || $json.skus }", (X(6), -300))
    w.tool("Check delivery promise", "get_sla", "{ order_id: " + oid + " }", (X(7), -300), executeOnce=True)
    w.chain("Each SKU", "Check stock", "Check delivery promise")
    # 1 supplier / deadline
    w.tool("Check delivery date", "get_sla", "{ order_id: " + oid + " }", (X(5), -120))
    w.tool("Check supplier", "get_supplier_status", "{ order_id: " + oid + " }", (X(6), -120))
    w.chain("Check delivery date", "Check supplier")
    # 2 payment, 3 pricing
    w.tool("Check payment", "get_payment_status", "{ order_id: " + oid + " }", (X(5), 60))
    w.tool("Check pricing rules", "get_pricing_rules", "{ order_id: " + oid + " }", (X(5), 240))
    for i, n in enumerate(["Each SKU", "Check delivery date", "Check payment", "Check pricing rules"]):
        w.link("Which systems to check?", n, i)

    exc = "$('Claim exception').first().json"
    w.tool("Look up past cases", "find_similar_cases", "{ domain: " + exc + ".domain, symptom: " + exc + ".symptom }",
           (X(8), 0), executeOnce=True)
    for n in ["Check delivery promise", "Check supplier", "Check payment", "Check pricing rules"]:
        w.link(n, "Look up past cases")

    # diagnose: Claude, else rule playbook
    w.post("Build Claude request", "/n8n/exceptions/{{ " + exc + ".id }}/llm-request", "{}", (X(9), 0))
    w.if_("Claude switched on?", "$json.enabled", "true", (X(10), 0))
    w.node("Ask Claude", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST", "url": "={{ ($env.OEA_ANTHROPIC_URL || 'https://api.anthropic.com') + '/v1/messages' }}",
        "sendHeaders": True, "headerParameters": {"parameters": [
            {"name": "x-api-key", "value": "={{ $env.ANTHROPIC_API_KEY }}"},
            {"name": "anthropic-version", "value": "2023-06-01"}]},
        "sendBody": True, "specifyBody": "json", "jsonBody": "={{ JSON.stringify($json.request) }}",
        "options": {"timeout": 30000, "response": {"response": {"neverError": True}}}}, (X(11), -100),
        onError="continueRegularOutput")
    w.code("Read Claude's answer", READ_CLAUDE, (X(12), -100))
    w.if_("Claude answered?", "$json.ok", "true", (X(13), -100))
    w.post("Rule playbook", "/n8n/exceptions/{{ " + exc + ".id }}/playbook?reason={{ encodeURIComponent($json.reason || '') }}",
           "{}", (X(13), 140))
    w.post("Check and record diagnosis", "/n8n/exceptions/{{ " + exc + ".id }}/prepare",
           "{ resolution: $json.resolution, source: $json.source || 'rules' }", (X(14), 0))
    w.if_("Diagnosis valid?", "$json.valid", "true", (X(15), 0))
    w.link("Look up past cases", "Build Claude request")
    w.link("Build Claude request", "Claude switched on?")
    w.link("Claude switched on?", "Ask Claude", 0)
    w.link("Claude switched on?", "Rule playbook", 1)
    w.chain("Ask Claude", "Read Claude's answer", "Claude answered?")
    w.link("Claude answered?", "Check and record diagnosis", 0)
    w.link("Claude answered?", "Rule playbook", 1)
    w.link("Rule playbook", "Check and record diagnosis")
    w.link("Check and record diagnosis", "Diagnosis valid?")
    w.link("Diagnosis valid?", "Rule playbook", 1)

    # policy, containment, decide
    w.code("Apply approval policy", (HERE / "policy_engine.js").read_text(), (X(16), -60))
    w.if_("Hold the order first?", "$json.containment.length > 0", "true", (X(17), -60))
    w.node("Each protective step", "n8n-nodes-base.splitOut", 1, {"fieldToSplitOut": "containment",
                                                                   "destinationFieldName": "step", "options": {}},
           (X(18), -200))
    w.post("Hold order", "/n8n/exceptions/{{ " + exc + ".id }}/step", "{ step: $json.step || ($json.type ? { type: $json.type, params: $json.params } : $json.containment), kind: 'containment' }",
           (X(19), -200))
    w.code("Back to the decision", BACK_TO_DECISION, (X(20), -200), executeOnce=True)
    w.post("Save options and decide", "/n8n/exceptions/{{ " + exc + ".id }}/decide",
           "{ options: $json.options, verdicts: $json.verdicts, context: $json.context, resume_url: $execution.resumeUrl }",
           (X(21), -60))
    w.link("Diagnosis valid?", "Apply approval policy", 0)
    w.link("Apply approval policy", "Hold the order first?")
    w.link("Hold the order first?", "Each protective step", 0)
    w.link("Hold the order first?", "Save options and decide", 1)
    w.chain("Each protective step", "Hold order", "Back to the decision", "Save options and decide")

    # act or wait for a person
    w.if_("Policy allows auto-fix?", "$json.auto", "true", (X(22), -60))
    w.post("Fix it automatically", "/n8n/actions/{{ $json.action_id }}/run", "{ by: 'agent' }", (X(23), -200))
    w.if_("Slack webhook set?", "$env.OEA_SLACK_WEBHOOK_URL || ''", "notEmpty", (X(23), 80))
    w.node("Ping the ops channel", "n8n-nodes-base.httpRequest", 4.2, {
        "method": "POST", "url": "={{ $env.OEA_SLACK_WEBHOOK_URL }}", "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ JSON.stringify({ text: $('Save options and decide').first().json.notify_text }) }}",
        "options": {"timeout": 10000}}, (X(24), 0), onError="continueRegularOutput")
    w.node("Wait for a person", "n8n-nodes-base.wait", 1.1, {
        "resume": "webhook", "httpMethod": "POST", "options": {}}, (X(25), 80), webhookId=uid("wait/decision"))
    w.if_("Approved?", "$json.body.verdict", "equals", (X(26), 80), right="approved")
    w.post("Run the approved fix", "/n8n/actions/{{ $json.body.action_id }}/run",
           "{ by: $json.body.by || 'human', steps: $json.body.steps || null }", (X(27), 0))
    w.node("Closed without action", "n8n-nodes-base.noOp", 1, {}, (X(27), 180))
    w.link("Save options and decide", "Policy allows auto-fix?")
    w.link("Policy allows auto-fix?", "Fix it automatically", 0)
    w.link("Policy allows auto-fix?", "Slack webhook set?", 1)
    w.link("Slack webhook set?", "Ping the ops channel", 0)
    w.link("Slack webhook set?", "Wait for a person", 1)
    w.link("Ping the ops channel", "Wait for a person")
    w.link("Wait for a person", "Approved?")
    w.link("Approved?", "Run the approved fix", 0)
    w.link("Approved?", "Closed without action", 1)
    return w


def main():
    OUT.mkdir(exist_ok=True)
    for wf, fname in [(detect(), "1-detect-exceptions.json"), (investigate(), "2-investigate-and-resolve.json")]:
        (OUT / fname).write_text(json.dumps(wf.json(), indent=2, ensure_ascii=False) + "\n")
        print(f"{fname}: {len(wf.nodes)} nodes")


if __name__ == "__main__":
    main()
