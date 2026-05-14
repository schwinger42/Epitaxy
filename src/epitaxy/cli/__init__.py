"""Epitaxy CLI — typer-based `epi` command surface.

PR1 (tracer-bullet) ships:
- `epi sync` — Python AST → .epitaxy/index.json
- `epi --version`

PR1 deferred to later commits (within this PR1 branch):
- `epi mcp serve` (commit 5)
- `epi serve` (commit 6)
"""

from .app import app

__all__ = ["app"]