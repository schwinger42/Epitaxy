"""References-edge final-pass extractor + BodyRecord pipeline.

PR2 scope per docs/SCHEMA.md §3.

Per Codex round-1 High-1, this runs AS THE FINAL graph pass — after both
the Python parser and the markdown parser have populated nodes — so that
a docstring → ADR link, an ADR → plan link, etc., all resolve.

Input: `BodyRecord` accumulator (filled by parser/python.py for docstring
bodies and parser/markdown.py for ADR/plan bodies) plus the union of
already-emitted nodes.

Output: `references` edges with `source="body-mention"` /
`provenance="body-mention"` per SCHEMA §1.4 + §3 vocab (Codex round-2 Low-1).

Edge cases honored (Codex round-2):

- **Med-1 (newline-preserving masking)**: fenced-code-block masking
  replaces each character with a space (preserves `\\n` positions) so
  subsequent line counting against the masked text yields correct
  in-body line offsets.
- **Med-3 (path normalization)**: `Path.resolve()` + `relative_to(repo_root)`
  rejects absolute paths and out-of-repo escapes portably.
- **Med-4 (fence state machine)**: handles backtick + tilde fences with
  varying delimiter lengths via a small line-by-line state machine; naive
  regex was insufficient. Indented-4-space code blocks are OUT OF SCOPE
  for PR2 — documented + tested via xfail.
- **High-2 (all node types as targets)**: target index includes adr/plan/
  module IDs so `[doc](decisions/x.md)` resolves to the ADR node, not
  just module/function nodes.

Image markdown (`![alt](path)`) rejected via `(?<!\\!)` lookbehind.
URL-scheme targets (`http:`, `mailto:`, etc.) rejected via scheme regex.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ..store.models import Edge, Node, ParameterNode


@dataclass(frozen=True)
class BodyRecord:
    """One textual body that may contain `[label](path)` references.

    Accumulated by parser/python.py (one per docstring with non-empty body)
    and parser/markdown.py (one per ADR/plan body). Consumed by
    `extract_references`.
    """

    body_text: str
    source_node_id: str
    source_path: str  # repo-relative POSIX path of containing file
    body_start_line: int  # 1-based line where body_text begins in the source file
    source_kind: Literal["docstring", "adr-body", "plan-body"]


# --------------------------------------------------------------------------- #
# Code-block masking — fence state machine + inline backticks                 #
# --------------------------------------------------------------------------- #

_FENCE_OPENER = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*\S*\s*$")


def _mask_code(text: str) -> str:
    """Replace fenced code blocks + inline code spans with spaces, preserving
    `\\n` positions and overall length so in-body line/offset math stays valid.
    """
    # Pass 1: fence state machine (line-oriented)
    lines = text.split("\n")
    out_lines: list[str] = []
    in_fence = False
    fence_char = ""
    fence_len = 0
    for line in lines:
        if not in_fence:
            m = _FENCE_OPENER.match(line)
            if m:
                in_fence = True
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                out_lines.append(" " * len(line))
                continue
            out_lines.append(line)
        else:
            stripped = line.strip()
            is_closer = (
                stripped
                and all(c == fence_char for c in stripped)
                and len(stripped) >= fence_len
            )
            out_lines.append(" " * len(line))
            if is_closer:
                in_fence = False
                fence_char = ""
                fence_len = 0
    masked = "\n".join(out_lines)

    # Pass 2: naive inline backticks — single-line spans only.
    # CommonMark allows `` `code with ` inside` `` (double-backtick spans);
    # PR2 explicitly does not handle that. Documented + tested via xfail.
    masked = re.sub(
        r"`[^`\n]*`",
        lambda m: " " * len(m.group(0)),
        masked,
    )
    return masked


# --------------------------------------------------------------------------- #
# Link parsing + target resolution                                            #
# --------------------------------------------------------------------------- #

_LINK_RE = re.compile(r"(?<!\!)\[([^\]\n]+)\]\(([^)\n]+)\)")
_URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+\-.]*:")


def _strip_target(target: str) -> str:
    """Drop URL fragment + query — `foo.md#L42?x=1` → `foo.md`."""
    return target.split("#", 1)[0].split("?", 1)[0].strip()


def _is_external_url(target: str) -> bool:
    return bool(_URL_SCHEME_RE.match(target))


def _candidate_repo_relative_paths(
    repo_root: Path, source_path: str, target: str
) -> list[str]:
    """Return all viable repo-relative POSIX paths for this link target.

    Two interpretations attempted, both via `Path.resolve()` +
    `relative_to(repo_root)` per Codex round-2 Med-3:
    1. Repo-relative — `[x](src/m.py)` from anywhere → `src/m.py`.
    2. Source-file-relative — `[x](m.py)` from `src/a.py` → `src/m.py`.

    Caller picks the one that exists in the target index. Both are returned
    (deduplicated) so the caller can disambiguate by node-presence.

    Rejects:
    - empty targets
    - absolute paths (`/etc/passwd`)
    - paths that resolve outside `repo_root`
    """
    if not target or Path(target).is_absolute():
        return []
    repo_root_resolved = repo_root.resolve()
    raw_candidates = [
        repo_root / target,
        (repo_root / source_path).parent / target,
    ]
    out: list[str] = []
    seen: set[str] = set()
    for cand in raw_candidates:
        try:
            resolved = Path(cand).resolve()
            rel = resolved.relative_to(repo_root_resolved)
        except (ValueError, OSError):
            continue
        rel_str = rel.as_posix()
        if rel_str not in seen:
            out.append(rel_str)
            seen.add(rel_str)
    return out


def _resolve_to_repo_relative(
    repo_root: Path, source_path: str, target: str
) -> str | None:
    """Single-path back-compat shim — returns the FIRST candidate.

    Kept for tests; callers that need disambiguation against a target index
    should use `_candidate_repo_relative_paths` directly.
    """
    cands = _candidate_repo_relative_paths(repo_root, source_path, target)
    return cands[0] if cands else None


# --------------------------------------------------------------------------- #
# Final-pass orchestrator                                                     #
# --------------------------------------------------------------------------- #


def extract_references(
    repo_root: Path,
    nodes: list[Node],
    body_records: list[BodyRecord],
) -> list[Edge]:
    """Emit `references` edges from markdown links in body texts.

    Per Codex round-2 High-2, the target index covers all emitted node types
    (module/function/adr/plan), not just module/function — `[doc](decisions/x.md)`
    resolves to the ADR node.

    Function-level links (`module.py#L42` or `module.py::Cls.method`) are OUT
    OF SCOPE for PR2 — the fragment is stripped before lookup so the link
    resolves to the containing module, not the function. PR3+ can add
    function-anchor parsing.
    """
    target_index: dict[str, str] = {}
    for node in nodes:
        path = getattr(node, "path", None)
        if path:
            target_index[path] = node.id

    edges: list[Edge] = []
    seen: set[tuple[str, str]] = set()

    for record in body_records:
        masked = _mask_code(record.body_text)
        for match in _LINK_RE.finditer(masked):
            raw_target = match.group(2)
            target = _strip_target(raw_target)
            if not target or _is_external_url(target):
                continue
            target_node_id: str | None = None
            for cand in _candidate_repo_relative_paths(
                repo_root, record.source_path, target
            ):
                candidate_id = target_index.get(cand)
                if candidate_id is not None:
                    target_node_id = candidate_id
                    break
            if target_node_id is None or target_node_id == record.source_node_id:
                continue

            in_body_offset = masked[: match.start()].count("\n")
            abs_line = record.body_start_line + in_body_offset

            edge_key = (record.source_node_id, target_node_id)
            if edge_key in seen:
                continue
            edges.append(
                Edge.model_validate(
                    {
                        "from": record.source_node_id,
                        "to": target_node_id,
                        "type": "references",
                        "source": "body-mention",
                        "line": abs_line,
                        "provenance": "body-mention",
                    }
                )
            )
            seen.add(edge_key)

    return edges


# --------------------------------------------------------------------------- #
# decided_by post-pass (PR4)                                                  #
# --------------------------------------------------------------------------- #


def populate_decided_by(nodes: list[Node], edges: list[Edge]) -> None:
    """Mutate `ParameterNode.decided_by` in-place from `decides` edges.

    Per SCHEMA §2.5: `decided_by` is the list of ADR IDs that decide this
    parameter's value. Walks `decides` edges (which were emitted by
    parser/markdown.py from ADR `decides:` frontmatter); for each edge whose
    target parameter exists in the index, appends the source ADR ID to that
    parameter's `decided_by` list.

    Codex round-1 Low-11: graph-shape operations belong here (alongside the
    references final-pass) rather than in cli/app.py orchestration.

    **Dangling decides edges** (target parameter absent — SCHEMA §6 amended
    in PR4 C1) leave `decided_by=None` on no real node. The dangling edge
    remains in the index as drift signal at the edge level; `decided_by`
    only carries resolvable provenance to keep node-level data faithful.

    Deterministic ordering: `decided_by` lists are sorted by ADR ID so
    `por_explain` / `por_trace` output is stable across runs (matches the
    PR1+PR3 "alphabetical by ID" sort convention from MCP.md §5).
    """
    node_by_id: dict[str, Node] = {n.id: n for n in nodes}
    # Collect first, then assign sorted lists — avoids mutating during iter.
    deciders_for: dict[str, list[str]] = {}
    for edge in edges:
        if edge.type != "decides":
            continue
        target = node_by_id.get(edge.to)
        if isinstance(target, ParameterNode):
            deciders_for.setdefault(target.id, []).append(edge.from_)

    for param_id, adr_ids in deciders_for.items():
        param = node_by_id[param_id]
        assert isinstance(param, ParameterNode)
        param.decided_by = sorted(set(adr_ids))
