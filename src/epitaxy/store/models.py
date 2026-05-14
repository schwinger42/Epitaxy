"""Pydantic models for Epitaxy intent-graph nodes, edges, and index.

See docs/SCHEMA.md §2 (nodes), §3 (edges), §5 (index envelope).

PR1 scope (tracer-bullet): only `module` and `function` node types; only
`depends-on` edges are emitted by the parser, but `references` and
`supersedes` are valid Edge.type values so PR2 can add them without a
model migration.
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


Node = Annotated[Union[ModuleNode, FunctionNode], Field(discriminator="type")]


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
