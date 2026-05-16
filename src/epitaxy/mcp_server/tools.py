"""MCP tool implementations: por_explain / por_trace / por_lineage.

See docs/MCP.md §2–§4 for the contract.

PR4 scope:
- `por_explain` (PR1 + PR2 + PR3 ongoing) — full intent dump for any node
  type in the SCHEMA-default-emit set (module/function/adr/plan/parameter).
- `por_trace` (PR4) — decision trail for a tuned value. Gated on
  `index.config.parameters_enabled` (Codex round-1 High-4); returns a
  `TraceResult` with `decision_chain` newest-first via supersedes,
  `parallel_heads` (always-present array; populated when multiple ADRs
  decide the same parameter and none are superseded), and `notes`
  (always-present array; populated when cycles in the supersedes chain
  truncate the walk).
- `por_lineage` — documented stub; always errors with
  `AssetTypeNotSupportedInV0` (-32004) per SCHEMA §2.6 deferral.

Each tool is split into a plain-function `_impl` (testable without the MCP SDK)
and a `FastMCP`-decorated wrapper inside `build_server` (handles transport).

Domain scope: `por_trace` is THE concrete instance of "agent checks intent
before changing a tuned value." Applies equally to ML hyperparameters
(`rank = 128`) and domain-constrained values (`sample_temperature_K = 77`,
`chamber_pressure_Torr = 1e-6`, `validation_threshold = 0.95`). See
[[feedback_epitaxy_product_framing]] for the broader product framing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.shared.exceptions import McpError
from mcp.types import ErrorData

from epitaxy.store import Index, read_index
from epitaxy.store.models import AdrNode, ParameterNode


# Epitaxy-specific error codes per docs/MCP.md §6.
#
# MCP semantics: tool errors surface to clients as
# `CallToolResult { isError: true, content: [TextContent(...)] }` — they are
# NOT JSON-RPC error envelopes (that route is reserved for protocol-level
# failures like invalid params shape). So clients see the `message` text, not
# the `code` int directly. To preserve machine-readable dispatch, we prefix
# the message with `[code:-XXXX]` (see `_make_error`). Codex review Medium-1.
ERR_NODE_NOT_FOUND = -32001
ERR_PARAMETER_PARSING_DISABLED = -32002
ERR_NOT_A_PARAMETER = -32003
ERR_ASSET_TYPE_NOT_SUPPORTED_IN_V0 = -32004


def _make_error(code: int, message: str) -> McpError:
    """Build an McpError whose message text carries the code as a
    machine-readable prefix, so MCP clients can dispatch on specific Epitaxy
    error categories without parsing free-form text."""
    return McpError(ErrorData(code=code, message=f"[code:{code}] {message}"))


def por_explain_impl(index: Index, node_id: str) -> dict[str, Any]:
    """Plain-function implementation; see docs/MCP.md §2 for return shape."""
    node = next((n for n in index.nodes if n.id == node_id), None)
    if node is None:
        raise _make_error(
            ERR_NODE_NOT_FOUND, f"node {node_id!r} not found in index"
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


@dataclass(frozen=True)
class _ChainResult:
    """Return shape from `_build_decision_chain`.

    `chain` — primary decision chain rooted at lex-first head, newest-first
    via supersedes.
    `parallel_heads` — ALL active heads (ADRs that decide this parameter
    AND are not superseded). Populated when len > 1; empty list otherwise.
    `notes` — cycle-truncation / no-head warnings, surfaced to the LLM
    consumer via TraceResult.
    """

    chain: list[AdrNode]
    parallel_heads: list[AdrNode] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _build_decision_chain(index: Index, parameter_id: str) -> _ChainResult:
    """Walk supersedes chain backwards from the head ADR that decides
    `parameter_id`. See docs/MCP.md §3 for the contract.

    Codex round-1 Med-6: parallel decision heads (multiple ADRs decide the
    same parameter, none superseded) → surface ALL heads in
    `parallel_heads` so LLM consumers see the ambiguity instead of getting
    a silently-picked single chain.

    Codex round-1 Low-12: cycle detection via `visited` set. When a cycle
    is hit, the walk truncates + a note is added to the `notes` list.
    """
    deciders = {
        e.from_ for e in index.edges if e.type == "decides" and e.to == parameter_id
    }
    adr_by_id: dict[str, AdrNode] = {
        n.id: n for n in index.nodes if isinstance(n, AdrNode)
    }
    relevant_adrs = [adr_by_id[d] for d in deciders if d in adr_by_id]
    if not relevant_adrs:
        return _ChainResult(chain=[], parallel_heads=[], notes=[])

    sup_to = {e.from_: e.to for e in index.edges if e.type == "supersedes"}
    sup_from = {e.to for e in index.edges if e.type == "supersedes"}
    heads = sorted(
        [a for a in relevant_adrs if a.id not in sup_from], key=lambda a: a.id
    )

    notes: list[str] = []
    parallel_heads = heads if len(heads) > 1 else []
    if not heads:
        # All deciders are themselves superseded — defensive fallback to
        # lex-first relevant ADR + surface in notes.
        heads = sorted(relevant_adrs, key=lambda a: a.id)
        notes.append(
            "no decision head found (all deciding ADRs are themselves "
            f"superseded); using lex-first ADR {heads[0].id!r} as chain root."
        )

    primary_head = heads[0]
    chain = [primary_head]
    visited: set[str] = {primary_head.id}
    current = primary_head.id
    while current in sup_to:
        next_id = sup_to[current]
        next_adr = adr_by_id.get(next_id)
        if next_adr is None:
            # Dangling supersedes target per SCHEMA §6 — stop walk silently
            # (the dangling edge is already in the graph; TraceResult
            # doesn't need to repeat it).
            break
        if next_id in visited:
            notes.append(
                f"cycle in supersedes chain involving {next_id!r} "
                f"(already visited); chain truncated at {current!r}."
            )
            break
        chain.append(next_adr)
        visited.add(next_id)
        current = next_id

    return _ChainResult(chain=chain, parallel_heads=parallel_heads, notes=notes)


def por_trace_impl(index: Index, parameter_id: str) -> dict[str, Any]:
    """Decision trail for a tuned value. See docs/MCP.md §3.

    Per the broader product framing: this is THE concrete instance of "agent
    checks intent before changing a tuned value." Applies equally to ML
    hyperparameters AND domain-constrained values (physical constraints,
    instrument settings, chemical thresholds, mathematical bounds,
    validation rules) — Epitaxy preserves intent across both audiences.
    See [[feedback_epitaxy_product_framing]].

    Error semantics (Codex round-1 High-4 — gate on
    `index.config.parameters_enabled`, NOT on "any parameter nodes exist"):

    - `parameters_enabled == False`: ParameterParsingDisabled (-32002).
      Re-run `epi sync --parameters`.
    - `parameters_enabled == True` + parameter_id resolves to a
      ParameterNode: return TraceResult.
    - `parameters_enabled == True` + parameter_id resolves to a
      non-parameter node: NotAParameter (-32003).
    - `parameters_enabled == True` + parameter_id doesn't resolve:
      NodeNotFound (-32001). (Distinct from ParameterParsingDisabled —
      parsing WAS enabled; this specific ID just doesn't exist.)
    """
    if not index.config.parameters_enabled:
        raise _make_error(
            ERR_PARAMETER_PARSING_DISABLED,
            "index was synced without parameter extraction "
            "(parameters_enabled = false). "
            "Re-run `epi sync --parameters` to enable.",
        )

    node = next((n for n in index.nodes if n.id == parameter_id), None)
    if node is None:
        raise _make_error(
            ERR_NODE_NOT_FOUND,
            f"node {parameter_id!r} not found in index. Parameter extraction "
            f"was enabled at sync time but no node with this ID exists — the "
            f"assignment may not be marked with `# epitaxy:param` or claimed "
            f"by any ADR's `decides:` frontmatter.",
        )
    if not isinstance(node, ParameterNode):
        raise _make_error(
            ERR_NOT_A_PARAMETER,
            f"node {parameter_id!r} is type {node.type!r}, not 'parameter'",
        )

    parameter = node
    chain_result = _build_decision_chain(index, parameter_id)

    return {
        "parameter": parameter.model_dump(by_alias=True, mode="json"),
        "current_value": parameter.value,
        "decision_chain": [
            a.model_dump(by_alias=True, mode="json") for a in chain_result.chain
        ],
        "parallel_heads": [
            a.model_dump(by_alias=True, mode="json")
            for a in chain_result.parallel_heads
        ],
        "notes": list(chain_result.notes),
        "provenance": {
            "parameter": parameter.provenance,
            "decisions": sorted(
                {
                    e.source
                    for e in index.edges
                    if (e.type == "decides" and e.to == parameter_id)
                    or (
                        e.type == "supersedes"
                        and e.from_ in {a.id for a in chain_result.chain}
                    )
                }
            ),
        },
    }


def por_lineage_impl(index: Index, asset_id: str) -> dict[str, Any]:
    """Documented stub in v0 — always errors. See docs/MCP.md §4."""
    _ = index, asset_id  # signature parity with future impl
    raise _make_error(
        ERR_ASSET_TYPE_NOT_SUPPORTED_IN_V0,
        "data_asset nodes are deferred to v1+; see SCHEMA §2.6. "
        "This tool is reserved in the v0 surface for forward compatibility.",
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