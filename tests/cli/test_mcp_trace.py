"""Tests for `por_trace` MCP tool implementation (PR4 commit 5).

Covers:
- Happy path: single-head decision chain, newest-first via supersedes
- Parallel heads: multiple ADRs decide same param, none superseded →
  TraceResult.parallel_heads populated (Codex round-1 Med-6)
- Cycle handling: supersedes cycle → chain truncates + notes entry
  (Codex round-1 Low-12)
- Error semantics gated on `index.config.parameters_enabled` (Codex
  round-1 High-4):
  - parameters_enabled=False → ParameterParsingDisabled (-32002)
  - parameters_enabled=True + param exists → TraceResult
  - parameters_enabled=True + param absent → NodeNotFound (-32001)
  - parameters_enabled=True + non-param-type ID → NotAParameter (-32003)

The fixtures here use ML-flavored examples (`rank`) for brevity but the
contract applies equally to domain-constrained values (physical
constraints, instrument settings, etc.) per
[[feedback_epitaxy_product_framing]].
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from mcp.shared.exceptions import McpError

from epitaxy.mcp_server.tools import (
    ERR_NODE_NOT_FOUND,
    ERR_NOT_A_PARAMETER,
    ERR_PARAMETER_PARSING_DISABLED,
    por_trace_impl,
)
from epitaxy.store.models import (
    AdrNode,
    Edge,
    Index,
    IndexConfig,
    IndexStats,
    ModuleNode,
    ParameterNode,
)


NOW = datetime(2026, 5, 16, tzinfo=timezone.utc)


def _make_index(
    *,
    parameters_enabled: bool,
    nodes: list,
    edges: list,
) -> Index:
    return Index(
        generated_at=NOW,
        generator="epitaxy 0.1.0",
        repo_root="/tmp/test",
        config=IndexConfig(parameters_enabled=parameters_enabled),
        stats=IndexStats(),
        nodes=nodes,
        edges=edges,
    )


def _param(name: str = "rank", value: str = "128", path: str = "m.py") -> ParameterNode:
    return ParameterNode(
        id=f"param:{path}::Cls.fit::{name}",
        module=f"module:{path}",
        scope="Cls.fit",
        name=name,
        value=value,
        line=3,
        provenance="ast+comment",
    )


def _adr(slug: str, status: str = "accepted") -> AdrNode:
    return AdrNode(
        id=f"adr:decisions/{slug}.md",
        path=f"decisions/{slug}.md",
        title=f"ADR {slug}",
        status=status,
        provenance="frontmatter+body",
    )


def _decides(adr_id: str, param_id: str) -> Edge:
    return Edge.model_validate({
        "from": adr_id,
        "to": param_id,
        "type": "decides",
        "source": "frontmatter:decides",
        "provenance": "frontmatter",
    })


def _supersedes(newer: str, older: str) -> Edge:
    return Edge.model_validate({
        "from": newer,
        "to": older,
        "type": "supersedes",
        "source": "frontmatter:supersedes",
        "provenance": "frontmatter",
    })


# --------------------------------------------------------------------------- #
# Error semantics (Codex round-1 High-4)                                      #
# --------------------------------------------------------------------------- #


def test_parameters_disabled_in_config_raises_parameter_parsing_disabled() -> None:
    """parameters_enabled=False → ParameterParsingDisabled regardless of whether
    parameter nodes happen to exist in the index."""
    param = _param()
    idx = _make_index(parameters_enabled=False, nodes=[param], edges=[])
    with pytest.raises(McpError) as exc_info:
        por_trace_impl(idx, param.id)
    assert exc_info.value.error.code == ERR_PARAMETER_PARSING_DISABLED
    assert "parameters_enabled = false" in exc_info.value.error.message
    assert "Re-run `epi sync --parameters`" in exc_info.value.error.message


def test_parameter_id_not_found_returns_node_not_found_not_disabled() -> None:
    """Codex round-1 High-4: parameters_enabled=True + param ID absent
    → NodeNotFound, NOT ParameterParsingDisabled. This is the regression
    target — PR1's stub returned ParameterParsingDisabled in this case."""
    idx = _make_index(parameters_enabled=True, nodes=[], edges=[])
    with pytest.raises(McpError) as exc_info:
        por_trace_impl(idx, "param:m.py::Cls.fit::ghost")
    assert exc_info.value.error.code == ERR_NODE_NOT_FOUND
    assert "not found in index" in exc_info.value.error.message


def test_non_parameter_node_returns_not_a_parameter() -> None:
    mod = ModuleNode(
        id="module:m.py", path="m.py", provenance="ast", extracted_at=NOW
    )
    idx = _make_index(parameters_enabled=True, nodes=[mod], edges=[])
    with pytest.raises(McpError) as exc_info:
        por_trace_impl(idx, "module:m.py")
    assert exc_info.value.error.code == ERR_NOT_A_PARAMETER
    assert "is type 'module'" in exc_info.value.error.message


# --------------------------------------------------------------------------- #
# Happy path: single-head decision chain                                      #
# --------------------------------------------------------------------------- #


def test_single_adr_decides_param_returns_trace_result() -> None:
    param = _param()
    adr = _adr("2026-04-rank")
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, adr],
        edges=[_decides(adr.id, param.id)],
    )
    result = por_trace_impl(idx, param.id)
    assert result["parameter"]["id"] == param.id
    assert result["current_value"] == "128"
    assert len(result["decision_chain"]) == 1
    assert result["decision_chain"][0]["id"] == adr.id
    assert result["parallel_heads"] == []
    assert result["notes"] == []
    assert "frontmatter:decides" in result["provenance"]["decisions"]


def test_chain_with_supersedes_ordered_newest_first() -> None:
    """ADR-04 supersedes ADR-02; both decide same param → chain is
    [ADR-04, ADR-02] (newest-first via supersedes)."""
    param = _param()
    new_adr = _adr("2026-04-rank")
    old_adr = _adr("2026-02-rank", status="superseded")
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, new_adr, old_adr],
        edges=[
            _decides(new_adr.id, param.id),
            _decides(old_adr.id, param.id),
            _supersedes(new_adr.id, old_adr.id),
        ],
    )
    result = por_trace_impl(idx, param.id)
    chain_ids = [a["id"] for a in result["decision_chain"]]
    assert chain_ids == [new_adr.id, old_adr.id]
    assert result["parallel_heads"] == []
    # Provenance includes the supersedes edge in the chain
    assert "frontmatter:supersedes" in result["provenance"]["decisions"]


# --------------------------------------------------------------------------- #
# Parallel heads (Codex round-1 Med-6)                                        #
# --------------------------------------------------------------------------- #


def test_parallel_heads_when_two_active_adrs_decide_same_param() -> None:
    """Two ADRs both decide same param, neither superseded → parallel_heads
    lists ALL active heads. decision_chain rooted at lex-first head."""
    param = _param()
    adr_a = _adr("2026-04-aaa")  # lex-first
    adr_b = _adr("2026-04-zzz")
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, adr_a, adr_b],
        edges=[
            _decides(adr_a.id, param.id),
            _decides(adr_b.id, param.id),
        ],
    )
    result = por_trace_impl(idx, param.id)
    # Primary chain rooted at lex-first head
    assert result["decision_chain"][0]["id"] == adr_a.id
    # ALL heads surfaced
    head_ids = sorted(h["id"] for h in result["parallel_heads"])
    assert head_ids == sorted([adr_a.id, adr_b.id])


def test_no_parallel_heads_when_one_superseded_by_other() -> None:
    """Two ADRs decide same param BUT one supersedes the other →
    parallel_heads stays empty (the chain is unambiguous)."""
    param = _param()
    new_adr = _adr("2026-04-rank")
    old_adr = _adr("2026-02-rank", status="superseded")
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, new_adr, old_adr],
        edges=[
            _decides(new_adr.id, param.id),
            _decides(old_adr.id, param.id),
            _supersedes(new_adr.id, old_adr.id),
        ],
    )
    result = por_trace_impl(idx, param.id)
    assert result["parallel_heads"] == []  # one is superseded, no ambiguity


# --------------------------------------------------------------------------- #
# Cycle handling (Codex round-1 Low-12)                                       #
# --------------------------------------------------------------------------- #


def test_cycle_in_supersedes_chain_truncates_with_note() -> None:
    """A→B→A supersedes cycle → chain walk truncates + notes records it."""
    param = _param()
    adr_a = _adr("2026-04-aaa")
    adr_b = _adr("2026-04-bbb")
    # ADR-A and ADR-B both decide same param.
    # Edges: A supersedes B; B supersedes A (a cycle).
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, adr_a, adr_b],
        edges=[
            _decides(adr_a.id, param.id),
            _decides(adr_b.id, param.id),
            _supersedes(adr_a.id, adr_b.id),
            _supersedes(adr_b.id, adr_a.id),
        ],
    )
    result = por_trace_impl(idx, param.id)
    # Both ADRs are superseded by the other → no head exists; defensive
    # fallback emits a "no head" note + uses lex-first relevant ADR.
    assert len(result["notes"]) >= 1
    note_text = " ".join(result["notes"])
    assert "no decision head" in note_text or "cycle" in note_text


def test_cycle_detection_walk_uses_visited_set() -> None:
    """3-cycle WITHIN the relevant set: A, B, C all decide same param;
    A→B, B→C, C→A (supersedes cycle). The walk should:
    - detect "no decision head" (every relevant ADR superseded by another
      relevant ADR) → defensive fallback note
    - then walk from lex-first head (A) → A→B→C→back to A → cycle note.
    """
    param = _param()
    adr_a = _adr("2026-04-aaa")
    adr_b = _adr("2026-04-bbb")
    adr_c = _adr("2026-04-ccc")
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, adr_a, adr_b, adr_c],
        edges=[
            _decides(adr_a.id, param.id),
            _decides(adr_b.id, param.id),
            _decides(adr_c.id, param.id),
            _supersedes(adr_a.id, adr_b.id),
            _supersedes(adr_b.id, adr_c.id),
            _supersedes(adr_c.id, adr_a.id),  # closes the cycle
        ],
    )
    result = por_trace_impl(idx, param.id)
    note_text = " ".join(result["notes"])
    # At least one of the two anomaly notes should fire (typically both)
    assert "no decision head" in note_text or "cycle" in note_text


def test_supersedes_outside_relevant_set_does_not_extend_chain() -> None:
    """If A decides the param + A supersedes B but B does NOT decide the
    param, the chain is just [A] — B isn't in this parameter's decision
    trail. Spec-conformance check per MCP §3."""
    param = _param()
    adr_a = _adr("2026-04")
    adr_b = _adr("2026-02-unrelated")
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, adr_a, adr_b],
        edges=[
            _decides(adr_a.id, param.id),
            # B doesn't decide param — only A does.
            _supersedes(adr_a.id, adr_b.id),
        ],
    )
    result = por_trace_impl(idx, param.id)
    chain_ids = [a["id"] for a in result["decision_chain"]]
    assert chain_ids == [adr_a.id]  # B not in chain (doesn't decide param)
    assert result["parallel_heads"] == []
    assert result["notes"] == []


def test_chain_skips_through_non_decider_to_reach_historical_decider() -> None:
    """Codex code-time Med-3 regression target. X decides param,
    X supersedes Y (Y doesn't decide), Y supersedes Z (Z decides).

    Z should appear in chain — it's a historical decider transitively
    reachable from X. Y skipped (doesn't decide). parallel_heads=[]
    because Z is transitively superseded by X (X → Y → Z).
    """
    param = _param()
    adr_x = _adr("2026-04-x")  # current decider
    adr_y = _adr("2026-03-y")  # intermediate non-decider
    adr_z = _adr("2026-02-z")  # historical decider
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, adr_x, adr_y, adr_z],
        edges=[
            _decides(adr_x.id, param.id),
            _decides(adr_z.id, param.id),
            _supersedes(adr_x.id, adr_y.id),
            _supersedes(adr_y.id, adr_z.id),
        ],
    )
    result = por_trace_impl(idx, param.id)
    chain_ids = [a["id"] for a in result["decision_chain"]]
    assert chain_ids == [adr_x.id, adr_z.id]  # Y skipped, Z appended
    assert result["parallel_heads"] == []  # X transitively supersedes Z
    assert result["notes"] == []


# --------------------------------------------------------------------------- #
# Edge cases                                                                  #
# --------------------------------------------------------------------------- #


def test_parameter_with_no_decides_returns_empty_chain() -> None:
    """A parameter exists but no ADR decides it — TraceResult is still
    returned (not an error); decision_chain is empty."""
    param = _param()
    idx = _make_index(parameters_enabled=True, nodes=[param], edges=[])
    result = por_trace_impl(idx, param.id)
    assert result["parameter"]["id"] == param.id
    assert result["decision_chain"] == []
    assert result["parallel_heads"] == []
    assert result["notes"] == []


def test_dangling_supersedes_target_stops_walk_silently() -> None:
    """Codex round-1 Med-6 spec: dangling supersedes target → walk stops
    without adding a note. The drift signal is at the edge level (the
    edge still in the graph)."""
    param = _param()
    adr = _adr("2026-04-rank")
    idx = _make_index(
        parameters_enabled=True,
        nodes=[param, adr],
        edges=[
            _decides(adr.id, param.id),
            _supersedes(adr.id, "adr:decisions/2026-02-ghost.md"),  # target absent
        ],
    )
    result = por_trace_impl(idx, param.id)
    # Chain is just the head; dangling target doesn't extend the chain.
    chain_ids = [a["id"] for a in result["decision_chain"]]
    assert chain_ids == [adr.id]
    # No note for dangling target (drift signal is at the edge level)
    assert result["notes"] == []


def test_current_value_preserves_verbatim_source() -> None:
    """MCP §3 contract: current_value is verbatim. Test scientific notation."""
    param = _param(name="lr", value="1e-3")
    idx = _make_index(parameters_enabled=True, nodes=[param], edges=[])
    result = por_trace_impl(idx, param.id)
    assert result["current_value"] == "1e-3"  # NOT "0.001"
