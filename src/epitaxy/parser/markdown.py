"""Markdown parser → ADR + plan nodes + supersedes + decides edges.

PR2 + PR4 scope per docs/SCHEMA.md §2.3, §2.4, §3.

Frontmatter parsing is strict-fail: malformed YAML or wrong-typed `supersedes:`
value produces a `ParseError` (sync exits 3 per CLI.md §7) — matches PR1's
fail-fast lesson per [[feedback_honor_published_contracts]].

Edge-emission rules:

- `supersedes` edges: emit unconditionally per SCHEMA §6, even when the target
  ADR is absent (the dangling edge is drift signal, not a parse error). PR3
  serve renderer handles missing-target rendering downstream.
- `decides` edges (PR4): emit only when `parameters_enabled=True` per SCHEMA
  §3. The dangling-target rule applies symmetrically (PR4 amended SCHEMA §6
  to bless this explicitly). When `parameters_enabled=False`, `decides:`
  frontmatter is still parsed into `AdrNode.decides` (the field is data per
  SCHEMA §2.3) but no edges are emitted.
- `decides:` entry format: each entry must match the canonical parameter ID
  `^param:[^:]+::[^:]+::[^:]+$` (Codex round-1 Med-8). Bare names like
  `decides: - rank` are malformed → ParseError.

PR4 return type: 5-tuple `(nodes, edges, errors, body_records,
decides_claimed_param_ids: set[str])`. The new set is consumed by
parser/python.py's parameter extractor — assignments whose candidate
ParameterNode ID matches an entry are emitted with provenance="adr-frontmatter"
even without a `# epitaxy:param` comment (SCHEMA §2.5 OR clause).
"""

from __future__ import annotations

import re
from datetime import date as date_type
from pathlib import Path

import yaml

from ..store.models import AdrNode, Edge, Node, PlanNode
from .python import ParseError
from .refs import BodyRecord

# --------------------------------------------------------------------------- #
# Frontmatter + body splitting                                                #
# --------------------------------------------------------------------------- #


def _split_frontmatter(text: str) -> tuple[str | None, str, int]:
    """Return (frontmatter_yaml, body, body_start_line).

    If no frontmatter, returns (None, text, 1) — body is the whole text starting
    at file line 1.

    Frontmatter is delimited by `---` on its own line at the file start and a
    second `---` on its own line. Both delimiters must be at column 0.

    `body_start_line` is the 1-based file line where `body` begins. Used by
    parser/refs.py to attribute markdown-link references to source lines.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None, text, 1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            fm = "\n".join(lines[1:idx])
            body = "\n".join(lines[idx + 1 :])
            # closing --- at 0-indexed `idx` is on 1-based line idx+1; body
            # starts on the next line.
            return fm, body, idx + 2
    # No closing --- — treat as no frontmatter (lenient on malformed-structure;
    # YAML-level malformation is still a hard error when frontmatter IS closed).
    return None, text, 1


def _split_first_h1(body: str) -> tuple[str | None, str]:
    """Return (h1_text, body_after_h1). H1 is `# Title` at column 0."""
    lines = body.split("\n")
    for idx, line in enumerate(lines):
        if line.startswith("# ") and not line.startswith("## "):
            h1 = line[2:].strip()
            after = "\n".join(lines[idx + 1 :])
            return h1, after
    return None, body


def _first_paragraph(body: str) -> str | None:
    """First non-empty paragraph (collapsed whitespace) or None."""
    blocks = [b.strip() for b in body.split("\n\n") if b.strip()]
    if not blocks:
        return None
    return " ".join(blocks[0].split())


# --------------------------------------------------------------------------- #
# Frontmatter coercion                                                        #
# --------------------------------------------------------------------------- #


def _coerce_str(value: object) -> str | None:
    """Coerce optional frontmatter scalar to string. None passes through."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, date_type):
        return value.isoformat()
    return str(value)


def _normalize_adr_target(value: str) -> str:
    """Normalize a `supersedes:` value to a full `adr:<path>` ID.

    Accepts the canonical form (`adr:decisions/x.md`) unchanged. Otherwise
    treats the value as a repo-relative path and prepends `adr:`.
    """
    value = value.strip()
    if value.startswith("adr:"):
        return value
    return f"adr:{value}"


# --------------------------------------------------------------------------- #
# ADR + plan parsers                                                          #
# --------------------------------------------------------------------------- #


class _MarkdownParseError(Exception):
    """Internal sentinel for orchestrator → ParseError conversion."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _parse_frontmatter_dict(frontmatter: str | None) -> dict:
    """yaml.safe_load on frontmatter; reject non-mapping payloads."""
    if frontmatter is None or not frontmatter.strip():
        return {}
    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as e:
        raise _MarkdownParseError(f"malformed YAML frontmatter: {e}") from e
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise _MarkdownParseError(
            f"YAML frontmatter must be a mapping, got {type(data).__name__}"
        )
    return data


# Canonical parameter-ID format per SCHEMA §2.5 — `param:<path>::<scope>::<name>`.
# Used to validate ADR `decides:` entries (Codex round-1 Med-8).
_PARAM_ID_RE = re.compile(r"^param:[^:]+::[^:]+::[^:]+$")


def _parse_adr_file(
    abs_path: Path,
    repo_root: Path,
    *,
    parameters_enabled: bool,
) -> tuple[AdrNode, list[Edge], BodyRecord | None]:
    """Parse one ADR markdown file. Raises _MarkdownParseError on hard failure.

    Returns (AdrNode, list_of_emitted_edges, body_record_or_None). The edge
    list always contains 0 or 1 supersedes edge plus 0..N decides edges
    (only when `parameters_enabled=True` per SCHEMA §3).

    `AdrNode.decides` is populated from frontmatter regardless of
    `parameters_enabled` (the field is data per SCHEMA §2.3). Edge
    emission is gated separately.
    """
    try:
        text = abs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise _MarkdownParseError(f"UnicodeDecodeError: {e}") from e

    rel_path = str(abs_path.relative_to(repo_root)).replace("\\", "/")
    frontmatter, body, body_start_line = _split_frontmatter(text)
    fm = _parse_frontmatter_dict(frontmatter)

    # title: frontmatter > first H1 > filename stem
    title_raw = fm.get("title")
    title = _coerce_str(title_raw) if title_raw is not None else None
    h1, body_after_h1 = _split_first_h1(body)
    if not title:
        title = h1 or abs_path.stem

    # summary: first paragraph after H1, or after frontmatter if no H1
    summary = _first_paragraph(body_after_h1 if h1 is not None else body)

    edges: list[Edge] = []

    # supersedes (PR2)
    supersedes_value = fm.get("supersedes")
    supersedes_id: str | None = None
    if supersedes_value is not None:
        if not isinstance(supersedes_value, str):
            raise _MarkdownParseError(
                f"supersedes value must be a string, got "
                f"{type(supersedes_value).__name__}"
            )
        supersedes_id = _normalize_adr_target(supersedes_value)
        edges.append(
            Edge.model_validate(
                {
                    "from": f"adr:{rel_path}",
                    "to": supersedes_id,
                    "type": "supersedes",
                    "source": "frontmatter:supersedes",
                    "provenance": "frontmatter",
                }
            )
        )

    # decides (PR4): validate format always; emit edges only when enabled
    decides_raw = fm.get("decides")
    decides_list: list[str] | None = None
    if decides_raw is not None:
        if not isinstance(decides_raw, list):
            raise _MarkdownParseError(
                f"decides frontmatter must be a list, got "
                f"{type(decides_raw).__name__}"
            )
        validated: list[str] = []
        for entry in decides_raw:
            if not isinstance(entry, str):
                raise _MarkdownParseError(
                    f"decides entry must be a string, got "
                    f"{type(entry).__name__}: {entry!r}"
                )
            entry_stripped = entry.strip()
            if not _PARAM_ID_RE.match(entry_stripped):
                raise _MarkdownParseError(
                    f"decides entry {entry_stripped!r} is not a canonical "
                    f"parameter ID; expected param:<path>::<scope>::<name>"
                )
            validated.append(entry_stripped)
        decides_list = validated if validated else None
        if parameters_enabled and validated:
            for param_id in validated:
                edges.append(
                    Edge.model_validate(
                        {
                            "from": f"adr:{rel_path}",
                            "to": param_id,
                            "type": "decides",
                            "source": "frontmatter:decides",
                            "provenance": "frontmatter",
                        }
                    )
                )

    node = AdrNode(
        id=f"adr:{rel_path}",
        path=rel_path,
        title=title,
        status=_coerce_str(fm.get("status")),
        date=_coerce_str(fm.get("date")),
        supersedes=supersedes_id,
        decides=decides_list,
        summary=summary,
        provenance="frontmatter+body",
    )

    body_record: BodyRecord | None = None
    if body and body.strip():
        body_record = BodyRecord(
            body_text=body,
            source_node_id=node.id,
            source_path=rel_path,
            body_start_line=body_start_line,
            source_kind="adr-body",
        )

    return node, edges, body_record


def _parse_plan_file(
    abs_path: Path, repo_root: Path
) -> tuple[PlanNode, BodyRecord | None]:
    """Parse one plan markdown file. Raises _MarkdownParseError on hard failure."""
    try:
        text = abs_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise _MarkdownParseError(f"UnicodeDecodeError: {e}") from e

    rel_path = str(abs_path.relative_to(repo_root)).replace("\\", "/")
    frontmatter, body, body_start_line = _split_frontmatter(text)
    fm = _parse_frontmatter_dict(frontmatter)

    title_raw = fm.get("title")
    title = _coerce_str(title_raw) if title_raw is not None else None
    h1, body_after_h1 = _split_first_h1(body)
    if not title:
        title = h1 or abs_path.stem

    summary = _first_paragraph(body_after_h1 if h1 is not None else body)

    node = PlanNode(
        id=f"plan:{rel_path}",
        path=rel_path,
        title=title,
        status=_coerce_str(fm.get("status")),
        summary=summary,
        provenance="body",
    )

    body_record: BodyRecord | None = None
    if body and body.strip():
        body_record = BodyRecord(
            body_text=body,
            source_node_id=node.id,
            source_path=rel_path,
            body_start_line=body_start_line,
            source_kind="plan-body",
        )

    return node, body_record


# --------------------------------------------------------------------------- #
# Orchestrator                                                                #
# --------------------------------------------------------------------------- #


def parse_markdown(
    repo_root: Path,
    *,
    adr_dir: str = "decisions/",
    plan_dir: str = "docs/plans/",
    parameters_enabled: bool = False,
) -> tuple[
    list[Node], list[Edge], list[ParseError], list[BodyRecord], set[str]
]:
    """Scan adr_dir + plan_dir under repo_root → ADR/plan nodes + supersedes/decides edges.

    Per CLI.md §7 exit codes: per-file errors are collected, not raised, so
    the caller can still write a partial index and exit 3.

    PR4 return shape (5-tuple):

    1. `list[Node]` — emitted ADR + plan nodes
    2. `list[Edge]` — emitted supersedes edges (always) + decides edges
       (when `parameters_enabled=True`)
    3. `list[ParseError]` — per-file parse failures (malformed YAML,
       wrong-typed values, non-canonical decides entries)
    4. `list[BodyRecord]` — post-frontmatter body texts for the
       references-edge final pass in parser/refs.py
    5. `set[str]` — `decides_claimed_param_ids`: union of validated
       parameter IDs from every ADR's `decides:` list, regardless of
       `parameters_enabled`. parser/python uses this for SCHEMA §2.5's
       OR-clause: assignments whose candidate ParameterNode ID is in
       this set get emitted as parameter nodes even without the
       `# epitaxy:param` comment marker.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []
    errors: list[ParseError] = []
    body_records: list[BodyRecord] = []
    decides_claimed: set[str] = set()

    adr_root = repo_root / adr_dir
    if adr_root.is_dir():
        for md_path in sorted(adr_root.rglob("*.md")):
            if not md_path.is_file():
                continue
            try:
                adr_node, file_edges, body_record = _parse_adr_file(
                    md_path, repo_root, parameters_enabled=parameters_enabled
                )
            except _MarkdownParseError as e:
                errors.append(ParseError(path=md_path, reason=e.reason))
                continue
            nodes.append(adr_node)
            edges.extend(file_edges)
            if body_record is not None:
                body_records.append(body_record)
            # Always accumulate the set, even when parameters_enabled=False:
            # parser/python may want to know (e.g. for a forthcoming dry-run
            # mode, or for diagnostic logging). Cheap to collect.
            if adr_node.decides:
                decides_claimed.update(adr_node.decides)

    plan_root = repo_root / plan_dir
    if plan_root.is_dir():
        for md_path in sorted(plan_root.rglob("*.md")):
            if not md_path.is_file():
                continue
            try:
                plan_node, body_record = _parse_plan_file(md_path, repo_root)
            except _MarkdownParseError as e:
                errors.append(ParseError(path=md_path, reason=e.reason))
                continue
            nodes.append(plan_node)
            if body_record is not None:
                body_records.append(body_record)

    return nodes, edges, errors, body_records, decides_claimed
