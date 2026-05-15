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

from epitaxy.cli.app import (
    _configure_http_transport,
    _host_to_url_authority,
    app,
)
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
# Code-time Low-1: IPv6 bracket helper                                        #
# --------------------------------------------------------------------------- #


def test_host_to_url_authority_ipv4() -> None:
    assert _host_to_url_authority("127.0.0.1", 7321) == "127.0.0.1:7321"


def test_host_to_url_authority_hostname() -> None:
    assert _host_to_url_authority("localhost", 7321) == "localhost:7321"


def test_host_to_url_authority_ipv6_brackets() -> None:
    """Code-time Low-1: raw IPv6 host needs brackets in URL/Host syntax."""
    assert _host_to_url_authority("::1", 7321) == "[::1]:7321"
    assert _host_to_url_authority("2001:db8::1", 8080) == "[2001:db8::1]:8080"


def test_host_to_url_authority_already_bracketed_passes_through() -> None:
    """If caller already pre-bracketed, don't double-bracket."""
    assert _host_to_url_authority("[::1]", 7321) == "[::1]:7321"


def test_ipv6_host_produces_bracketed_allowed_hosts_and_origins(server) -> None:
    """IPv6 --host ::1 must emit `[::1]:port` everywhere URL/Host syntax applies."""
    _configure_http_transport(
        server, host="::1", port=7321, allowed_origins_arg=None
    )
    ts = server.settings.transport_security
    assert "[::1]:7321" in ts.allowed_hosts
    assert "http://[::1]:7321" in ts.allowed_origins
    # Negative: bare ::1:7321 must not appear
    assert "::1:7321" not in ts.allowed_hosts
    assert "http://::1:7321" not in ts.allowed_origins


# --------------------------------------------------------------------------- #
# Code-time Med-2: --allowed-hosts flag for LAN exposure                      #
# --------------------------------------------------------------------------- #


def test_custom_allowed_hosts_replaces_auto_derive(server) -> None:
    _configure_http_transport(
        server,
        host="0.0.0.0",
        port=7321,
        allowed_origins_arg="http://192.168.1.5:7321",
        allowed_hosts_arg="192.168.1.5:7321,server.local:7321",
    )
    ts = server.settings.transport_security
    assert ts.allowed_hosts == ["192.168.1.5:7321", "server.local:7321"]
    assert ts.allowed_origins == ["http://192.168.1.5:7321"]


def test_custom_allowed_hosts_whitespace_stripped(server) -> None:
    _configure_http_transport(
        server,
        host="0.0.0.0",
        port=7321,
        allowed_origins_arg="http://a.com",
        allowed_hosts_arg=" a.com:7321 , b.com:7321 ",
    )
    ts = server.settings.transport_security
    assert ts.allowed_hosts == ["a.com:7321", "b.com:7321"]


def test_non_loopback_bind_without_allowed_hosts_emits_warning(
    server, capsys
) -> None:
    """Code-time Med-2: --host 0.0.0.0 with auto-derived hosts blocks LAN."""
    _configure_http_transport(
        server, host="0.0.0.0", port=7321, allowed_origins_arg=None
    )
    err = capsys.readouterr().err
    assert "--host 0.0.0.0 binds all interfaces" in err
    assert "--allowed-hosts" in err
    assert "HTTP 421" in err


def test_non_loopback_bind_with_explicit_allowed_hosts_no_lan_warning(
    server, capsys
) -> None:
    """User-provided --allowed-hosts means they took responsibility."""
    _configure_http_transport(
        server,
        host="0.0.0.0",
        port=7321,
        allowed_origins_arg="http://192.168.1.5:7321",
        allowed_hosts_arg="192.168.1.5:7321",
    )
    err = capsys.readouterr().err
    # The general non-loopback exposure warning still fires (HTTP is
    # unauthenticated), but the LAN-host-allowlist-gap warning should NOT.
    assert "unauthenticated" in err
    assert "blocks LAN" not in err
    assert "--host 0.0.0.0 binds all interfaces" not in err


# --------------------------------------------------------------------------- #
# CLI errno-specific bind failure paths                                       #
# --------------------------------------------------------------------------- #


def _patch_socket_bind_to_raise(monkeypatch, exc: BaseException) -> None:
    """Make socket.socket.bind() raise `exc` — drives _probe_bind_or_exit.

    Code-time Codex review caught: the OLD tests monkey-patched FastMCP.run
    which made the assertions pass but never hit the real dispatch path
    (uvicorn catches OSError internally and sys.exit(1)s before our
    `except OSError` could fire). New strategy: probe-the-bind helper
    handles errno dispatch BEFORE server.run is called, so the right
    target to mock is `socket.socket.bind`.
    """
    real_bind = socket.socket.bind

    def fake_bind(self, *args, **kwargs):  # noqa: ARG001
        raise exc

    monkeypatch.setattr(socket.socket, "bind", fake_bind)
    # Keep a reference so the original is restorable (monkeypatch does this
    # automatically, but kept for diagnostic clarity).
    _ = real_bind


def test_eaddrinuse_exits_2_with_port_hint_via_real_bind(
    synced_index: Path, tmp_path: Path
) -> None:
    """Integration: actually bind a port + verify the second bind exits 2.

    This is the real-world path Codex round-2 Med-3 was concerned about —
    earlier mock-based tests asserted a code path that uvicorn would have
    bypassed. Holds the port in-process so it's tied to test scope.
    """
    # Bind a socket on a free port so we own it for the duration of the test.
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    held_port = holder.getsockname()[1]
    try:
        result = runner.invoke(
            app,
            ["mcp", "serve", "--transport", "http", "--port", str(held_port)],
        )
        assert result.exit_code == 2, result.output
        assert "port is in use" in result.output
        assert "Choose a different --port" in result.output
    finally:
        holder.close()


def test_eacces_exits_2_with_root_hint(
    synced_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_socket_bind_to_raise(
        monkeypatch, OSError(errno.EACCES, "Permission denied")
    )
    result = runner.invoke(
        app, ["mcp", "serve", "--transport", "http", "--port", "80"]
    )
    assert result.exit_code == 2
    assert "permission denied" in result.output
    assert "<1024" in result.output


def test_eaddrnotavail_exits_2_with_bind_hint(
    synced_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_socket_bind_to_raise(
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
    """Bad hostname → getaddrinfo raises gaierror → clean exit 2."""

    def fake_getaddrinfo(*args, **kwargs):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    result = runner.invoke(
        app, ["mcp", "serve", "--transport", "http", "--host", "not-a-real-host.invalid"]
    )
    assert result.exit_code == 2
    assert "cannot resolve --host" in result.output


def test_unknown_oserror_does_not_mask_as_port_in_use(
    synced_index: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex round-2 Med-3 lock: bare OSError without known errno surfaces
    as-is — does NOT get masked as 'port in use'."""
    _patch_socket_bind_to_raise(
        monkeypatch, OSError(errno.EINTR, "Interrupted")
    )
    result = runner.invoke(
        app, ["mcp", "serve", "--transport", "http"]
    )
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
