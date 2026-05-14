"""MCP tool implementations: por_explain / por_trace / por_lineage.

See docs/MCP.md §2–§4 for the contract.

PR1 scope:
- `por_explain` is fully functional for `module` and `function` nodes.
- `por_trace` raises `ParameterParsingDisabled` (-32002) when the index has no
  parameter nodes — the dominant PR1 case since `epi sync --parameters` fails
  fast.
- `por_lineage` always raises `AssetTypeNotSupportedInV0` (-32004); `data_asset`
  node type is deferred to v1+ per SCHEMA §2.6.

Each tool is split into a plain-function `_impl` (testable without the MCP SDK)
and a `FastMCP`-decorated wrapper inside `build_server` (handles transport).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from epitaxy.store import Index, read_index


# Epitaxy-specific JSON-RPC error codes per docs/MCP.md §6
ERR_NODE_NOT_FOUND = -32001
ERR_PARAMETER_PARSING_DISABLED = -32002
ERR_NOT_A_PARAMETER = -32003
ERR_ASSET_TYPE_NOT_SUPPORTED_IN_V0 = -32004


def por_explain_impl(index: Index, node_id: str) -> dict[str, Any]:
    """Plain-function implementation; see docs/MCP.md §2 for return shape."""
    node = next((n for n in index.nodes if n.id == node_id), None)
    if node is None:
        raise McpError(
            ErrorData(code=ERR_NODE_NOT_FOUND, message=f"node {node_id!r} not found in index")
        )

    incident_edges = [e for e in index.edges if e.from_ == node_id or e.to == node_id]
    # Outgoing first, then incoming (per MCP.md §5 sort order).
    incident_edges.sort(key=lambda e: (0 if e.from_ == node_id else 1, e.from_, e.to))

    return {
        "node": node.model_dump(by_alias=True, mode="json"),
        "incident_edges": [
            e.model_dump(by_alias=True, mode="json") for e in incident_edges
        ],
        # PR1 has no POR blocks parsed yet; deferred to PR2.
        "related_pors": [],
        "provenance_summary": {
            "node_source": node.provenance,
            "edge_sources": sorted({e.source for e in incident_edges}),
        },
    }


def por_trace_impl(index: Index, parameter_id: str) -> dict[str, Any]:
    """Decision trail for a tuned parameter. See docs/MCP.md §3."""
    has_parameters = any(n.type == "parameter" for n in index.nodes)
    if not has_parameters:
        raise McpError(
            ErrorData(
                code=ERR_PARAMETER_PARSING_DISABLED,
                message=(
                    "index has no parameter nodes. "
                    "Re-run `epi sync --parameters` to enable. "
                    "(PR1 tracer-bullet build does not implement parameter "
                    "extraction; tracking in PR4.)"
                ),
            )
        )

    # Forward-compat shape — unreachable in PR1.
    node = next((n for n in index.nodes if n.id == parameter_id), None)
    if node is None:
        raise McpError(
            ErrorData(code=ERR_NODE_NOT_FOUND, message=f"node {parameter_id!r} not found")
        )
    if node.type != "parameter":
        raise McpError(
            ErrorData(
                code=ERR_NOT_A_PARAMETER,
                message=f"node {parameter_id!r} is type {node.type!r}, not 'parameter'",
            )
        )
    raise McpError(
        ErrorData(code=-32603, message="por_trace body not implemented in PR1 (tracking in PR4)")
    )


def por_lineage_impl(index: Index, asset_id: str) -> dict[str, Any]:
    """Documented stub in v0 — always errors. See docs/MCP.md §4."""
    _ = index, asset_id  # signature parity with future impl
    raise McpError(
        ErrorData(
            code=ERR_ASSET_TYPE_NOT_SUPPORTED_IN_V0,
            message=(
                "data_asset nodes are deferred to v1+; see SCHEMA §2.6. "
                "This tool is reserved in the v0 surface for forward compatibility."
            ),
        )
    )


def build_server(index_path: Path) -> FastMCP:
    """Build a FastMCP server bound to `index_path`.

    Per docs/MCP.md §7: stateless — re-reads the index on every tool call so
    concurrent `epi sync` in another shell is observed without restart.
    """
    server: FastMCP = FastMCP("epitaxy")

    def _load() -> Index:
        if not index_path.exists():
            raise McpError(
                ErrorData(
                    code=-32603,
                    message=f"index file not found at {index_path}. Run `epi sync` first.",
                )
            )
        return read_index(index_path)

    @server.tool()
    def por_explain(node_id: str) -> dict[str, Any]:  # noqa: D401
        """Full intent dump for one node."""
        return por_explain_impl(_load(), node_id)

    @server.tool()
    def por_trace(parameter_id: str) -> dict[str, Any]:
        """Decision trail for a tuned parameter."""
        return por_trace_impl(_load(), parameter_id)

    @server.tool()
    def por_lineage(asset_id: str) -> dict[str, Any]:
        """Upstream/downstream chain for a data asset (v0 stub)."""
        return por_lineage_impl(_load(), asset_id)

    return server