"""Read / write `.epitaxy/index.json` from disk."""

from __future__ import annotations

from pathlib import Path

from .models import Index


def write_index(index: Index, path: Path) -> None:
    """Write `index` to `path` as pretty-printed JSON.

    Parent directories are created if absent (matches CLI.md §2 bootstrap UX —
    `epi sync` self-creates `.epitaxy/` on first run).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = index.model_dump_json(by_alias=True, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")


def read_index(path: Path) -> Index:
    """Load and validate an Index from disk.

    Raises FileNotFoundError if missing, pydantic.ValidationError if malformed.
    """
    raw = path.read_text(encoding="utf-8")
    return Index.model_validate_json(raw)
