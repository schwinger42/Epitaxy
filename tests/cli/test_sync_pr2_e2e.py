"""End-to-end `epi sync` tests for PR2 doc-parsing scope.

Exercises the full sample_repo fixture (Python + ADR + plan + POR docstrings)
through the CLI sync command. Asserts that the on-disk index.json contains
all 4 default node types and all 3 default edge types per SCHEMA §1.2.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from epitaxy.cli.app import app

runner = CliRunner()


@pytest.fixture
def sample_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy the on-disk sample_repo fixture into a tmp dir + cd into it."""
    src_root = Path(__file__).parent.parent / "fixtures" / "sample_repo"
    dest = tmp_path / "repo"

    import shutil

    shutil.copytree(src_root, dest)
    # Sync needs a pyproject.toml at the working root so [tool.epitaxy] config
    # resolution doesn't escape to the actual repo's pyproject.
    (dest / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.0.0"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(dest)
    return dest


def _read_index(repo: Path) -> dict:
    return json.loads((repo / ".epitaxy" / "index.json").read_text())


def test_sync_emits_all_four_default_node_types(sample_repo: Path) -> None:
    """SCHEMA §1.2 commits 4 of 7 node types to the default parser."""
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0, result.output

    payload = _read_index(sample_repo)
    node_types = sorted({n["type"] for n in payload["nodes"]})
    assert node_types == ["adr", "function", "module", "plan"]


def test_sync_emits_all_three_default_edge_types(sample_repo: Path) -> None:
    """SCHEMA §1.2 commits 3 of 4 edge types to the default parser (decides=PR4)."""
    runner.invoke(app, ["sync", "--quiet"])
    payload = _read_index(sample_repo)
    edge_types = sorted({e["type"] for e in payload["edges"]})
    assert edge_types == ["depends-on", "references", "supersedes"]


def test_sync_stats_counts_match(sample_repo: Path) -> None:
    runner.invoke(app, ["sync", "--quiet"])
    payload = _read_index(sample_repo)
    stats = payload["stats"]

    assert stats["modules"] == sum(1 for n in payload["nodes"] if n["type"] == "module")
    assert stats["functions"] == sum(
        1 for n in payload["nodes"] if n["type"] == "function"
    )
    assert stats["adrs"] >= 2  # two ADRs in fixture
    assert stats["plans"] >= 1  # one plan in fixture
    assert stats["edges"] == len(payload["edges"])


def test_supersedes_edge_emitted(sample_repo: Path) -> None:
    """The newer ADR supersedes the baseline; edge must appear."""
    runner.invoke(app, ["sync", "--quiet"])
    payload = _read_index(sample_repo)
    sup_edges = [e for e in payload["edges"] if e["type"] == "supersedes"]
    assert len(sup_edges) == 1
    assert sup_edges[0]["from"] == "adr:decisions/2026-04-rank-dim.md"
    assert sup_edges[0]["to"] == "adr:decisions/2026-02-rank-baseline.md"
    assert sup_edges[0]["source"] == "frontmatter:supersedes"
    assert sup_edges[0]["provenance"] == "frontmatter"


def test_references_edges_present(sample_repo: Path) -> None:
    """Plan body links to model.py + data.py; ADR body links to model.py."""
    runner.invoke(app, ["sync", "--quiet"])
    payload = _read_index(sample_repo)
    ref_edges = [e for e in payload["edges"] if e["type"] == "references"]
    assert len(ref_edges) >= 1

    # All references use SCHEMA's body-mention vocab
    for e in ref_edges:
        assert e["source"] == "body-mention"
        assert e["provenance"] == "body-mention"

    # The plan body references the model module
    pairs = {(e["from"], e["to"]) for e in ref_edges}
    assert (
        "plan:docs/plans/q2-launch.md",
        "module:src/sample/model.py",
    ) in pairs


def test_por_docstring_frontmatter_populated(sample_repo: Path) -> None:
    """data.py + model.py have POR frontmatter; node.por must reflect that."""
    runner.invoke(app, ["sync", "--quiet"])
    payload = _read_index(sample_repo)
    by_id = {n["id"]: n for n in payload["nodes"]}

    data_module = by_id["module:src/sample/data.py"]
    assert data_module.get("por") is not None
    assert "goal" in data_module["por"]

    load_fn = by_id.get("function:src/sample/data.py::load")
    assert load_fn is not None
    assert load_fn.get("por") is not None
    assert load_fn["por"]["goal"] == "return fake rows for testing"


def test_module_doc_is_post_frontmatter_body_not_yaml(sample_repo: Path) -> None:
    """Codex round-1 Low-1: node.doc reads body AFTER closing `---`, not YAML."""
    runner.invoke(app, ["sync", "--quiet"])
    payload = _read_index(sample_repo)
    by_id = {n["id"]: n for n in payload["nodes"]}

    data_module = by_id["module:src/sample/data.py"]
    doc = data_module.get("doc") or ""
    assert "goal:" not in doc  # YAML field name must NOT leak into narrative
    assert "Sample data loader" in doc


def test_existing_pr1_edges_still_present(sample_repo: Path) -> None:
    """PR2 must not regress PR1's depends-on edges."""
    runner.invoke(app, ["sync", "--quiet"])
    payload = _read_index(sample_repo)
    deps = {
        (e["from"], e["to"])
        for e in payload["edges"]
        if e["type"] == "depends-on"
    }
    assert (
        "module:src/sample/model.py",
        "module:src/sample/data.py",
    ) in deps


def test_supersedes_edge_persists_even_if_target_missing(
    sample_repo: Path,
) -> None:
    """Add an ADR that supersedes a non-existent file; edge should still emit."""
    (sample_repo / "decisions" / "ghost-supersedes.md").write_text(
        "---\ntitle: ghost\nsupersedes: adr:decisions/never-existed.md\n---\n# ghost",
        encoding="utf-8",
    )
    runner.invoke(app, ["sync", "--quiet"])
    payload = _read_index(sample_repo)
    sup_targets = {
        e["to"] for e in payload["edges"] if e["type"] == "supersedes"
    }
    assert "adr:decisions/never-existed.md" in sup_targets


def test_malformed_adr_yaml_fails_fast_with_exit_3(sample_repo: Path) -> None:
    """Per CLI.md §7: partial-success exit code 3 + warning on stderr."""
    (sample_repo / "decisions" / "broken.md").write_text(
        "---\ntitle: [unclosed\n---\n# x",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 3
    # Index still written (partial-success)
    assert (sample_repo / ".epitaxy" / "index.json").exists()


def test_malformed_por_yaml_fails_fast_with_exit_3(sample_repo: Path) -> None:
    """Codex round-2 Med-2: malformed POR YAML must trigger ParseError."""
    (sample_repo / "src" / "sample" / "bad_por.py").write_text(
        '"""---\nbroken: [unclosed\n---\nbody"""\n',
        encoding="utf-8",
    )
    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 3
    assert (sample_repo / ".epitaxy" / "index.json").exists()
