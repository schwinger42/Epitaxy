# Epitaxy — Roadmap (v0 → v3)

> Detailed phasing plan, always-on extension architecture, and design principles for [Epitaxy](https://github.com/schwinger42/Epitaxy). Companion to [README.md](../README.md) — README is the public landing page (honest scope of what's shipped); this is the deeper engineering plan (where it's going and why).

## 1. Problem

ML codebases lose their intent layer first. When a senior engineer hands off a pipeline, the receiver gets the **structure** (call graph, data lineage, file tree) but loses the **why** — why `mu = 0.05`, why this regularizer, why this scheduler runs at 3am, why this model serves the trending carousel but not the new-released one. That tacit knowledge lives in the original author's head, and walks out the door with them.

Existing tools cover the structure side well: AST parsers, lineage trackers (Dagster), code-index MCP servers (`mcp-codebase-index`, Cody), vector code-search (Cursor `@codebase`). None of them recover intent — because intent is **non-extractable by deterministic tools**.

The 2026 update to this framing: LLMs CAN synthesise first-pass intent from code + ADRs + commits + plan markdowns + tests + READMEs. The bottleneck is not extraction; it's **persistence and freshness**. Whatever an LLM extracts today goes stale tomorrow unless the system has a maintenance loop. And whatever a human writes once goes stale next month unless the system can detect drift and propose updates.

So the problem decomposes into four moves: (1) **install** an intent layer onto an existing repo, (2) **maintain** it as code changes, (3) **consume** it (human + AI), (4) **expose** it to other AI agents. These are the 4 pillars Epitaxy is built around.

## 2. Architecture: the 4 pillars

### Pillar 1 — Bootstrap

Install the intent layer onto an existing repo. Input: a grown ML codebase with messy structure (carousels, pipelines, utils, dags all in one repo). Output: proposed nested `CLAUDE.md` per major subsystem, a new-engineer onboarding playbook, initial POR docstrings sketched for high-stakes modules.

**Primary user action**: review proposals per-item, edit or reject, Epitaxy writes the approved files. One-shot at adoption, not continuous.

**Why this matters**: most real ML repos have 1 root `CLAUDE.md` and 0 nested, despite structure crying out for nested guidance. No existing tool does this — Backstage requires upfront governance setup; autodocgen produces structure-not-intent text.

### Pillar 2 — Maintain (always-on)

The hardest pillar. The intent layer must stay fresh as code evolves, otherwise it becomes a liability — documentation that lies is worse than no documentation. Two sub-modes serve different trigger sources:

**Pillar 2a — In-session (AI agent–driven)**

Trigger: an AI agent (Claude Code, Codex, Cursor) is actively writing code in a session.

Epitaxy's MCP server exports **prompts** (not just tools — this is MCP's underused capability). When an agent connects, it picks up Epitaxy's prompts and learns the protocol:

- When editing a file, check whether the relevant nested `CLAUDE.md` needs updating
- When making a parameter decision (e.g., `mu = 0.05`), propose an ADR before merging
- When tempted to spawn throwaway markdown (`PLAN_v2.md`, `notes_temp.md`), consolidate into existing `CLAUDE.md` or playbook instead

The agent's behavior changes because Epitaxy taught it the protocol via MCP prompts — no human intervention per session.

**Pillar 2b — Out-of-session (event-driven, always-on)**

Trigger: any change to the repo, regardless of who or what made it.

Four event-driven daemons, each catching a different change source:

- **`epi precommit`** (git hook) — on every commit, generate drift report. Example: "You modified `models/als.py` but `models/CLAUDE.md` says ALS uses `rank=64`; current code has `rank=128`."
- **`epi daemon`** (filesystem watcher) — on file save, mark `CLAUDE.md` drift badge in the Pillar-3 UI. Real-time signal that something is out of sync.
- **GitHub Action** (CI integration) — auto-open drift-PR with proposed `CLAUDE.md` updates after merge to main.
- **Cron health check** (daily) — scan for: dirs that grew past nested-CLAUDE.md threshold, stale ADR references, orphaned modules with no `CLAUDE.md` mention anywhere.

All four follow LLM-drafts-human-commits (§6) — proposals to `.epitaxy/drift/`, never autonomous applies.

### Pillar 3 — Consume

The drill-down explorer. Input: repo with Epitaxy artifacts. Output: local web UI (`epi serve` opens `localhost:4321`).

Three navigation axes:

- **Horizontal**: side-by-side comparison of peer subsystems (e.g., trending carousel vs new-released carousel). See what is the same and what diverges.
- **Vertical**: pipeline → script → function → internal logic drill-down. Each level shows intent in context.
- **Cross-cutting**: data lineage (where does this table come from?), script lineage (what depends on this?), decision trail (why is `mu = 0.05`?), plan history (what changed since the original plan?).

Each piece of intent text shows provenance: `🤖 LLM-extracted (confidence: 0.84)` or `👤 human-curated`. User sees at a glance what to trust and what to verify.

**Primary user action**: consume, read. Occasionally override LLM extraction with human-curated text (sticky — beats LLM extraction in priority).

**Target user**: new MLE onboarding to the repo. 1-hour read-through gets them oriented across all major subsystems.

### Pillar 4 — Query

The MCP server. Input: same artifacts as Pillar 3, accessed programmatically.

Tools exposed to any AI agent (Claude Code, Codex, Cursor, future agents):

- `por_explain(module)` — full intent dump for a module
- `por_trace(param)` — decision trail for a parameter (where defined, all reasons given, all changes over time)
- `por_lineage(asset)` — upstream/downstream chain for a data asset or model
- `playbook_for_role(role)` — "how should a new MLE / DE / TL read this repo"
- `next_action_for_path(file)` — "I'm changing this file, what should I watch for"

**Primary user action**: not direct human use. AI agents call these tools during their own sessions and surface answers in their chat with the human.

**Why bundled with Pillar 3 in v0**: same JSON data layer powers both. Marginal cost of Pillar 4 once Pillar 3 exists is 1-2 days.

### Intent graph as primary, code/docs as projection

Beneath the 4 pillars sits a single mental model that makes them composable: **the intent graph is the truth, and every visible artifact in the repo is a projection of it**.

Concretely, "intent graph" means typed nodes (modules, functions, ADRs, parameters, data assets, decisions, plans) connected by typed edges (`decides`, `depends-on`, `derives-from`, `modifies`, `supersedes`). The graph lives in `.epitaxy/index.json` from v0 onward, and grows new node/edge types as later pillars come online.

Every surface that humans or agents actually read is then a **projection** of this graph:

| Projection | Surface | Where it lives |
|---|---|---|
| Executable | Python source | `src/**/*.py` |
| Decision | ADRs | `decisions/*.md` |
| Narrative | nested `CLAUDE.md` | per-subsystem |
| Temporal | commit log | `git log` |
| Onboarding | role playbooks | `docs/playbooks/*.md` |
| Pedagogical (v2/v3) | HTML explainers | `epi serve` output |

This is the same single-source-of-truth + materialized-views pattern that `dbt`, event sourcing, and `terraform plan/apply` all run on. It earns its place in the architecture because it makes three otherwise-fuzzy things sharp:

1. **What drift actually is** (Pillar 2b). Not "the docs are out of date" — drift is **projection desync**: two projections of the same graph node disagree. `models/CLAUDE.md` says `rank=64`, `models/als.py` says `rank=128`: same graph node (`als.rank`), two projections, conflicting values. Detection becomes a concrete equality check, not a fuzzy "feels stale" judgment.

2. **What sacred-vs-safe means** (§6). Sacred ops **mutate the graph itself** (new ADR node, deleted module node, changed root rule edge). Safe ops **re-render one projection from the graph** (cross-ref rename, `<!-- epitaxy-auto -->` block refresh). The classification table in §6 is a downstream consequence of this distinction, not an independent ruleset.

3. **What Pillar 3's three navigation axes are**. Horizontal / vertical / cross-cutting aren't three UI gimmicks — they are three orthogonal ways to slice the same graph for a human reader (peer comparison, depth drill-down, cross-cutting traversal).

**Implementation consequence for v0**: the data layer in `.epitaxy/index.json` must be graph-shaped from day one, even though v0 only ships one projection (the Pillar-3 drill-down site) and one read interface (Pillar-4 MCP tools). If v0 ships a flat document store, every later pillar pays for the mistake. See [docs/SCHEMA.md](SCHEMA.md) for the concrete spec — 7 node types, 4 edge types, parameter extraction opt-in, generic ML worked example.

## 3. Phasing v0 → v3

| Phase | Pillars added | Effort | What ships |
|---|---|---|---|
| **v0** | Pillar 3 + Pillar 4 | ~3-5 days | `epi sync` CLI (parses repo → `.epitaxy/index.json` data layer), `epi serve` static markdown drill-down site, MCP server with `por_explain` / `por_trace` / `por_lineage` tools — all **read-only** on user repo |
| **v1** | + Pillar 1 + Pillar 2a | ~5-7 days after v0 | `epi bootstrap` (proposes nested `CLAUDE.md` + onboarding playbook), MCP `prompts/` export (Claude Code / Cursor auto-update `CLAUDE.md` when editing files) |
| **v2** | + Pillar 2b basics | ~3-5 days | `epi precommit` git hook + commit-time drift report; `.epitaxy/drift/<timestamp>-<file>.md` proposal directory pattern |
| **v3** | + Pillar 2b full | ~5-7 days | `epi daemon` (filesystem watcher), GitHub Action for auto-PR drift, cron health checks (orphaned modules, stale ADRs, growth-threshold scans) |

Each phase gated on 4-8 weeks of observation from the previous. Decisive non-traction → stop; blog post + portfolio piece is the payoff regardless of which version Epitaxy stops at.

## 4. Design Principle — Progressive Enhancement

All Epitaxy static output (Pillar 3 explorer pages, and the v2/v3 HTML
Explainer Generator) follows the **"CSS-first, JS islands"** pattern,
inspired by `phase2_new_released.html` — a 1705-line hand-written
pedagogical doc that proves the pattern at production quality.

**Distribution of responsibility:**

- **Semantic HTML** drives structure (`<section>`, `<details>`, `<figure>`,
  `<dl>`). Accessibility automatic, SEO-friendly, AI-readable.
- **CSS** drives layout, drill-down (`<details>/<summary>`), hover glossary
  (`:hover`), navigation (`:target`), sticky positioning, theming.
- **JS islands** ONLY for capabilities CSS cannot deliver:
  - Math typesetting (KaTeX)
  - Flowchart diagrams (Mermaid) — or hand-written SVG to avoid JS entirely
  - Copy-to-clipboard buttons
  - Syntax highlighting (preferred: server-rendered via Shiki at build time)

**Explicitly out of scope for static output (require JS, layered ABOVE static base):**

- Pillar 2b live drift-badge updates → WebSocket / Server-Sent Events
- Pillar 4 in-browser MCP queries → JS HTTP client
- Pillar 3 stretch — interactive graph (pan/zoom/expand) → Cytoscape.js or ReactFlow

**Why this principle:**

- Forces semantic-first thinking — you cannot paper over bad structure with JS
- Accessibility automatic (screen readers love semantic HTML)
- SEO benefit (static content indexes reliably)
- No build-step bloat — `epi serve` starts in ~200ms, not 8s
- Aligns with 2026 industry direction (Astro, Tailwind's "progressive enhancement")
- Output renders in any browser, archive-stable — Pillar 1 BOOTSTRAP value is
  unaffected by future JS framework churn

## 5. Stretch (v2/v3) — HTML Explainer Generator

Inspired by hand-written pedagogical docs like the 1705-line RecSys
`phase2_new_released.html`, Epitaxy in v2/v3 can compose a similar-quality
HTML explainer for any subsystem by combining structured POR data with
LLM-written narrative segments. Output follows the Progressive Enhancement
principle above.

**Template features extracted from the reference doc:**

- Multi-audience framing (new teammate / interviewer / future-self)
- Story-arc structure (legacy → architecture → tuning → fix → A/B)
- `<details>` drill-down for depth (CSS-only)
- Glossary hover popovers (CSS-only)
- Q&A pressure-test section (interview-ready 2-min answers)
- Failure modes + recovery procedure tables
- Timezone audit / cross-source rules
- Decision provenance with audit history (e.g., "owning a mistake" retrospectives)

**Workflow:**

1. User provides structured POR docstrings + ADRs + commits (the truth layer).
2. Epitaxy LLM composes prose narrative segments from that truth.
3. User reviews narrative (structured data was already verified during extraction).
4. Commit. Future parameter changes trigger Pillar 2b drift detection, which
   proposes amendments to affected narrative segments — never the structured layer.

**Why this is the right v2/v3 stretch:**

- Hand-written pedagogical docs are "human gold standard" — 100% quality, but
  don't scale (a single solo engineer cannot hand-write 10 of these).
- LLM-composed at 80% quality × 10 subsystems × always-fresh
  >> 100% × 1 × drift.
- No competitor produces pedagogical HTML explainers from code intent.
- Demo asset: the reference `phase2_new_released.html` IS the target output
  spec — any reviewer / interviewer / hiring manager can compare Epitaxy's
  auto-output side-by-side with the human gold standard.

**Anti-pattern to avoid:**

LLM auto-publishing without human review of the narrative layer.
Structured data is auto-verified (extracted from ground truth). **Prose
narrative ALWAYS gets human approval before commit.** Hallucinated narrative
in a pedagogical doc is worse than no doc — it teaches wrong things to new
hires and lies in interviews. The human-in-the-loop gate is non-negotiable
for this feature.

## 6. Safety design — LLM-drafts-human-commits

**Principle**: never autonomous edits to truth sources.

**Why**: cascade contamination is real. A hallucinated ADR is read as ground truth by every downstream AI agent → those agents generate code aligned to wrong intent → that wrong code reinforces the bad ADR → local error compounds globally. The safety boundary must catch this loop before it closes.

**Mechanism**:

1. Epitaxy generates proposals to `.epitaxy/drift/<timestamp>-<file>.md`
2. Pillar-3 UI shows proposal with provenance, confidence score (0-1), and diff against current truth
3. Human approves or rejects → approved proposals are git-applied with a traceable commit message

**Sacred vs Safe classification**:

| Op type | Auto-apply? | Why |
|---|---|---|
| Cross-ref rename (function moved files) | Yes | Local impact, easily reverted |
| Generated section marked `<!-- epitaxy-auto -->` | Yes | Section is by-contract auto-managed |
| ADR edit (any file in `decisions/`) | **No** | High blast radius — downstream agents read as ground truth |
| Root `CLAUDE.md` rule change | **No** | Affects all sessions, all files |
| Deletions of any kind | **No** | Asymmetric — undo is hard |
| Plan markdown edits (`docs/plans/`) | **No** | Encodes strategic intent |

**Rule of thumb**: blast radius determines sacredness, NOT output type. An LLM-generated cross-ref rename can auto-apply; a human-typed ADR change still needs review (because it's an ADR, not because of who wrote it).

## 7. Positioning — where Epitaxy lives in the platform-tools landscape

Epitaxy occupies a specific cell in the dev-infrastructure space. The clearest framing is by analogy to existing always-on tools, not by competitor comparison:

- **`pre-commit`** (universal pre-commit hook framework, ~12k stars) gives you **mechanical checks**: line length, imports sorted, no debug prints. Epitaxy gives you **intent-drift checks**: "this `CLAUDE.md` no longer matches this file."
- **Dependabot** (acquired by GitHub) generates **PRs for dependency updates** with human approval. Epitaxy generates **PRs for documentation drift** with the same human-approval pattern.
- **`dbt docs`** renders **a static site from your model graph** on every build. Epitaxy renders **a static site from your intent graph** with the same auto-generate-on-build pattern.

The common shape: event-driven, generates proposals, requires human approval for high-stakes ops, integrates with the existing dev loop instead of replacing it. Epitaxy adds a category that doesn't yet exist in this shape — **intent-layer maintenance for ML codebases**.

This is the "always-on knowledge maintenance" angle that elevates Epitaxy from "explorer tool" to **dev infrastructure for AI-agent-native engineering**. The interview answer to "tell me about a system you designed" writes itself: depth across in-session AI integration (Pillar 2a), event-driven daemons (Pillar 2b), and human-in-the-loop safety (LLM-drafts-human-commits).

## 8. Non-goals

Explicit non-goals — Epitaxy is NOT trying to be these things:

- **NOT a Backstage replacement.** Backstage targets enterprise governance / catalog at org scale. Epitaxy targets a single solo-maintained ML codebase. Different cell.
- **NOT an autodocgen.** Tools like Autodoc and `doc-comments-ai` generate docstrings from code structure. Epitaxy is intent-first; structured-text generation is a side effect, not the product.
- **NOT an LLM coding assistant.** Epitaxy doesn't write production code for you. It maintains the intent layer around code so that other tools (Claude Code, Codex, Cursor) write better code.
- **NOT a Claude Code (or any agent's) config orchestrator.** Epitaxy manages `CLAUDE.md` — the shared, repo-tracked intent surface. It does NOT manage `MEMORY.md` (per-user session state, Anthropic-owned lifecycle) or `.claude/rules/` / agent self-config (user-owned, with recursive-contamination risk if agents auto-edit their own behavior rules). Scope discipline IS the moat: when Claude Code, Codex, or successor agents improve, Epitaxy gets stronger as a combination, not displaced. Over-scoping into agent-config orchestration is how earlier IDPs (Backstage) got hollowed out — Epitaxy explicitly refuses that path.
- **NOT a vector code-search tool.** Cursor's `@codebase` and Cody already do semantic search well. Epitaxy is structured intent (typed fields: `goal`, `why`, `prereqs`, `effects`, `decisions`), not free-text vector retrieval.
- **NOT a project management tool.** No tickets, no sprints, no roadmap-as-product. (This ROADMAP.md is for design, not project tracking.)

## 9. Open design questions

These are deliberately unresolved; the plan is to pick after evidence from v0+ rather than guessing now.

- **Drift detection mechanism** — how does Epitaxy decide "this `CLAUDE.md` is stale vs this code"? Options: (1) timestamp diff (cheap, false positives), (2) embedding similarity (semantic, expensive per check), (3) LLM judge (most accurate, slow + expensive). v2 picks after observing real drift frequency during v0 dogfooding.

- **Multi-language extension** — Pillar 3 is Python-AST-bound in v0. Extending to Scala / R / SQL means generalizing the parser and POR schema. Deferred to v1+ only if traction signal arrives.

- **ADR format compatibility** — adopt existing (`madr`, `adr-tools`) or define Epitaxy's own ADR schema? Default: adopt existing. Revisit if existing schemas don't carry enough metadata for MCP query tools.

- **MCP prompt schema stability across clients** — Pillar 2a depends on MCP `prompts/` behaving uniformly across clients, and adoption varies. **Decision (2026-05-13): Claude Code and Codex are first-class dogfood targets** — Pillar 2a (v1) is tested against both as a release gate before announcement. Cursor and other MCP clients added when their `prompts/` support reaches feature parity.

- **POR location: docstring vs CLAUDE.md** — should structured POR live as YAML frontmatter in code docstrings, or as sections inside `CLAUDE.md`, or both? Need v0 dogfooding evidence before committing to a canonical form.

---

*Public landing page: [README.md](../README.md) · Project memory for Claude Code sessions: [CLAUDE.md](../CLAUDE.md) · Repo: https://github.com/schwinger42/Epitaxy*
