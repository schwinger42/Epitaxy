"""`epi` CLI entry point.

See docs/CLI.md for the user-facing contract. PR1 ships `sync` only;
`mcp` lands in commit 5 and `serve` in commit 6 (both on this PR1 branch).
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer

from epitaxy import __version__
from epitaxy.parser import extract_references, parse_markdown, parse_repo
from epitaxy.store import Index, IndexConfig, IndexStats, write_index


app = typer.Typer(
    no_args_is_help=True,
    help="Epitaxy — Process-of-Record explorer for ML codebases.",
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"epi {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """Top-level entry — see `epi sync --help` etc. for subcommand usage."""


def _load_tomllib():
    if sys.version_info >= (3, 11):
        import tomllib

        return tomllib
    import tomli  # type: ignore[import-not-found]

    return tomli


def _load_config(repo_root: Path) -> IndexConfig:
    """Read `[tool.epitaxy]` from `pyproject.toml`; fall back to defaults."""
    toml_path = repo_root / "pyproject.toml"
    if not toml_path.exists():
        return IndexConfig()

    tomllib = _load_tomllib()
    try:
        data = tomllib.loads(toml_path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        typer.echo(f"error: malformed pyproject.toml: {e}", err=True)
        raise typer.Exit(2) from e

    tool_cfg = data.get("tool", {}).get("epitaxy", {})
    if not tool_cfg:
        return IndexConfig()
    try:
        return IndexConfig.model_validate(tool_cfg)
    except Exception as e:
        typer.echo(f"error: invalid [tool.epitaxy] config: {e}", err=True)
        raise typer.Exit(2) from e


def _resolve_files(repo_root: Path, roots: list[str], excludes: list[str]) -> list[Path]:
    found: set[Path] = set()
    for pat in roots:
        for p in repo_root.glob(pat):
            if p.is_file() and p.suffix == ".py":
                found.add(p.resolve())
    excluded: set[Path] = set()
    for pat in excludes:
        for p in repo_root.glob(pat):
            excluded.add(p.resolve())
    return sorted(found - excluded)


def _package_roots_from_globs(roots: list[str]) -> list[str]:
    """Extract package-root prefixes from glob patterns.

    `src/**/*.py` → `src/` (PEP src-layout: `src/foo/bar.py` imports as `foo.bar`)
    `lib/**/*.py` → `lib/`
    `**/*.py` → no prefix
    `pkg/*.py` → `pkg/`

    Without this, src-layout repos like Epitaxy itself produce zero
    function-call edges because `from foo.bar import baz` doesn't resolve to
    a node keyed under `src.foo.bar` (Codex review High-2).
    """
    prefixes: list[str] = []
    glob_chars = ("*", "?", "[")
    for pattern in roots:
        first = len(pattern)
        for c in glob_chars:
            i = pattern.find(c)
            if 0 <= i < first:
                first = i
        prefix = pattern[:first]
        slash = prefix.rfind("/")
        prefix = prefix[: slash + 1] if slash >= 0 else ""
        if prefix:
            prefixes.append(prefix)
    return list(dict.fromkeys(prefixes))


def _gitignore_lists_epitaxy(repo_root: Path) -> bool:
    g = repo_root / ".gitignore"
    if not g.exists():
        return False
    for raw in g.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s in (".epitaxy/", ".epitaxy", ".epitaxy/index.json"):
            return True
    return False


@app.command()
def sync(
    parameters: bool = typer.Option(
        False,
        "--parameters",
        help="Enable parameter extraction (not implemented in PR1 — fails fast).",
    ),
    roots: Optional[list[str]] = typer.Option(
        None,
        "--roots",
        help="Override [tool.epitaxy].roots (repeatable; replaces, not merges).",
    ),
    output: Optional[Path] = typer.Option(
        None,
        "--output",
        help="Path to write the index JSON (default: .epitaxy/index.json).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Per-file parse summary."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Errors only."),
) -> None:
    """Parse the repo and write `.epitaxy/index.json`."""
    if verbose and quiet:
        typer.echo("error: --verbose and --quiet are mutually exclusive.", err=True)
        raise typer.Exit(2)

    repo_root = Path.cwd()
    config = _load_config(repo_root)
    if roots:
        config = config.model_copy(update={"roots": list(roots)})
    if parameters:
        config = config.model_copy(update={"parameters_enabled": True})

    # Fail-fast AFTER config + CLI flag merge — both `--parameters` AND
    # `[tool.epitaxy] parameters_enabled = true` route to the same effective
    # value and must trip the same error. Codex review High-1 caught that
    # checking only the CLI flag reintroduces silent no-op through config.
    if config.parameters_enabled:
        typer.echo(
            "error: parameter extraction is not implemented in this build "
            "(PR1 tracer-bullet). Tracking in PR4.",
            err=True,
        )
        raise typer.Exit(2)

    py_files = _resolve_files(repo_root, config.roots, config.excludes)
    package_roots = _package_roots_from_globs(config.roots)

    if verbose:
        typer.echo(
            f"parsing {len(py_files)} file(s) from roots={config.roots} "
            f"(package_roots={package_roots})",
            err=True,
        )

    py_nodes, py_edges, py_errors, py_bodies = parse_repo(
        repo_root, py_files, package_roots=package_roots
    )
    md_nodes, md_edges, md_errors, md_bodies = parse_markdown(
        repo_root, adr_dir=config.adr_dir, plan_dir=config.plan_dir
    )

    nodes = [*py_nodes, *md_nodes]
    edges = [*py_edges, *md_edges]
    parse_errors = [*py_errors, *md_errors]

    # Final pass: references edges from markdown links in docstring + ADR/plan
    # bodies, resolved against the union of all emitted nodes. Per Codex
    # round-1 High-1, this MUST run last so docstring→ADR / ADR→plan links
    # resolve. Per Codex round-2 High-2, the target index includes adr/plan
    # node IDs, not just module/function.
    ref_edges = extract_references(
        repo_root, nodes, [*py_bodies, *md_bodies]
    )
    edges.extend(ref_edges)

    for err in parse_errors:
        typer.echo(f"warning: failed to parse {err.path}: {err.reason}", err=True)

    stats = IndexStats(
        modules=sum(1 for n in nodes if n.type == "module"),
        functions=sum(1 for n in nodes if n.type == "function"),
        adrs=sum(1 for n in nodes if n.type == "adr"),
        plans=sum(1 for n in nodes if n.type == "plan"),
        edges=len(edges),
    )

    index = Index(
        generated_at=datetime.now(timezone.utc),
        generator=f"epitaxy {__version__}",
        repo_root=str(repo_root.resolve()),
        config=config,
        stats=stats,
        nodes=nodes,
        edges=edges,
    )

    # Precedence per CLI.md §6: CLI flag > [tool.epitaxy].output > built-in default
    if output is not None:
        out_path = output
    else:
        configured = Path(config.output)
        out_path = configured if configured.is_absolute() else (repo_root / configured)
    write_index(index, out_path)

    if not _gitignore_lists_epitaxy(repo_root):
        typer.echo(
            "tip: add `.epitaxy/` to your .gitignore "
            "(or `.epitaxy/index.json` to track-but-ignore).",
            err=True,
        )

    if not quiet:
        typer.echo(
            f"wrote {out_path} "
            f"({stats.modules} modules, {stats.functions} functions, "
            f"{stats.adrs} ADRs, {stats.plans} plans, {stats.edges} edges)",
            err=True,
        )

    # CLI.md §7: exit 3 when one or more files failed to parse but a partial
    # index was still written. Lets CI treat this as 'warn but proceed'.
    if parse_errors:
        raise typer.Exit(3)


@app.command()
def serve(
    port: int = typer.Option(4321, "--port", help="HTTP port (default 4321)."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host (default loopback)."),
    index: Optional[Path] = typer.Option(
        None,
        "--index",
        help="Path to .epitaxy/index.json (default: cwd/.epitaxy/index.json).",
    ),
    no_open: bool = typer.Option(False, "--no-open", help="Skip auto-launching the browser."),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Serve the Pillar-3 drill-down site (ugly-but-functional PR1 build)."""
    from http.server import HTTPServer

    from epitaxy.serve.app import build_handler

    if verbose and quiet:
        typer.echo("error: --verbose and --quiet are mutually exclusive.", err=True)
        raise typer.Exit(2)

    index_path = index if index else (Path.cwd() / ".epitaxy" / "index.json")
    if not index_path.exists():
        typer.echo(
            f"error: index not found at {index_path}. Run `epi sync` first.",
            err=True,
        )
        raise typer.Exit(2)

    handler = build_handler(index_path)
    httpd = HTTPServer((host, port), handler)

    url = f"http://{host}:{port}/"
    if not quiet:
        typer.echo(f"serving {index_path} at {url} (Ctrl-C to stop)", err=True)

    if not no_open:
        import webbrowser

        webbrowser.open(url)

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        if not quiet:
            typer.echo("\nshutting down", err=True)
    finally:
        httpd.server_close()


mcp_app = typer.Typer(
    no_args_is_help=True,
    help="Pillar-4 MCP server commands.",
)
app.add_typer(mcp_app, name="mcp")


@mcp_app.command("serve")
def mcp_serve(
    transport: str = typer.Option(
        "stdio",
        "--transport",
        help="Transport: 'stdio' (default) or 'http' (MCP streamable-http per spec).",
    ),
    port: int = typer.Option(
        7321,
        "--port",
        help="Bind port for HTTP transport. Ignored when --transport stdio.",
    ),
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help=(
            "Bind host for HTTP transport. Default 127.0.0.1 (loopback only). "
            "Pass 0.0.0.0 for LAN exposure — emits an unauthenticated-exposure warning."
        ),
    ),
    allowed_origins: Optional[str] = typer.Option(
        None,
        "--allowed-origins",
        help=(
            "Comma-separated Origin allowlist for HTTP transport (DNS-rebinding "
            "protection per MCP spec). Default: auto-derive from --host and --port. "
            "Pass empty string ('') to disable protection — NOT recommended."
        ),
    ),
    allowed_hosts: Optional[str] = typer.Option(
        None,
        "--allowed-hosts",
        help=(
            "Comma-separated Host header allowlist for HTTP transport. Default: "
            "auto-derive from --host + --port (loopback variants when --host is "
            "127.0.0.1). Pass `host1:port1,host2:port2` for LAN exposure scenarios "
            "where clients send a different Host than the bind address."
        ),
    ),
    index: Optional[Path] = typer.Option(
        None,
        "--index",
        help="Path to .epitaxy/index.json (default: cwd/.epitaxy/index.json).",
    ),
) -> None:
    """Start the Pillar-4 MCP server (3 tools: por_explain / por_trace / por_lineage)."""
    if transport not in ("stdio", "http"):
        typer.echo(
            f"error: unknown --transport value {transport!r}. Expected 'stdio' or 'http'.",
            err=True,
        )
        raise typer.Exit(2)

    index_path = index if index else (Path.cwd() / ".epitaxy" / "index.json")
    if not index_path.exists():
        typer.echo(
            f"error: index not found at {index_path}. Run `epi sync` first.",
            err=True,
        )
        raise typer.Exit(2)

    from epitaxy.mcp_server import build_server

    server = build_server(index_path)

    if transport == "stdio":
        server.run(transport="stdio")
        return

    _run_http_transport(
        server,
        host=host,
        port=port,
        allowed_origins_arg=allowed_origins,
        allowed_hosts_arg=allowed_hosts,
    )


_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


def _host_to_url_authority(host: str, port: int) -> str:
    """Format `host:port` for URL/Host syntax, bracketing IPv6 literals.

    Code-time Codex review Low-1: `--host ::1` previously produced
    malformed `::1:7321`. HTTP/URL grammar requires brackets for IPv6
    literals — `[::1]:7321`.

    Heuristic: a host containing `:` and not already starting with `[`
    is treated as an IPv6 literal. Hostnames + IPv4 pass through.
    """
    if ":" in host and not host.startswith("["):
        return f"[{host}]:{port}"
    return f"{host}:{port}"


def _configure_http_transport(
    server,
    *,
    host: str,
    port: int,
    allowed_origins_arg: Optional[str],
    allowed_hosts_arg: Optional[str] = None,
) -> None:
    """Configure FastMCP `server.settings` for streamable-http transport.

    Pure configuration step — does NOT call `server.run()`. Extracted from
    `_run_http_transport` so unit tests can exercise allowlist derivation,
    warning emission, and TransportSecuritySettings shape without binding
    a socket. Codex round-2 High-1 + MCP Streamable HTTP spec; Codex
    code-time Med-2 added `--allowed-hosts` so LAN clients with Host
    headers carrying their actual IP can reach the server when
    `--host 0.0.0.0` is in use.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    authority = _host_to_url_authority(host, port)

    if allowed_origins_arg is None:
        origins = [f"http://{authority}"]
        if host == "127.0.0.1":
            origins.extend([f"http://localhost:{port}", f"http://[::1]:{port}"])
        protection_enabled = True
    elif allowed_origins_arg == "":
        origins = []
        protection_enabled = False
        typer.echo(
            "warning: --allowed-origins '' DISABLES DNS-rebinding protection — any "
            "origin can reach the MCP server. Pass --allowed-origins URL1,URL2 to "
            "restrict cross-origin access instead.",
            err=True,
        )
    else:
        origins = [o.strip() for o in allowed_origins_arg.split(",") if o.strip()]
        protection_enabled = True

    if host not in _LOOPBACK_HOSTS:
        typer.echo(
            "warning: MCP HTTP transport is unauthenticated and exposes read-only "
            "repo intent data over the network: module file paths, function "
            "signatures + POR blocks, ADR/plan summaries + frontmatter, "
            "depends-on/references/supersedes edges with line numbers, and "
            "provenance metadata. 'Read-only' means no mutation risk but does NOT "
            "imply low sensitivity. Use --host 127.0.0.1 (the default) for "
            "local-only access, or pass --allowed-origins to restrict cross-origin "
            "access explicitly.",
            err=True,
        )
        # Code-time Codex Med-2: LAN clients send `Host: <actual-ip>:<port>`,
        # not `Host: 0.0.0.0:<port>`. The auto-derived allowed_hosts entry
        # won't match. Surface the gap if the user hasn't passed
        # --allowed-hosts to expand the allowlist explicitly.
        if allowed_hosts_arg is None and protection_enabled:
            typer.echo(
                "warning: --host 0.0.0.0 binds all interfaces, but the auto-derived "
                "Host-header allowlist only includes the bind address. LAN clients "
                "with Host headers carrying their actual IP/hostname will be rejected "
                "(HTTP 421). Pass --allowed-hosts 'ip1:port,hostname:port' to expand "
                "the allowlist, or use --host <specific-ip> instead of 0.0.0.0.",
                err=True,
            )

    server.settings.host = host
    server.settings.port = port

    # `allowed_hosts` matches the request's Host header which includes
    # the port. For loopback, include the IPv4 + IPv6 synonyms so curl
    # with any loopback-named Host works.
    if not protection_enabled:
        allowed_hosts: list[str] = []
    elif allowed_hosts_arg is None or allowed_hosts_arg == "":
        # auto-derive
        allowed_hosts = [authority]
        if host == "127.0.0.1":
            allowed_hosts.extend([f"localhost:{port}", f"[::1]:{port}"])
    else:
        allowed_hosts = [
            h.strip() for h in allowed_hosts_arg.split(",") if h.strip()
        ]
    server.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=protection_enabled,
        allowed_hosts=allowed_hosts,
        allowed_origins=origins,
    )


def _probe_bind_or_exit(host: str, port: int) -> None:
    """Try a brief bind on host:port so errno-specific UX surfaces BEFORE
    `server.run()` swallows it.

    Code-time Codex review caught: `FastMCP.run("streamable-http")` calls
    uvicorn, which catches OSError inside `Server.startup()` and emits its
    own `sys.exit(1)` path. The post-`server.run()` `except OSError` we
    used to have was dead code in production — only the monkey-patched
    tests ever hit it.

    Strategy: open a probe socket, try to bind, immediately release. If
    the bind fails, dispatch errno-specific exit(2). If it succeeds, the
    port was free at probe time; uvicorn picks it up next.

    Race-condition limitation: between probe-release and uvicorn-bind,
    another process could grab the port. Uncommon enough to accept — that
    path falls through to uvicorn's own sys.exit(1) with a stderr log.
    """
    import errno
    import socket

    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        typer.echo(f"error: cannot resolve --host {host!r}: {e}", err=True)
        raise typer.Exit(2) from e

    if not infos:
        typer.echo(
            f"error: no addresses for --host {host!r}. "
            f"Use --host 127.0.0.1 or 0.0.0.0.",
            err=True,
        )
        raise typer.Exit(2)

    # Probe the first resolved address (uvicorn's typical pick). If the
    # probe succeeds, uvicorn binds the same address next.
    family, sock_type, proto, _, sockaddr = infos[0]
    sock = socket.socket(family, sock_type, proto)
    try:
        sock.bind(sockaddr)
    except OSError as e:
        if e.errno == errno.EADDRINUSE:
            typer.echo(
                f"error: cannot bind {host}:{port}; port is in use. "
                f"Choose a different --port.",
                err=True,
            )
            raise typer.Exit(2) from e
        if e.errno == errno.EACCES:
            typer.echo(
                f"error: permission denied binding {host}:{port} (ports <1024 "
                f"typically require root). Choose --port >=1024.",
                err=True,
            )
            raise typer.Exit(2) from e
        if e.errno == errno.EADDRNOTAVAIL:
            typer.echo(
                f"error: host {host!r} is not a valid bind address on this "
                f"machine. Use --host 127.0.0.1 or 0.0.0.0.",
                err=True,
            )
            raise typer.Exit(2) from e
        # Unexpected OSError — re-raise so it surfaces, don't mask as
        # "port in use" (Codex round-2 Med-3 caught the lump-everything
        # risk).
        raise
    finally:
        sock.close()


def _run_http_transport(
    server,
    *,
    host: str,
    port: int,
    allowed_origins_arg: Optional[str],
    allowed_hosts_arg: Optional[str] = None,
) -> None:
    """Configure + run FastMCP streamable-http.

    Errno-specific bind-failure UX is handled BEFORE server.run() via
    `_probe_bind_or_exit` (the post-`server.run()` handler was dead code
    per code-time Codex review).
    """
    _configure_http_transport(
        server,
        host=host,
        port=port,
        allowed_origins_arg=allowed_origins_arg,
        allowed_hosts_arg=allowed_hosts_arg,
    )
    _probe_bind_or_exit(host, port)
    server.run(transport="streamable-http")


if __name__ == "__main__":  # pragma: no cover
    app()