"""Draw the n8n workflows as PNGs (for the README and the pitch). Needs Playwright + Chromium.

    python n8n/render_workflows.py        → n8n/workflow-1-detect.png, n8n/workflow-2-investigate.png

Positions and links come straight from the workflow JSON, so the picture matches what n8n shows.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
W, H = 200, 64

KIND = {  # node type → (label, fill, stroke)
    "trigger": ("Trigger", "#fff6dc", "#e8a200"),
    "bridge": ("Calls backend", "#eef3fb", "#5a6a80"),
    "claude": ("Claude API", "#f3ecfb", "#7a4fb5"),
    "code": ("Code", "#eaf6ef", "#1e7f55"),
    "branch": ("Decision", "#ffffff", "#1a2433"),
    "wait": ("Waits for a person", "#fbe8e5", "#bf3a2b"),
    "other": ("Step", "#f5f7fa", "#93a1b3"),
}


def kind(n):
    t = n["type"].split(".")[-1]
    if t in ("scheduleTrigger", "webhook", "executeWorkflowTrigger"):
        return "trigger"
    if t == "httpRequest":
        url = n["parameters"].get("url", "")
        return "claude" if "anthropic" in url else "bridge"
    if t == "code":
        return "code"
    if t in ("if", "switch"):
        return "branch"
    if t == "wait":
        return "wait"
    return "other"


def render(wf: dict) -> str:
    nodes = {n["name"]: n for n in wf["nodes"] if not n["type"].endswith("stickyNote")}
    xs = [n["position"][0] for n in nodes.values()]
    ys = [n["position"][1] for n in nodes.values()]
    ox, oy = min(xs) - 40, min(ys) - 90
    sx = 1.05
    pos = {k: ((n["position"][0] - ox) * sx, n["position"][1] - oy) for k, n in nodes.items()}
    width = int(max(x for x, _ in pos.values()) + W + 60)
    height = int(max(y for _, y in pos.values()) + H + 80)
    edges = []
    for src, c in wf["connections"].items():
        for out_i, outs in enumerate(c.get("main", [])):
            for t in outs:
                if src not in pos or t["node"] not in pos:
                    continue
                (x1, y1), (x2, y2) = pos[src], pos[t["node"]]
                a, b = (x1 + W, y1 + H / 2), (x2, y2 + H / 2)
                dx = max(40, abs(b[0] - a[0]) / 2)
                label = ""
                typ = nodes[src]["type"]
                if typ.endswith(".if"):
                    label = "yes" if out_i == 0 else "no"
                elif typ.endswith(".switch"):
                    label = ["stock", "supplier / date", "payment", "pricing"][out_i]
                edges.append(f'<path d="M{a[0]},{a[1]} C{a[0]+dx},{a[1]} {b[0]-dx},{b[1]} {b[0]},{b[1]}" '
                             f'fill="none" stroke="#93a1b3" stroke-width="2" marker-end="url(#ar)"/>')
                if label:
                    edges.append(f'<text x="{a[0]+8}" y="{a[1]-6}" class="lbl">{label}</text>')
    boxes = []
    for k, (x, y) in pos.items():
        lab, fill, stroke = KIND[kind(nodes[k])]
        boxes.append(f'<g><rect x="{x}" y="{y}" width="{W}" height="{H}" rx="10" fill="{fill}" stroke="{stroke}" '
                     f'stroke-width="2"/><text x="{x+12}" y="{y+26}" class="n">{html.escape(k)}</text>'
                     f'<text x="{x+12}" y="{y+46}" class="k">{lab}</text></g>')
    legend = "".join(
        f'<g transform="translate({20 + i*170},{height-34})"><rect width="16" height="16" rx="4" fill="{f}" '
        f'stroke="{s}" stroke-width="2"/><text x="24" y="13" class="k">{l}</text></g>'
        for i, (l, f, s) in enumerate(KIND.values()) if l != "Step")
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
body{{margin:0;background:#fff;font-family:Barlow,system-ui,sans-serif}}
.n{{font:600 14px system-ui,sans-serif;fill:#1a2433}} .k{{font:12px system-ui,sans-serif;fill:#5a6a80}}
.lbl{{font:600 11px system-ui,sans-serif;fill:#5a6a80}} h1{{font:700 22px system-ui;margin:18px 20px 0;color:#1a2433}}
</style></head><body><h1>{html.escape(wf['name'])}</h1>
<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
<defs><marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto">
<path d="M0,0 L10,5 L0,10 z" fill="#93a1b3"/></marker></defs>{''.join(edges)}{''.join(boxes)}{legend}</svg>
</body></html>"""


def main():
    from playwright.sync_api import sync_playwright
    out = {"1-detect-exceptions.json": "workflow-1-detect.png",
           "2-investigate-and-resolve.json": "workflow-2-investigate.png"}
    with sync_playwright() as p:
        b = p.chromium.launch()
        for src, png in out.items():
            wf = json.loads((HERE / "workflows" / src).read_text())
            pg = b.new_page(viewport={"width": 800, "height": 400}, device_scale_factor=2)
            pg.set_content(render(wf))
            pg.screenshot(path=str(HERE / png), full_page=True)
            print(png)
        b.close()


if __name__ == "__main__":
    main()
