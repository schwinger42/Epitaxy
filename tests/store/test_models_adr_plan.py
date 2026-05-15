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


def test_parameter_node_remains_deferred_to_pr4() -> None:
    """Codex round-1 High-2 lock: ParameterNode is NOT in the v0 schema yet.

    Adding it pre-PR4 would create a live deserialization path no PR2 code
    can consume. This test fails the day someone tries to sneak the model
    in without going through the PR4 plan.
    """
    from typing import get_args

    from epitaxy.store import models

    # Node is Annotated[Union[...], Field(discriminator=...)]
    union_args = get_args(get_args(Node)[0])
    type_names = {arg.__name__ for arg in union_args}

    assert type_names == {"ModuleNode", "FunctionNode", "AdrNode", "PlanNode"}
    assert not hasattr(models, "ParameterNode")


def test_decides_edge_type_remains_deferred_to_pr4() -> None:
    """Same lock: `decides` is not in the Edge.type Literal until PR4."""
    from typing import get_args

    type_field = Edge.model_fields["type"]
    allowed = set(get_args(type_field.annotation))
    assert allowed == {"depends-on", "references", "supersedes"}
    assert "decides" not in allowed


def test_adr_extra_fields_rejected() -> None:
    """extra='forbid' guards against PR4 fields slipping in early."""
    with pytest.raises(ValidationError):
        AdrNode(
            id="adr:x",
            path="x",
            title="t",
            provenance="frontmatter+body",
            decides=["param:src/m.py::foo::rank"],  # type: ignore[call-arg]
        )
