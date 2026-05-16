# Epitaxy — MCP tool surface (v0, Pillar 4)

> Design-first specification for the Epitaxy v0 MCP server's tool surface. Defines tool names, input/output shapes, error codes, and contract behavior — **before** server code is written. Companion to [SCHEMA.md](SCHEMA.md) (the index format the tools consume) and [CLI.md](CLI.md) (the `epi mcp serve` startup command).

## 0. Why this doc exists

[ROADMAP §2.4](ROADMAP.md#pillar-4--query) commits Epitaxy to a Pillar-4 MCP server exposing 5 tools long-term; v0 ships 3 of them. [CLI.md §4](CLI.md#4-epi-mcp--pillar-4-mcp-server-startup) commits to `epi mcp serve` as the startup command. Neither doc defines the actual JSON-RPC tool surface. This doc does.

Specifically it commits to:

1. **Three v0 tools** — `por_explain` (functional), `por_trace` (conditional on parameter parsing having been enabled at sync time), `por_lineage` (documented stub returning a typed error in v0).
2. **Wrapped result DTOs** with explicit field names (`ExplainResult`, `TraceResult`, etc.) rather than raw `index.json` node dumps. Explicit fields help LLM consumers understand what's available without re-deriving structure from context.
3. **JSON-RPC 2.0 error codes** in the server-defined range for Epitaxy-specific failures (`NodeNotFound`, `ParameterParsingDisabled`, etc.).
4. **Read-only contract.** No tool mutates the index, repo, or any file. Pillar 2a (in-session MCP `prompts/` exports that drive write-back) is v1.

What's NOT in v0:

- MCP `prompts/` exports — Pillar 2a, deferred to v1.
- Tools `playbook_for_role`, `next_action_for_path` from ROADMAP §2.4 — deferred to v1.
- Real `por_lineage` implementation — gated on `data_asset` node type (deferred to v1+, see [SCHEMA §2.6](SCHEMA.md#26-data_asset-deferred-to-v1)).
- Authentication / TLS / rate-limiting on HTTP transport. The non-loopback warning per [CLI.md §4](CLI.md#4-epi-mcp--pillar-4-mcp-server-startup) covers the exposure surface; v0 ships read-only + DNS-rebinding protection + opt-in `--allowed-origins` but no auth.

## 1. Overview

v0 MCP server lifecycle:

1. User runs `epi mcp serve` (see [CLI.md §4](CLI.md#4-epi-mcp--pillar-4-mcp-server-startup)).
2. Server reads `.epitaxy/index.json` once at startup to validate it exists, then re-reads on every tool call (per CLI.md "no long-lived state").
3. Server registers 3 tools via the Anthropic MCP SDK; client (Claude Code / Codex / Cursor / etc.) calls them via JSON-RPC 2.0 over stdio (default) or HTTP (MCP streamable-http). Both transports are first-class — see [CLI.md §4](CLI.md#4-epi-mcp--pillar-4-mcp-server-startup) for flags + DNS-rebinding protection details.
4. Server is read-only and stateless. Stop with SIGINT.

All tools follow this call shape (MCP spec):

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "por_explain",
    "arguments": { "node_id": "..." }
  }
}
```

The sections below document each tool's `arguments` shape (input) and `result` shape (output). The JSON-RPC envelope is per MCP spec and is not duplicated per tool.

## 2. `por_explain(node_id)` — full intent dump for one node

Primary tool. Functional in v0 for the 4 node types the default parser emits (`module`, `function`, `adr`, `plan`); also works for `parameter` nodes when `--parameters` was enabled at sync time.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `node_id` | string | yes | A node ID per [SCHEMA §2](SCHEMA.md#2-node-types), e.g. `function:src/ranker/model.py::Ranker.fit` |

### Output (`ExplainResult`)

```json
{
  "node": { /* one full node dict per SCHEMA §2 */ },
  "incident_edges": [ /* edges where this node is `from` or `to`, per SCHEMA §3 */ ],
  "related_pors": [ /* inline POR blocks for this node + adjacent nodes, deduplicated */ ],
  "provenance_summary": {
    "node_source": "ast",
    "edge_sources": ["import", "frontmatter:supersedes"]
  }
}
```

Field notes:

- `node` — verbatim node dict from the index, including any `por` block.
- `incident_edges` — both directions (`from == node_id` OR `to == node_id`). Sorted: outgoing first, then incoming.
- `related_pors` — POR blocks (per [SCHEMA §4](SCHEMA.md#4-inline-por-structure-optional)) extracted from the requested node AND its immediate neighbors via `incident_edges`. Lets a single tool call return enough context for an LLM to reason without follow-up calls.
- `provenance_summary` — flat summary of `provenance` field values across all returned nodes and edges. Makes "show me only `ast`-sourced facts" trivial for downstream LLMs.

### Example

Input:

```json
{ "node_id": "function:src/ranker/model.py::Ranker.fit" }
```

Output (excerpted):

```json
{
  "node": {
    "id": "function:src/ranker/model.py::Ranker.fit",
    "type": "function",
    "module": "module:src/ranker/model.py",
    "name": "fit",
    "qualname": "Ranker.fit",
    "signature": "def fit(self, interactions, rank=128)",
    "line": 24,
    "provenance": "ast"
  },
  "incident_edges": [
    {
      "from": "function:src/ranker/model.py::Ranker.fit",
      "to": "function:src/data/load.py::load_interactions",
      "type": "depends-on",
      "source": "call",
      "line": 27,
      "provenance": "ast"
    }
  ],
  "related_pors": [],
  "provenance_summary": {
    "node_source": "ast",
    "edge_sources": ["call"]
  }
}
```

### Errors

| Code | Name | When |
|---|---|---|
| `-32602` | (JSON-RPC invalid params) | `node_id` is not a string, or malformed format |
| `-32001` | `NodeNotFound` | `node_id` has valid syntax but no node with that ID exists in the index |

## 3. `por_trace(parameter_id)` — decision trail for a tuned parameter

Conditional tool. Functional when the index was synced with `epi sync --parameters` (or `parameters_enabled = true` in `pyproject.toml`). Returns a typed error otherwise — same tool name, no client breakage.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `parameter_id` | string | yes | A parameter node ID per [SCHEMA §2.5](SCHEMA.md#25-parameter-opt-in---parameters), e.g. `param:src/ranker/model.py::Ranker.fit::rank` |

### Output (`TraceResult`)

```json
{
  "parameter": { /* parameter node dict per SCHEMA §2.5 */ },
  "current_value": "128",
  "decision_chain": [
    /*
     * ADR nodes that have a `decides` edge to this parameter,
     * ordered newest-first via the supersedes chain rooted at the
     * lex-first active head. Index 0 = currently-active ADR; later
     * entries are historical (superseded) decisions retained for audit.
     */
  ],
  "parallel_heads": [
    /*
     * Populated when MULTIPLE ADRs decide this parameter AND none is
     * superseded — i.e. the decision-chain has more than one root.
     * Empty array otherwise. LLM consumers should treat a non-empty
     * `parallel_heads` as an ambiguity-signal: the active decision
     * for this parameter is not unique and may need human triage.
     * `decision_chain` is still populated with the lex-first head's
     * chain so single-head consumers can ignore this field.
     */
  ],
  "notes": [
    /*
     * Cycle-truncation + no-head warnings, e.g.
     *   "cycle in supersedes chain involving 'adr:decisions/x.md'
     *    (already visited); chain truncated at 'adr:decisions/y.md'."
     * Empty array when no anomalies. Surfaces graph-shape issues so
     * downstream consumers can flag malformed ADR chains for cleanup.
     */
  ],
  "provenance": {
    "parameter": "ast+comment",
    "decisions": ["frontmatter:decides", "frontmatter:supersedes"]
  }
}
```

Field notes:

- `current_value` — verbatim RHS of the assignment from `parameter.value`, source-rendered. Not evaluated; the LLM sees `"128"` (or `"int(os.environ['RANK'])"`) as-is. Per the PR4 implementation this uses `ast.get_source_segment`, so `1e-3` stays `"1e-3"` (NOT `"0.001"` — which `ast.unparse` would produce).
- `decision_chain` — full ADR nodes (not just IDs) so the LLM can read titles, statuses, and dates without additional `por_explain` calls. Order: currently-accepted ADR first, then chained backwards via `supersedes` edges. When `parallel_heads` is non-empty, the chain is rooted at the lex-first head.
- `parallel_heads` — ALWAYS present as an array; empty when there's a single active head. Populated when multiple ADRs decide the same parameter and none is superseded by another. LLM consumers reading the TraceResult should check `len(parallel_heads) > 0` to detect ambiguity.
- `notes` — ALWAYS present as an array; empty when the chain walks cleanly. Populated for cycles in the supersedes graph (chain truncates + a note is emitted) or the no-head edge case (all deciders are themselves superseded; defensive fallback to lex-first relevant ADR).

### Example

Input:

```json
{ "parameter_id": "param:src/ranker/model.py::Ranker.fit::rank" }
```

Output (excerpted):

```json
{
  "parameter": {
    "id": "param:src/ranker/model.py::Ranker.fit::rank",
    "type": "parameter",
    "value": "128",
    "line": 24,
    "provenance": "ast+comment"
  },
  "current_value": "128",
  "decision_chain": [
    {
      "id": "adr:decisions/2026-04-rank-dim.md",
      "title": "ALS rank dimension — 128 over 64",
      "status": "accepted",
      "date": "2026-04-12",
      "supersedes": "adr:decisions/2026-02-rank-baseline.md"
    },
    {
      "id": "adr:decisions/2026-02-rank-baseline.md",
      "title": "Initial ALS rank: 64",
      "status": "superseded"
    }
  ],
  "parallel_heads": [],
  "notes": [],
  "provenance": {
    "parameter": "ast+comment",
    "decisions": ["frontmatter:decides", "frontmatter:supersedes"]
  }
}
```

(Both `parallel_heads` and `notes` are always-present arrays per the field-notes contract above; they're empty in this single-head clean-walk example.)

### Errors

| Code | Name | When |
|---|---|---|
| `-32001` | `NodeNotFound` | Parameter extraction WAS enabled (`index.config.parameters_enabled == true`) but no node with `parameter_id` exists in the index. Distinct from `ParameterParsingDisabled` — parsing was enabled; this specific ID just isn't there (assignment may not be marked with `# epitaxy:param` or claimed by any ADR's `decides:` frontmatter). |
| `-32002` | `ParameterParsingDisabled` | `index.config.parameters_enabled == false` — the index was synced without `--parameters`. Error message: `"index was synced without parameter extraction (parameters_enabled = false). Re-run \`epi sync --parameters\` to enable."` Note (PR4): this is gated on the CONFIG flag, not on the actual presence of parameter nodes. An enabled index with zero marked parameters returns `NodeNotFound` for any `parameter_id`, not `ParameterParsingDisabled`. |
| `-32003` | `NotAParameter` | `parameter_id` resolves, but the node's `type` is not `"parameter"` |

## 4. `por_lineage(asset_id)` — documented stub in v0

Spec'd in v0 for surface completeness; gated on the `data_asset` node type, which is deferred to v1+ per [SCHEMA §2.6](SCHEMA.md#26-data_asset-deferred-to-v1). v0 returns a typed error on every call.

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `asset_id` | string | yes | A data asset ID per [SCHEMA §2.6](SCHEMA.md#26-data_asset-deferred-to-v1), e.g. `data:bigquery:project.dataset.users_clicks` |

### Output (v0)

Always errors. Never returns a `LineageResult` body in v0.

### Errors

| Code | Name | When |
|---|---|---|
| `-32004` | `AssetTypeNotSupportedInV0` | All v0 calls. Error message: `"data_asset nodes are deferred to v1+ — see SCHEMA §2.6. This tool is reserved in the v0 surface for forward compatibility."` |

### Why spec it anyway

Three reasons to ship the tool name and error in v0 rather than removing it:

1. **Forward compatibility.** MCP clients that cache tool lists (some do, by spec or by convention) don't have to invalidate-and-rediscover later. They see `por_lineage` in v0 and get a useful error today; same wire shape returns real data in v1+.
2. **Honest scope signaling.** A client developer who sees `por_lineage` listed but failing knows lineage is part of Epitaxy's long-term surface, not an oversight or planning gap.
3. **Surface symmetry with [ROADMAP §2.4](ROADMAP.md#pillar-4--query).** ROADMAP commits to all 3 v0 tools by name; removing one would force a ROADMAP edit and create a documentation-vs-shipped-surface mismatch.

## 5. Result shape conventions

All tools follow these rules:

- **Wrapped DTOs**, not raw node dumps. Every output has a top-level object with named fields (`node`, `incident_edges`, `parameter`, `decision_chain`, etc.) — even when the wrapper feels redundant for a single-node case. Future fields can be added without breaking existing clients.
- **Embed full sub-objects** (not just IDs) wherever cross-reference is natural. Saves the LLM follow-up calls and reduces round-trip latency. Cost is index size in the response — acceptable for v0 repo sizes.
- **Provenance always preserved.** Every node dict and edge dict carries its original `provenance` field. Tools that aggregate also expose a `provenance_summary` for filterability.
- **Sort order is stable.** Edges: outgoing first, then incoming. Decision chains: currently-active first, then historical via `supersedes`. Lists otherwise: alphabetical by ID. Lets LLM-generated diffs between calls be meaningful.

## 6. Error codes

MCP distinguishes two error layers:

**Protocol-level errors** — JSON-RPC 2.0 error envelope. Fires for malformed requests, missing methods, or input that fails the tool's pydantic schema. MCP clients receive these as standard JSON-RPC `error` objects.

| Code | Name | When |
|---|---|---|
| `-32700` | Parse error | Malformed JSON-RPC payload |
| `-32600` | Invalid request | Missing `jsonrpc`/`method`/`id` fields |
| `-32601` | Method not found | Unknown tool name |
| `-32602` | Invalid params | Input doesn't match tool's input schema (e.g. `node_id` not a string) |
| `-32603` | Internal error | Server bug / unexpected exception |

**Tool-level errors** — per MCP spec, surface as `CallToolResult` with `isError: true` and a `TextContent` describing the failure. They are NOT delivered through the JSON-RPC `error` envelope. Because MCP clients only see the text content (not a structured code field), Epitaxy prefixes every tool-error message with a machine-readable `[code:-XXXX]` token so clients can dispatch on category without parsing free-form text.

| Code | Name | Tool(s) | Surfaces as |
|---|---|---|---|
| `-32001` | `NodeNotFound` | all | `[code:-32001] node 'X' not found in index` |
| `-32002` | `ParameterParsingDisabled` | `por_trace` | `[code:-32002] index was synced without parameter extraction (parameters_enabled = false). Re-run \`epi sync --parameters\` to enable.` |
| `-32003` | `NotAParameter` | `por_trace` | `[code:-32003] node 'X' is type 'Y', not 'parameter'` |
| `-32004` | `AssetTypeNotSupportedInV0` | `por_lineage` | `[code:-32004] data_asset nodes are deferred to v1+; see SCHEMA §2.6` |

Range `-32005` through `-32099` reserved for v1+ tools.

**Client implementation note**: to extract the code, match `^\[code:(-?\d+)\]` against `result.content[0].text` when `result.isError` is true.

## 7. Implementation notes

Not strictly part of the design surface — captured here so the implementation PR has a clear starting reference and the design-doc / code boundary is explicit.

- **Library**: Anthropic MCP SDK (Python `mcp` package, see `pyproject.toml` future-dependencies comment).
- **Tool registration**: pydantic-derived JSON schemas at registration time. Output shapes spec'd in this doc become `pydantic.BaseModel` subclasses; input shapes map to method signatures with typed parameters.
- **Index access**: re-read `.epitaxy/index.json` on every tool call (sub-millisecond for v0 repo sizes per [CLI.md §4](CLI.md#4-epi-mcp--pillar-4-mcp-server-startup)). No caching, no incremental update tracking.
- **No write paths.** Tool implementations never call `open(..., "w")`. The v0 MCP server has no write capability at all — enforced by code review, not just convention.
- **Transport**: stdio (default) for local AI-agent integration on the same machine; HTTP (MCP streamable-http) for remote-host or multi-client scenarios per [CLI.md §4](CLI.md#4-epi-mcp--pillar-4-mcp-server-startup). HTTP binds on `--host` (default `127.0.0.1`) + `--port` (default `7321`) with DNS-rebinding protection ON by default — `Origin` and `Host` headers validated against an allowlist; invalid origins receive HTTP 403. The allowlist auto-derives from `--host` + `--port`; users can expand it via `--allowed-origins URL1,URL2`. Explicit `--allowed-origins ""` disables protection (NOT recommended; warned in stderr).

## 8. Open items / reserved for v1+

These are explicit non-goals for v0; listed so the surface boundary is clear.

- **`playbook_for_role(role)`** — ROADMAP §2.4 lists this tool. Deferred to v1 alongside the playbook generation pipeline (Pillar 1 work).
- **`next_action_for_path(file)`** — ROADMAP §2.4. Deferred to v1; requires drift-detection signal that doesn't exist until Pillar 2b.
- **MCP `prompts/` exports** — Pillar 2a (in-session AI-driven CLAUDE.md updates). Deferred to v1.
- **Real `por_lineage` implementation** — gated on `data_asset` parser support ([SCHEMA §2.6](SCHEMA.md#26-data_asset-deferred-to-v1), v1+).
- **Streaming tool results** — MCP supports streaming for long-running tools. v0 doesn't need it (all calls sub-millisecond); revisit if v1+ tools become slow.
- **Tool-level caching** — see implementation notes; deferred until profiling justifies it.

---

*Companion documents: [README.md](../README.md) · [ROADMAP.md](ROADMAP.md) · [SCHEMA.md](SCHEMA.md) · [CLI.md](CLI.md) · [CLAUDE.md](../CLAUDE.md)*
