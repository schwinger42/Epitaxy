"""Pillar-3 drill-down site — semantic HTML for the intent graph.

PR3 [B] rewrite per ROADMAP §4 Progressive Enhancement: semantic
<section> / <details>/<summary> / <dl> structure for all 4 default
node types (module / function / adr / plan) and all 3 default edge
types (depends-on / references / supersedes).

CSS + auto-open JS island land in PR3 commit 5 — this commit ships
the semantic scaffold + content rendering only. The output is plain
HTML that renders correctly in any browser (including text-mode
browsers) without styling; CSS in C5 is presentational enhancement.

Render strategy:

- One `<main>` element wraps everything; one `<nav>` ToC near the top.
- Per-section `<section id="{kind}">` for modules, ADRs, plans (each
  is independently anchor-targetable from the ToC).
- Each node is a `<details id="n-{anchor}"><summary>...</summary>...</details>`
  block, collapsed by default. The summary shows path + status badge
  where applicable; the body shows POR + edges + nested content.
- Edge links that point at known nodes render as `<a href="#anchor">`;
  edge links whose target is absent from the index render as
  `<span class="missing-target">target (target not in index)</span>`
  per SCHEMA §6 worked example (PR2 retained the supersedes edge
  even when the target ADR file is gone; PR3 renders that case
  honestly instead of emitting a broken anchor — Codex round-2 Med-9).

The renderer is deliberately split into small per-section helpers so
each is readable and unit-testable in isolation. CSS will sit in a
sibling `_CSS` constant introduced in C5.
"""

from __future__ import annotations

import hashlib
import html as html_lib
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from epitaxy.store import Index, read_index
from epitaxy.store.models import (
    AdrNode,
    Edge,
    FunctionNode,
    ModuleNode,
    Node,
    PlanNode,
)


# --------------------------------------------------------------------------- #
# Primitives                                                                  #
# --------------------------------------------------------------------------- #


_CSS = """
:root {
  --fg: #1a1a1a;
  --bg: #ffffff;
  --muted: #666;
  --accent: #0066cc;
  --accent-bg: #fff8d0;
  --code-bg: #f4f4f4;
  --border: #d0d0d0;
  --badge-accepted-bg: #d4edda;
  --badge-accepted-fg: #155724;
  --badge-superseded-bg: #f8d7da;
  --badge-superseded-fg: #721c24;
  --badge-proposed-bg: #fff3cd;
  --badge-proposed-fg: #856404;
  --badge-in-progress-bg: #d1ecf1;
  --badge-in-progress-fg: #0c5460;
  --missing-fg: #999;
}
@media (prefers-color-scheme: dark) {
  :root {
    --fg: #e8e8e8;
    --bg: #1a1a1a;
    --muted: #999;
    --accent: #6bb6ff;
    --accent-bg: #3a3a1a;
    --code-bg: #2a2a2a;
    --border: #3a3a3a;
    --badge-accepted-bg: #1d3a26;
    --badge-accepted-fg: #8be4a8;
    --badge-superseded-bg: #3a1d22;
    --badge-superseded-fg: #f08594;
    --badge-proposed-bg: #3a331a;
    --badge-proposed-fg: #f0d77a;
    --badge-in-progress-bg: #1d343a;
    --badge-in-progress-fg: #8bd0f0;
    --missing-fg: #777;
  }
}
* { box-sizing: border-box; }
body {
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.5;
  color: var(--fg);
  background: var(--bg);
  margin: 0;
}
main {
  max-width: 960px;
  margin: 0 auto;
  padding: 1.5rem;
}
header h1 { margin-top: 0; }
header p { color: var(--muted); margin: 0.3rem 0; }
nav {
  position: sticky;
  top: 0;
  background: var(--bg);
  border-bottom: 1px solid var(--border);
  padding: 0.5rem 0;
  margin-bottom: 1rem;
  z-index: 10;
}
nav h2 { display: inline; margin: 0 0.5rem 0 0; font-size: 1rem; }
nav ul { display: inline; list-style: none; padding: 0; margin: 0; }
nav li { display: inline; margin-right: 1rem; }
nav a { color: var(--accent); text-decoration: none; }
nav a:hover { text-decoration: underline; }
section { margin: 2rem 0; }
section > h2 {
  border-bottom: 2px solid var(--border);
  padding-bottom: 0.3rem;
}
details {
  border: 1px solid var(--border);
  border-radius: 4px;
  margin: 0.5rem 0;
  padding: 0.5rem 0.75rem;
  background: var(--bg);
}
details > summary {
  cursor: pointer;
  font-weight: 500;
  list-style: none;
  display: flex;
  align-items: baseline;
  gap: 0.6rem;
}
details > summary::-webkit-details-marker { display: none; }
details > summary::before {
  content: "\\25B8";
  display: inline-block;
  width: 1em;
  color: var(--muted);
  transition: transform 0.1s;
}
details[open] > summary::before { content: "\\25BE"; }
details > .module-detail,
details > .adr-detail,
details > .plan-detail {
  margin-top: 0.75rem;
  padding-top: 0.5rem;
  border-top: 1px solid var(--border);
}
:target {
  background: var(--accent-bg);
  scroll-margin-top: 4rem;
}
summary .path { font-family: ui-monospace, "SF Mono", Menlo, monospace; }
.status {
  font-size: 0.75rem;
  font-weight: 600;
  padding: 0.1rem 0.4rem;
  border-radius: 3px;
  text-transform: uppercase;
  letter-spacing: 0.03em;
  background: var(--badge-proposed-bg);
  color: var(--badge-proposed-fg);
}
.status[data-status="accepted"] {
  background: var(--badge-accepted-bg); color: var(--badge-accepted-fg);
}
.status[data-status="superseded"] {
  background: var(--badge-superseded-bg); color: var(--badge-superseded-fg);
}
.status[data-status="in-progress"] {
  background: var(--badge-in-progress-bg); color: var(--badge-in-progress-fg);
}
code, .path {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 0.92em;
}
code { background: var(--code-bg); padding: 0.05em 0.3em; border-radius: 3px; }
dl.por dt, dl.frontmatter dt {
  font-weight: 600;
  color: var(--muted);
  margin-top: 0.4rem;
}
dl.por dd, dl.frontmatter dd { margin-left: 1.2rem; margin-bottom: 0.2rem; }
dl.functions > dt {
  margin-top: 0.6rem;
  padding-top: 0.4rem;
  border-top: 1px dashed var(--border);
}
dl.functions > dd { margin-left: 1rem; }
ul { padding-left: 1.4rem; }
.doc { font-style: italic; color: var(--muted); margin: 0.3rem 0; }
.summary { margin: 0.5rem 0; }
.missing-target {
  color: var(--missing-fg);
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  font-size: 0.92em;
}
.missing-target em { font-style: normal; font-size: 0.85em; opacity: 0.7; }
h3, h4 { margin: 0.8rem 0 0.3rem; }
h4 { font-size: 0.95rem; color: var(--muted); }
@media (max-width: 720px) {
  main { padding: 1rem; }
  nav { position: static; }
}
"""

# The single JS island per ROADMAP §4: CSS :target cannot drive details[open],
# so without this the drill-down primitive is broken on URL-anchor navigation.
# Listens for BOTH DOMContentLoaded and hashchange (Codex round-2 Med-3 — the
# latter handles in-page link clicks that fire after initial load).
_OPEN_HASH_JS = """
function openHashTarget() {
  var hash = location.hash;
  if (!hash || hash.length < 2) return;
  var el = document.getElementById(hash.slice(1));
  if (el && el.tagName === 'DETAILS') el.open = true;
}
document.addEventListener('DOMContentLoaded', openHashTarget);
window.addEventListener('hashchange', openHashTarget);
"""


def _esc(text: str | None) -> str:
    return html_lib.escape(text or "")


def _anchor_for(node_id: str) -> str:
    """Hash-based stable anchor — guaranteed attribute-safe.

    Codex review Medium-2 (PR1): the prior approach char-substituted `:` `/`
    `.` out of the node_id but left `"` `'` `<` `>` `&` and whitespace
    untouched. Hashing produces a constant `[0-9a-f]+` alphabet that is
    safe in attributes by construction.
    """
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:12]
    return f"n-{digest}"


def _render_node_ref(target_id: str, node_by_id: dict[str, Node]) -> str:
    """Anchor link if target node exists; plain-text + indicator if not.

    Codex round-2 Med-9: SCHEMA §6 allows `supersedes` (and by extension any
    `references`) target to point at a node that's no longer in the index.
    Emitting `<a href="#missing-anchor">` would be a broken link — render
    plain text + an explicit indicator instead.
    """
    if target_id in node_by_id:
        return f'<a href="#{_anchor_for(target_id)}">{_esc(target_id)}</a>'
    return (
        f'<span class="missing-target">{_esc(target_id)} '
        f"<em>(target not in index)</em></span>"
    )


def _edge_line_suffix(edge: Edge) -> str:
    return f" (line {edge.line})" if edge.line is not None else ""


# --------------------------------------------------------------------------- #
# Section renderers                                                           #
# --------------------------------------------------------------------------- #


def _render_header(index: Index, counts: dict[str, int]) -> str:
    return (
        '<header>'
        '<h1>Epitaxy index</h1>'
        f'<p>Generated: {_esc(index.generated_at.isoformat())} · '
        f"generator: {_esc(index.generator)}</p>"
        f'<p>{counts["modules"]} modules · {counts["functions"]} functions · '
        f'{counts["adrs"]} ADRs · {counts["plans"]} plans · '
        f'{counts["edges"]} edges</p>'
        "</header>"
    )


def _render_nav(counts: dict[str, int]) -> str:
    parts = ['<nav><h2>Sections</h2><ul>']
    parts.append(f'<li><a href="#modules">{counts["modules"]} modules</a></li>')
    if counts["adrs"]:
        parts.append(f'<li><a href="#adrs">{counts["adrs"]} ADRs</a></li>')
    if counts["plans"]:
        parts.append(f'<li><a href="#plans">{counts["plans"]} plans</a></li>')
    parts.append("</ul></nav>")
    return "".join(parts)


def _render_por_dl(por: dict | None) -> str:
    if not por:
        return ""
    rows: list[str] = ['<dl class="por">']
    for key, value in por.items():
        rows.append(f"<dt>{_esc(str(key))}</dt>")
        if isinstance(value, list):
            items = "".join(f"<li>{_esc(str(v))}</li>" for v in value)
            rows.append(f"<dd><ul>{items}</ul></dd>")
        else:
            rows.append(f"<dd>{_esc(str(value))}</dd>")
    rows.append("</dl>")
    return "".join(rows)


def _render_edge_list(
    title: str,
    edges: list[Edge],
    *,
    direction: str,
    node_by_id: dict[str, Node],
) -> str:
    """Render incoming/outgoing edges as a labeled `<ul>`. Empty list → ''."""
    if not edges:
        return ""
    parts = [f"<h4>{title}</h4><ul>"]
    for e in edges:
        target = e.to if direction == "out" else e.from_
        parts.append(
            f"<li>{_render_node_ref(target, node_by_id)}{_edge_line_suffix(e)}</li>"
        )
    parts.append("</ul>")
    return "".join(parts)


def _render_function(
    fn: FunctionNode,
    *,
    edges_from: dict[str, list[Edge]],
    edges_to: dict[str, list[Edge]],
    node_by_id: dict[str, Node],
) -> str:
    fn_anchor = _anchor_for(fn.id)
    parts: list[str] = [
        f'<dt id="{fn_anchor}"><code>{_esc(fn.signature)}</code> '
        f"— line {fn.line}</dt>"
    ]
    parts.append("<dd>")
    if fn.doc:
        parts.append(f'<p class="doc">{_esc(fn.doc)}</p>')
    parts.append(_render_por_dl(fn.por))

    out_calls = [e for e in edges_from.get(fn.id, []) if e.type == "depends-on"]
    in_calls = [e for e in edges_to.get(fn.id, []) if e.type == "depends-on"]
    parts.append(
        _render_edge_list("Calls", out_calls, direction="out", node_by_id=node_by_id)
    )
    parts.append(
        _render_edge_list(
            "Called by", in_calls, direction="in", node_by_id=node_by_id
        )
    )

    out_refs = [e for e in edges_from.get(fn.id, []) if e.type == "references"]
    in_refs = [e for e in edges_to.get(fn.id, []) if e.type == "references"]
    parts.append(
        _render_edge_list(
            "References", out_refs, direction="out", node_by_id=node_by_id
        )
    )
    parts.append(
        _render_edge_list(
            "Referenced by", in_refs, direction="in", node_by_id=node_by_id
        )
    )

    parts.append("</dd>")
    return "".join(parts)


def _render_module(
    mod: ModuleNode,
    *,
    functions: list[FunctionNode],
    edges_from: dict[str, list[Edge]],
    edges_to: dict[str, list[Edge]],
    node_by_id: dict[str, Node],
) -> str:
    anchor = _anchor_for(mod.id)
    parts: list[str] = [
        f'<details id="{anchor}" class="node-module">',
        f'<summary><span class="path">{_esc(mod.path)}</span></summary>',
        '<div class="module-detail">',
    ]
    if mod.doc:
        parts.append(f'<p class="doc">{_esc(mod.doc)}</p>')
    parts.append(_render_por_dl(mod.por))

    imports = [e for e in edges_from.get(mod.id, []) if e.type == "depends-on"]
    parts.append(
        _render_edge_list(
            "Imports", imports, direction="out", node_by_id=node_by_id
        )
    )

    out_refs = [e for e in edges_from.get(mod.id, []) if e.type == "references"]
    in_refs = [e for e in edges_to.get(mod.id, []) if e.type == "references"]
    parts.append(
        _render_edge_list(
            "References", out_refs, direction="out", node_by_id=node_by_id
        )
    )
    parts.append(
        _render_edge_list(
            "Referenced by", in_refs, direction="in", node_by_id=node_by_id
        )
    )

    if functions:
        parts.append("<h3>Functions</h3><dl class=\"functions\">")
        for fn in sorted(functions, key=lambda f: f.line):
            parts.append(
                _render_function(
                    fn,
                    edges_from=edges_from,
                    edges_to=edges_to,
                    node_by_id=node_by_id,
                )
            )
        parts.append("</dl>")

    parts.append("</div></details>")
    return "".join(parts)


def _render_adr(
    adr: AdrNode,
    *,
    edges_from: dict[str, list[Edge]],
    edges_to: dict[str, list[Edge]],
    node_by_id: dict[str, Node],
) -> str:
    anchor = _anchor_for(adr.id)
    parts: list[str] = [
        f'<details id="{anchor}" class="node-adr">',
        '<summary>',
        f'<span class="path">{_esc(adr.path)}</span>',
    ]
    if adr.status:
        parts.append(
            f'<span class="status" data-status="{_esc(adr.status)}">'
            f"{_esc(adr.status)}</span>"
        )
    parts.append("</summary>")
    parts.append('<div class="adr-detail">')
    parts.append(f"<h3>{_esc(adr.title)}</h3>")

    fm_rows: list[str] = []
    if adr.date:
        fm_rows.append(f"<dt>date</dt><dd>{_esc(adr.date)}</dd>")
    if adr.supersedes:
        fm_rows.append(
            f"<dt>supersedes</dt><dd>"
            f"{_render_node_ref(adr.supersedes, node_by_id)}</dd>"
        )
    if fm_rows:
        parts.append('<dl class="frontmatter">' + "".join(fm_rows) + "</dl>")

    if adr.summary:
        parts.append(f'<p class="summary">{_esc(adr.summary)}</p>')

    out_sup = [e for e in edges_from.get(adr.id, []) if e.type == "supersedes"]
    in_sup = [e for e in edges_to.get(adr.id, []) if e.type == "supersedes"]
    parts.append(
        _render_edge_list(
            "Supersedes", out_sup, direction="out", node_by_id=node_by_id
        )
    )
    parts.append(
        _render_edge_list(
            "Superseded by", in_sup, direction="in", node_by_id=node_by_id
        )
    )

    out_refs = [e for e in edges_from.get(adr.id, []) if e.type == "references"]
    in_refs = [e for e in edges_to.get(adr.id, []) if e.type == "references"]
    parts.append(
        _render_edge_list(
            "References", out_refs, direction="out", node_by_id=node_by_id
        )
    )
    parts.append(
        _render_edge_list(
            "Referenced by", in_refs, direction="in", node_by_id=node_by_id
        )
    )

    parts.append("</div></details>")
    return "".join(parts)


def _render_plan(
    plan: PlanNode,
    *,
    edges_from: dict[str, list[Edge]],
    edges_to: dict[str, list[Edge]],
    node_by_id: dict[str, Node],
) -> str:
    anchor = _anchor_for(plan.id)
    parts: list[str] = [
        f'<details id="{anchor}" class="node-plan">',
        '<summary>',
        f'<span class="path">{_esc(plan.path)}</span>',
    ]
    if plan.status:
        parts.append(
            f'<span class="status" data-status="{_esc(plan.status)}">'
            f"{_esc(plan.status)}</span>"
        )
    parts.append("</summary>")
    parts.append('<div class="plan-detail">')
    parts.append(f"<h3>{_esc(plan.title)}</h3>")
    if plan.summary:
        parts.append(f'<p class="summary">{_esc(plan.summary)}</p>')

    out_refs = [e for e in edges_from.get(plan.id, []) if e.type == "references"]
    in_refs = [e for e in edges_to.get(plan.id, []) if e.type == "references"]
    parts.append(
        _render_edge_list(
            "References", out_refs, direction="out", node_by_id=node_by_id
        )
    )
    parts.append(
        _render_edge_list(
            "Referenced by", in_refs, direction="in", node_by_id=node_by_id
        )
    )

    parts.append("</div></details>")
    return "".join(parts)


# --------------------------------------------------------------------------- #
# Top-level                                                                   #
# --------------------------------------------------------------------------- #


def render_index(index: Index) -> str:
    """Render the entire Index as one HTML page."""
    modules = [n for n in index.nodes if isinstance(n, ModuleNode)]
    functions = [n for n in index.nodes if isinstance(n, FunctionNode)]
    adrs = [n for n in index.nodes if isinstance(n, AdrNode)]
    plans = [n for n in index.nodes if isinstance(n, PlanNode)]

    functions_by_module: dict[str, list[FunctionNode]] = {}
    for fn in functions:
        functions_by_module.setdefault(fn.module, []).append(fn)

    edges_from: dict[str, list[Edge]] = {}
    edges_to: dict[str, list[Edge]] = {}
    for e in index.edges:
        edges_from.setdefault(e.from_, []).append(e)
        edges_to.setdefault(e.to, []).append(e)

    node_by_id: dict[str, Node] = {n.id: n for n in index.nodes}

    counts = {
        "modules": len(modules),
        "functions": len(functions),
        "adrs": len(adrs),
        "plans": len(plans),
        "edges": len(index.edges),
    }

    parts: list[str] = []
    parts.append("<!DOCTYPE html>")
    parts.append('<html lang="en"><head>')
    parts.append('<meta charset="utf-8">')
    parts.append('<meta name="viewport" content="width=device-width, initial-scale=1">')
    parts.append("<title>Epitaxy index</title>")
    parts.append(f"<style>{_CSS}</style>")
    parts.append("</head><body><main>")
    parts.append(_render_header(index, counts))
    parts.append(_render_nav(counts))

    parts.append('<section id="modules"><h2>Modules</h2>')
    for mod in sorted(modules, key=lambda m: m.path):
        parts.append(
            _render_module(
                mod,
                functions=functions_by_module.get(mod.id, []),
                edges_from=edges_from,
                edges_to=edges_to,
                node_by_id=node_by_id,
            )
        )
    parts.append("</section>")

    if adrs:
        parts.append('<section id="adrs"><h2>ADRs</h2>')
        for adr in sorted(adrs, key=lambda a: a.path):
            parts.append(
                _render_adr(
                    adr,
                    edges_from=edges_from,
                    edges_to=edges_to,
                    node_by_id=node_by_id,
                )
            )
        parts.append("</section>")

    if plans:
        parts.append('<section id="plans"><h2>Plans</h2>')
        for plan in sorted(plans, key=lambda p: p.path):
            parts.append(
                _render_plan(
                    plan,
                    edges_from=edges_from,
                    edges_to=edges_to,
                    node_by_id=node_by_id,
                )
            )
        parts.append("</section>")

    parts.append("</main>")
    parts.append(f"<script>{_OPEN_HASH_JS}</script>")
    parts.append("</body></html>")
    return "\n".join(parts)


def build_handler(index_path: Path):
    """Return a BaseHTTPRequestHandler subclass bound to `index_path`.

    The handler re-reads `index_path` on every request (matches CLI.md §3
    "rewriting index.json while serving reflects on next page load").
    """

    class IndexHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802  stdlib name
            if self.path not in ("/", "/index.html"):
                self.send_error(404, f"Path not found: {self.path}")
                return
            try:
                index = read_index(index_path)
            except FileNotFoundError:
                self.send_error(503, f"Index not found at {index_path}. Run `epi sync`.")
                return
            html = render_index(index).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_args, **_kwargs) -> None:
            """Silence default stderr access log; CLI's --verbose owns logging."""

    return IndexHandler
