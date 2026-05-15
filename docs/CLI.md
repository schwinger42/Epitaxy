# Epitaxy — `epi` CLI surface (v0)

> Design-first specification for Epitaxy's command-line surface and `pyproject.toml` configuration. Defines what flags exist, what config keys exist, and what UX `epi sync` / `epi serve` / `epi mcp` deliver — **before** parser code is written. Companion to [SCHEMA.md](SCHEMA.md) (data layer) and [ROADMAP.md](ROADMAP.md) (4-pillar architecture).

## 0. Why this doc exists

[SCHEMA.md §1.3](SCHEMA.md#13-parameter-extraction-is-opt-in) commits to `epi sync --parameters` as the opt-in flag for parameter extraction. [SCHEMA.md §5](SCHEMA.md#5-epitaxyindexjson--concrete-shape) commits to `pyproject.toml [tool.epitaxy]` as the config surface. Neither doc actually defines those surfaces. This doc does.

Specifically it commits to:

1. **Three v0 commands**: `epi sync` (generate index), `epi serve` (Pillar-3 drill-down site), `epi mcp` (Pillar-4 MCP server startup).
2. **Framework-agnostic spec**. typer / click / argparse is a parser-PR-time decision, not a design-time one. This doc documents the user-facing contract only.
3. **`[tool.epitaxy]` config schema** paired with the CLI flag set, governed by a single precedence rule: CLI flag > pyproject.toml > built-in default.
4. **No `epi init`** command in v0. `epi sync` self-bootstraps; a separate command for "create dir + print suggestion" is over-scoping.

MCP tool *schemas* (`por_explain` etc.) are NOT in this doc — see [MCP.md](MCP.md) (next design doc to ship).

## 1. Top-level UX

```
$ epi --help
Usage: epi [OPTIONS] COMMAND [ARGS]...

  Epitaxy — Process-of-Record explorer for ML codebases.

Options:
  --version    Show the version and exit.
  --help       Show this message and exit.

Commands:
  sync   Parse the repo and write .epitaxy/index.json.
  serve  Serve the Pillar-3 drill-down site (default :4321).
  mcp    Start the Pillar-4 MCP server.
```

Conventions across all subcommands:

- `--help` is supported on every subcommand.
- `--version` is top-level only; prints the version from `pyproject.toml [project].version` at install time.
- Every subcommand respects `--verbose / -v` and `--quiet / -q` for log level (default: human-friendly summary on stderr). The two flags are mutually exclusive.

## 2. `epi sync` — generate the intent index

Reads source files + ADRs + plans per `[tool.epitaxy]` config, writes a fresh `.epitaxy/index.json` per [SCHEMA.md](SCHEMA.md).

### Flags

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--parameters` | bool | `false` | Gate [SCHEMA §2.5](SCHEMA.md#25-parameter-opt-in---parameters) parameter extraction. Off by default — parameter nodes are noise without user-signal markers. |
| `--roots PATH` | string (repeatable) | from `[tool.epitaxy].roots` | Override config. Passing any `--roots` fully replaces the config value (does not merge). |
| `--output PATH` | string | from `[tool.epitaxy].output` | Where `index.json` is written. Parent dir auto-created. |
| `--verbose / -v` | bool | `false` | Per-file parse summary on stderr. |
| `--quiet / -q` | bool | `false` | Errors only. Mutually exclusive with `--verbose`. |

### Behavior contract

- **Full regenerate per run.** No incremental mode in v0. Watch-mode (`epi daemon`) is v3.
- **Idempotent.** Running `epi sync` twice with no source change produces byte-identical `index.json` modulo the `generated_at` timestamp.
- **Partial success allowed.** If one file fails to AST-parse, log the error and continue. Exit code 3, but `index.json` is still written with successful nodes.
- **Provenance preserved.** Every emitted node carries a `provenance` field per [SCHEMA §1.4](SCHEMA.md#14-provenance-on-every-node-and-edge). No untagged data.

### Bootstrap UX (first run in a fresh repo)

1. Create `.epitaxy/` if absent.
2. Write `index.json`.
3. Inspect `.gitignore`. If `.epitaxy/` is not listed, print:
   ```
   Tip: add `.epitaxy/` to your .gitignore (or just `.epitaxy/index.json` if you prefer to track-then-ignore).
   ```
4. Do **not** auto-edit `.gitignore`. Per [ROADMAP §6](ROADMAP.md#6-safety-design--llm-drafts-human-commits) LLM-drafts-human-commits, even one-line suggestions go through the user. Print only.

No `epi init` command in v0. Bootstrap is implicit and idempotent — re-running on an already-set-up repo is a no-op beyond regenerating `index.json`.

## 3. `epi serve` — Pillar-3 drill-down site

Starts a local web server rendering `.epitaxy/index.json` as a navigable drill-down. Static HTML + CSS + minimal JS islands per [ROADMAP §4](ROADMAP.md#4-design-principle--progressive-enhancement) Progressive Enhancement.

### Flags

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--port INT` | int | `4321` | Matches [ROADMAP §2.3](ROADMAP.md#pillar-3--consume) "`localhost:4321`". |
| `--host STR` | string | `127.0.0.1` | Loopback only by default; pass `0.0.0.0` for LAN access. |
| `--index PATH` | string | `.epitaxy/index.json` | Path to the index produced by `epi sync`. |
| `--no-open` | bool | `false` | Skip auto-launching the user's browser on startup. |
| `--verbose / -v` / `--quiet / -q` | bool | `false` | Same conventions as `epi sync`. |

### Behavior contract

- **Fails fast if `--index` missing.** Exit code 2 with `Run \`epi sync\` first` message. Does not implicitly invoke sync — coupling them would surprise scripted use.
- **Re-reads `index.json` on each request.** If `epi sync` is re-run in another shell, the next page load reflects the new data. No filesystem watch on source files — that's v3.
- **No write paths.** `epi serve` is read-only over the index. Drift-detection and proposal-writing live in v2+ commands.

## 4. `epi mcp` — Pillar-4 MCP server startup

Starts an MCP server exposing the v0 tool surface (`por_explain` / `por_trace` / `por_lineage`) backed by `.epitaxy/index.json`. **Tool schemas live in [MCP.md](MCP.md), not this doc.** This section documents only how to start the server.

### Subcommand structure

`epi mcp` reserves the namespace for future MCP-related subcommands (`epi mcp test`, `epi mcp list-tools` in v1+). v0 ships exactly one subcommand:

```
$ epi mcp serve [OPTIONS]
```

### Flags

| Flag | Type | Default | Notes |
|---|---|---|---|
| `--transport stdio\|http` | choice | `stdio` | `stdio` for local AI-agent integration (Claude Code / Codex / Cursor). `http` uses [MCP streamable-http](https://modelcontextprotocol.io/specification) for remote-host or multi-client scenarios. |
| `--host STR` | string | `127.0.0.1` | Bind host for `--transport http`. Loopback only by default — pass `0.0.0.0` for LAN exposure (emits an unauthenticated-exposure warning). Ignored when `--transport stdio`. |
| `--port INT` | int | `7321` | Bind port for `--transport http`. Arbitrary high port; no MCP-standard HTTP port exists. Ignored when `--transport stdio`. |
| `--allowed-origins STR` | string | (auto) | Comma-separated `Origin` allowlist for HTTP DNS-rebinding protection. Default auto-derives from `--host` + `--port` (loopback variants when host is `127.0.0.1`). Pass `""` to disable protection — NOT recommended; emits a stderr warning. |
| `--index PATH` | string | `.epitaxy/index.json` | Path to the index. |
| `--verbose / -v` / `--quiet / -q` | bool | `false` | Same conventions. |

### Behavior contract

- **Read-only.** v0 MCP tools never mutate the index or any repo file. Pillar 2a (in-session writes via MCP `prompts/`) lands in v1.
- **Fails fast if `--index` missing.** Exit code 2.
- **No long-lived state.** Each tool call re-reads `index.json` (sub-millisecond for v0 repo sizes). Caching is reserved for v2+ if real-world measurement justifies it.
- **HTTP transport: DNS-rebinding protection ON by default.** Per [MCP Streamable HTTP spec](https://modelcontextprotocol.io/specification), the server validates `Origin` and `Host` headers against the allowlist; invalid origins get HTTP 403. Disabling protection requires the explicit `--allowed-origins ""` opt-out and emits a stderr warning.
- **HTTP transport: errno-specific bind failures.** Port-in-use → exit 2 with port hint; permission-denied → exit 2 with "ports <1024 need root" hint; bad host → exit 2 with bind-address hint. Unknown `OSError` surfaces as-is (not masked as port-in-use).
- **HTTP transport: no auth, no TLS in v0.** Read-only doesn't mean low-sensitivity — the non-loopback warning enumerates the exposed surface (file paths, signatures, POR blocks, ADR/plan summaries, edges, provenance) so users can course-correct before leaking corporate intent metadata.

## 5. `pyproject.toml [tool.epitaxy]` config schema

All keys optional. Built-in defaults make `epi sync` work on a conventional Python repo (sources under `src/`, ADRs under `decisions/`, plans under `docs/plans/`) with zero config.

| Key | Type | Default | Notes |
|---|---|---|---|
| `roots` | list[str] | `["src/**/*.py"]` | Glob patterns; matches [SCHEMA §2.1](SCHEMA.md#21-module). |
| `adr_dir` | string | `"decisions/"` | Trailing slash required; matches [SCHEMA §2.3](SCHEMA.md#23-adr). |
| `plan_dir` | string | `"docs/plans/"` | Trailing slash required; matches [SCHEMA §2.4](SCHEMA.md#24-plan). |
| `parameters_enabled` | bool | `false` | Mirrors the `--parameters` flag default. |
| `output` | string | `".epitaxy/index.json"` | Repo-relative path; parent dir auto-created. |
| `excludes` | list[str] | `["**/test_*.py", "**/conftest.py"]` | Glob patterns to skip during parse. Applied after `roots` expansion. |

### Example config

```toml
[tool.epitaxy]
roots = ["src/**/*.py", "lib/**/*.py"]
adr_dir = "docs/adrs/"
parameters_enabled = true
excludes = ["**/test_*.py", "**/_legacy/**"]
```

A repo with this config:

- Parses Python under `src/` and `lib/`.
- Reads ADRs from `docs/adrs/` instead of the default `decisions/`.
- Emits parameter nodes by default (equivalent to passing `--parameters` every time).
- Skips legacy code.

## 6. Precedence

Single rule, no exceptions:

```
CLI flag  >  pyproject.toml [tool.epitaxy]  >  built-in default
```

Examples:

- `epi sync --parameters` with `parameters_enabled = false` in config → parameter extraction runs (CLI wins).
- `epi sync` (no flag) with `parameters_enabled = true` in config → parameter extraction runs (config wins over default).
- `epi sync --output /tmp/idx.json` overrides whatever `[tool.epitaxy].output` says.
- `--roots` is **repeatable** but **replaces** rather than **merges**: passing one or more `--roots` discards the entire `roots` list from the toml. If you want to add, edit the toml.

## 7. Exit codes

| Code | Meaning | When |
|---|---|---|
| `0` | Success | All operations completed without error. |
| `1` | Generic failure | Unexpected exception or runtime error not covered below. |
| `2` | Config / input error | Malformed `[tool.epitaxy]`, missing `--index` path, invalid `--transport` value. Nothing was written. |
| `3` | Parse error with partial output | One or more source files failed to parse; `index.json` was still written with successful nodes. Useful for CI — treat as "warn but proceed". |

## 8. Open items (deferred to v1+)

These commands are reserved in the `epi` namespace but not implemented in v0.

- **`epi bootstrap`** — Pillar 1 (proposes nested `CLAUDE.md` per subsystem). v1, gated on v0 traction.
- **`epi precommit`** — Pillar 2b git-hook entry point. v2.
- **`epi daemon`** — Pillar 2b filesystem watcher (always-on drift). v3.
- **`epi diff <old> <new>`** — compare two `.epitaxy/index.json` snapshots. Foundation for v2 drift detection.
- **Per-command `--config PATH`** — override `pyproject.toml` location. v0 assumes the toml lives at repo root.
- **`epi sync --format json`** — stream the index payload to stdout instead of a file. Deferred until a concrete scripting use case appears.

---

*Companion documents: [README.md](../README.md) · [ROADMAP.md](ROADMAP.md) · [SCHEMA.md](SCHEMA.md) · [CLAUDE.md](../CLAUDE.md)*
