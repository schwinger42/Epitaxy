"""Epitaxy parser — extract intent-graph nodes and edges from source files.

PR1 scope: Python AST → module + function nodes + depends-on edges.
PR2 adds ADR + plan markdown parsing.
"""

from .python import parse_repo

__all__ = ["parse_repo"]