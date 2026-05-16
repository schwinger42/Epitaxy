"""End-to-end `epi sync --parameters` tests using the PR4 sample_repo fixture.

Exercises the full pipeline:
- parser/markdown collects decides_claimed_param_ids from ADR frontmatter
- parser/python emits ParameterNodes via both signals (SCHEMA §2.5 OR clause)
- populate_decided_by populates ParameterNode.decided_by from decides edges
- por_trace returns full TraceResult shape with supersedes-chain ordering

Fixture (in tests/fixtures/sample_repo) exercises all 4 SCHEMA §2.5 paths:
- Comment-marked: rank, DEFAULT_RANK, sample_temperature_K
- ADR-only (no comment, listed in 2026-04's decides:): learning_rate
- Composite (comment AND ADR-listed): rank (in BOTH 2026-04 and 2026-02 decides)
- Negative: cleanup_threshold (no marker + not ADR-claimed) → no node

Domain mix per [[feedback_epitaxy_product_framing]]: `sample_temperature_K`
sits alongside ML-flavored `rank` to demonstrate the broader framing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from epitaxy.cli.app import app
from epitaxy.mcp_server.tools import por_trace_impl
from epitaxy.store import read_index


FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_repo"
runner = CliRunner()


@pytest.fixture
def sample_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    return dest


@pytest.fixture
def synced_with_params(sample_repo: Path) -> Path:
    result = runner.invoke(app, ["sync", "--parameters", "--quiet"])
    assert result.exit_code == 0, result.output
    return sample_repo / ".epitaxy" / "index.json"


@pytest.fixture
def synced_without_params(sample_repo: Path) -> Path:
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0, result.output
    return sample_repo / ".epitaxy" / "index.json"


# --------------------------------------------------------------------------- #
# Gating regression (Codex round-1 High-1)                                    #
# --------------------------------------------------------------------------- #


def test_zero_parameters_when_flag_omitted(synced_without_params: Path) -> None:
    """Plain `epi sync` (no --parameters) → 0 parameter nodes, 0 decides edges."""
    payload = json.loads(synced_without_params.read_text())
    params = [n for n in payload["nodes"] if n["type"] == "parameter"]
    assert params == []
    decides = [e for e in payload["edges"] if e["type"] == "decides"]
    assert decides == []
    assert payload["config"]["parameters_enabled"] is False


# --------------------------------------------------------------------------- #
# All 4 SCHEMA §2.5 OR-clause paths emit                                      #
# --------------------------------------------------------------------------- #


def test_all_four_parameter_paths_emit_with_correct_provenance(
    synced_with_params: Path,
) -> None:
    """Each fixture parameter has a specific expected provenance value."""
    payload = json.loads(synced_with_params.read_text())
    params = {n["name"]: n for n in payload["nodes"] if n["type"] == "parameter"}

    # Comment-only signals
    assert params["DEFAULT_RANK"]["provenance"] == "ast+comment"
    assert params["sample_temperature_K"]["provenance"] == "ast+comment"

    # ADR-only signal (no comment in source)
    assert params["learning_rate"]["provenance"] == "adr-frontmatter"

    # Composite: rank is both commented AND in 2026-04's decides list
    assert params["rank"]["provenance"] == "ast+comment+adr-frontmatter"

    # Negative case
    assert "cleanup_threshold" not in params


def test_parameter_values_preserved_verbatim(synced_with_params: Path) -> None:
    """`learning_rate = 0.001` source-text preservation."""
    payload = json.loads(synced_with_params.read_text())
    params = {n["name"]: n for n in payload["nodes"] if n["type"] == "parameter"}
    assert params["rank"]["value"] == "128"
    assert params["DEFAULT_RANK"]["value"] == "64"
    assert params["sample_temperature_K"]["value"] == "77"
    assert params["learning_rate"]["value"] == "0.001"


# --------------------------------------------------------------------------- #
# decides edges + decided_by post-pass                                        #
# --------------------------------------------------------------------------- #


def test_decides_edges_emitted_with_correct_targets(
    synced_with_params: Path,
) -> None:
    payload = json.loads(synced_with_params.read_text())
    decides = [e for e in payload["edges"] if e["type"] == "decides"]

    # Expected:
    #  2026-04 decides {rank, learning_rate}
    #  2026-02 decides {rank}
    pairs = {(e["from"], e["to"]) for e in decides}
    assert pairs == {
        (
            "adr:decisions/2026-04-rank-dim.md",
            "param:src/sample/model.py::M.fit::rank",
        ),
        (
            "adr:decisions/2026-04-rank-dim.md",
            "param:src/sample/model.py::M.fit::learning_rate",
        ),
        (
            "adr:decisions/2026-02-rank-baseline.md",
            "param:src/sample/model.py::M.fit::rank",
        ),
    }


def test_decided_by_populated_on_rank_with_both_adrs(
    synced_with_params: Path,
) -> None:
    """rank is decided by BOTH 2026-04 and 2026-02 (the latter superseded);
    decided_by lists both, sorted (Codex round-1 Low-11)."""
    payload = json.loads(synced_with_params.read_text())
    rank = next(
        n
        for n in payload["nodes"]
        if n.get("name") == "rank" and n["type"] == "parameter"
    )
    assert rank["decided_by"] == [
        "adr:decisions/2026-02-rank-baseline.md",
        "adr:decisions/2026-04-rank-dim.md",
    ]


def test_decided_by_populated_on_learning_rate(synced_with_params: Path) -> None:
    payload = json.loads(synced_with_params.read_text())
    lr = next(
        n
        for n in payload["nodes"]
        if n.get("name") == "learning_rate" and n["type"] == "parameter"
    )
    assert lr["decided_by"] == ["adr:decisions/2026-04-rank-dim.md"]


def test_decided_by_none_when_no_adr_decides(synced_with_params: Path) -> None:
    """sample_temperature_K is marked with `# epitaxy:param` but no ADR
    decides it → decided_by stays None."""
    payload = json.loads(synced_with_params.read_text())
    temp = next(
        n
        for n in payload["nodes"]
        if n.get("name") == "sample_temperature_K" and n["type"] == "parameter"
    )
    assert temp.get("decided_by") is None


# --------------------------------------------------------------------------- #
# AdrNode.decides field populated from frontmatter                            #
# --------------------------------------------------------------------------- #


def test_adr_decides_field_populated(synced_with_params: Path) -> None:
    """AdrNode.decides reflects the frontmatter list verbatim."""
    payload = json.loads(synced_with_params.read_text())
    adrs = {n["id"]: n for n in payload["nodes"] if n["type"] == "adr"}

    adr_04 = adrs["adr:decisions/2026-04-rank-dim.md"]
    assert sorted(adr_04["decides"]) == sorted([
        "param:src/sample/model.py::M.fit::rank",
        "param:src/sample/model.py::M.fit::learning_rate",
    ])

    adr_02 = adrs["adr:decisions/2026-02-rank-baseline.md"]
    assert adr_02["decides"] == ["param:src/sample/model.py::M.fit::rank"]


def test_adr_decides_field_populated_even_without_parameters_flag(
    synced_without_params: Path,
) -> None:
    """SCHEMA §2.3: AdrNode.decides is data — populated regardless of
    parameters_enabled. Only edge emission is gated."""
    payload = json.loads(synced_without_params.read_text())
    adrs = {n["id"]: n for n in payload["nodes"] if n["type"] == "adr"}
    adr_04 = adrs["adr:decisions/2026-04-rank-dim.md"]
    # Field populated even though decides edges are not emitted
    assert adr_04["decides"] is not None
    assert "param:src/sample/model.py::M.fit::rank" in adr_04["decides"]
    # ... but no decides edges in the index
    assert [e for e in payload["edges"] if e["type"] == "decides"] == []


# --------------------------------------------------------------------------- #
# por_trace end-to-end against the synced index                               #
# --------------------------------------------------------------------------- #


def test_por_trace_returns_chain_with_supersedes_ordering(
    synced_with_params: Path,
) -> None:
    """rank is decided by 2026-04 (active head) which supersedes 2026-02.
    por_trace returns chain = [2026-04, 2026-02] newest-first."""
    index = read_index(synced_with_params)
    result = por_trace_impl(index, "param:src/sample/model.py::M.fit::rank")
    chain_ids = [a["id"] for a in result["decision_chain"]]
    assert chain_ids == [
        "adr:decisions/2026-04-rank-dim.md",
        "adr:decisions/2026-02-rank-baseline.md",
    ]
    # Single head; no ambiguity
    assert result["parallel_heads"] == []
    assert result["notes"] == []
    # Current value preserved verbatim
    assert result["current_value"] == "128"


def test_por_trace_learning_rate_single_adr_chain(
    synced_with_params: Path,
) -> None:
    """learning_rate is decided only by 2026-04 (not 2026-02) → chain = [2026-04]."""
    index = read_index(synced_with_params)
    result = por_trace_impl(
        index, "param:src/sample/model.py::M.fit::learning_rate"
    )
    chain_ids = [a["id"] for a in result["decision_chain"]]
    assert chain_ids == ["adr:decisions/2026-04-rank-dim.md"]
    assert result["current_value"] == "0.001"


def test_por_trace_unmarked_assignment_node_not_found(
    synced_with_params: Path,
) -> None:
    """cleanup_threshold is in source but unmarked + not ADR-claimed
    → no ParameterNode → NodeNotFound (NOT ParameterParsingDisabled,
    since extraction WAS enabled)."""
    from mcp.shared.exceptions import McpError

    from epitaxy.mcp_server.tools import ERR_NODE_NOT_FOUND

    index = read_index(synced_with_params)
    with pytest.raises(McpError) as exc:
        por_trace_impl(
            index, "param:src/sample/model.py::M.cleanup::cleanup_threshold"
        )
    assert exc.value.error.code == ERR_NODE_NOT_FOUND


def test_por_trace_disabled_when_no_parameters_flag(
    synced_without_params: Path,
) -> None:
    """epi sync without --parameters → config.parameters_enabled=False
    → por_trace returns ParameterParsingDisabled."""
    from mcp.shared.exceptions import McpError

    from epitaxy.mcp_server.tools import ERR_PARAMETER_PARSING_DISABLED

    index = read_index(synced_without_params)
    with pytest.raises(McpError) as exc:
        por_trace_impl(index, "param:src/sample/model.py::M.fit::rank")
    assert exc.value.error.code == ERR_PARAMETER_PARSING_DISABLED


# --------------------------------------------------------------------------- #
# Stats + index integrity                                                     #
# --------------------------------------------------------------------------- #


def test_index_stats_parameters_count_matches_nodes(
    synced_with_params: Path,
) -> None:
    payload = json.loads(synced_with_params.read_text())
    actual_count = sum(1 for n in payload["nodes"] if n["type"] == "parameter")
    assert payload["stats"]["parameters"] == actual_count == 4
