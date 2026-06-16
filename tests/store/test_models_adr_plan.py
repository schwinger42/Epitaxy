"""PR2 smoke tests: AdrNode + PlanNode round-trips + references/supersedes edges.

Comprehensive fixture-driven tests live in tests/cli/test_sync_pr2_e2e.py
(commit 7); this file just verifies the new pydantic models + discriminator
union work end-to-end through write_index → read_index.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from epitaxy.store import (
    AdrNode,
    Edge,
    Index,
    IndexConfig,
    IndexStats,
    ModuleNode,
    Node,
    ParameterNode,
    PlanNode,
    read_index,
    write_index,
)


def _adr_plan_index() -> Index:
    now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    return Index(
        generated_at=now,
        generator="epitaxy 0.1.0",
        repo_root="/tmp/sample",
        config=IndexConfig(roots=["src/**/*.py"]),
        stats=IndexStats(modules=1, adrs=2, plans=1, edges=3),
        nodes=[
            ModuleNode(
                id="module:src/m.py",
                path="src/m.py",
                provenance="ast",
                extracted_at=now,
            ),
            AdrNode(
                id="adr:decisions/2026-04-rank-dim.md",
                path="decisions/2026-04-rank-dim.md",
                title="ALS rank dimension — 128 over 64",
                status="accepted",
                date="2026-04-12",
                supersedes="adr:decisions/2026-02-rank-baseline.md",
                summary="Bumps rank from 64 to 128 for long-tail items.",
                provenance="frontmatter+body",
            ),
            AdrNode(
                id="adr:decisions/2026-02-rank-baseline.md",
                path="decisions/2026-02-rank-baseline.md",
                title="Initial ALS rank: 64",
                status="superseded",
                provenance="frontmatter+body",
            ),
            PlanNode(
                id="plan:docs/plans/q2-launch.md",
                path="docs/plans/q2-launch.md",
                title="Q2 ranker launch",
                status="in-progress",
                summary="Ship ALS ranker by end of Q2.",
                provenance="body",
            ),
        ],
        edges=[
            Edge.model_validate(
                {
                    "from": "adr:decisions/2026-04-rank-dim.md",
                    "to": "adr:decisions/2026-02-rank-baseline.md",
                    "type": "supersedes",
                    "source": "frontmatter:supersedes",
                    "provenance": "frontmatter",
                }
            ),
            Edge.model_validate(
                {
                    "from": "plan:docs/plans/q2-launch.md",
                    "to": "module:src/m.py",
                    "type": "references",
                    "source": "body-mention",
                    "line": 12,
                    "provenance": "body-mention",
                }
            ),
            Edge.model_validate(
                {
                    "from": "module:src/m.py",
                    "to": "adr:decisions/2026-04-rank-dim.md",
                    "type": "references",
                    "source": "body-mention",
                    "line": 3,
                    "provenance": "body-mention",
                }
            ),
        ],
    )


def test_adr_plan_round_trip_preserves_all_fields(tmp_path: Path) -> None:
    original = _adr_plan_index()
    path = tmp_path / "index.json"

    write_index(original, path)
    restored = read_index(path)

    assert restored == original


def test_node_discriminator_picks_adr_and_plan(tmp_path: Path) -> None:
    """`type: adr` must deserialize to AdrNode, `type: plan` to PlanNode."""
    path = tmp_path / "index.json"
    write_index(_adr_plan_index(), path)
    restored = read_index(path)

    types = [type(n) for n in restored.nodes]
    assert ModuleNode in types
    assert types.count(AdrNode) == 2
    assert types.count(PlanNode) == 1


def test_references_edge_round_trips(tmp_path: Path) -> None:
    """references edges must accept body-mention source + line + provenance."""
    path = tmp_path / "index.json"
    write_index(_adr_plan_index(), path)
    restored = read_index(path)

    refs = [e for e in restored.edges if e.type == "references"]
    assert len(refs) == 2
    assert all(e.source == "body-mention" for e in refs)
    assert all(e.provenance == "body-mention" for e in refs)


def test_supersedes_edge_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "index.json"
    write_index(_adr_plan_index(), path)
    restored = read_index(path)

    sup = [e for e in restored.edges if e.type == "supersedes"]
    assert len(sup) == 1
    assert sup[0].source == "frontmatter:supersedes"
    assert sup[0].provenance == "frontmatter"


def test_supersedes_target_may_not_exist_in_node_set(tmp_path: Path) -> None:
    """Per SCHEMA §6: supersedes edge persists even when target node absent.

    Codex round-2 High-1 reverted round-1 Med-2 — missing supersedes target
    is historical, not a ParseError. The model layer must allow this.
    """
    now = datetime(2026, 5, 15, 12, 0, 0, tzinfo=timezone.utc)
    idx = Index(
        generated_at=now,
        generator="epitaxy 0.1.0",
        repo_root="/tmp/sample",
        config=IndexConfig(),
        stats=IndexStats(adrs=1, edges=1),
        nodes=[
            AdrNode(
                id="adr:decisions/2026-04.md",
                path="decisions/2026-04.md",
                title="latest",
                supersedes="adr:decisions/historical-no-longer-exists.md",
                provenance="frontmatter+body",
            ),
        ],
        edges=[
            Edge.model_validate(
                {
                    "from": "adr:decisions/2026-04.md",
                    "to": "adr:decisions/historical-no-longer-exists.md",
                    "type": "supersedes",
                    "source": "frontmatter:supersedes",
                    "provenance": "frontmatter",
                }
            ),
        ],
    )
    path = tmp_path / "index.json"
    write_index(idx, path)
    restored = read_index(path)
    assert restored == idx


def test_parameter_node_present_post_pr4() -> None:
    """Forward-compat guard: ParameterNode IS in the Node union post-PR4.

    Was the PR2-era `test_parameter_node_remains_deferred_to_pr4`; flipped
    in PR4 commit 1. This test fails if a future PR accidentally removes
    ParameterNode from the union.
    """
    from typing import get_args

    from epitaxy.store import models

    union_args = get_args(get_args(Node)[0])
    type_names = {arg.__name__ for arg in union_args}

    assert type_names == {
        "ModuleNode",
        "FunctionNode",
        "AdrNode",
        "PlanNode",
        "ParameterNode",
    }
    assert hasattr(models, "ParameterNode")


def test_decides_edge_type_present_post_pr4() -> None:
    """Forward-compat guard: full Edge.type Literal set is current.

    Post-PR4: `decides` added (parameter extraction).
    Post-v0.2-PR1: `follows` added (POR `decisions:` → ADR).
    """
    from typing import get_args

    type_field = Edge.model_fields["type"]
    allowed = set(get_args(type_field.annotation))
    assert allowed == {"depends-on", "references", "supersedes", "decides", "follows"}


def test_parameter_node_round_trip(tmp_path) -> None:
    """ParameterNode round-trips through the discriminated union + JSON I/O."""
    from datetime import datetime, timezone
    from pathlib import Path

    now = datetime(2026, 5, 16, tzinfo=timezone.utc)
    idx = Index(
        generated_at=now,
        generator="epitaxy 0.1.0",
        repo_root="/tmp/sample",
        config=IndexConfig(parameters_enabled=True),
        stats=IndexStats(modules=1, parameters=3, edges=2),
        nodes=[
            ModuleNode(
                id="module:src/m.py",
                path="src/m.py",
                provenance="ast",
                extracted_at=now,
            ),
            ParameterNode(
                id="param:src/m.py::Cls.fit::rank",
                module="module:src/m.py",
                scope="Cls.fit",
                name="rank",
                value="128",
                line=24,
                decided_by=["adr:decisions/x.md"],
                provenance="ast+comment",
            ),
            ParameterNode(
                id="param:src/m.py::<module>::DEFAULT_RANK",
                module="module:src/m.py",
                scope="<module>",
                name="DEFAULT_RANK",
                value="64",
                line=1,
                provenance="adr-frontmatter",
            ),
            ParameterNode(
                id="param:src/m.py::Cls.fit::lr",
                module="module:src/m.py",
                scope="Cls.fit",
                name="lr",
                value="1e-3",
                line=25,
                provenance="ast+comment+adr-frontmatter",
            ),
        ],
        edges=[
            Edge.model_validate({
                "from": "adr:decisions/x.md",
                "to": "param:src/m.py::Cls.fit::rank",
                "type": "decides",
                "source": "frontmatter:decides",
                "provenance": "frontmatter",
            }),
            Edge.model_validate({
                "from": "adr:decisions/x.md",
                "to": "param:src/m.py::ghost::removed",
                "type": "decides",
                "source": "frontmatter:decides",
                "provenance": "frontmatter",
            }),
        ],
    )
    path: Path = tmp_path / "index.json"
    write_index(idx, path)
    restored = read_index(path)
    assert restored == idx

    # Discriminator routes correctly
    params = [n for n in restored.nodes if isinstance(n, ParameterNode)]
    assert len(params) == 3
    provenances = sorted(p.provenance for p in params)
    assert provenances == [
        "adr-frontmatter",
        "ast+comment",
        "ast+comment+adr-frontmatter",
    ]


def test_decides_edge_dangling_target_round_trip(tmp_path) -> None:
    """SCHEMA §6 (PR4 amendment): decides edges persist even when target
    parameter is absent — same drift-signal rule as supersedes."""
    from datetime import datetime, timezone

    now = datetime(2026, 5, 16, tzinfo=timezone.utc)
    idx = Index(
        generated_at=now,
        generator="epitaxy 0.1.0",
        repo_root="/tmp/sample",
        config=IndexConfig(parameters_enabled=True),
        stats=IndexStats(adrs=1, edges=1),
        nodes=[
            AdrNode(
                id="adr:decisions/x.md",
                path="decisions/x.md",
                title="t",
                decides=["param:src/m.py::ghost::removed"],
                provenance="frontmatter+body",
            ),
        ],
        edges=[
            Edge.model_validate({
                "from": "adr:decisions/x.md",
                "to": "param:src/m.py::ghost::removed",
                "type": "decides",
                "source": "frontmatter:decides",
                "provenance": "frontmatter",
            }),
        ],
    )
    path = tmp_path / "index.json"
    write_index(idx, path)
    restored = read_index(path)
    assert restored == idx
    # The dangling target is preserved on both the AdrNode field AND the edge
    assert restored.nodes[0].decides == ["param:src/m.py::ghost::removed"]
    assert restored.edges[0].to == "param:src/m.py::ghost::removed"


def test_adr_decides_field_accepted() -> None:
    """Forward-compat guard: AdrNode.decides field IS accepted post-PR4.

    Was the PR2-era `test_adr_extra_fields_rejected` which asserted that
    passing `decides=[...]` to AdrNode raised ValidationError. Flipped in
    PR4 commit 1 (Codex round-1 High-3 — SCHEMA §2.3 lists decides as an
    optional field).
    """
    adr = AdrNode(
        id="adr:x",
        path="x",
        title="t",
        provenance="frontmatter+body",
        decides=["param:src/m.py::foo::rank"],
    )
    assert adr.decides == ["param:src/m.py::foo::rank"]

    # Other extra fields are still rejected — only `decides` is now allowed.
    with pytest.raises(ValidationError):
        AdrNode(
            id="adr:x",
            path="x",
            title="t",
            provenance="frontmatter+body",
            not_a_real_field="oops",  # type: ignore[call-arg]
        )
