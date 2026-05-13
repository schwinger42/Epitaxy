# Epitaxy

> **Process-of-Record for ML codebases — built for AI agents to understand the *why*, not just the *what*.**

[![status](https://img.shields.io/badge/status-🚧%20v0%20in%20development-orange)]()
[![license](https://img.shields.io/badge/license-MIT-blue)]()
[![mcp](https://img.shields.io/badge/MCP-compatible-purple)]()

---

## The story

In a semiconductor fab, every recipe step has a documented **goal**, **prerequisites**, and **downstream effects**. This is called a **Process of Record (POR)** — and it's why a TSMC engineer can hand a process to someone new on Monday and have them shipping wafers on Friday.

**Software has no equivalent.**

When a senior ML engineer hands you a 30-script PySpark pipeline, you can read the code, trace the call graph, even map the data lineage — but you can't recover **why** `mu = 0.05`, **what** must run first, or **which** downstream caches break if you change line 23. That tacit knowledge lives in their head.

AI agents have the same problem. Claude Code can read your repo, but it has to *guess* at intent. Garbage in, garbage POR out.

## What Epitaxy does

Epitaxy treats your ML repo like a semiconductor wafer — layer by layer:

1. **Structured intent** — every module declares `goal`, `why`, `prereqs`, `effects`, `decisions` in YAML frontmatter inside its docstring. *You* write the intent (no LLM guessing).

2. **Hierarchical drill-down explorer** — project → pipeline → module → function, with intent visible at every level. Click any reference, jump anywhere.

3. **MCP-native from day 1** — your AI agent (Claude Code, Cursor, Cody) queries intent through MCP tools: `por_explain(module)`, `por_trace(param)`, `por_lineage(asset)`. No more "I think this is because…".

4. **Solo-engineer scale** — no Backstage deployment, no enterprise contract. `pip install epitaxy && epi init`.

## How it differs from what exists

|                              | Auto-extracts code structure | Human-curated intent | ML-pipeline shape | MCP-native | Solo-scale |
|------------------------------|:---:|:---:|:---:|:---:|:---:|
| Cortex / Backstage           | ⚠️  | ⚠️  | ❌  | ❌  | ❌  |
| mcp-codebase-index, Cody     | ✅  | ❌  | ❌  | ✅  | ✅  |
| Dagster UI                   | ✅  | ⚠️  | ⚠️  | ❌  | ⚠️  |
| Autodoc, doc-comments-ai     | ✅  | ❌  | ❌  | ❌  | ✅  |
| **Epitaxy**                  | ✅  | ✅  | ✅  | ✅  | ✅  |

## Status

🚧 **v0 in development** — this README is a public claim of the design space.
🎯 **v0 ship target:** ~3 weeks from June 2026.

What v0 will include:
- [ ] POR schema spec (YAML frontmatter)
- [ ] Python AST parser → POR data layer (JSON)
- [ ] CLI: `epi explain` / `epi trace` / `epi lineage`
- [ ] MCP server (read-only)
- [ ] Markdown explorer (static site generator)

Beyond v0 (if traction):
- [ ] Interactive graph UI (ReactFlow / Cytoscape)
- [ ] Multi-language support (Scala, R, SQL)
- [ ] Auto-suggestion mode (LLM proposes POR, human approves)

⭐ **Star the repo** to get notified when v0 ships.

📋 **[Detailed Roadmap (v0 → v3)](docs/ROADMAP.md)** — phasing, always-on extension, and design principles.

## Why I'm building this

I'm a solo ML engineer running production recommendation systems at 300–400K DAU scale. After a month of work on a single carousel pipeline, I couldn't keep the system in my own head — and reading my own `.md` / `.html` docs didn't help. Existing tools addressed structure, not intent.

I came to software from a semiconductor R&D background. The POR pattern from fabs maps cleanly onto this problem. So I'm building it.

If you're an ML engineer drowning in your own pipelines, or a TL trying to onboard new hires onto an organic codebase, this is for you.

## License

MIT. Personal project, not affiliated with any employer.
