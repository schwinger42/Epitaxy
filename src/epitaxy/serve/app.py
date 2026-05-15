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
    # CSS + JS island land in commit 5.
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

    parts.append("</main></body></html>")
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
