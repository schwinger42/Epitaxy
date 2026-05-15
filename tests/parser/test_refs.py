"""Tests for parser/refs.py — references-edge final pass + code-block masking."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from epitaxy.parser.refs import (
    BodyRecord,
    _candidate_repo_relative_paths,
    _mask_code,
    _resolve_to_repo_relative,
    extract_references,
)
from epitaxy.store.models import AdrNode, ModuleNode, PlanNode


# --------------------------------------------------------------------------- #
# _mask_code — fenced + inline code stripping                                 #
# --------------------------------------------------------------------------- #


def test_mask_preserves_length_and_newlines() -> None:
    text = "before\n```\ncode\n```\nafter"
    masked = _mask_code(text)
    assert len(masked) == len(text)
    assert masked.count("\n") == text.count("\n")
    # `code` line is fully masked
    assert "code" not in masked


def test_mask_backtick_fence() -> None:
    text = "x\n```python\n[link](path.md)\n```\ny"
    masked = _mask_code(text)
    # The link inside the fence should be erased from match-able text
    assert "[link]" not in masked
    # But surrounding text preserved
    assert "x" in masked and "y" in masked


def test_mask_tilde_fence() -> None:
    """Codex round-2 Med-4: tilde fences must be handled, not just backticks."""
    text = "before\n~~~\n[link](nope.md)\n~~~\nafter"
    masked = _mask_code(text)
    assert "[link]" not in masked


def test_mask_varying_fence_lengths() -> None:
    """A `````` (6-backtick) fence closes only with ≥6 backticks."""
    text = "x\n``````\n```\n[link](x.md)\n```\n``````\ny"
    masked = _mask_code(text)
    # The triple-backticks inside aren't closers (only 3 < 6) — link still masked
    assert "[link]" not in masked


def test_mask_inline_code() -> None:
    text = "see `[fake](path.md)` not really"
    masked = _mask_code(text)
    assert "[fake]" not in masked


@pytest.mark.xfail(reason="PR2 known limitation: indented-4-space blocks not masked")
def test_indented_code_block_false_positive_known() -> None:
    """Codex round-2 Med-4: 4-space-indented code blocks are out of PR2 scope."""
    text = "see this code:\n\n    [link](path.md)\n\nback to prose"
    masked = _mask_code(text)
    assert "[link]" not in masked


# --------------------------------------------------------------------------- #
# _resolve_to_repo_relative — path normalization                              #
# --------------------------------------------------------------------------- #


def test_resolve_repo_relative_path(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").touch()
    rel = _resolve_to_repo_relative(tmp_path, "src/m.py", "src/m.py")
    assert rel == "src/m.py"


def test_resolve_source_relative_path(tmp_path: Path) -> None:
    """`m.py` linked from `src/x.py` yields both candidates; caller disambiguates."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").touch()
    cands = _candidate_repo_relative_paths(tmp_path, "src/x.py", "m.py")
    # Both interpretations returned: repo-root-relative ("m.py") + source-relative ("src/m.py")
    assert "src/m.py" in cands
    assert "m.py" in cands


def test_resolve_rejects_absolute_path(tmp_path: Path) -> None:
    rel = _resolve_to_repo_relative(tmp_path, "src/x.py", "/etc/passwd")
    assert rel is None


def test_resolve_rejects_outside_repo(tmp_path: Path) -> None:
    """`../../../etc/passwd` must not resolve to anything inside repo."""
    rel = _resolve_to_repo_relative(tmp_path, "src/x.py", "../../../etc/passwd")
    assert rel is None


def test_resolve_dotdot_into_repo_root(tmp_path: Path) -> None:
    """`../decisions/x.md` from `docs/plans/y.md` resolves to `decisions/x.md`."""
    rel = _resolve_to_repo_relative(
        tmp_path, "docs/plans/y.md", "../../decisions/x.md"
    )
    assert rel == "decisions/x.md"


# --------------------------------------------------------------------------- #
# extract_references — end-to-end                                             #
# --------------------------------------------------------------------------- #


def _module(rel_path: str) -> ModuleNode:
    return ModuleNode(
        id=f"module:{rel_path}",
        path=rel_path,
        provenance="ast",
        extracted_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
    )


def _adr(rel_path: str) -> AdrNode:
    return AdrNode(
        id=f"adr:{rel_path}",
        path=rel_path,
        title="t",
        provenance="frontmatter+body",
    )


def _plan(rel_path: str) -> PlanNode:
    return PlanNode(
        id=f"plan:{rel_path}",
        path=rel_path,
        title="t",
        provenance="body",
    )


def test_docstring_to_module_link(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "model.py").touch()
    (tmp_path / "src" / "data.py").touch()

    nodes = [_module("src/model.py"), _module("src/data.py")]
    bodies = [
        BodyRecord(
            body_text="See [data loader](src/data.py) for input format.",
            source_node_id="module:src/model.py",
            source_path="src/model.py",
            body_start_line=1,
            source_kind="docstring",
        )
    ]
    edges = extract_references(tmp_path, nodes, bodies)
    assert len(edges) == 1
    assert edges[0].from_ == "module:src/model.py"
    assert edges[0].to == "module:src/data.py"
    assert edges[0].source == "body-mention"
    assert edges[0].provenance == "body-mention"


def test_links_to_adr_and_plan_resolve(tmp_path: Path) -> None:
    """Codex round-2 High-2: target index covers adr + plan, not just modules."""
    (tmp_path / "decisions").mkdir()
    (tmp_path / "decisions" / "x.md").touch()
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    (tmp_path / "docs" / "plans" / "y.md").touch()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").touch()

    nodes = [
        _module("src/m.py"),
        _adr("decisions/x.md"),
        _plan("docs/plans/y.md"),
    ]
    bodies = [
        BodyRecord(
            body_text=(
                "Module body refs [ADR](decisions/x.md) and "
                "[plan](docs/plans/y.md)."
            ),
            source_node_id="module:src/m.py",
            source_path="src/m.py",
            body_start_line=1,
            source_kind="docstring",
        )
    ]
    edges = extract_references(tmp_path, nodes, bodies)
    targets = sorted(e.to for e in edges)
    assert targets == ["adr:decisions/x.md", "plan:docs/plans/y.md"]


def test_url_scheme_targets_rejected(tmp_path: Path) -> None:
    bodies = [
        BodyRecord(
            body_text=(
                "Web: [google](https://example.com) and "
                "[email](mailto:x@y.com)."
            ),
            source_node_id="module:src/m.py",
            source_path="src/m.py",
            body_start_line=1,
            source_kind="docstring",
        )
    ]
    edges = extract_references(tmp_path, [_module("src/m.py")], bodies)
    assert edges == []


def test_image_markdown_rejected(tmp_path: Path) -> None:
    (tmp_path / "img.png").touch()
    bodies = [
        BodyRecord(
            body_text="![alt text](img.png) not a link",
            source_node_id="module:src/m.py",
            source_path="src/m.py",
            body_start_line=1,
            source_kind="docstring",
        )
    ]
    edges = extract_references(tmp_path, [_module("src/m.py")], bodies)
    assert edges == []


def test_unresolvable_target_silently_dropped(tmp_path: Path) -> None:
    """Per plan: unresolvable references are silent, unlike supersedes."""
    bodies = [
        BodyRecord(
            body_text="[ghost](src/does-not-exist.py) link",
            source_node_id="module:src/m.py",
            source_path="src/m.py",
            body_start_line=1,
            source_kind="docstring",
        )
    ]
    edges = extract_references(tmp_path, [_module("src/m.py")], bodies)
    assert edges == []


def test_self_reference_dropped(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").touch()
    bodies = [
        BodyRecord(
            body_text="See [me](src/m.py).",
            source_node_id="module:src/m.py",
            source_path="src/m.py",
            body_start_line=1,
            source_kind="docstring",
        )
    ]
    edges = extract_references(tmp_path, [_module("src/m.py")], bodies)
    assert edges == []


def test_fragment_stripped_resolves_to_module(tmp_path: Path) -> None:
    """`module.py#L42` strips fragment → resolves to module-level edge."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").touch()
    bodies = [
        BodyRecord(
            body_text="see [line](src/m.py#L42)",
            source_node_id="adr:decisions/x.md",
            source_path="decisions/x.md",
            body_start_line=1,
            source_kind="adr-body",
        )
    ]
    edges = extract_references(
        tmp_path, [_module("src/m.py"), _adr("decisions/x.md")], bodies
    )
    assert len(edges) == 1
    assert edges[0].to == "module:src/m.py"


def test_link_inside_fence_not_emitted(tmp_path: Path) -> None:
    """Code-block masking prevents links inside ```fence``` from emitting edges."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").touch()
    bodies = [
        BodyRecord(
            body_text="```\nSee [m](src/m.py)\n```\nprose",
            source_node_id="adr:decisions/x.md",
            source_path="decisions/x.md",
            body_start_line=1,
            source_kind="adr-body",
        )
    ]
    edges = extract_references(
        tmp_path, [_module("src/m.py"), _adr("decisions/x.md")], bodies
    )
    assert edges == []


def test_line_offset_attribution(tmp_path: Path) -> None:
    """edge.line = body_start_line + offset of link line within body."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").touch()
    bodies = [
        BodyRecord(
            body_text="line 0 of body\nline 1\nsee [m](src/m.py)",
            source_node_id="adr:decisions/x.md",
            source_path="decisions/x.md",
            body_start_line=5,  # body starts at file line 5
            source_kind="adr-body",
        )
    ]
    edges = extract_references(
        tmp_path, [_module("src/m.py"), _adr("decisions/x.md")], bodies
    )
    assert edges[0].line == 5 + 2  # body_start_line + 2 newlines before link


def test_duplicate_links_deduplicated(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").touch()
    bodies = [
        BodyRecord(
            body_text="see [m](src/m.py) and [m again](src/m.py)",
            source_node_id="adr:decisions/x.md",
            source_path="decisions/x.md",
            body_start_line=1,
            source_kind="adr-body",
        )
    ]
    edges = extract_references(
        tmp_path, [_module("src/m.py"), _adr("decisions/x.md")], bodies
    )
    assert len(edges) == 1


def test_no_bodies_no_edges(tmp_path: Path) -> None:
    edges = extract_references(tmp_path, [_module("src/m.py")], [])
    assert edges == []
