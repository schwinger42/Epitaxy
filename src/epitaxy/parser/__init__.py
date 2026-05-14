"""Epitaxy parser — extract intent-graph nodes and edges from source files.

PR1 scope: Python AST → module + function nodes + depends-on edges.
PR2 adds ADR + plan markdown parsing.
"""

from .python import ParseError, parse_repo

__all__ = ["ParseError", "parse_repo"]