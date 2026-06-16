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
from epitaxy.store.models import AdrNode, Edge, FunctionNode, ModuleNode, PlanNode


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


# --------------------------------------------------------------------------- #
# populate_decided_by post-pass (PR4 commit 4 / Codex round-1 Low-11)         #
# --------------------------------------------------------------------------- #


def test_populate_decided_by_populates_parameter_field() -> None:
    """ADR decides → parameter: the parameter's `decided_by` lists the ADR."""
    from epitaxy.parser.refs import populate_decided_by
    from epitaxy.store.models import AdrNode, ParameterNode

    param = ParameterNode(
        id="param:m.py::fit::rank",
        module="module:m.py",
        scope="fit",
        name="rank",
        value="128",
        line=3,
        provenance="ast+comment",
    )
    adr = AdrNode(
        id="adr:decisions/x.md",
        path="decisions/x.md",
        title="t",
        provenance="frontmatter+body",
    )
    edge = Edge.model_validate({
        "from": "adr:decisions/x.md",
        "to": "param:m.py::fit::rank",
        "type": "decides",
        "source": "frontmatter:decides",
        "provenance": "frontmatter",
    })
    populate_decided_by([param, adr], [edge])
    assert param.decided_by == ["adr:decisions/x.md"]


def test_populate_decided_by_dangling_target_leaves_node_unaffected() -> None:
    """SCHEMA §6 (PR4 amendment): dangling decides edge → parameter doesn't
    exist → no node mutation. The dangling edge stays in the graph as drift
    signal at the EDGE level; `decided_by=None` at the node level is honest."""
    from epitaxy.parser.refs import populate_decided_by
    from epitaxy.store.models import AdrNode

    adr = AdrNode(
        id="adr:decisions/x.md",
        path="decisions/x.md",
        title="t",
        provenance="frontmatter+body",
    )
    edge = Edge.model_validate({
        "from": "adr:decisions/x.md",
        "to": "param:m.py::ghost::removed",
        "type": "decides",
        "source": "frontmatter:decides",
        "provenance": "frontmatter",
    })
    # The target parameter is absent from the nodes list — dangling edge case.
    populate_decided_by([adr], [edge])
    # No assertion needed — call should not raise. Edge stays valid.
    assert edge.to == "param:m.py::ghost::removed"


def test_populate_decided_by_multiple_adrs_decide_same_param() -> None:
    """Two ADRs both decide the same parameter → decided_by has both IDs,
    sorted (per the deterministic-ordering rule)."""
    from epitaxy.parser.refs import populate_decided_by
    from epitaxy.store.models import AdrNode, ParameterNode

    param = ParameterNode(
        id="param:m.py::fit::rank",
        module="module:m.py",
        scope="fit",
        name="rank",
        value="128",
        line=3,
        provenance="ast+comment",
    )
    adr_a = AdrNode(
        id="adr:decisions/2026-04-zzz.md",
        path="decisions/2026-04-zzz.md",
        title="newer",
        provenance="frontmatter+body",
    )
    adr_b = AdrNode(
        id="adr:decisions/2026-02-aaa.md",
        path="decisions/2026-02-aaa.md",
        title="older",
        provenance="frontmatter+body",
    )
    edges = [
        Edge.model_validate({
            "from": "adr:decisions/2026-04-zzz.md",
            "to": "param:m.py::fit::rank",
            "type": "decides",
            "source": "frontmatter:decides",
            "provenance": "frontmatter",
        }),
        Edge.model_validate({
            "from": "adr:decisions/2026-02-aaa.md",
            "to": "param:m.py::fit::rank",
            "type": "decides",
            "source": "frontmatter:decides",
            "provenance": "frontmatter",
        }),
    ]
    populate_decided_by([param, adr_a, adr_b], edges)
    # Sorted lexicographically (deterministic for downstream stable output)
    assert param.decided_by == [
        "adr:decisions/2026-02-aaa.md",
        "adr:decisions/2026-04-zzz.md",
    ]


def test_populate_decided_by_no_decides_edges_leaves_param_unchanged() -> None:
    """Parameter with no decides edges keeps decided_by=None."""
    from epitaxy.parser.refs import populate_decided_by
    from epitaxy.store.models import ParameterNode

    param = ParameterNode(
        id="param:m.py::fit::rank",
        module="module:m.py",
        scope="fit",
        name="rank",
        value="128",
        line=3,
        provenance="ast+comment",
    )
    populate_decided_by([param], [])
    assert param.decided_by is None


# --------------------------------------------------------------------------- #
# emit_follows_edges final-pass (v0.2-PR1)                                    #
# --------------------------------------------------------------------------- #


def _module_with_por(rel_path: str, por: dict | None) -> ModuleNode:
    """Variant of _module that attaches a POR dict (or None)."""
    return ModuleNode(
        id=f"module:{rel_path}",
        path=rel_path,
        por=por,
        provenance="ast",
        extracted_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )


def _function_with_por(
    module_rel_path: str, qualname: str, por: dict | None
) -> FunctionNode:
    """FunctionNode factory with a POR dict attached."""
    return FunctionNode(
        id=f"function:{module_rel_path}::{qualname}",
        module=f"module:{module_rel_path}",
        name=qualname.rsplit(".", 1)[-1],
        qualname=qualname,
        signature=f"def {qualname.rsplit('.', 1)[-1]}()",
        line=1,
        por=por,
        provenance="ast",
    )


def test_emits_follows_edge_for_module_por_decisions() -> None:
    """Module with POR `decisions:` produces one follows edge per ADR entry."""
    from epitaxy.parser.refs import emit_follows_edges

    module = _module_with_por(
        "src/m.py", {"decisions": ["adr:decisions/001-foo.md"]}
    )
    adr = _adr("decisions/001-foo.md")
    edges = emit_follows_edges([module, adr])

    assert len(edges) == 1
    edge = edges[0]
    assert edge.from_ == "module:src/m.py"
    assert edge.to == "adr:decisions/001-foo.md"
    assert edge.type == "follows"
    assert edge.source == "por-frontmatter"
    assert edge.provenance == "por-frontmatter"
    assert edge.line is None


def test_emits_follows_edge_for_function_por_decisions() -> None:
    """Function with POR `decisions:` produces follows edge from function ID
    (NOT from the containing module ID)."""
    from epitaxy.parser.refs import emit_follows_edges

    func = _function_with_por(
        "src/m.py", "fit", {"decisions": ["adr:decisions/002-rank.md"]}
    )
    edges = emit_follows_edges([func])

    assert len(edges) == 1
    assert edges[0].from_ == "function:src/m.py::fit"
    assert edges[0].to == "adr:decisions/002-rank.md"
    assert edges[0].type == "follows"


def test_dangling_follows_edge_when_target_adr_missing() -> None:
    """POR decisions referencing a nonexistent ADR still emit the edge
    (drift signal per SCHEMA §6 amended in v0.2-PR1). Silent emission —
    no exception, consistent with `supersedes` / `decides` precedent."""
    from epitaxy.parser.refs import emit_follows_edges

    module = _module_with_por(
        "src/m.py", {"decisions": ["adr:decisions/does-not-exist.md"]}
    )
    # Note: no ADR node in the input list — dangling target.
    edges = emit_follows_edges([module])

    assert len(edges) == 1
    assert edges[0].to == "adr:decisions/does-not-exist.md"
    assert edges[0].type == "follows"


def test_no_follows_edges_when_decisions_field_absent() -> None:
    """POR present but no `decisions:` key emits zero follows edges."""
    from epitaxy.parser.refs import emit_follows_edges

    module = _module_with_por(
        "src/m.py", {"goal": "do a thing", "why": "because of X"}
    )
    edges = emit_follows_edges([module])
    assert edges == []


def test_multiple_follows_edges_one_per_decisions_entry() -> None:
    """N entries in `decisions:` produce N follows edges from the same source."""
    from epitaxy.parser.refs import emit_follows_edges

    module = _module_with_por(
        "src/m.py",
        {
            "decisions": [
                "adr:decisions/a.md",
                "adr:decisions/b.md",
                "adr:decisions/c.md",
            ]
        },
    )
    edges = emit_follows_edges([module])

    assert len(edges) == 3
    targets = {e.to for e in edges}
    assert targets == {
        "adr:decisions/a.md",
        "adr:decisions/b.md",
        "adr:decisions/c.md",
    }
    assert all(e.from_ == "module:src/m.py" for e in edges)
    assert all(e.type == "follows" for e in edges)


def test_no_follows_edges_when_por_is_none() -> None:
    """Module with `por=None` emits zero follows edges (no exception)."""
    from epitaxy.parser.refs import emit_follows_edges

    module = _module_with_por("src/m.py", None)
    assert emit_follows_edges([module]) == []


def test_follows_edges_returned_in_deterministic_order() -> None:
    """Output edges are sorted by (from_, to) so index JSON is stable."""
    from epitaxy.parser.refs import emit_follows_edges

    # Two modules with shuffled decisions; multiple runs MUST produce the
    # same ordering. Input order is deliberately reversed alphabetical.
    module_b = _module_with_por(
        "src/b.py",
        {"decisions": ["adr:decisions/z.md", "adr:decisions/a.md"]},
    )
    module_a = _module_with_por(
        "src/a.py",
        {"decisions": ["adr:decisions/y.md", "adr:decisions/b.md"]},
    )

    runs = [emit_follows_edges([module_b, module_a]) for _ in range(10)]
    keys = [[(e.from_, e.to) for e in run] for run in runs]
    # All 10 runs identical
    assert all(k == keys[0] for k in keys)
    # And the canonical order is sorted ascending by (from_, to)
    assert keys[0] == [
        ("module:src/a.py", "adr:decisions/b.md"),
        ("module:src/a.py", "adr:decisions/y.md"),
        ("module:src/b.py", "adr:decisions/a.md"),
        ("module:src/b.py", "adr:decisions/z.md"),
    ]


def test_composite_por_consumes_decisions_not_decides() -> None:
    """POR with both `decisions:` (ADR IDs this code follows) AND `decides:`
    (parameter names) emits follows edges ONLY from `decisions:`. The
    `decides:` key is not a follows source — it's metadata for the
    parameter-extraction path. Defends against accidental key confusion."""
    from epitaxy.parser.refs import emit_follows_edges

    module = _module_with_por(
        "src/m.py",
        {
            "decisions": ["adr:decisions/follow-me.md"],
            "decides": ["param:src/m.py::<module>::rank"],
        },
    )
    edges = emit_follows_edges([module])

    assert len(edges) == 1
    assert edges[0].to == "adr:decisions/follow-me.md"
    assert edges[0].type == "follows"
    # And no edge with `to` matching the parameter ID.
    assert not any(e.to.startswith("param:") for e in edges)


def test_combined_module_and_function_por_in_same_file() -> None:
    """Both module-level POR and function-level POR in the same file each
    emit their own follows edges with distinct `from` IDs. Defends against
    iteration bugs that might process only the first POR-bearing node."""
    from epitaxy.parser.refs import emit_follows_edges

    module = _module_with_por(
        "src/m.py", {"decisions": ["adr:decisions/module-level.md"]}
    )
    func = _function_with_por(
        "src/m.py", "fit", {"decisions": ["adr:decisions/function-level.md"]}
    )
    edges = emit_follows_edges([module, func])

    assert len(edges) == 2
    from_ids = {e.from_ for e in edges}
    assert from_ids == {"module:src/m.py", "function:src/m.py::fit"}
    targets = {e.to for e in edges}
    assert targets == {
        "adr:decisions/module-level.md",
        "adr:decisions/function-level.md",
    }


# Edge tolerance — defensive checks for malformed POR (silently skip)
def test_malformed_por_decisions_silently_skipped() -> None:
    """Malformed POR cases that emit_follows_edges must tolerate without
    raising: por=None, decisions field absent (covered above), decisions
    is None, decisions is a non-list value, list contains non-string
    entries. Each case emits zero edges from that node."""
    from epitaxy.parser.refs import emit_follows_edges

    nodes = [
        _module_with_por("src/a.py", {"decisions": None}),
        _module_with_por("src/b.py", {"decisions": "adr:not-a-list.md"}),
        _module_with_por("src/c.py", {"decisions": [42, None, {"oops": 1}]}),
        # Sanity: one well-formed node alongside the malformed ones to confirm
        # we don't bail on the whole batch when one node is malformed.
        _module_with_por(
            "src/good.py", {"decisions": ["adr:decisions/real.md"]}
        ),
    ]
    edges = emit_follows_edges(nodes)
    assert len(edges) == 1
    assert edges[0].from_ == "module:src/good.py"
    assert edges[0].to == "adr:decisions/real.md"
