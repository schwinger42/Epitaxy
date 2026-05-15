"""Markdown parser → ADR + plan nodes + supersedes edges.

PR2 scope per docs/SCHEMA.md §2.3, §2.4, §3.

Frontmatter parsing is strict-fail: malformed YAML or wrong-typed `supersedes:`
value produces a `ParseError` (sync exits 3 per CLI.md §7) — matches PR1's
fail-fast lesson per [[feedback_honor_published_contracts]].

Supersedes edges are emitted **unconditionally** — even when the target ADR
is absent from this index, per SCHEMA §6 worked example: "the superseded ADR
appears as the target of a supersedes edge even if the file no longer exists.
v0 keeps the edge as a historical reference." This reverts an earlier plan
draft (Codex round-1 Med-2) against SCHEMA ground truth (Codex round-2 High-1).

NOT in PR2:
- `decides:` frontmatter — ignored (PR4 territory; Codex round-1 High-2 lock)
- POR-style body parsing — that's parser/por.py for Python docstrings
- markdown link `references` edges — that's parser/refs.py (final-pass)
"""

from __future__ import annotations

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


def _parse_adr_file(
    abs_path: Path, repo_root: Path
) -> tuple[AdrNode, Edge | None, BodyRecord | None]:
    """Parse one ADR markdown file. Raises _MarkdownParseError on hard failure.

    Returns (AdrNode, supersedes_edge_or_None, body_record_or_None).
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

    # supersedes
    supersedes_value = fm.get("supersedes")
    supersedes_id: str | None = None
    supersedes_edge: Edge | None = None
    if supersedes_value is not None:
        if not isinstance(supersedes_value, str):
            raise _MarkdownParseError(
                f"supersedes value must be a string, got "
                f"{type(supersedes_value).__name__}"
            )
        supersedes_id = _normalize_adr_target(supersedes_value)
        supersedes_edge = Edge.model_validate(
            {
                "from": f"adr:{rel_path}",
                "to": supersedes_id,
                "type": "supersedes",
                "source": "frontmatter:supersedes",
                "provenance": "frontmatter",
            }
        )

    node = AdrNode(
        id=f"adr:{rel_path}",
        path=rel_path,
        title=title,
        status=_coerce_str(fm.get("status")),
        date=_coerce_str(fm.get("date")),
        supersedes=supersedes_id,
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

    return node, supersedes_edge, body_record


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
) -> tuple[list[Node], list[Edge], list[ParseError], list[BodyRecord]]:
    """Scan adr_dir + plan_dir under repo_root → ADR/plan nodes + supersedes edges.

    Per CLI.md §7 exit codes: per-file errors are collected, not raised, so the
    caller can still write a partial index and exit 3.

    PR2: returns a 4th element — `list[BodyRecord]` accumulating ADR + plan
    body texts (post-frontmatter) for the references-edge final pass in
    parser/refs.py.
    """
    nodes: list[Node] = []
    edges: list[Edge] = []
    errors: list[ParseError] = []
    body_records: list[BodyRecord] = []

    adr_root = repo_root / adr_dir
    if adr_root.is_dir():
        for md_path in sorted(adr_root.rglob("*.md")):
            if not md_path.is_file():
                continue
            try:
                adr_node, supersedes_edge, body_record = _parse_adr_file(
                    md_path, repo_root
                )
            except _MarkdownParseError as e:
                errors.append(ParseError(path=md_path, reason=e.reason))
                continue
            nodes.append(adr_node)
            if supersedes_edge is not None:
                edges.append(supersedes_edge)
            if body_record is not None:
                body_records.append(body_record)

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

    return nodes, edges, errors, body_records
