"""MCP server tests — tool unit tests + in-process server smoke test.

In-process testing uses `mcp.shared.memory.create_connected_server_and_client_session`
which spins the FastMCP server + a ClientSession in the same process via async
in-memory queues — faster + less flaky than subprocess stdio.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest
from mcp.shared.exceptions import McpError
from typer.testing import CliRunner

from epitaxy.cli.app import app
from epitaxy.mcp_server.tools import (
    ERR_ASSET_TYPE_NOT_SUPPORTED_IN_V0,
    ERR_NODE_NOT_FOUND,
    ERR_PARAMETER_PARSING_DISABLED,
    build_server,
    por_explain_impl,
    por_lineage_impl,
    por_trace_impl,
)
from epitaxy.store import (
    Index,
    IndexConfig,
    IndexStats,
    ModuleNode,
    read_index,
    write_index,
)


FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_repo"
runner = CliRunner()


# --------------------------------------------------------------------------- #
# Unit tests of the plain-function implementations                            #
# --------------------------------------------------------------------------- #


def _tiny_index() -> Index:
    now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    return Index(
        generated_at=now,
        generator="test",
        repo_root="/tmp",
        config=IndexConfig(),
        stats=IndexStats(modules=1),
        nodes=[
            ModuleNode(id="module:m.py", path="m.py", provenance="ast", extracted_at=now),
        ],
        edges=[],
    )


def test_por_explain_returns_node_dump_for_known_id():
    index = _tiny_index()
    result = por_explain_impl(index, "module:m.py")
    assert result["node"]["id"] == "module:m.py"
    assert result["node"]["type"] == "module"
    assert result["incident_edges"] == []
    assert result["provenance_summary"]["node_source"] == "ast"


def test_por_explain_raises_node_not_found_for_unknown_id():
    with pytest.raises(McpError) as excinfo:
        por_explain_impl(_tiny_index(), "module:does-not-exist.py")
    assert excinfo.value.error.code == ERR_NODE_NOT_FOUND
    # Codex review Medium-1: machine-readable code prefix lets clients
    # dispatch even though MCP wire format only surfaces text content.
    assert "[code:-32001]" in excinfo.value.error.message


def test_por_trace_raises_parameter_parsing_disabled_when_no_parameters():
    """Dominant PR1 case — no parameter nodes in index."""
    with pytest.raises(McpError) as excinfo:
        por_trace_impl(_tiny_index(), "param:foo.py::x::y")
    assert excinfo.value.error.code == ERR_PARAMETER_PARSING_DISABLED
    assert "epi sync --parameters" in excinfo.value.error.message
    assert "PR4" in excinfo.value.error.message


def test_por_lineage_always_raises_asset_type_not_supported():
    with pytest.raises(McpError) as excinfo:
        por_lineage_impl(_tiny_index(), "data:bigquery:foo.bar")
    assert excinfo.value.error.code == ERR_ASSET_TYPE_NOT_SUPPORTED_IN_V0
    assert "v1+" in excinfo.value.error.message


# --------------------------------------------------------------------------- #
# In-process integration test — wire FastMCP server + ClientSession           #
# --------------------------------------------------------------------------- #


@pytest.fixture
def synced_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy sample_repo into tmp_path, run epi sync, return path to index.json."""
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0, result.output
    return dest / ".epitaxy" / "index.json"


async def _call_tool(server, tool_name: str, args: dict):
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool(tool_name, args)


def test_mcp_server_por_explain_returns_module_node(synced_index: Path) -> None:
    server = build_server(synced_index)
    result = asyncio.run(
        _call_tool(server, "por_explain", {"node_id": "module:src/sample/data.py"})
    )

    assert not result.isError, result
    # FastMCP serializes dict-returning tools as JSON text content.
    text = result.content[0].text  # type: ignore[attr-defined]
    payload = json.loads(text)
    assert payload["node"]["id"] == "module:src/sample/data.py"
    assert payload["node"]["type"] == "module"


def test_mcp_server_por_explain_reports_node_not_found(synced_index: Path) -> None:
    server = build_server(synced_index)
    result = asyncio.run(
        _call_tool(server, "por_explain", {"node_id": "module:does-not-exist.py"})
    )
    assert result.isError
    # Error message surfaces through the tool result's text content.
    text = result.content[0].text  # type: ignore[attr-defined]
    assert "not found" in text.lower()
    # Machine-readable code prefix per MCP.md §6 — lets clients dispatch
    # without parsing free-form text. Codex review Medium-1.
    assert "[code:-32001]" in text


def test_mcp_server_por_trace_reports_parameter_parsing_disabled(
    synced_index: Path,
) -> None:
    server = build_server(synced_index)
    result = asyncio.run(
        _call_tool(server, "por_trace", {"parameter_id": "param:x"})
    )
    assert result.isError
    text = result.content[0].text  # type: ignore[attr-defined]
    assert "epi sync --parameters" in text
    assert "[code:-32002]" in text


def test_mcp_server_por_lineage_reports_v0_stub(synced_index: Path) -> None:
    server = build_server(synced_index)
    result = asyncio.run(
        _call_tool(server, "por_lineage", {"asset_id": "data:bigquery:x.y.z"})
    )
    assert result.isError
    text = result.content[0].text  # type: ignore[attr-defined]
    assert "v1+" in text
    assert "[code:-32004]" in text


# --------------------------------------------------------------------------- #
# CLI fail-fast tests for unimplemented transport                             #
# --------------------------------------------------------------------------- #


def test_mcp_serve_transport_http_fails_fast(synced_index: Path) -> None:
    """`--transport http` must exit 2, NOT silently fall back to stdio."""
    result = runner.invoke(app, ["mcp", "serve", "--transport", "http"])
    assert result.exit_code == 2
    assert "reserved for PR3" in result.output
    assert "not implemented in v0" in result.output


def test_mcp_serve_unknown_transport_fails_fast(synced_index: Path) -> None:
    result = runner.invoke(app, ["mcp", "serve", "--transport", "websocket"])
    assert result.exit_code == 2
    assert "unknown --transport" in result.output


def test_mcp_serve_missing_index_fails_fast(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If no index.json exists, fail fast with 'run epi sync first'."""
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["mcp", "serve"])
    assert result.exit_code == 2
    assert "Run `epi sync`" in result.output


def test_round_trip_index_through_mcp_smoke(synced_index: Path) -> None:
    """Sanity: index that the MCP server reads is itself valid per the store schema."""
    idx = read_index(synced_index)
    assert idx.stats.modules >= 4  # sample_repo: __init__, data, model, boundary
    # Round-trip via store (independent of MCP wiring)
    tmp_out = synced_index.parent / "round-trip.json"
    write_index(idx, tmp_out)
    assert read_index(tmp_out) == idx