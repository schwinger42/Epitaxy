"""Tests for parser/markdown.py — ADR + plan parsing + supersedes edges."""

from __future__ import annotations

from pathlib import Path

import pytest

from epitaxy.parser import parse_markdown
from epitaxy.parser.python import ParseError
from epitaxy.store.models import AdrNode, PlanNode


def _write(p: Path, content: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# ADR happy paths                                                             #
# --------------------------------------------------------------------------- #


def test_adr_full_frontmatter(tmp_path: Path) -> None:
    _write(
        tmp_path / "decisions" / "2026-04-rank-dim.md",
        """---
title: ALS rank dimension — 128 over 64
status: accepted
date: 2026-04-12
supersedes: adr:decisions/2026-02-rank-baseline.md
---

# ALS rank dimension — 128 over 64

Bumps rank from 64 to 128 to give headroom on long-tail items.
""",
    )

    nodes, edges, errors, _bodies, _ = parse_markdown(tmp_path)

    assert errors == []
    assert len(nodes) == 1
    assert len(edges) == 1

    node = nodes[0]
    assert isinstance(node, AdrNode)
    assert node.id == "adr:decisions/2026-04-rank-dim.md"
    assert node.title == "ALS rank dimension — 128 over 64"
    assert node.status == "accepted"
    assert node.date == "2026-04-12"
    assert node.supersedes == "adr:decisions/2026-02-rank-baseline.md"
    assert node.summary == "Bumps rank from 64 to 128 to give headroom on long-tail items."
    assert node.provenance == "frontmatter+body"

    edge = edges[0]
    assert edge.type == "supersedes"
    assert edge.from_ == "adr:decisions/2026-04-rank-dim.md"
    assert edge.to == "adr:decisions/2026-02-rank-baseline.md"
    assert edge.source == "frontmatter:supersedes"
    assert edge.provenance == "frontmatter"


def test_adr_no_frontmatter_uses_h1_as_title(tmp_path: Path) -> None:
    _write(
        tmp_path / "decisions" / "minimal.md",
        "# Minimal ADR\n\nNo frontmatter at all.",
    )
    nodes, edges, errors, _bodies, _ = parse_markdown(tmp_path)
    assert errors == [] and edges == [] and len(nodes) == 1
    assert nodes[0].title == "Minimal ADR"
    assert nodes[0].status is None
    assert nodes[0].date is None


def test_adr_no_frontmatter_no_h1_uses_filename(tmp_path: Path) -> None:
    _write(tmp_path / "decisions" / "bare-stem.md", "No structure here.")
    nodes, _, errors, _, _ = parse_markdown(tmp_path)
    assert errors == [] and nodes[0].title == "bare-stem"


def test_adr_yaml_date_coerced_to_iso_string(tmp_path: Path) -> None:
    """yaml.safe_load returns datetime.date for `date: 2026-04-12`; must coerce to str."""
    _write(
        tmp_path / "decisions" / "date.md",
        "---\ntitle: t\ndate: 2026-04-12\n---\n# t",
    )
    nodes, _, _, _, _ = parse_markdown(tmp_path)
    assert nodes[0].date == "2026-04-12"
    assert isinstance(nodes[0].date, str)


# --------------------------------------------------------------------------- #
# Supersedes — emit edge even when target absent (Codex round-2 High-1)       #
# --------------------------------------------------------------------------- #


def test_supersedes_edge_emitted_even_when_target_absent(tmp_path: Path) -> None:
    """SCHEMA §6: 'supersedes edge persists even if the file no longer exists.'"""
    _write(
        tmp_path / "decisions" / "current.md",
        "---\ntitle: current\nsupersedes: adr:decisions/historical-gone.md\n---\n# current",
    )
    nodes, edges, errors, _bodies, _ = parse_markdown(tmp_path)

    assert errors == []
    assert len(edges) == 1
    assert edges[0].to == "adr:decisions/historical-gone.md"
    # The target ADR file does NOT exist in tmp_path; the edge is still emitted.


def test_supersedes_bare_path_normalized_to_adr_prefix(tmp_path: Path) -> None:
    _write(
        tmp_path / "decisions" / "current.md",
        "---\ntitle: t\nsupersedes: decisions/old.md\n---\n# t",
    )
    nodes, edges, _, _, _ = parse_markdown(tmp_path)
    assert edges[0].to == "adr:decisions/old.md"
    assert nodes[0].supersedes == "adr:decisions/old.md"


# --------------------------------------------------------------------------- #
# Fail-fast: malformed YAML / wrong types                                     #
# --------------------------------------------------------------------------- #


def test_malformed_yaml_frontmatter_emits_parse_error(tmp_path: Path) -> None:
    _write(
        tmp_path / "decisions" / "broken.md",
        "---\ntitle: [unclosed\nstatus: accepted\n---\n# t",
    )
    nodes, edges, errors, _bodies, _ = parse_markdown(tmp_path)
    assert nodes == [] and edges == []
    assert len(errors) == 1
    assert "malformed YAML frontmatter" in errors[0].reason


def test_supersedes_non_string_value_emits_parse_error(tmp_path: Path) -> None:
    _write(
        tmp_path / "decisions" / "wrong_type.md",
        "---\ntitle: t\nsupersedes:\n  - one\n  - two\n---\n# t",
    )
    nodes, edges, errors, _bodies, _ = parse_markdown(tmp_path)
    assert nodes == [] and edges == []
    assert len(errors) == 1
    assert "supersedes value must be a string" in errors[0].reason


def test_yaml_frontmatter_not_a_mapping_emits_parse_error(tmp_path: Path) -> None:
    """Top-level YAML list is not a valid frontmatter shape."""
    _write(
        tmp_path / "decisions" / "list.md",
        "---\n- not\n- a\n- mapping\n---\n# t",
    )
    _, _, errors, _, _ = parse_markdown(tmp_path)
    assert len(errors) == 1
    assert "must be a mapping" in errors[0].reason


def test_partial_failure_other_files_still_parsed(tmp_path: Path) -> None:
    """Per CLI.md §7: one file's ParseError doesn't drop the others."""
    _write(tmp_path / "decisions" / "good.md", "# good")
    _write(
        tmp_path / "decisions" / "broken.md",
        "---\n[unclosed\n---\n# x",
    )
    nodes, _, errors, _, _ = parse_markdown(tmp_path)
    assert len(nodes) == 1 and len(errors) == 1
    assert nodes[0].title == "good"


# --------------------------------------------------------------------------- #
# Plans                                                                       #
# --------------------------------------------------------------------------- #


def test_plan_h1_and_summary(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "plans" / "q2-launch.md",
        "---\nstatus: in-progress\n---\n\n# Q2 ranker launch\n\nShip ALS by Q2 end.",
    )
    nodes, edges, errors, _bodies, _ = parse_markdown(tmp_path)
    assert errors == [] and edges == [] and len(nodes) == 1
    plan = nodes[0]
    assert isinstance(plan, PlanNode)
    assert plan.id == "plan:docs/plans/q2-launch.md"
    assert plan.title == "Q2 ranker launch"
    assert plan.status == "in-progress"
    assert plan.summary == "Ship ALS by Q2 end."
    assert plan.provenance == "body"


def test_plan_with_no_frontmatter(tmp_path: Path) -> None:
    _write(
        tmp_path / "docs" / "plans" / "loose.md",
        "# Loose plan\n\nNo frontmatter.",
    )
    nodes, _, errors, _, _ = parse_markdown(tmp_path)
    assert errors == [] and nodes[0].title == "Loose plan"


# --------------------------------------------------------------------------- #
# Directory handling                                                          #
# --------------------------------------------------------------------------- #


def test_missing_dirs_emit_no_errors(tmp_path: Path) -> None:
    """A repo without decisions/ or docs/plans/ is valid — silent skip."""
    nodes, edges, errors, _bodies, _ = parse_markdown(tmp_path)
    assert nodes == [] and edges == [] and errors == []


def test_custom_dirs_honored(tmp_path: Path) -> None:
    _write(tmp_path / "adrs" / "a.md", "# A")
    _write(tmp_path / "plans" / "p.md", "# P")
    nodes, _, _, _, _ = parse_markdown(tmp_path, adr_dir="adrs/", plan_dir="plans/")
    ids = sorted(n.id for n in nodes)
    assert ids == ["adr:adrs/a.md", "plan:plans/p.md"]


def test_nested_md_files_discovered(tmp_path: Path) -> None:
    _write(tmp_path / "decisions" / "sub" / "nested.md", "# Nested")
    nodes, _, _, _, _ = parse_markdown(tmp_path)
    assert len(nodes) == 1 and nodes[0].path == "decisions/sub/nested.md"


def test_adr_decides_field_populated_regardless_of_parameters_enabled(
    tmp_path: Path,
) -> None:
    """SCHEMA §2.3: AdrNode.decides is data — populate from frontmatter
    regardless of parameters_enabled. Only edge emission is gated.
    """
    _write(
        tmp_path / "decisions" / "with-decides.md",
        "---\ntitle: t\ndecides:\n  - param:src/m.py::foo::rank\n  - param:src/m.py::Cls.fit::lr\n---\n# t",
    )
    # parameters_enabled=False → field populated, NO decides edges emitted
    nodes, edges, errors, _bodies, claimed = parse_markdown(
        tmp_path, parameters_enabled=False
    )
    assert errors == [] and len(nodes) == 1
    assert nodes[0].decides == [
        "param:src/m.py::foo::rank",
        "param:src/m.py::Cls.fit::lr",
    ]
    assert [e for e in edges if e.type == "decides"] == []
    # decides_claimed set still accumulates regardless of gating
    assert claimed == {
        "param:src/m.py::foo::rank",
        "param:src/m.py::Cls.fit::lr",
    }


def test_decides_edges_emitted_only_when_parameters_enabled(
    tmp_path: Path,
) -> None:
    """SCHEMA §3 + Codex round-1 High-1: `decides` edges emit only when
    parameters_enabled=True. Plain epi sync produces zero decides edges."""
    _write(
        tmp_path / "decisions" / "with-decides.md",
        "---\ntitle: t\ndecides:\n  - param:src/m.py::foo::rank\n---\n# t",
    )
    nodes, edges, errors, _bodies, _ = parse_markdown(
        tmp_path, parameters_enabled=True
    )
    assert errors == []
    decides_edges = [e for e in edges if e.type == "decides"]
    assert len(decides_edges) == 1
    e = decides_edges[0]
    assert e.from_ == "adr:decisions/with-decides.md"
    assert e.to == "param:src/m.py::foo::rank"
    assert e.source == "frontmatter:decides"
    assert e.provenance == "frontmatter"


def test_decides_canonical_target_format_validated(tmp_path: Path) -> None:
    """Codex round-1 Med-8: bare names like `decides: - rank` are malformed."""
    _write(
        tmp_path / "decisions" / "bad-target.md",
        "---\ntitle: t\ndecides:\n  - rank\n---\n# t",  # bare name, not canonical
    )
    nodes, edges, errors, _bodies, _ = parse_markdown(
        tmp_path, parameters_enabled=True
    )
    assert nodes == [] and edges == []
    assert len(errors) == 1
    assert "not a canonical parameter ID" in errors[0].reason
    assert "'rank'" in errors[0].reason


def test_decides_non_list_value_emits_parse_error(tmp_path: Path) -> None:
    _write(
        tmp_path / "decisions" / "wrong-shape.md",
        "---\ntitle: t\ndecides: param:src/m.py::foo::rank\n---\n# t",  # string, not list
    )
    _, _, errors, _, _ = parse_markdown(tmp_path, parameters_enabled=True)
    assert len(errors) == 1
    assert "decides frontmatter must be a list" in errors[0].reason


def test_decides_non_string_entry_emits_parse_error(tmp_path: Path) -> None:
    _write(
        tmp_path / "decisions" / "wrong-entry.md",
        "---\ntitle: t\ndecides:\n  - 42\n---\n# t",  # int, not str
    )
    _, _, errors, _, _ = parse_markdown(tmp_path, parameters_enabled=True)
    assert len(errors) == 1
    assert "decides entry must be a string" in errors[0].reason


def test_decides_dangling_target_emits_edge_anyway(tmp_path: Path) -> None:
    """SCHEMA §6 (PR4 amendment): decides edges persist even when the
    referenced parameter doesn't exist in source — drift signal."""
    _write(
        tmp_path / "decisions" / "with-decides.md",
        "---\ntitle: t\ndecides:\n  - param:src/m.py::ghost::removed\n---\n# t",
    )
    nodes, edges, errors, _bodies, _ = parse_markdown(
        tmp_path, parameters_enabled=True
    )
    assert errors == []
    assert len(edges) == 1
    assert edges[0].to == "param:src/m.py::ghost::removed"
    # The target parameter doesn't exist — parser/python isn't even run here —
    # but the decides edge is emitted regardless. PR3 missing-target serve
    # renderer handles display.


def test_decides_claimed_set_accumulates_across_adrs(tmp_path: Path) -> None:
    """Multiple ADRs decide different params → set is the union."""
    _write(
        tmp_path / "decisions" / "adr-a.md",
        "---\ntitle: A\ndecides:\n  - param:src/m.py::Cls.fit::rank\n---\n# A",
    )
    _write(
        tmp_path / "decisions" / "adr-b.md",
        "---\ntitle: B\ndecides:\n  - param:src/m.py::<module>::DEFAULT_RANK\n  - param:src/n.py::go::lr\n---\n# B",
    )
    _, _, _, _, claimed = parse_markdown(tmp_path, parameters_enabled=True)
    assert claimed == {
        "param:src/m.py::Cls.fit::rank",
        "param:src/m.py::<module>::DEFAULT_RANK",
        "param:src/n.py::go::lr",
    }


def test_unicode_paths_and_titles(tmp_path: Path) -> None:
    _write(
        tmp_path / "decisions" / "中文.md",
        "---\ntitle: 中文標題\n---\n# 中文",
    )
    nodes, _, _, _, _ = parse_markdown(tmp_path)
    assert nodes[0].title == "中文標題"
