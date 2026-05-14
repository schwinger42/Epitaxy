"""Cross-pillar integration test — sync → mcp → serve on one shared index.

Locks the contract that all 3 v0 commands consume the same `.epitaxy/index.json`
without coordination. If any one pillar drifts on the file format, this fails
before the per-pillar smoke tests do.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
from typer.testing import CliRunner

from epitaxy.cli.app import app
from epitaxy.mcp_server.tools import build_server as build_mcp_server
from epitaxy.serve.app import build_handler


FIXTURE = Path(__file__).parent / "fixtures" / "sample_repo"
runner = CliRunner()


async def _por_explain(server, node_id: str):
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(server) as session:
        await session.initialize()
        return await session.call_tool("por_explain", {"node_id": node_id})


def test_all_three_pillars_consume_same_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pillar boundary 1: epi sync produces index.json
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    sync_result = runner.invoke(app, ["sync", "--quiet"])
    assert sync_result.exit_code == 0, sync_result.output
    index_path = dest / ".epitaxy" / "index.json"
    assert index_path.exists()

    # Pillar boundary 2: MCP server reads + serves the same index
    mcp_server = build_mcp_server(index_path)
    mcp_result = asyncio.run(
        _por_explain(mcp_server, "module:src/sample/model.py")
    )
    assert not mcp_result.isError, mcp_result
    payload = json.loads(mcp_result.content[0].text)  # type: ignore[attr-defined]
    assert payload["node"]["path"] == "src/sample/model.py"

    # Pillar boundary 3: HTTP serve reads + renders the same index
    httpd = HTTPServer(("127.0.0.1", 0), build_handler(index_path))
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        # The same module that por_explain returned must appear in the HTML view.
        assert "src/sample/model.py" in body
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
