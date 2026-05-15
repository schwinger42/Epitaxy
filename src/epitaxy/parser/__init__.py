"""Epitaxy parser — extract intent-graph nodes and edges from source files.

PR1 scope: Python AST → module + function nodes + depends-on edges.
PR2 adds: ADR + plan markdown parsing (parser/markdown.py);
POR docstring frontmatter (parser/por.py); references-edge final pass
(parser/refs.py) wired into `epi sync` in commit 6.
"""

from .markdown import parse_markdown
from .python import ParseError, parse_repo

__all__ = ["ParseError", "parse_markdown", "parse_repo"]