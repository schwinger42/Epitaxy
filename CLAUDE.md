# CLAUDE.md — Epitaxy

> Binding context for any Claude Code session working on Epitaxy itself. Read before writing code, opening PRs, or modifying public-facing docs.

## Project

**Epitaxy** — a 4-pillar Process-of-Record framework for AI-assisted applied science and ML engineering. It captures the *why* behind research / engineering projects, not just the *what*. MCP-native, solo-practitioner scale.

Public repo: https://github.com/schwinger42/Epitaxy

## Product thesis

Epitaxy's core user is not only a software engineer with a messy repo. It is also the domain expert, applied scientist, ML engineer, or industrial R&D practitioner using Claude Code / Codex to connect AI, ML, LLMs, simulations, instruments, data pipelines, and reports into a long-running project.

The failure mode Epitaxy exists to prevent is **loss of research / engineering intent**:

- An agent can read code but still miss physical constraints, chemical assumptions, mathematical boundaries, instrument settings, sample provenance, validation rules, downstream claims, and "why this was done this way."
- Standard SWE discipline (specs, plans, TDD, worktrees, code review, agent skills) helps execution, but does not by itself preserve domain-constrained reasoning.
- Epitaxy is the Process-of-Record / truth layer that AI agents query before changing code, data workflows, parameters, protocols, docs, or claims.

Do not narrow the product framing back to "documentation generator", "token saver", "organized markdown", "code search", or "Claude Code memory." Those are implementation conveniences. The product is a scalable POR layer for AI-assisted applied science and ML engineering.

## Status

🚀 **PR1 + PR2 + PR3 merged** through 2026-05-16. Version `0.1.0`, `Development Status :: 2 - Pre-Alpha`. **156 tests pass + 1 xfail, 91% coverage.**

- **PR1 tracer-bullet** (`54542c4`): `epi sync` + `epi serve` + `epi mcp serve` — Python AST → `module`/`function` nodes + `depends-on` edges; stdio MCP transport only.
- **PR2 doc-parsing** (`5228c07`): adds `adr`+`plan` nodes + `references`+`supersedes` edges + POR YAML docstring frontmatter. Default-emit subset complete (4/4 node types, 3/3 edge types).
- **PR3 HTTP transport + Progressive-Enhancement HTML** (`a7a47ce`): `epi mcp serve --transport http` with DNS-rebinding protection (`TransportSecuritySettings`, `--host` + `--allowed-origins` + `--allowed-hosts` flags); semantic HTML for `epi serve` with `<details>` drill-down, inline CSS, ~8-line auto-open JS island, dark-mode via `prefers-color-scheme`. SCHEMA §6 dangling-target rule honored end-to-end (PR2 parser + PR3 renderer).

**Shipped: PR4 — `--parameters` + `ParameterNode` + `decides` edge + real `por_trace`** (`9508651`, 2026-05-17). Closed the opt-in `--parameters` fail-fast + the `por_trace` MCP stub. v0.1.0 SCHEMA-default-emit surface feature-complete at that point.

**Active: v0.2-PR1 — `follows` edge type** (this PR). First sub-PR of the v0.2 systems-engineer-dashboard milestone (spec merged PR #5). Closes the dogfood-discovered gap where POR `decisions:` was stored as data but emitted zero graph edges. After v0.2-PR1: 5 emitted node types + 5 edge types (`depends-on` / `references` / `supersedes` / `decides` / `follows`); only `data_asset` + `decision` remain v1+ reserved.

Detail: [docs/ROADMAP.md](docs/ROADMAP.md) · specs: [docs/SCHEMA.md](docs/SCHEMA.md) · [docs/CLI.md](docs/CLI.md) · [docs/MCP.md](docs/MCP.md).

## Layout (post-PR3)

```
~/PycharmProjects/Epitaxy/
├── src/epitaxy/
│   ├── parser/      # python.py + markdown.py + por.py + refs.py (PR4 wires --parameters extraction + decides edges)
│   ├── store/       # pydantic models + .epitaxy/index.json read/write (PR4 adds ParameterNode + AdrNode.decides + 'decides' Edge.type)
│   ├── serve/       # `epi serve` semantic HTML drill-down + inline CSS + auto-open JS island (PR3)
│   ├── mcp_server/  # `epi mcp serve` — por_explain / por_trace / por_lineage; stdio + streamable-http (PR3); PR4 wires real por_trace
│   └── cli/         # `epi sync`, `epi serve`, `epi mcp serve` with --transport stdio|http + --host + --allowed-origins + --allowed-hosts (PR3)
├── docs/
│   ├── ROADMAP.md   # v0 → v3 phasing
│   ├── SCHEMA.md    # node/edge types + inline POR structure (PR4 amends §2.5 + §6)
│   ├── CLI.md       # `epi *` command contracts + exit codes
│   └── MCP.md       # MCP tool contracts + transport wire format (PR4 amends §3 TraceResult + Errors)
├── tests/           # 156 tests, 91% coverage as of PR3
├── CLAUDE.md        # this file (project memory)
├── README.md
└── pyproject.toml
```

Subpackages scaffolded by PR1 — extend in place per PR scope, don't reshape directory layout absent a strong reason.

## Core principles (binding for any session writing Epitaxy code)

1. **LLM-drafts-human-commits.** Epitaxy's own development follows the safety design it preaches for user codebases. Drift proposals, generated refactors, and any LLM-authored content go to a PR for review. Never auto-merge. Sacred ops (ADR edits, root CLAUDE.md changes, deletions in `decisions/`) always require explicit human approval. See [docs/ROADMAP.md](docs/ROADMAP.md) §4 for the sacred-vs-safe classification.

2. **Eat your own dog food.** Every module Epitaxy ships must have a POR docstring (once v0 ships the POR schema) and appear in nested CLAUDE.md. If Epitaxy can't sustain its own codebase, it can't credibly sell intent-layer maintenance to anyone else. Non-negotiable.

3. **No `Co-Authored-By: Claude` trailers in commits.** Repo is the author's interview portfolio (Nvidia / Anthropic / ByteDance audience). AI co-author tags signal heavy AI dependency. Default: no trailer. Exception: the first commit (`ddb09a5`) has a trailer — kept because rewriting day-1 history wasn't worth the cost. All subsequent commits: no trailer.

4. **README must match shipped reality.** Anti-pattern: promising v2/v3 always-on dev infrastructure in README when current code can't deliver it. Forward-looking framing lives in `docs/ROADMAP.md`, never in README. Two-layer narrative: README front door = honest scope; ROADMAP back door = depth.

5. **Execution methodology is not the moat.** Tools such as Superpowers can make an agent behave like a disciplined junior SWE (brainstorm, spec, TDD, subagents, review). Epitaxy's moat is different: preserving and querying the domain Process-of-Record so agents do not violate project intent, physical constraints, data provenance, validation boundaries, or downstream claims while iterating.

## Current focus

**Active: PR4 — `--parameters` extraction + `ParameterNode` + `decides` + real `por_trace`.** Final v0 piece. After PR4, the v0 surface is genuinely feature-complete and a real v0.2 / v1 conversation (Pillar 1 Bootstrap, Pillar 2a in-session MCP prompts) can start.

What PR4 adds:

- **`ParameterNode` model** ([SCHEMA §2.5](docs/SCHEMA.md#25-parameter-opt-in---parameters)) — emits when `epi sync --parameters` OR `[tool.epitaxy].parameters_enabled = true`, via EITHER `# epitaxy:param` comment on the assignment line OR inclusion in an ADR's `decides:` frontmatter list (the SCHEMA §2.5 OR clause). Composite provenance `"ast+comment+adr-frontmatter"` when both signals are present.
- **`decides` edge type** ([SCHEMA §3](docs/SCHEMA.md#3-edge-types)) — ADRs → parameters; gated on `parameters_enabled`. Dangling-target rule (same as `supersedes`): edge emitted even when target parameter is absent (drift signal). SCHEMA §6 amendment ships in this PR to explicitly bless it.
- **`AdrNode.decides` field** ([SCHEMA §2.3](docs/SCHEMA.md#23-adr)) — populated from frontmatter regardless of `parameters_enabled` (the field is data; edge emission is gated).
- **Real `por_trace` MCP tool** ([MCP §3](docs/MCP.md)) — returns `TraceResult` with `decision_chain` (newest-first via supersedes) + new `parallel_heads` field (when multiple active heads decide the same parameter) + new `notes` field (cycle-truncation + no-head warnings). MCP.md §3 schema + Errors table updated.
- **CLI fail-fast removal** — `epi sync --parameters` becomes first-class. PR1-tracer-bullet error strings in `cli/app.py` and `mcp_server/tools.py` cleaned.
- **3 PR2 lock-guard tests flip** — currently assert `ParameterNode` / `decides` / `AdrNode.decides` ABSENCE; flip to assert PRESENCE post-PR4. Become forward-compat regression guards.

Out of PR4 scope: `data_asset` node + `derives-from` edges, `decision` atomic sub-ADR nodes — both v1+.

Plan: [~/.claude/plans/plan-quartz-cipher.md](~/.claude/plans/plan-quartz-cipher.md) — Codex-reviewed twice (12 + 7 = 19 findings folded; 3 user-product decisions resolved toward spec-faithful interpretation). ~1 focused day per [ROADMAP §3](docs/ROADMAP.md#3-phasing-v0--v3).

## Detail reference

- [docs/ROADMAP.md](docs/ROADMAP.md) — 4 pillars in depth, v0 → v3 phasing with what-ships-per-phase, LLM-drafts safety design, positioning vs platform tools (pre-commit / dependabot / dbt docs), explicit non-goals, open design questions.
- [README.md](README.md) — public landing page, honest v0 scope.
- [pyproject.toml](pyproject.toml) — `epitaxy v0.1.0`, `requires-python = ">=3.10"`, deps: `typer`, `pydantic`, `mcp`, `pyyaml`, `tomli` (py<3.11). Dev deps include `httpx`, `pytest-asyncio`, `beautifulsoup4` (added in PR3). PR4 adds no new deps.
