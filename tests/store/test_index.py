"""Round-trip tests for `.epitaxy/index.json` read/write."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from epitaxy.store import (
    Edge,
    FunctionNode,
    Index,
    IndexConfig,
    IndexStats,
    ModuleNode,
    read_index,
    write_index,
)


def make_sample_index() -> Index:
    now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    return Index(
        generated_at=now,
        generator="epitaxy 0.1.0a1",
        repo_root="/tmp/sample",
        config=IndexConfig(roots=["src/**/*.py"]),
        stats=IndexStats(modules=1, functions=1, edges=1),
        nodes=[
            ModuleNode(
                id="module:src/m.py",
                path="src/m.py",
                provenance="ast",
                extracted_at=now,
            ),
            FunctionNode(
                id="function:src/m.py::foo",
                module="module:src/m.py",
                name="foo",
                qualname="foo",
                signature="def foo()",
                line=1,
                provenance="ast",
            ),
        ],
        edges=[
            Edge.model_validate(
                {
                    "from": "module:src/m.py",
                    "to": "module:src/n.py",
                    "type": "depends-on",
                    "source": "import",
                    "provenance": "ast",
                }
            ),
        ],
    )


def test_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    original = make_sample_index()
    path = tmp_path / "index.json"

    write_index(original, path)
    restored = read_index(path)

    assert restored == original


def test_node_discriminator_picks_correct_subclass(tmp_path: Path) -> None:
    """`type: module` must deserialize to ModuleNode, `function` to FunctionNode."""
    original = make_sample_index()
    path = tmp_path / "index.json"

    write_index(original, path)
    restored = read_index(path)

    assert isinstance(restored.nodes[0], ModuleNode)
    assert isinstance(restored.nodes[1], FunctionNode)


def test_edge_serializes_with_from_not_from_underscore(tmp_path: Path) -> None:
    """`from` is a Python keyword; the alias must survive JSON round-trip."""
    path = tmp_path / "index.json"
    write_index(make_sample_index(), path)
    payload = path.read_text(encoding="utf-8")

    assert '"from":' in payload
    assert '"from_":' not in payload


def test_write_creates_parent_dirs(tmp_path: Path) -> None:
    """Bootstrap UX: `epi sync` first-run must create `.epitaxy/` if absent."""
    nested = tmp_path / ".epitaxy" / "deep" / "index.json"
    assert not nested.parent.exists()

    write_index(make_sample_index(), nested)

    assert nested.exists()
