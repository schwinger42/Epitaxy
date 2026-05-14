# Epitaxy — `.epitaxy/index.json` schema (v0)

> Design-first specification for Epitaxy's intent-graph data layer. Defines what gets parsed, what gets stored, and what shape the on-disk JSON takes — **before** parser code is written. Companion to [ROADMAP.md](ROADMAP.md); read [§2](ROADMAP.md#intent-graph-as-primary-codedocs-as-projection) of that first.

## 0. Why this doc exists

[ROADMAP §2](ROADMAP.md#intent-graph-as-primary-codedocs-as-projection) commits Epitaxy to a single architectural bet: **the intent graph is the truth, every visible artifact in the repo is a projection of it**. That bet only pays off if the graph is shaped correctly from v0 — every later pillar (drift detection, MCP query, HTML explainer, event-driven daemons) reads through this same shape.

This doc fixes the shape before parser implementation begins. Specifically it commits to:

1. **Seven node types** that Epitaxy treats as first-class long-term — covering executable, decision, narrative, and lineage surfaces.
2. **Four edge types** that wire nodes into a queryable graph.
3. **v0 parser ships parsing for 4 of the 7 node types** (`module` / `function` / `adr` / `plan`). The remaining 3 (`parameter` / `data_asset` / `decision`) are reserved in the schema so v1+ extensions don't require a breaking format change.
4. **`.epitaxy/index.json`** — the concrete on-disk format that `epi sync` writes and `epi serve` / Pillar-4 MCP tools read.

If this design is wrong, every later pillar pays the cost. If it is right, later pillars become local extensions rather than re-architectures.

## 1. Design principles

### 1.1 Graph-shaped from day one

Even though v0 ships only one projection (the Pillar-3 drill-down site) and one read interface (Pillar-4 MCP tools), the underlying data layer is a typed-node + typed-edge graph. Flat document stores are cheap to write today and impossible to extend tomorrow.

Concretely: nodes have explicit `type` and stable `id`; edges have explicit `from` / `to` / `type`. Adding a new node type in v1 means appending one entry to the `type` enum, not migrating every consumer.

### 1.2 Schema-stable, parser-progressive

The seven node types in §2 are **fixed for v0**. They will not change in v0.x point releases. They define the long-term shape of the graph.

What changes between v0 and v1+ is **how many of those types the parser actually populates**. v0 ships parsing for 4 (`module` / `function` / `adr` / `plan`). The other three (`parameter` / `data_asset` / `decision`) are valid node types in the schema, but the v0 parser does not emit them by default. This separates "what the format supports" from "what today's tool produces" — a distinction any always-on tool needs for forward compatibility.

### 1.3 Parameter extraction is opt-in

Parameter nodes (e.g. `rank = 128`, `learning_rate = 1e-3`) are the highest-value query target but the messiest to extract reliably. Most ML code has hundreds of literal values, only a handful of which are tuned decisions worth surfacing. Auto-extracting all of them produces noise; auto-extracting none misses the point.

v0 resolves this by making parameter extraction **explicit opt-in** via `epi sync --parameters`. The user opts in once they have marked parameters of interest (e.g., a `# epitaxy:param` comment on the assignment line, or inclusion in an ADR's `decides:` frontmatter list). The default `epi sync` produces zero parameter nodes — preferable to producing 200 noisy ones.

This also keeps v0 honest about what it can deliver well: module / function / adr / plan extraction works at near-100% recall on conventional Python repos. Parameter extraction depends on user signal; without it, the result is unreliable and would erode trust in everything else Epitaxy produces.

### 1.4 Provenance on every node and edge

Every node and edge carries a `provenance` field describing how it was extracted — examples: `ast` (Python AST parse), `frontmatter` (YAML block in a markdown file), `body-mention` (textual reference in markdown body), `manual` (human-curated).

Pillar-3 UI uses this to badge each piece of intent (`🤖 LLM-extracted` vs `👤 human-curated`); Pillar-4 MCP tools use it to filter by source. No node or edge enters the graph without provenance. Untracked sources break the audit chain that Pillar-2 drift detection and the LLM-drafts-human-commits safety design ([ROADMAP §6](ROADMAP.md#6-safety-design--llm-drafts-human-commits)) both depend on.

### 1.5 IDs are paths, not hashes

Node IDs are deterministic strings derived from repo-relative file paths plus an optional in-file qualifier (function name, parameter name, anchor). Not content hashes, not UUIDs.

Trade-off accepted: moving a file invalidates every edge pointing at it. Mitigation: `epi sync` regenerates the whole index from scratch each run; there is no incremental-update protocol in v0. Drift detection (v2) compares old-index vs new-index after a regenerate, not before.

Benefits chosen over the trade-off: IDs are human-readable, greppable, and stable across machines; they round-trip through git diffs cleanly; they make `.epitaxy/index.json` reviewable as a normal text artifact.

## 2. Node types

Seven node types, fixed for v0. The "v0 parser?" column states whether the **default** `epi sync` run emits this type.

| Type | What it is | v0 parser? | ID convention |
|---|---|---|---|
| `module` | Python module / source file | ✅ default | `module:<repo-relative-path>` |
| `function` | Python function or method | ✅ default | `function:<module-path>::<qualname>` |
| `adr` | Architecture Decision Record (markdown in `decisions/`) | ✅ default | `adr:<repo-relative-path>` |
| `plan` | Plan markdown (`docs/plans/*.md`) | ✅ default | `plan:<repo-relative-path>` |
| `parameter` | Tuned value (e.g. `rank = 128`) | ⚙️ opt-in (`--parameters`) | `param:<module-path>::<scope>::<name>` |
| `data_asset` | Table, file, or model artifact | ⏳ deferred to v1+ | `data:<scheme>:<identifier>` |
| `decision` | Atomic decision (anchor inside ADR) | ⏳ deferred to v1+ | `decision:<adr-id>#<anchor>` |

### 2.1 `module`

Source: a Python source file under configured roots (default `src/**/*.py`; configurable via `pyproject.toml [tool.epitaxy] roots`).

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | e.g. `module:src/ranker/model.py` |
| `type` | `"module"` | yes | |
| `path` | string | yes | repo-relative |
| `doc` | string | no | first paragraph of module-level docstring |
| `por` | object | no | structured POR if recognized (see §4) |
| `provenance` | string | yes | `ast` for v0 |
| `extracted_at` | ISO 8601 | yes | sync run timestamp |

### 2.2 `function`

Source: any `def` / `async def` inside a parsed module. Methods on classes are included; their `qualname` is `<ClassName>.<method_name>`.

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | e.g. `function:src/ranker/model.py::Ranker.fit` |
| `type` | `"function"` | yes | |
| `module` | string | yes | parent module ID |
| `name` | string | yes | unqualified name |
| `qualname` | string | yes | dotted name within module |
| `signature` | string | yes | source-rendered signature |
| `line` | int | yes | 1-based start line |
| `doc` | string | no | first paragraph of docstring |
| `por` | object | no | structured POR if recognized |
| `provenance` | string | yes | `ast` for v0 |

### 2.3 `adr`

Source: any markdown file under `decisions/` (configurable). Parsed for YAML frontmatter and the first H1 / status block.

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | e.g. `adr:decisions/2026-04-rank-dim.md` |
| `type` | `"adr"` | yes | |
| `path` | string | yes | repo-relative |
| `title` | string | yes | from H1 or frontmatter `title` |
| `status` | string | no | `proposed` / `accepted` / `superseded` / `rejected` |
| `date` | string | no | ISO date, from frontmatter |
| `supersedes` | string | no | ID of older ADR this replaces |
| `decides` | string[] | no | parameter IDs (used only if parameter parsing enabled) |
| `summary` | string | no | first paragraph after H1 |
| `provenance` | string | yes | `frontmatter+body` |

### 2.4 `plan`

Source: any markdown file under `docs/plans/` (configurable). Strategic / multi-step intent that's narrower in scope than an ADR — typically time-bounded ("Q2 launch plan") rather than decision-bounded ("why we chose rank=128").

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | e.g. `plan:docs/plans/q2-launch.md` |
| `type` | `"plan"` | yes | |
| `path` | string | yes | repo-relative |
| `title` | string | yes | from H1 |
| `status` | string | no | `draft` / `in-progress` / `shipped` / `abandoned` |
| `summary` | string | no | first paragraph after H1 |
| `provenance` | string | yes | `body` |

### 2.5 `parameter` (opt-in, `--parameters`)

Source: an assignment in Python source marked either by (a) a `# epitaxy:param` comment on the same line, or (b) inclusion in an ADR's `decides:` frontmatter list.

Fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | e.g. `param:src/ranker/model.py::Ranker.fit::rank` |
| `type` | `"parameter"` | yes | |
| `module` | string | yes | parent module ID |
| `scope` | string | yes | function qualname or `<module>` for module-level |
| `name` | string | yes | variable / kwarg name |
| `value` | string | yes | source-rendered RHS (not evaluated) |
| `line` | int | yes | |
| `decided_by` | string[] | no | ADR IDs that decide this value |
| `provenance` | string | yes | `ast+comment` or `adr-frontmatter` |

### 2.6 `data_asset` (deferred to v1+)

Reserved type. Represents a table, parquet file, model artifact, or similar bytes-level dependency. Deferred because v0 has no integration with DAG runtimes (Dagster, Airflow, dbt) and standalone lineage parsing is high-effort / low-signal.

ID scheme reserved: `data:<scheme>:<identifier>`, e.g. `data:bigquery:project.dataset.users_clicks` or `data:gcs:bucket/path/model.pkl`.

### 2.7 `decision` (deferred to v1+)

Reserved type. An atomic decision unit inside an ADR — finer-grained than the ADR file itself (an ADR can encode 1-5 distinct decisions). v0 collapses these into the parent `adr` node; v1+ may expand them for finer-grained drift detection.

ID scheme reserved: `decision:<adr-id>#<anchor>` where `<anchor>` is a markdown heading slug within the ADR.

## 3. Edge types

Four edge types in the v0 schema. Edges are directional.

| Type | From → To | Source |
|---|---|---|
| `depends-on` | `module` → `module`, `function` → `function` | Python AST: imports + call expressions |
| `references` | `adr` / `plan` / `module` → any | Markdown body or docstring textual mention |
| `supersedes` | `adr` → `adr` | ADR frontmatter `supersedes:` field |
| `decides` | `adr` → `parameter` | ADR frontmatter `decides:` field (only emitted when `--parameters`) |

Edge fields:

| Field | Type | Required | Notes |
|---|---|---|---|
| `from` | string | yes | source node ID |
| `to` | string | yes | target node ID |
| `type` | string | yes | one of the four above |
| `source` | string | yes | e.g. `import` / `call` / `body-mention` / `frontmatter:supersedes` |
| `line` | int | no | source line if applicable |
| `provenance` | string | yes | same scheme as nodes |

`derives-from` (data-asset lineage) and `modifies` (commit → module) are reserved for v1+ alongside their target node types; they do not appear in v0 output.

## 4. Inline POR structure (optional)

If a module or function docstring contains a recognized POR block, the parser populates the node's `por` field. Recognition is best-effort in v0; a missed block produces `por: null`, not an error.

Recognized block (YAML frontmatter inside the docstring):

```python
def fit(interactions, rank=128):
    """
    ---
    goal: train a low-rank factorization on user-item interactions
    why: rank=128 chosen for headroom on long-tail items; see ADR 2026-04
    prereqs: interactions loaded by data.load.load_interactions
    effects: writes model.pkl to artifacts/
    decisions:
      - adr:decisions/2026-04-rank-dim.md
    ---
    Long-form prose continues here.
    """
```

Fields inside `por` (all optional):

- `goal` — one-sentence intent
- `why` — rationale, including the load-bearing trade-offs
- `prereqs` — what must exist or run before this code
- `effects` — what state this code changes (files written, tables updated)
- `decisions` — list of ADR IDs this code follows

Whether POR lives in docstrings vs nested `CLAUDE.md` vs both is an [open question](ROADMAP.md#9-open-design-questions); v0 supports docstring frontmatter as the first canonical form and will revisit after dogfooding evidence.

## 5. `.epitaxy/index.json` — concrete shape

One file per repo, written by `epi sync`. Pretty-printed JSON, UTF-8, LF line endings. Reviewable as a git artifact.

```json
{
  "version": "0.1",
  "generated_at": "2026-05-15T10:30:00Z",
  "generator": "epitaxy 0.1.0",
  "repo_root": "/home/user/projects/example-ranker",
  "config": {
    "roots": ["src/"],
    "adr_dir": "decisions/",
    "plan_dir": "docs/plans/",
    "parameters_enabled": false
  },
  "stats": {
    "modules": 12,
    "functions": 58,
    "adrs": 4,
    "plans": 2,
    "parameters": 0,
    "edges": 134
  },
  "nodes": [ /* see §2 */ ],
  "edges": [ /* see §3 */ ]
}
```

Single-file format is chosen deliberately for v0 — easy to diff, easy to ship, easy to git-track if the user opts in. Partitioning per node type may arrive in v3 if the file grows past comfortable diff size on real repos; the format version bump (`"version": "0.2"`) would signal that change.

## 6. Worked example

Given this minimal repo:

```
example-ranker/
├── src/ranker/model.py        # defines class Ranker, method fit()
├── src/data/load.py           # defines load_interactions()
├── decisions/2026-04-rank-dim.md
└── docs/plans/q2-launch.md
```

Where `decisions/2026-04-rank-dim.md` has frontmatter:

```yaml
---
title: ALS rank dimension — 128 over 64
status: accepted
date: 2026-04-12
supersedes: adr:decisions/2026-02-rank-baseline.md
---
```

And `src/ranker/model.py` imports from `src/data/load.py`.

A default `epi sync` (no `--parameters`) writes:

```json
{
  "version": "0.1",
  "generated_at": "2026-05-15T10:30:00Z",
  "stats": { "modules": 2, "functions": 2, "adrs": 1, "plans": 1, "parameters": 0, "edges": 3 },
  "nodes": [
    {
      "id": "module:src/ranker/model.py",
      "type": "module",
      "path": "src/ranker/model.py",
      "doc": "Low-rank matrix factorization ranker.",
      "provenance": "ast",
      "extracted_at": "2026-05-15T10:30:00Z"
    },
    {
      "id": "function:src/ranker/model.py::Ranker.fit",
      "type": "function",
      "module": "module:src/ranker/model.py",
      "name": "fit",
      "qualname": "Ranker.fit",
      "signature": "def fit(self, interactions, rank=128)",
      "line": 24,
      "provenance": "ast"
    },
    {
      "id": "module:src/data/load.py",
      "type": "module",
      "path": "src/data/load.py",
      "provenance": "ast"
    },
    {
      "id": "function:src/data/load.py::load_interactions",
      "type": "function",
      "module": "module:src/data/load.py",
      "name": "load_interactions",
      "qualname": "load_interactions",
      "signature": "def load_interactions(path: str) -> pd.DataFrame",
      "line": 8,
      "provenance": "ast"
    },
    {
      "id": "adr:decisions/2026-04-rank-dim.md",
      "type": "adr",
      "path": "decisions/2026-04-rank-dim.md",
      "title": "ALS rank dimension — 128 over 64",
      "status": "accepted",
      "date": "2026-04-12",
      "supersedes": "adr:decisions/2026-02-rank-baseline.md",
      "provenance": "frontmatter+body"
    },
    {
      "id": "plan:docs/plans/q2-launch.md",
      "type": "plan",
      "path": "docs/plans/q2-launch.md",
      "title": "Q2 ranker launch",
      "status": "in-progress",
      "provenance": "body"
    }
  ],
  "edges": [
    {
      "from": "module:src/ranker/model.py",
      "to": "module:src/data/load.py",
      "type": "depends-on",
      "source": "import",
      "line": 3,
      "provenance": "ast"
    },
    {
      "from": "function:src/ranker/model.py::Ranker.fit",
      "to": "function:src/data/load.py::load_interactions",
      "type": "depends-on",
      "source": "call",
      "line": 27,
      "provenance": "ast"
    },
    {
      "from": "adr:decisions/2026-04-rank-dim.md",
      "to": "adr:decisions/2026-02-rank-baseline.md",
      "type": "supersedes",
      "source": "frontmatter:supersedes",
      "provenance": "frontmatter"
    }
  ]
}
```

Note: the superseded ADR (`adr:decisions/2026-02-rank-baseline.md`) appears as the *target* of a `supersedes` edge even if the file no longer exists in the repo. v0 keeps the edge as a historical reference; v2+ drift detection can flag missing target nodes for cleanup.

## 7. What v0 deliberately does NOT do

Inclusions and exclusions equally bound the scope. v0 does not:

- **Extract semantic POR from code without docstring frontmatter.** No LLM call during `epi sync`. Pillar 1 (Bootstrap, v1) is where LLM extraction lands, and it generates *proposals*, not direct index entries.
- **Track data lineage.** No `data_asset` nodes, no `derives-from` edges. Dagster / dbt / Airflow integration is v2+ territory.
- **Track git history.** No `modifies` edges from commits. Drift detection (v2) reads `git log` directly when needed; it does not store commit-level data in the graph.
- **Cross-language parsing.** Python only in v0. Scala / SQL / R deferred to v1+ pending traction signal.
- **Incremental sync.** `epi sync` regenerates the whole index every run. Watch-mode (`epi daemon`) is v3.
- **Resolve external references.** `from third_party_lib import X` produces no `depends-on` edge to a node outside the repo (no node would exist to target).

## 8. Open questions

These items are deliberately unresolved at v0 specification time; v0 ships with a default and revisits after dogfooding evidence accumulates.

- **POR location** — docstring frontmatter (v0 default), CLAUDE.md sections, or both? Decision deferred to post-v0. The schema accommodates either by reading `por` from whichever source the parser populates.
- **Parameter marker syntax** — `# epitaxy:param` line comment is the v0 default. Alternatives considered: dedicated decorator (`@epitaxy.param`), type-annotation marker, ADR-frontmatter-only. Open until v0 dogfooding shows what feels natural to type.
- **ADR frontmatter schema compatibility** — adopt `madr` or `adr-tools` conventions, or define Epitaxy's own? Default: best-effort compatibility with `madr` field names, with Epitaxy-specific extensions (`decides:`) layered on top. Revisit if the field-name overlap is awkward in practice.
- **Index file location** — `.epitaxy/index.json` (v0 default, `.gitignore`'d by default) or repo-tracked under `epitaxy/`? Default is gitignored to avoid noisy diffs during early dogfooding; revisit once `epi sync` output is stable enough to track.

---

*Companion documents: [README.md](../README.md) (public landing page) · [ROADMAP.md](ROADMAP.md) (4-pillar architecture & v0→v3 phasing) · [CLAUDE.md](../CLAUDE.md) (session rules)*
