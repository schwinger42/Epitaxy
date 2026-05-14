"""Pillar-3 drill-down site — stdlib `http.server` renderer.

PR1 ugly-but-functional single-page output. No JS, no CSS framework, no Jinja.
Progressive-Enhancement rewrite per ROADMAP §4 is PR3 territory.
"""

from __future__ import annotations

import hashlib
import html as html_lib
from http.server import BaseHTTPRequestHandler
from pathlib import Path

from epitaxy.store import Index, read_index
from epitaxy.store.models import FunctionNode, ModuleNode


def _esc(text: str | None) -> str:
    return html_lib.escape(text or "")


def _anchor_for(node_id: str) -> str:
    """Hash-based stable anchor — guaranteed safe for use in HTML attributes
    (href / id) without further escaping.

    Codex review Medium-2: the prior approach char-substituted `:` `/` `.`
    out of the node_id but left through `"` `'` `<` `>` `&` and whitespace
    untouched. A repo path containing any of those would break out of the
    quoted attribute. Hashing produces a constant `[0-9a-f]+` alphabet that
    is safe in attributes by construction, and shorter on the wire.
    """
    digest = hashlib.sha1(node_id.encode("utf-8")).hexdigest()[:12]
    return f"n-{digest}"


def render_index(index: Index) -> str:
    """Render the entire Index as one HTML page.

    Sections: header stats → module list (anchors) → per-module detail
    (functions + incident edges).
    """
    modules = [n for n in index.nodes if isinstance(n, ModuleNode)]
    functions = [n for n in index.nodes if isinstance(n, FunctionNode)]

    functions_by_module: dict[str, list[FunctionNode]] = {}
    for fn in functions:
        functions_by_module.setdefault(fn.module, []).append(fn)

    edges_from: dict[str, list] = {}
    edges_to: dict[str, list] = {}
    for e in index.edges:
        edges_from.setdefault(e.from_, []).append(e)
        edges_to.setdefault(e.to, []).append(e)

    lines: list[str] = []
    lines.append("<!DOCTYPE html>")
    lines.append('<html lang="en"><head><meta charset="utf-8">')
    lines.append("<title>Epitaxy index</title>")
    lines.append("</head><body>")
    lines.append("<h1>Epitaxy index</h1>")
    lines.append(
        f"<p>Generated: {_esc(index.generated_at.isoformat())} · "
        f"generator: {_esc(index.generator)}<br>"
        f"{index.stats.modules} modules · {index.stats.functions} functions · "
        f"{index.stats.edges} edges</p>"
    )
    lines.append('<p><em>PR1 tracer-bullet output — Progressive Enhancement in PR3.</em></p>')

    # Module table of contents
    lines.append("<h2>Modules</h2><ul>")
    for mod in sorted(modules, key=lambda m: m.path):
        lines.append(
            f'<li><a href="#{_anchor_for(mod.id)}">{_esc(mod.path)}</a></li>'
        )
    lines.append("</ul>")

    # Per-module detail
    lines.append("<h2>Detail</h2>")
    for mod in sorted(modules, key=lambda m: m.path):
        lines.append(f'<section id="{_anchor_for(mod.id)}">')
        lines.append(f"<h3>{_esc(mod.path)}</h3>")
        if mod.doc:
            lines.append(f"<p><em>{_esc(mod.doc)}</em></p>")

        # Module-level depends-on (outgoing imports)
        out_mod_edges = [e for e in edges_from.get(mod.id, []) if e.source == "import"]
        if out_mod_edges:
            lines.append("<p><strong>Imports:</strong><ul>")
            for e in out_mod_edges:
                lines.append(f'<li><a href="#{_anchor_for(e.to)}">{_esc(e.to)}</a> (line {e.line})</li>')
            lines.append("</ul></p>")

        # Functions in this module
        mod_funcs = sorted(functions_by_module.get(mod.id, []), key=lambda f: f.line)
        if mod_funcs:
            lines.append("<h4>Functions</h4>")
            for fn in mod_funcs:
                lines.append(f'<div id="{_anchor_for(fn.id)}">')
                lines.append(f"<p><code>{_esc(fn.signature)}</code> &mdash; line {fn.line}</p>")
                if fn.doc:
                    lines.append(f"<p>{_esc(fn.doc)}</p>")

                fn_out = [e for e in edges_from.get(fn.id, []) if e.source == "call"]
                fn_in = [e for e in edges_to.get(fn.id, []) if e.source == "call"]
                if fn_out:
                    lines.append("<p>Calls:<ul>")
                    for e in fn_out:
                        lines.append(f'<li><a href="#{_anchor_for(e.to)}">{_esc(e.to)}</a> (line {e.line})</li>')
                    lines.append("</ul></p>")
                if fn_in:
                    lines.append("<p>Called by:<ul>")
                    for e in fn_in:
                        lines.append(f'<li><a href="#{_anchor_for(e.from_)}">{_esc(e.from_)}</a></li>')
                    lines.append("</ul></p>")
                lines.append("</div>")
        lines.append("</section>")

    lines.append("</body></html>")
    return "\n".join(lines)


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
