"""End-to-end tests for `epi sync` — uses the sample_repo fixture."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from epitaxy.cli.app import app


FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_repo"
runner = CliRunner()


@pytest.fixture
def sample_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Copy sample_repo fixture into tmp_path and chdir to it."""
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    monkeypatch.chdir(dest)
    return dest


def test_sync_writes_index_json(sample_repo: Path) -> None:
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0, result.output

    index_path = sample_repo / ".epitaxy" / "index.json"
    assert index_path.exists()


def test_sync_index_contains_expected_modules_and_functions(sample_repo: Path) -> None:
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0

    payload = json.loads((sample_repo / ".epitaxy" / "index.json").read_text())
    module_ids = {n["id"] for n in payload["nodes"] if n["type"] == "module"}
    assert "module:src/sample/data.py" in module_ids
    assert "module:src/sample/model.py" in module_ids
    assert "module:src/sample/boundary.py" in module_ids

    fn_ids = {n["id"] for n in payload["nodes"] if n["type"] == "function"}
    assert "function:src/sample/data.py::load" in fn_ids
    assert "function:src/sample/model.py::M.fit" in fn_ids


def test_sync_index_contains_expected_edges(sample_repo: Path) -> None:
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0

    payload = json.loads((sample_repo / ".epitaxy" / "index.json").read_text())
    edges = payload["edges"]
    edge_pairs = {(e["from"], e["to"]) for e in edges}

    # import-based module edge
    assert (
        "module:src/sample/model.py",
        "module:src/sample/data.py",
    ) in edge_pairs
    # imported-name call edge
    assert (
        "function:src/sample/model.py::M.fit",
        "function:src/sample/data.py::load",
    ) in edge_pairs


def test_sync_parameters_flag_runs_extraction(sample_repo: Path) -> None:
    """PR4: `--parameters` actually extracts (was fail-fast in PR1–PR3).

    Fixture exercises all 4 SCHEMA §2.5 paths:
    - Comment-marked (rank, DEFAULT_RANK, sample_temperature_K)
    - ADR-claimed only (learning_rate — no comment, listed in
      decisions/2026-04-rank-dim.md `decides:` frontmatter)
    - Composite (rank also in 2026-04 `decides:`)
    - Negative (cleanup_threshold has no marker + no ADR claim)
    """
    result = runner.invoke(app, ["sync", "--parameters"])
    assert result.exit_code == 0, result.output
    payload = json.loads(
        (sample_repo / ".epitaxy" / "index.json").read_text()
    )
    assert payload["config"]["parameters_enabled"] is True
    param_nodes = [n for n in payload["nodes"] if n["type"] == "parameter"]
    param_names = sorted(n["name"] for n in param_nodes)
    assert param_names == [
        "DEFAULT_RANK",
        "learning_rate",
        "rank",
        "sample_temperature_K",
    ]
    assert payload["stats"]["parameters"] == 4
    # Negative case: cleanup_threshold has no marker + no ADR claim
    assert "cleanup_threshold" not in param_names


def test_sync_prints_gitignore_tip_when_missing(sample_repo: Path) -> None:
    """Bootstrap UX: sample_repo has no .gitignore, so tip should fire."""
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0
    assert "add `.epitaxy/`" in result.output


def test_sync_skips_tip_when_gitignore_lists_epitaxy(sample_repo: Path) -> None:
    (sample_repo / ".gitignore").write_text(".epitaxy/\n")
    result = runner.invoke(app, ["sync", "--quiet"])
    assert result.exit_code == 0
    assert "add `.epitaxy/`" not in result.output


def test_version_flag(sample_repo: Path) -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_sync_parameters_enabled_in_config_runs_extraction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PR4: `[tool.epitaxy] parameters_enabled = true` now runs extraction
    the same way as `--parameters` per CLI.md §6 precedence (both route to
    the same effective `parameters_enabled` flag). Was fail-fast in PR1–PR3.
    """
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / "pyproject.toml").write_text(
        "[tool.epitaxy]\n"
        'roots = ["src/**/*.py"]\n'
        "parameters_enabled = true\n"
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["sync"])
    assert result.exit_code == 0, result.output
    payload = json.loads((repo / ".epitaxy" / "index.json").read_text())
    assert payload["config"]["parameters_enabled"] is True


def test_sync_honors_output_config_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`[tool.epitaxy] output = "..."` overrides the default `.epitaxy/index.json`.

    Codex review Medium-5: this key was documented in CLI.md §5 but missing
    from IndexConfig — `extra="forbid"` rejected it as a config error.
    """
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / "pyproject.toml").write_text(
        "[tool.epitaxy]\n"
        'roots = ["src/**/*.py"]\n'
        'output = "custom/idx.json"\n'
    )
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["sync", "--quiet"])

    assert result.exit_code == 0, result.output
    assert (repo / "custom" / "idx.json").exists()
    assert not (repo / ".epitaxy" / "index.json").exists()


def test_sync_exits_3_when_a_file_fails_to_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Per CLI.md §7: exit 3 = partial success (parse error, index still written).

    Codex review Medium-4: previously SyntaxError was silently swallowed and
    sync exited 0; downstream CI couldn't distinguish clean run from partial.
    """
    repo = tmp_path / "evil_repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("")
    (repo / "src" / "pkg" / "ok.py").write_text("def good(): pass\n")
    (repo / "src" / "pkg" / "bad.py").write_text("def broken(:  # syntax error\n")
    monkeypatch.chdir(repo)

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 3, result.output
    # Warning surfaces in stderr / output
    assert "failed to parse" in result.output
    assert "bad.py" in result.output
    # Index still written with the good file
    payload = json.loads((repo / ".epitaxy" / "index.json").read_text())
    assert any(n["id"] == "module:src/pkg/ok.py" for n in payload["nodes"])