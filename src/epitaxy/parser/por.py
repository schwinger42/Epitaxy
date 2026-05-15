"""POR YAML frontmatter parser for Python docstrings.

PR2 scope per docs/SCHEMA.md §4.

Recognition rules:

- If the docstring's first non-whitespace line is `---`, the block from that
  line through the next `---` (also on its own line, leading whitespace
  ignored) is the POR frontmatter.
- The rest of the docstring after the closing `---` is the "body" used for
  `node.doc` (first paragraph) and — once parser/refs.py wires in (commit 6)
  — for markdown-link scanning.

Error semantics (Codex round-2 Med-2, reversing round-1 Med-3):

- **Absent POR** (no `---` at docstring start, or no docstring at all) →
  `POResult(por=None, body=<raw docstring>)`. Not an error. SCHEMA §4: "a
  missed block produces por: null, not an error."
- **Recognized but malformed POR** (starts with `---` but YAML inside fails
  to parse, OR second `---` never appears within the docstring) →
  raises `PORParseError`. Caller surfaces as `ParseError`, sync exits 3 per
  CLI.md §7. Rationale: `---` signals intent; silent drop = the PR1
  fail-fast lesson per [[feedback_honor_published_contracts]].

yaml.safe_load only — never yaml.load (per plan §"Open items" YAML safety).
"""

from __future__ import annotations

from dataclasses import dataclass

import yaml


class PORParseError(Exception):
    """Recognized POR frontmatter (`---` at docstring start) failed to parse.

    Caller (parser/python.py) converts this to a ParseError for the
    containing module/function's source file.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class POResult:
    """Output of `parse_docstring`.

    - `por`: the parsed POR dict, or None if absent (no `---` at start).
    - `body`: the docstring text AFTER the closing `---`, OR the raw
      docstring if no POR was present. This is what `_first_paragraph`
      should read — never the YAML block (Codex round-1 Low-1).
    """

    por: dict | None
    body: str | None


def _split_por_block(docstring: str) -> tuple[str, str] | None:
    """Return (frontmatter_yaml, body) when a POR block is present, else None.

    Recognition: first non-whitespace line stripped of surrounding whitespace
    must equal `---`. The closing `---` must also be a line whose stripped
    content equals `---`. Lines BEFORE the opening `---` (whitespace-only)
    are tolerated and discarded.

    Raises PORParseError when an opening `---` has no matching closing `---`
    — recognized-but-broken POR (Codex round-2 Med-2 fail-fast lesson).
    """
    lines = docstring.split("\n")
    start_idx: int | None = None
    for i, line in enumerate(lines):
        if line.strip() == "":
            continue
        if line.strip() == "---":
            start_idx = i
            break
        return None  # first content line is not `---` → no POR
    if start_idx is None:
        return None  # all-blank docstring

    for j in range(start_idx + 1, len(lines)):
        if lines[j].strip() == "---":
            fm = "\n".join(lines[start_idx + 1 : j])
            body = "\n".join(lines[j + 1 :])
            return fm, body

    raise PORParseError(
        "POR frontmatter opened with `---` but no closing `---` found"
    )


def parse_docstring(docstring: str | None) -> POResult:
    """Extract POR frontmatter + body from a Python docstring.

    Raises PORParseError on recognized-but-malformed blocks.
    """
    if docstring is None:
        return POResult(por=None, body=None)

    split = _split_por_block(docstring)
    if split is None:
        return POResult(por=None, body=docstring)
    frontmatter, body = split

    try:
        data = yaml.safe_load(frontmatter)
    except yaml.YAMLError as e:
        raise PORParseError(f"malformed POR YAML frontmatter: {e}") from e

    if data is None:
        # Empty YAML body inside `---…---` — recognized as a POR block, but no fields.
        # Per SCHEMA §4 (all POR fields are optional), this is a valid empty POR.
        return POResult(por={}, body=body)

    if not isinstance(data, dict):
        raise PORParseError(
            f"POR YAML frontmatter must be a mapping, got {type(data).__name__}"
        )

    return POResult(por=data, body=body)
