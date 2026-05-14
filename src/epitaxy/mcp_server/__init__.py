"""Epitaxy MCP server (Pillar 4) — exposes intent-graph tools to AI agents.

PR1 ships:
- por_explain (functional, module + function nodes)
- por_trace (typed-error stub — ParameterParsingDisabled until PR4)
- por_lineage (typed-error stub — AssetTypeNotSupportedInV0 until v1+)
"""

from .tools import build_server

__all__ = ["build_server"]