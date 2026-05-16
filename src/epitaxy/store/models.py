"""Pydantic models for Epitaxy intent-graph nodes, edges, and index.

See docs/SCHEMA.md §2 (nodes), §3 (edges), §5 (index envelope).

PR4 scope (parameter extraction): adds `ParameterNode` per SCHEMA §2.5,
`AdrNode.decides` field per SCHEMA §2.3, and `"decides"` to the
Edge.type Literal per SCHEMA §3. After PR4, the v0 default-emit surface
is complete: 5 emitted node types (`module` / `function` / `adr` / `plan` /
`parameter`) + 4 edge types (`depends-on` / `references` / `supersedes` /
`decides`). Only `data_asset` + `decision` remain reserved for v1+.
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

    `decides` populated from frontmatter regardless of `parameters_enabled`
    (the field is data per SCHEMA §2.3); `decides` edge emission is gated
    separately in parser/markdown.py per SCHEMA §3.
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["adr"] = "adr"
    id: str
    path: str
    title: str
    status: str | None = None
    date: str | None = None
    supersedes: str | None = None
    decides: list[str] | None = None  # parameter IDs (canonical form: param:<path>::<scope>::<name>)
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


class ParameterNode(BaseModel):
    """A tuned parameter — a Python assignment recognized via either
    `# epitaxy:param` comment or inclusion in an ADR's `decides:` list.
    See SCHEMA.md §2.5.

    Emitted only when `parameters_enabled` is True (CLI `--parameters` or
    `[tool.epitaxy].parameters_enabled = true`). Two signals per SCHEMA
    §2.5; composite provenance value `"ast+comment+adr-frontmatter"`
    when both apply (SCHEMA §2.5 amendment in PR4).
    """

    model_config = ConfigDict(extra="forbid")

    type: Literal["parameter"] = "parameter"
    id: str  # param:<rel_path>::<scope>::<name>
    module: str  # parent module ID
    scope: str  # function qualname OR "<module>" for module-level
    name: str
    value: str  # verbatim source-text RHS (via ast.get_source_segment); never evaluated
    line: int
    decided_by: list[str] | None = None  # ADR IDs that decide this value (populated by parser/refs.py final pass)
    provenance: str  # "ast+comment", "adr-frontmatter", or "ast+comment+adr-frontmatter"


Node = Annotated[
    Union[ModuleNode, FunctionNode, AdrNode, PlanNode, ParameterNode],
    Field(discriminator="type"),
]


class Edge(BaseModel):
    """A typed edge in the intent graph. See SCHEMA.md §3."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str = Field(alias="from")
    to: str
    type: Literal["depends-on", "references", "supersedes", "decides"]
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
