# Epitaxy

> **Process-of-Record for AI-assisted applied science and ML engineering — built for agents to understand the *why*, not just the *what*.**

[![status](https://img.shields.io/badge/status-🚧%20v0%20in%20development-orange)]()
[![license](https://img.shields.io/badge/license-MIT-blue)]()
[![mcp](https://img.shields.io/badge/MCP-compatible-purple)]()

---

## The story

In a semiconductor fab, every recipe step has a documented **goal**, **prerequisites**, and **downstream effects**. This is called a **Process of Record (POR)** — and it's why a TSMC engineer can hand a process to someone new on Monday and have them shipping wafers on Friday.

**AI-assisted research and engineering projects have no lightweight equivalent.**

When a senior ML engineer hands you a 30-script PySpark pipeline, you can read the code, trace the call graph, even map the data lineage — but you can't recover **why** `mu = 0.05`, **what** must run first, or **which** downstream caches break if you change line 23. When a domain scientist hands you a Raman-spectrum classification project, the same problem appears as preprocessing choices, instrument constraints, sample provenance, validation boundaries, and paper claims. That tacit knowledge lives in people's heads.

AI coding agents have the same problem. They can read your repo, but they still have to *guess* at intent and domain constraints. Garbage in, garbage POR out.

Epitaxy's bet: complex applied-science and ML projects don't need longer agent chat history. They need a queryable Process-of-Record the agent can consult when code, data, protocols, or parameters change — so a change can surface the scripts, downstream artifacts, ADRs, POR docs, and claims that must be reviewed.

## What Epitaxy does

Epitaxy treats your project like a semiconductor wafer — layer by layer:

1. **Structured intent** — every module declares `goal`, `why`, `prereqs`, `effects`, `decisions` in YAML frontmatter inside its docstring. *You* write the intent (no LLM guessing).

2. **Hierarchical drill-down explorer** — project → pipeline → module → function, with intent visible at every level. Click any reference, jump anywhere.

3. **MCP-native from day 1** — your AI agent (Claude Code, Codex, Cursor, or any MCP client) queries intent through MCP tools: `por_explain(module)`, `por_trace(param)`, `por_lineage(asset)`. No more "I think this is because…".

4. **Domain constraints as first-class context** — Epitaxy is designed to preserve not only code structure, but also the experimental intent, physical constraints, data provenance, modeling assumptions, validation boundaries, and downstream claims that make applied work valid.

5. **Intent graph for change impact** — Epitaxy's data layer connects code, decisions, parameters, plans, and downstream effects so later guardrails can answer: not just "will tests pass?", but "which intent is now stale?"

6. **Solo-practitioner scale** — no Backstage deployment, no enterprise contract. `pip install epitaxy && epi sync`.

## How it differs from what exists

|                              | Auto-extracts code structure | Human-curated intent | Research / ML shape | MCP-native | Solo-scale |
|------------------------------|:---:|:---:|:---:|:---:|:---:|
| Cortex / Backstage           | ⚠️  | ⚠️  | ❌  | ❌  | ❌  |
| mcp-codebase-index, Cody     | ✅  | ❌  | ❌  | ✅  | ✅  |
| Dagster UI                   | ✅  | ⚠️  | ⚠️  | ❌  | ⚠️  |
| Autodoc, doc-comments-ai     | ✅  | ❌  | ❌  | ❌  | ✅  |
| **Epitaxy**                  | ✅  | ✅  | ✅  | ✅  | ✅  |

## Status

🚧 **v0 in development** — all SCHEMA-default + opt-in parser pieces have landed (PR1–PR4); final hardening + HN-launch polish remain.
🎯 **v0 ship target:** June 2026.

What v0 includes / will include:

- [x] Python AST parser → intent-graph JSON — _spec'd: [docs/SCHEMA.md](docs/SCHEMA.md)_
- [x] ADR / plan / POR docstring parsing
- [x] CLI: `epi sync` / `epi serve` / `epi mcp` — _spec'd: [docs/CLI.md](docs/CLI.md)_
- [x] MCP server (read-only): `por_explain` / `por_trace` / `por_lineage` — _spec'd: [docs/MCP.md](docs/MCP.md)_
- [x] Static semantic HTML drill-down site (Pillar 3)
- [x] Parameter extraction opt-in (`epi sync --parameters`) — ML hyperparameters + domain-constrained values (instrument settings, physical constraints, etc.)

Beyond v0 (if traction):
- [ ] Interactive graph UI (ReactFlow / Cytoscape)
- [ ] Multi-language support (Scala, R, SQL)
- [ ] Change-impact + intent-drift guardrails (pre-commit, PR comments, stale badges)
- [ ] Auto-suggestion mode (LLM proposes POR, human approves)
- [ ] Applied-science extensions: samples, protocols, instrument runs, datasets, models, validations, claims

⭐ **Star the repo** to get notified when v0 ships.

📋 **[Detailed Roadmap (v0 → v3)](docs/ROADMAP.md)** — phasing, always-on extension, and design principles.

## Why I'm building this

I'm a solo ML engineer running production recommendation systems at 300–400K DAU scale. After a month of work on a single carousel pipeline, I couldn't keep the system in my own head — and reading my own `.md` / `.html` docs didn't help. Existing tools addressed structure, not intent.

I came to software from a semiconductor R&D background. The POR pattern from fabs maps cleanly onto this problem: applied work does not fail only because code is disorganized; it fails when the reasoning behind recipes, constraints, experiments, data, and claims disappears.

If you're an ML engineer drowning in your own pipelines, a domain expert using Claude Code / Codex to bring AI into applied science, or a TL trying to onboard people onto an organic research codebase, this is for you.

## License

MIT. Personal project, not affiliated with any employer.
