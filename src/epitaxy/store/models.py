"""Pydantic models for Epitaxy intent-graph nodes, edges, and index.

See docs/SCHEMA.md §2 (nodes), §3 (edges), §5 (index envelope).

PR2 scope (doc-parsing): adds `adr` + `plan` node types; the
`references` + `supersedes` Edge.type literals (reserved in PR1) are
now populated by the parser. `parameter` node type + `decides` edge
type remain deferred to PR4 alongside `--parameters` extraction —
keeping the surface narrow per Codex round-1 High-2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class ModuleNode(BaseModel):
    """A Python module / source file. See SCHEMA.md §2.1."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["module"] = "module"
    id: str
    path: str
    doc: str | None = None
    por: dict | None = None
    provenance: str
    extracted_at: datetime


class FunctionNode(BaseModel):
    """A Python function or method. See SCHEMA.md §2.2."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["function"] = "function"
    id: str
    module: str
    name: str
    qualname: str
    signature: str
    line: int
    doc: str | None = None
    por: dict | None = None
    provenance: str


class AdrNode(BaseModel):
    """An Architecture Decision Record. See SCHEMA.md §2.3.

    `decides` is intentionally absent — PR2 parser ignores it per the
    Codex round-1 High-2 deferral; PR4 will add the field alongside
    `ParameterNode` and the `decides` edge type.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["adr"] = "adr"
    id: str
    path: str
    title: str
    status: str | None = None
    date: str | None = None
    supersedes: str | None = None
    summary: str | None = None
    provenance: str


class PlanNode(BaseModel):
    """A plan markdown document. See SCHEMA.md §2.4."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["plan"] = "plan"
    id: str
    path: str
    title: str
    status: str | None = None
    summary: str | None = None
    provenance: str


Node = Annotated[
    Union[ModuleNode, FunctionNode, AdrNode, PlanNode],
    Field(discriminator="type"),
]


class Edge(BaseModel):
    """A typed edge in the intent graph. See SCHEMA.md §3."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    type: Literal["depends-on", "references", "supersedes"]
    source: str
    line: int | None = None
    provenance: str


class IndexConfig(BaseModel):
    """The [tool.epitaxy] config captured at sync time (provenance trail).

    All keys defined here match docs/CLI.md §5. Codex review Medium-5: previously
    `output` was documented in CLI.md but missing here, so a real user's
    `[tool.epitaxy] output = "..."` would be rejected by `extra="forbid"`.
    """

    model_config = ConfigDict(extra="forbid")

    roots: list[str] = Field(default_factory=lambda: ["src/**/*.py"])
    adr_dir: str = "decisions/"
    plan_dir: str = "docs/plans/"
    parameters_enabled: bool = False
    output: str = ".epitaxy/index.json"
    excludes: list[str] = Field(
        default_factory=lambda: ["**/test_*.py", "**/conftest.py"]
    )


class IndexStats(BaseModel):
    """Counts surfaced in the index header; not authoritative — derive from
    nodes/edges if you need an exact count."""

    model_config = ConfigDict(extra="forbid")

    modules: int = 0
    functions: int = 0
    adrs: int = 0
    plans: int = 0
    parameters: int = 0
    edges: int = 0


class Index(BaseModel):
    """The `.epitaxy/index.json` document. See SCHEMA.md §5.

    `version` is the SCHEMA format version, not the Epitaxy package version.
    Bump on breaking format changes (next planned bump: `0.2` when
    `extras="forbid"` relaxation or major field rename happens).
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "0.1"
    generated_at: datetime
    generator: str
    repo_root: str
    config: IndexConfig
    stats: IndexStats
    nodes: list[Node]
    edges: list[Edge]
