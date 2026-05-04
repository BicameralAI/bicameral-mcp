"""Generate a rich standalone HTML report from Phase 3 E2E test artifacts.

Reads JSON artifacts from test-results/e2e/ and produces a single
self-contained HTML report with:
  - SDLC failure mode → test mapping
  - Full tool response panels (collapsible)
  - SurrealDB graph visualizations (node/edge tables)
  - Mermaid sequence diagrams for each test flow

Run: python tests/generate_e2e_report.py
Output: test-results/e2e-report.html
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timezone
from pathlib import Path

E2E_DIR = Path(__file__).parent.parent / "test-results" / "e2e"
OUTPUT = Path(__file__).parent.parent / "test-results" / "e2e-report.html"

# Maps artifact prefix → SDLC context
SDLC_SECTIONS = [
    {
        "prefix": "01_constraint_lost",
        "sdlc": "CONSTRAINT_LOST",
        "title": "Known limits surface mid-sprint",
        "description": "A rate limit, auth model, or compliance rule was discussed — but only discovered by engineering after implementation is underway.",
        "tools": "bicameral.ingest → bicameral.search",
        "color": "#f06a6a",
    },
    {
        "prefix": "02_context_scattered",
        "sdlc": "CONTEXT_SCATTERED",
        "title": "The 'why' is split across 4 tools",
        "description": "An architectural decision's rationale lives in a Slack thread, a huddle recording, someone's memory, and a Notion doc.",
        "tools": "bicameral.ingest (transcript + PRD) → bicameral.status",
        "color": "#f0b94a",
    },
    {
        "prefix": "03_undocumented",
        "sdlc": "DECISION_UNDOCUMENTED",
        "title": "Verbal 'let's do X' never lands in a ticket",
        "description": "A decision is made in a meeting but never written down. Implementation diverges from intent because intent was never captured.",
        "tools": "bicameral.ingest → bicameral.status (ungrounded)",
        "color": "#a88af0",
    },
    {
        "prefix": "04_repeated_explanation",
        "sdlc": "REPEATED_EXPLANATION",
        "title": "Same context tax paid twice",
        "description": "PM explains intent to design, then re-explains to engineering. Engineer re-discovers constraints PM already surfaced.",
        "tools": "bicameral.search (full provenance chain)",
        "color": "#4ab8f0",
    },
    {
        "prefix": "05_tribal_knowledge",
        "sdlc": "TRIBAL_KNOWLEDGE",
        "title": "Only one person knows why",
        "description": "The system works the way it does because of a conversation 6 months ago. That person is on vacation.",
        "tools": "bicameral.drift → surfaces decisions for a file",
        "color": "#6af0a0",
    },
    {
        "prefix": "06_lifecycle",
        "sdlc": "FULL LIFECYCLE",
        "title": "End-to-end pipeline integrity",
        "description": "Ingest → link_commit → status → search → drift — verify the graph is internally consistent at each step.",
        "tools": "All 5 ledger tools + graph integrity check",
        "color": "#4af0c4",
    },
]


def _load_artifacts(prefix: str) -> tuple[list[dict], list[dict]]:
    """Load tool response and graph artifacts for a prefix."""
    responses = []
    graphs = []
    if not E2E_DIR.exists():
        return responses, graphs

    # Tool responses: {prefix}*.json (e.g. 01_constraint_lost_ingest.json)
    for f in sorted(E2E_DIR.glob(f"{prefix}*.json")):
        try:
            data = json.loads(f.read_text())
            responses.append({"name": f.stem, "data": data})
        except Exception:
            pass

    # Graph dumps: graph_{prefix}*.json (e.g. graph_01_constraint_lost.json)
    for f in sorted(E2E_DIR.glob(f"graph_{prefix}*.json")):
        try:
            data = json.loads(f.read_text())
            graphs.append({"name": f.stem, "data": data})
        except Exception:
            pass

    return responses, graphs


def _render_json(data: dict, max_lines: int = 40) -> str:
    """Render JSON with syntax highlighting."""
    raw = json.dumps(data, indent=2, default=str)
    lines = raw.split("\n")
    truncated = len(lines) > max_lines
    if truncated:
        lines = lines[:max_lines]
    text = "\n".join(lines)
    if truncated:
        text += f"\n... ({len(raw.split(chr(10))) - max_lines} more lines)"
    # Basic syntax coloring
    import re

    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'"([^"]*)"(?=\s*:)', r'<span style="color:#a88af0">"\1"</span>', text)
    text = re.sub(r':\s*"([^"]*)"', r': <span style="color:#6af0a0">"\1"</span>', text)
    text = re.sub(r":\s*(\d+\.?\d*)", r': <span style="color:#4af0c4">\1</span>', text)
    text = re.sub(r":\s*(true|false|null)", r': <span style="color:#f0b94a">\1</span>', text)
    return text


_graph_counter = 0


def _render_graph_section(graph: dict) -> str:
    """Render a graph artifact as an interactive Cytoscape.js graph + detail tables."""
    global _graph_counter
    _graph_counter += 1
    cy_id = f"cy_{_graph_counter}"

    nodes = graph.get("nodes", {})
    edges = graph.get("edges", {})
    counts = graph.get("counts", {})

    # Build Cytoscape elements
    cy_elements = []
    node_id_set = set()

    for intent in nodes.get("intents", []):
        nid = str(intent.get("id", ""))
        desc = str(intent.get("description", ""))[:50]
        status = intent.get("cached_status", "—")
        cy_elements.append(
            {
                "data": {"id": nid, "label": desc, "status": status, "type": "intent"},
                "classes": "intent",
            }
        )
        node_id_set.add(nid)

    for symbol in nodes.get("symbols", []):
        nid = str(symbol.get("id", ""))
        name = str(symbol.get("name", nid))
        cy_elements.append(
            {
                "data": {"id": nid, "label": name, "type": "symbol"},
                "classes": "symbol",
            }
        )
        node_id_set.add(nid)

    for region in nodes.get("code_regions", []):
        nid = str(region.get("id", ""))
        fp = str(region.get("file_path", "?"))
        sym = str(region.get("symbol", ""))
        label = f"{sym}\n{fp.split('/')[-1]}" if sym else fp.split("/")[-1]
        cy_elements.append(
            {
                "data": {"id": nid, "label": label, "file": fp, "type": "code_region"},
                "classes": "code_region",
            }
        )
        node_id_set.add(nid)

    for edge_type, edge_list in edges.items():
        if not isinstance(edge_list, list):
            continue
        for i, edge in enumerate(edge_list):
            src = str(edge.get("out", ""))
            tgt = str(edge.get("in", ""))
            if src in node_id_set and tgt in node_id_set:
                cy_elements.append(
                    {
                        "data": {
                            "id": f"e_{edge_type}_{i}_{_graph_counter}",
                            "source": src,
                            "target": tgt,
                            "label": edge_type,
                        },
                    }
                )

    elements_json = json.dumps(cy_elements, default=str)

    # Summary badges
    parts = []
    for k, v in counts.items():
        parts.append(f'<span class="count-badge">{v} {k}</span>')
    summary = " ".join(parts)

    # Detail tables (collapsible)
    intent_rows = ""
    for intent in nodes.get("intents", []):
        desc = str(intent.get("description", ""))[:80]
        status = intent.get("cached_status", "—")
        color = {
            "reflected": "#6af0a0",
            "drifted": "#f06a6a",
            "pending": "#f0b94a",
            "ungrounded": "#4ab8f0",
        }.get(status, "#6b7699")
        intent_rows += f'<tr><td class="mono">{str(intent.get("id", "?"))[-12:]}</td><td>{desc}</td><td style="color:{color};font-weight:600">{status}</td></tr>\n'

    region_rows = ""
    for region in nodes.get("code_regions", []):
        fp = str(region.get("file_path", "?"))
        sym = str(region.get("symbol", "?"))
        lines = f"{region.get('start_line', '?')}-{region.get('end_line', '?')}"
        region_rows += f'<tr><td class="mono">{fp}</td><td>{sym}</td><td>{lines}</td></tr>\n'

    tables_html = ""
    if intent_rows:
        tables_html += f"""<h4 style="color:#a88af0;margin:12px 0 6px">Intents</h4>
<table class="data-table"><tr><th>ID</th><th>Description</th><th>Status</th></tr>
{intent_rows}</table>"""
    if region_rows:
        tables_html += f"""<h4 style="color:#4af0c4;margin:12px 0 6px">Code Regions</h4>
<table class="data-table"><tr><th>File</th><th>Symbol</th><th>Lines</th></tr>
{region_rows}</table>"""

    return f'''
<div class="graph-summary">{summary}</div>
<div id="{cy_id}" class="cy-container"></div>
<div id="{cy_id}_tooltip" class="cy-tooltip"></div>
<script>
(function() {{
  var el = document.getElementById("{cy_id}");
  var tip = document.getElementById("{cy_id}_tooltip");
  var cy = cytoscape({{
    container: el,
    elements: {elements_json},
    style: [
      {{ selector: 'node', style: {{
        'label': 'data(label)',
        'text-valign': 'center',
        'text-halign': 'center',
        'text-wrap': 'wrap',
        'text-max-width': '120px',
        'font-size': 10,
        'color': '#d4daf0',
        'text-outline-color': '#0b0d12',
        'text-outline-width': 2,
        'width': 50,
        'height': 50,
        'border-width': 2,
      }}}},
      {{ selector: 'node.intent', style: {{
        'background-color': '#2a1f4e',
        'border-color': '#a88af0',
        'shape': 'round-rectangle',
        'width': 'label',
        'height': 40,
        'padding': '12px',
      }}}},
      {{ selector: 'node.symbol', style: {{
        'background-color': '#1a3a2a',
        'border-color': '#6af0a0',
        'shape': 'ellipse',
        'width': 'label',
        'height': 40,
        'padding': '10px',
      }}}},
      {{ selector: 'node.code_region', style: {{
        'background-color': '#1a2a3a',
        'border-color': '#4ab8f0',
        'shape': 'round-rectangle',
        'width': 'label',
        'height': 40,
        'padding': '10px',
      }}}},
      {{ selector: 'edge', style: {{
        'label': 'data(label)',
        'curve-style': 'bezier',
        'target-arrow-shape': 'triangle',
        'line-color': '#2e3548',
        'target-arrow-color': '#4af0c4',
        'font-size': 9,
        'color': '#6b7699',
        'text-rotation': 'autorotate',
        'text-outline-color': '#0b0d12',
        'text-outline-width': 1.5,
        'width': 1.5,
        'arrow-scale': 0.8,
      }}}},
    ],
    layout: {{
      name: 'cose',
      padding: 30,
      nodeRepulsion: function(node){{ return 8000; }},
      idealEdgeLength: function(edge){{ return 120; }},
      animate: false,
    }},
    minZoom: 0.3,
    maxZoom: 3,
    wheelSensitivity: 0.3,
  }});
  cy.on('mouseover', 'node', function(e) {{
    var d = e.target.data();
    var lines = ['<b>' + d.type + '</b>'];
    if (d.label) lines.push(d.label);
    if (d.status) lines.push('Status: ' + d.status);
    if (d.file) lines.push(d.file);
    tip.innerHTML = lines.join('<br>');
    tip.style.display = 'block';
  }});
  cy.on('mouseout', 'node', function() {{ tip.style.display = 'none'; }});
  cy.on('mousemove', function(e) {{
    tip.style.left = e.originalEvent.offsetX + 12 + 'px';
    tip.style.top = e.originalEvent.offsetY + 12 + 'px';
  }});
}})();
</script>
<details style="margin-top:8px">
  <summary style="cursor:pointer;color:var(--text-dimmer);font-size:12px">Raw data tables</summary>
  {tables_html}
</details>'''


def generate() -> str:
    global _graph_counter
    _graph_counter = 0
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    sections_html = ""
    total_artifacts = 0

    for section in SDLC_SECTIONS:
        responses, graphs = _load_artifacts(section["prefix"])
        total_artifacts += len(responses) + len(graphs)

        # Tool response panels
        response_panels = ""
        for resp in responses:
            rendered = _render_json(resp["data"])
            response_panels += f"""
<details class="artifact-panel">
  <summary>{resp["name"].replace("_", " ").title()}</summary>
  <pre class="json-output">{rendered}</pre>
</details>"""

        # Graph panels
        graph_panels = ""
        for graph in graphs:
            graph_html = _render_graph_section(graph["data"])
            c = graph["data"].get("counts", {})
            graph_panels += f"""
<div class="artifact-panel graph-panel" style="padding:14px;">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
    <span style="color:var(--accent);font-weight:600;font-size:13px;">Knowledge Graph — {c.get("intents", 0)} intents, {c.get("symbols", 0)} symbols, {c.get("code_regions", 0)} regions</span>
    <div class="cy-legend"><span class="lg-intent">intent</span><span class="lg-symbol">symbol</span><span class="lg-region">code_region</span></div>
  </div>
  {graph_html}
</div>"""

        has_content = responses or graphs
        sections_html += f"""
<div class="sdlc-section" style="border-left-color:{section["color"]}">
  <div class="sdlc-badge" style="color:{section["color"]}">{section["sdlc"]}</div>
  <h3>{section["title"]}</h3>
  <p class="sdlc-desc">{section["description"]}</p>
  <div class="tools-used">Tools: <span class="mono">{section["tools"]}</span></div>
  {"<div class='artifacts'>" + response_panels + graph_panels + "</div>" if has_content else '<p class="no-artifacts">No artifacts generated — test may not have run.</p>'}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bicameral MCP — E2E Test Report</title>
<script src="https://unpkg.com/cytoscape@3.30.4/dist/cytoscape.min.js"></script>
<style>
:root {{
  --bg: #0b0d12; --bg2: #11141c; --bg3: #181d28;
  --border: #252b3a; --border2: #2e3548;
  --text: #d4daf0; --text-dim: #6b7699; --text-dimmer: #3d4560;
  --accent: #4af0c4; --amber: #f0b94a; --rose: #f06a6a;
  --sage: #6af0a0; --violet: #a88af0; --sky: #4ab8f0;
}}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ background: var(--bg); color: var(--text); font-family: -apple-system, 'Segoe UI', sans-serif; font-size: 14px; line-height: 1.6; }}
.page {{ max-width: 960px; margin: 0 auto; padding: 32px 24px 80px; }}
.mono {{ font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px; }}

header {{ border-bottom: 1px solid var(--border); padding-bottom: 28px; margin-bottom: 36px; }}
header h1 {{ font-size: 1.8rem; font-weight: 700; color: #fff; margin-bottom: 8px; }}
header h1 span {{ color: var(--accent); }}
header .meta {{ color: var(--text-dim); font-size: 13px; }}
header .meta span {{ margin-right: 20px; }}

.stats {{ display: flex; gap: 16px; margin-bottom: 32px; flex-wrap: wrap; }}
.stat-card {{ background: var(--bg2); border: 1px solid var(--border); border-radius: 8px; padding: 16px 20px; min-width: 140px; }}
.stat-card .label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); margin-bottom: 4px; }}
.stat-card .value {{ font-size: 1.6rem; font-weight: 700; color: #fff; }}
.stat-card .sub {{ font-size: 11px; color: var(--text-dimmer); }}

.sdlc-section {{ background: var(--bg2); border: 1px solid var(--border); border-left: 3px solid; border-radius: 8px; padding: 24px; margin-bottom: 20px; }}
.sdlc-badge {{ font-family: 'SF Mono', monospace; font-size: 11px; font-weight: 600; letter-spacing: 0.08em; margin-bottom: 8px; }}
.sdlc-section h3 {{ font-size: 1.1rem; color: #fff; margin-bottom: 6px; }}
.sdlc-desc {{ color: var(--text-dim); font-size: 13px; margin-bottom: 10px; }}
.tools-used {{ font-size: 12px; color: var(--text-dimmer); margin-bottom: 16px; }}

.artifact-panel {{ background: var(--bg3); border: 1px solid var(--border); border-radius: 6px; margin-top: 10px; }}
.artifact-panel summary {{ padding: 10px 14px; cursor: pointer; font-size: 13px; font-weight: 600; color: var(--amber); }}
.artifact-panel summary:hover {{ color: #fff; }}
.graph-panel summary {{ color: var(--accent); }}
.json-output {{ padding: 12px 16px; font-family: 'SF Mono', monospace; font-size: 11px; line-height: 1.5; overflow-x: auto; white-space: pre; color: var(--text-dim); max-height: 400px; overflow-y: auto; }}

.graph-summary {{ display: flex; gap: 8px; flex-wrap: wrap; padding: 8px 14px; }}
.count-badge {{ font-family: 'SF Mono', monospace; font-size: 11px; background: var(--bg); padding: 3px 10px; border-radius: 4px; border: 1px solid var(--border); color: var(--text-dim); }}

.data-table {{ width: 100%; border-collapse: collapse; font-size: 12px; margin-bottom: 8px; }}
.data-table th {{ text-align: left; padding: 6px 12px; color: var(--text-dimmer); font-weight: 500; font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; border-bottom: 1px solid var(--border); }}
.data-table td {{ padding: 6px 12px; border-bottom: 1px solid var(--bg); color: var(--text-dim); }}

.no-artifacts {{ color: var(--text-dimmer); font-size: 12px; font-style: italic; }}
h4 {{ font-size: 13px; font-weight: 600; }}

.cy-container {{ width: 100%; height: 360px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; margin-top: 8px; position: relative; }}
.cy-tooltip {{ display: none; position: absolute; background: var(--bg3); border: 1px solid var(--border2); border-radius: 6px; padding: 8px 12px; font-size: 11px; color: var(--text); pointer-events: none; z-index: 10; max-width: 300px; line-height: 1.5; }}
.cy-legend {{ display: flex; gap: 16px; padding: 8px 0; font-size: 11px; color: var(--text-dim); }}
.cy-legend span::before {{ content: ''; display: inline-block; width: 10px; height: 10px; border-radius: 3px; margin-right: 5px; vertical-align: middle; }}
.cy-legend .lg-intent::before {{ background: #a88af0; }}
.cy-legend .lg-symbol::before {{ background: #6af0a0; }}
.cy-legend .lg-region::before {{ background: #4ab8f0; }}
</style>
</head>
<body>
<div class="page">

<header>
  <h1>Bicameral <span>MCP</span> — E2E Test Report</h1>
  <div class="meta">
    <span>Generated: {now}</span>
    <span>Artifacts: {total_artifacts}</span>
    <span>SURREAL_URL: memory://</span>
  </div>
</header>

<div class="stats">
  <div class="stat-card"><div class="label">SDLC Scenarios</div><div class="value">{len([s for s in SDLC_SECTIONS if _load_artifacts(s["prefix"])[0]])}</div><div class="sub">of {len(SDLC_SECTIONS)} tested</div></div>
  <div class="stat-card"><div class="label">Tool Responses</div><div class="value">{sum(len(_load_artifacts(s["prefix"])[0]) for s in SDLC_SECTIONS)}</div><div class="sub">captured</div></div>
  <div class="stat-card"><div class="label">Graph Dumps</div><div class="value">{sum(len(_load_artifacts(s["prefix"])[1]) for s in SDLC_SECTIONS)}</div><div class="sub">with node/edge data</div></div>
</div>

{sections_html}

</div>
</body>
</html>"""


def main():
    if not E2E_DIR.exists():
        print(f"No artifacts found at {E2E_DIR} — run Phase 3 tests first.", file=sys.stderr)
        return 1

    html = generate()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(html)
    print(f"Report generated: {OUTPUT}")
    print(f"  Artifacts read from: {E2E_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
