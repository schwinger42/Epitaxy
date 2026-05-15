"""Tests for `epi mcp serve --transport http` (PR3 [A]).

Covers:
- DNS-rebinding protection via TransportSecuritySettings (Codex round-2 High-1)
- Non-loopback exposure warning (Codex round-1 Med-1 / round-2 Med-5)
- --allowed-origins flag: default auto-derive, custom list, empty-string opt-out
- Errno-specific bind-failure UX (Codex round-1 Low-2 / round-2 Med-3)
- ASGI-level happy path: valid Origin reaches MCP layer; invalid Origin → 403
"""

from __future__ import annotations

import errno
import socket
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from epitaxy.cli.app import _configure_http_transport, app
from epitaxy.mcp_server import build_server


runner = CliRunner()


# --------------------------------------------------------------------------- #
# Fixtures                                                                    #
# --------------------------------------------------------------------------- #


@pytest.fixture
def synced_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Minimal `.epitaxy/index.json` in a tmp repo + chdir."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text('"""doc."""\n', encoding="utf-8")
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0, result.output
    return tmp_path / ".epitaxy" / "index.json"


@pytest.fixture
def server(synced_index: Path):
    return build_server(synced_index)


# --------------------------------------------------------------------------- #
# _configure_http_transport — pure-config unit tests                          #
# --------------------------------------------------------------------------- #


def test_default_host_derives_loopback_origins(server) -> None:
    _configure_http_transport(
        server, host="127.0.0.1", port=7321, allowed_origins_arg=None
    )
    ts = server.settings.transport_security
    assert ts.enable_dns_rebinding_protection is True
    assert "http://127.0.0.1:7321" in ts.allowed_origins
    assert "http://localhost:7321" in ts.allowed_origins
    assert "http://[::1]:7321" in ts.allowed_origins
    # Host header allowlist includes port + loopback synonyms
    assert "127.0.0.1:7321" in ts.allowed_hosts
    assert "localhost:7321" in ts.allowed_hosts
    assert server.settings.host == "127.0.0.1"
    assert server.settings.port == 7321


def test_non_default_host_uses_only_that_host(server) -> None:
    _configure_http_transport(
        server, host="10.0.0.5", port=7321, allowed_origins_arg=None
    )
    ts = server.settings.transport_security
    assert ts.allowed_origins == ["http://10.0.0.5:7321"]
    assert ts.allowed_hosts == ["10.0.0.5:7321"]


def test_non_loopback_host_emits_warning(server, capsys) -> None:
    _configure_http_transport(
        server, host="0.0.0.0", port=7321, allowed_origins_arg=None
    )
    err = capsys.readouterr().err
    assert "MCP HTTP transport is unauthenticated" in err
    assert "POR blocks" in err  # exposed-surface enumeration per round-2 Med-5
    assert "Read-only" in err
    assert "--host 127.0.0.1" in err


def test_loopback_host_emits_no_warning(server, capsys) -> None:
    _configure_http_transport(
        server, host="127.0.0.1", port=7321, allowed_origins_arg=None
    )
    err = capsys.readouterr().err
    assert "unauthenticated" not in err


def test_custom_allowed_origins_overrides_auto_derive(server) -> None:
    _configure_http_transport(
        server,
        host="127.0.0.1",
        port=7321,
        allowed_origins_arg="https://agent.example.com,https://other.example.com",
    )
    ts = server.settings.transport_security
    assert ts.allowed_origins == [
        "https://agent.example.com",
        "https://other.example.com",
    ]
    assert ts.enable_dns_rebinding_protection is True


def test_custom_origins_with_whitespace_stripped(server) -> None:
    _configure_http_transport(
        server,
        host="127.0.0.1",
        port=7321,
        allowed_origins_arg=" https://a.com , https://b.com ",
    )
    ts = server.settings.transport_security
    assert ts.allowed_origins == ["https://a.com", "https://b.com"]


def test_empty_allowed_origins_disables_protection_with_warning(
    server, capsys
) -> None:
    _configure_http_transport(
        server, host="127.0.0.1", port=7321, allowed_origins_arg=""
    )
    ts = server.settings.transport_security
    assert ts.enable_dns_rebinding_protection is False
    assert ts.allowed_origins == []
    err = capsys.readouterr().err
    assert "DISABLES DNS-rebinding protection" in err


# --------------------------------------------------------------------------- #
# CLI errno-specific bind failure paths                                       #
# --------------------------------------------------------------------------- #


def _patch_run_to_raise(monkeypatch, exc: BaseException) -> None:
    """Make FastMCP.run() raise `exc` instead of binding."""
    from mcp.server.fastmcp import FastMCP

    def fake_run(self, transport: str = "stdio", **_kwargs) -> None:  # noqa: ARG001
        raise exc

    monkeypatch.setattr(FastMCP, "run", fake_run)


def test_eaddrinuse_exits_2_with_port_hint(
    synced_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run_to_raise(monkeypatch, OSError(errno.EADDRINUSE, "Address in use"))
    result = runner.invoke(
        app, ["mcp", "serve", "--transport", "http", "--port", "7321"]
    )
    assert result.exit_code == 2
    assert "port is in use" in result.output
    assert "Choose a different --port" in result.output


def test_eacces_exits_2_with_root_hint(
    synced_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run_to_raise(monkeypatch, OSError(errno.EACCES, "Permission denied"))
    result = runner.invoke(
        app, ["mcp", "serve", "--transport", "http", "--port", "80"]
    )
    assert result.exit_code == 2
    assert "permission denied" in result.output
    assert "<1024" in result.output


def test_eaddrnotavail_exits_2_with_bind_hint(
    synced_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run_to_raise(
        monkeypatch, OSError(errno.EADDRNOTAVAIL, "Cannot assign requested address")
    )
    result = runner.invoke(
        app, ["mcp", "serve", "--transport", "http", "--host", "203.0.113.1"]
    )
    assert result.exit_code == 2
    assert "not a valid bind address" in result.output


def test_gaierror_exits_2_with_resolve_hint(
    synced_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_run_to_raise(
        monkeypatch, socket.gaierror(-2, "Name or service not known")
    )
    result = runner.invoke(
        app, ["mcp", "serve", "--transport", "http", "--host", "not-a-real-host.invalid"]
    )
    assert result.exit_code == 2
    assert "cannot resolve --host" in result.output


def test_unknown_oserror_does_not_mask_as_port_in_use(
    synced_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round-2 Med-3: bare OSError without known errno surfaces as-is."""
    _patch_run_to_raise(monkeypatch, OSError(errno.EINTR, "Interrupted"))
    result = runner.invoke(
        app, ["mcp", "serve", "--transport", "http"]
    )
    # Non-handled errno re-raises rather than masking as port-in-use
    assert result.exit_code != 0
    assert "port is in use" not in result.output


# --------------------------------------------------------------------------- #
# ASGI-level smoke: DNS-rebinding-protection verified end-to-end              #
# --------------------------------------------------------------------------- #


def test_invalid_origin_returns_403(server) -> None:
    """Cross-origin request from non-allowlisted Origin must be blocked.

    Uses starlette TestClient so FastMCP's lifespan-bound task group
    initializes (httpx.ASGITransport alone doesn't trigger lifespan).
    """
    from starlette.testclient import TestClient

    _configure_http_transport(
        server, host="127.0.0.1", port=7321, allowed_origins_arg=None
    )
    asgi_app = server.streamable_http_app()
    with TestClient(asgi_app, base_url="http://127.0.0.1:7321") as client:
        response = client.post(
            "/mcp",
            headers={
                "Origin": "http://evil.example.com",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": "127.0.0.1:7321",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    assert response.status_code == 403


def test_valid_origin_passes_security_layer(server) -> None:
    """Allowlisted Origin must NOT be 403 (may be 400/406 from MCP protocol layer)."""
    from starlette.testclient import TestClient

    _configure_http_transport(
        server, host="127.0.0.1", port=7321, allowed_origins_arg=None
    )
    asgi_app = server.streamable_http_app()
    with TestClient(asgi_app, base_url="http://127.0.0.1:7321") as client:
        response = client.post(
            "/mcp",
            headers={
                "Origin": "http://127.0.0.1:7321",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": "127.0.0.1:7321",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
    # 200/400/406 all signal "transport security passed"; 403 would mean it didn't
    assert response.status_code != 403
