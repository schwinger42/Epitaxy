# CLAUDE.md — Epitaxy

> Binding context for any Claude Code session working on Epitaxy itself. Read before writing code, opening PRs, or modifying public-facing docs.

## Project

**Epitaxy** — a 4-pillar framework for ML codebase intent. Process-of-Record explorer that captures the *why* behind ML pipelines, not just the *what*. MCP-native, solo-engineer scale.

Public repo: https://github.com/schwinger42/Epitaxy

## Status

🚧 **v0 design-space-claim phase, 2026-05-13**: README + LICENSE + .gitignore + pyproject.toml shipped; first commit pushed. **NO Python code yet.** Implementation gated on RecSys Phase 2 (Statsig launch) shipping first (~2-3 weeks from 2026-05-13).

Detail: [docs/ROADMAP.md](docs/ROADMAP.md).

## Future layout (placeholder — applies when v0 implementation begins)

```
~/PycharmProjects/Epitaxy/
├── src/epitaxy/
│   ├── parser/      # Python AST → POR data layer
│   ├── store/       # JSON index, .epitaxy/index.json schema
│   ├── mcp_server/  # MCP tools: por_explain / por_trace / por_lineage
│   └── cli/         # `epi sync`, `epi serve`, etc.
├── docs/
│   └── ROADMAP.md   # v0 → v3 phasing (already shipped)
├── tests/
├── CLAUDE.md        # this file (project memory)
├── README.md
└── pyproject.toml
```

**Do not pre-create `src/epitaxy/` skeleton before v0 work actually starts.** The repo stays placeholder-only until RecSys Phase 2 ships.

## Core principles (binding for any session writing Epitaxy code)

1. **LLM-drafts-human-commits.** Epitaxy's own development follows the safety design it preaches for user codebases. Drift proposals, generated refactors, and any LLM-authored content go to a PR for review. Never auto-merge. Sacred ops (ADR edits, root CLAUDE.md changes, deletions in `decisions/`) always require explicit human approval. See [docs/ROADMAP.md](docs/ROADMAP.md) §4 for the sacred-vs-safe classification.

2. **Eat your own dog food.** Every module Epitaxy ships must have a POR docstring (once v0 ships the POR schema) and appear in nested CLAUDE.md. If Epitaxy can't sustain its own codebase, it can't credibly sell intent-layer maintenance to anyone else. Non-negotiable.

3. **No `Co-Authored-By: Claude` trailers in commits.** Repo is the author's interview portfolio (Nvidia / Anthropic / ByteDance audience). AI co-author tags signal heavy AI dependency. Default: no trailer. Exception: the first commit (`ddb09a5`) has a trailer — kept because rewriting day-1 history wasn't worth the cost. All subsequent commits: no trailer.

4. **README must match shipped reality.** Anti-pattern: promising v2/v3 always-on dev infrastructure in README when current code can't deliver it. Forward-looking framing lives in `docs/ROADMAP.md`, never in README. Two-layer narrative: README front door = honest scope; ROADMAP back door = depth.

## Current focus

Waiting on RecSys Phase 2 (fresh_min_tier Round 3 + Statsig 3-arm A/B launch) to ship. When that lands (~early June 2026), return here for v0 implementation: Pillar 3 (Consume) + Pillar 4 (Query), read-only on user repo, ~3-5 focused days.

Until then: design discussions and roadmap edits are fine; no code, no `src/epitaxy/`, no MCP server init.

## Detail reference

- [docs/ROADMAP.md](docs/ROADMAP.md) — 4 pillars in depth, v0 → v3 phasing with what-ships-per-phase, LLM-drafts safety design, positioning vs platform tools (pre-commit / dependabot / dbt docs), explicit non-goals, open design questions.
- [README.md](README.md) — public landing page, honest v0 scope.
- [pyproject.toml](pyproject.toml) — `epitaxy v0.0.1`, `requires-python = ">=3.10"`, no dependencies yet.
