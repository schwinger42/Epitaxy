# CLAUDE.md — Epitaxy

> Binding context for any Claude Code session working on Epitaxy itself. Read before writing code, opening PRs, or modifying public-facing docs.

## Project

**Epitaxy** — a 4-pillar framework for ML codebase intent. Process-of-Record explorer that captures the *why* behind ML pipelines, not just the *what*. MCP-native, solo-engineer scale.

Public repo: https://github.com/schwinger42/Epitaxy

## Status

🚀 **PR1 (tracer-bullet) merged 2026-05-15**, version `0.1.0a1`. `epi sync` + `epi serve` + `epi mcp serve` functional end-to-end on Python repos. SCHEMA subset only: `module`/`function` nodes + `depends-on` edges (2 of 4 default node types, 1 of 3 default edge types). ADR/plan parsing, POR docstring frontmatter, parameter extraction, MCP HTTP transport all **fail-fast** (not silent no-op) and tracked for PR2/PR4. 48 tests, 87% coverage.

**Active: PR2** — ADR + plan markdown parsing + POR docstring frontmatter. Closes the doc-parsing gap so v0 SCHEMA-default-conformance is real. After PR2: version `0.1.0a1` → `0.1.0`, classifier `1 - Planning` → `2 - Pre-Alpha`.

Detail: [docs/ROADMAP.md](docs/ROADMAP.md) · specs: [docs/SCHEMA.md](docs/SCHEMA.md) · [docs/CLI.md](docs/CLI.md) · [docs/MCP.md](docs/MCP.md).

## Layout (post-PR1)

```
~/PycharmProjects/Epitaxy/
├── src/epitaxy/
│   ├── parser/      # Python AST → POR data layer (PR1: module/function/depends-on; PR2: ADR/plan/POR/references/supersedes)
│   ├── store/       # pydantic models + .epitaxy/index.json read/write
│   ├── serve/       # `epi serve` drill-down (PR1: hash-based anchors, no client JS)
│   ├── mcp_server/  # `epi mcp serve` — MCP tools por_explain / por_trace / por_lineage (PR1: stdio only; HTTP fail-fast tracked for PR3)
│   └── cli/         # `epi sync`, `epi serve`, `epi mcp serve`
├── docs/
│   ├── ROADMAP.md   # v0 → v3 phasing
│   ├── SCHEMA.md    # node/edge types + inline POR structure
│   ├── CLI.md       # `epi *` command contracts + exit codes
│   └── MCP.md       # MCP tool contracts + transport wire format
├── tests/           # 48 tests, 87% coverage as of PR1
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

## Current focus

**Active: PR2 — doc-parsing.** PR1 shipped the Python AST → index pipeline + drill-down + MCP stdio. PR2 closes the SCHEMA gap:

- Adds `adr` + `plan` node types ([SCHEMA §2.3 / §2.4](docs/SCHEMA.md#2-node-types))
- Adds `references` + `supersedes` edge types ([SCHEMA §3](docs/SCHEMA.md#3-edge-types))
- Adds POR YAML frontmatter recognition in module/function docstrings ([SCHEMA §4](docs/SCHEMA.md#4-inline-por-structure-optional))
- Bumps `0.1.0a1` → `0.1.0` + `Development Status :: 2 - Pre-Alpha`

Out of PR2 scope: HTTP MCP transport (PR3 alongside Progressive-Enhancement HTML for `epi serve`); parameter extraction + `ParameterNode` + `decides` edge (PR4); `data_asset` + real `por_lineage` (v1+).

Pillar 3 (Consume) + Pillar 4 (Query), read-only on user repo. ~1 focused day for PR2 per [ROADMAP §3](docs/ROADMAP.md#3-phasing-v0--v3).

## Detail reference

- [docs/ROADMAP.md](docs/ROADMAP.md) — 4 pillars in depth, v0 → v3 phasing with what-ships-per-phase, LLM-drafts safety design, positioning vs platform tools (pre-commit / dependabot / dbt docs), explicit non-goals, open design questions.
- [README.md](README.md) — public landing page, honest v0 scope.
- [pyproject.toml](pyproject.toml) — `epitaxy v0.1.0a1`, `requires-python = ">=3.10"`, deps: `typer`, `pydantic`, `mcp`, `tomli` (py<3.11). PR2 adds `pyyaml`.
