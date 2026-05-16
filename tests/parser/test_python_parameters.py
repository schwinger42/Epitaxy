"""Tests for parser/python.py `_extract_parameters` (PR4).

Per SCHEMA §2.5, a parameter is recognized via EITHER:
  (a) `# epitaxy:param` comment on the assignment line (strict physical
      line per Codex round-1 Med-9 + tightened regex per Low-10), OR
  (b) Inclusion in an ADR's `decides:` frontmatter list (the
      decides_claimed set from parser/markdown.py).

Composite case: BOTH signals → provenance "ast+comment+adr-frontmatter"
per SCHEMA §2.5 amended in PR4.

The fixtures here intentionally mix ML-flavored parameters (`rank`) and
domain-constrained values (`sample_temperature_K`, `chamber_pressure_Torr`)
to exercise the broader product framing per
[[feedback_epitaxy_product_framing]].
"""

from __future__ import annotations

from pathlib import Path

import pytest

from epitaxy.parser import parse_repo
from epitaxy.store.models import ModuleNode, ParameterNode


def _write(p: Path, content: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def _parse(repo: Path, **kwargs):
    py_files = sorted(repo.rglob("*.py"))
    return parse_repo(repo, py_files, **kwargs)


# --------------------------------------------------------------------------- #
# Comment-only signal                                                         #
# --------------------------------------------------------------------------- #


def test_comment_marked_function_level_param(tmp_path: Path) -> None:
    _write(
        tmp_path / "m.py",
        "class Cls:\n"
        "    def fit(self):\n"
        "        rank = 128  # epitaxy:param\n"
        "        return rank\n",
    )
    nodes, _edges, errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert errors == []
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1
    p = params[0]
    assert p.id == "param:m.py::Cls.fit::rank"
    assert p.module == "module:m.py"
    assert p.scope == "Cls.fit"
    assert p.name == "rank"
    assert p.value == "128"
    assert p.line == 3
    assert p.provenance == "ast+comment"


def test_comment_marked_module_level_param(tmp_path: Path) -> None:
    """Domain-flavored example: a cryogenic-measurement physical constraint."""
    _write(
        tmp_path / "lab.py",
        "sample_temperature_K = 77  # epitaxy:param\n"
        "def measure():\n    pass\n",
    )
    nodes, _edges, errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert errors == []
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1
    p = params[0]
    assert p.id == "param:lab.py::<module>::sample_temperature_K"
    assert p.scope == "<module>"
    assert p.name == "sample_temperature_K"
    assert p.value == "77"
    assert p.provenance == "ast+comment"


def test_ann_assign_with_marker(tmp_path: Path) -> None:
    """`x: int = 1` (AnnAssign) is supported alongside plain `x = 1`."""
    _write(
        tmp_path / "m.py",
        "def go():\n    rank: int = 128  # epitaxy:param\n    return rank\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1
    assert params[0].name == "rank" and params[0].value == "128"


# --------------------------------------------------------------------------- #
# ADR-driven (decides_claimed) signal                                         #
# --------------------------------------------------------------------------- #


def test_adr_claimed_param_emitted_without_comment(tmp_path: Path) -> None:
    """SCHEMA §2.5 OR clause: ADR claims the parameter; no comment needed."""
    _write(
        tmp_path / "m.py",
        "def go():\n    rank = 128\n    return rank\n",
    )
    nodes, _edges, _errors, _bodies = _parse(
        tmp_path,
        parameters_enabled=True,
        decides_claimed={"param:m.py::go::rank"},
    )
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1
    p = params[0]
    assert p.id == "param:m.py::go::rank"
    assert p.provenance == "adr-frontmatter"


def test_adr_claimed_param_id_not_matching_any_assignment_silently_dropped(
    tmp_path: Path,
) -> None:
    """ADR claims a parameter that doesn't exist in source: parser/python
    silently ignores (the decides edge from C2's markdown parser still
    points at the dangling target per SCHEMA §6 — drift signal at the edge
    level, not a parameter node)."""
    _write(tmp_path / "m.py", "def go():\n    rank = 128\n")
    nodes, _edges, _errors, _bodies = _parse(
        tmp_path,
        parameters_enabled=True,
        decides_claimed={"param:m.py::ghost::removed"},
    )
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert params == []  # no assignment matches; no node emitted


# --------------------------------------------------------------------------- #
# Composite signal — both comment AND ADR claim                               #
# --------------------------------------------------------------------------- #


def test_composite_provenance_when_both_signals_present(tmp_path: Path) -> None:
    """SCHEMA §2.5 amendment (PR4 C1): composite provenance vocab is
    `ast+comment+adr-frontmatter` when both signals apply."""
    _write(
        tmp_path / "m.py",
        "def go():\n    rank = 128  # epitaxy:param\n",
    )
    nodes, _edges, _errors, _bodies = _parse(
        tmp_path,
        parameters_enabled=True,
        decides_claimed={"param:m.py::go::rank"},
    )
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1
    assert params[0].provenance == "ast+comment+adr-frontmatter"


# --------------------------------------------------------------------------- #
# Verbatim value preservation (Codex round-1 Med-5)                           #
# --------------------------------------------------------------------------- #


def test_value_preserved_as_source_text_scientific_notation(tmp_path: Path) -> None:
    """MCP §3 says current_value is verbatim. `1e-3` must NOT normalize to
    `0.001` (that's what ast.unparse would do)."""
    _write(
        tmp_path / "m.py",
        "def go():\n    lr = 1e-3  # epitaxy:param\n    return lr\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1
    assert params[0].value == "1e-3"


def test_value_preserved_expression(tmp_path: Path) -> None:
    """Non-literal RHS: keep the expression verbatim."""
    _write(
        tmp_path / "m.py",
        "import os\n"
        "def go():\n"
        "    rank = int(os.environ['RANK'])  # epitaxy:param\n"
        "    return rank\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1
    assert params[0].value == "int(os.environ['RANK'])"


def test_value_preserved_negative_float(tmp_path: Path) -> None:
    """Domain-flavored: a mathematical bound that needs verbatim preservation."""
    _write(
        tmp_path / "m.py",
        "def filter_signal():\n"
        "    cutoff_dB = -30.0  # epitaxy:param\n"
        "    return cutoff_dB\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert params[0].value == "-30.0"


# --------------------------------------------------------------------------- #
# Strict physical-line rule (Codex round-1 Med-9)                             #
# --------------------------------------------------------------------------- #


def test_continuation_line_marker_not_recognized(tmp_path: Path) -> None:
    """Marker MUST be on the assignment-opener line. A comment on a
    continuation line is NOT recognized — assign.lineno points at the
    opener, and we look only at that physical line."""
    _write(
        tmp_path / "m.py",
        "def go():\n"
        "    rank = (\n"
        "        1 + 2  # epitaxy:param\n"  # continuation line — NOT recognized
        "    )\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert params == []  # continuation-line marker doesn't count


def test_marker_on_opener_line_with_multi_line_value(tmp_path: Path) -> None:
    """Workaround for the strict rule: put the marker on the opener line."""
    _write(
        tmp_path / "m.py",
        "def go():\n"
        "    rank = (  # epitaxy:param\n"
        "        1 + 2\n"
        "    )\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1
    assert params[0].name == "rank"


# --------------------------------------------------------------------------- #
# Tightened regex (Codex round-1 Low-10)                                      #
# --------------------------------------------------------------------------- #


def test_marker_typo_with_trailing_s_not_recognized(tmp_path: Path) -> None:
    _write(
        tmp_path / "m.py",
        "def go():\n    rank = 128  # epitaxy:params\n",  # typo
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


def test_marker_with_suffix_not_recognized(tmp_path: Path) -> None:
    _write(
        tmp_path / "m.py",
        "def go():\n    rank = 128  # epitaxy:param-extra-stuff\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


def test_marker_followed_by_explanation_text_recognized(tmp_path: Path) -> None:
    """`# epitaxy:param  some explanation` should match (explanation is fine
    after the marker; the regex accepts whitespace or EOL after the marker)."""
    _write(
        tmp_path / "m.py",
        "def go():\n    rank = 128  # epitaxy:param  per ADR 2026-04\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    assert len(params) == 1


# --------------------------------------------------------------------------- #
# Unsupported assignment forms — silently skipped                             #
# --------------------------------------------------------------------------- #


def test_tuple_lhs_assignment_skipped(tmp_path: Path) -> None:
    _write(
        tmp_path / "m.py",
        "def go():\n    a, b = 1, 2  # epitaxy:param\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


def test_augmented_assignment_skipped(tmp_path: Path) -> None:
    """AugAssign (`x += 1`) is not a base assignment."""
    _write(
        tmp_path / "m.py",
        "def go():\n    x = 0\n    x += 1  # epitaxy:param\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    # The `x = 0` line has no marker; the `x += 1` line is AugAssign which is
    # explicitly skipped — only the initial Assign on line 2 is a candidate,
    # and it has no marker.
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


def test_attribute_lhs_skipped(tmp_path: Path) -> None:
    """`self.x = 1` is not a parameter."""
    _write(
        tmp_path / "m.py",
        "class Cls:\n"
        "    def fit(self):\n"
        "        self.rank = 128  # epitaxy:param\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


def test_ann_assign_without_value_skipped(tmp_path: Path) -> None:
    """Bare annotation `x: int` (no value) is not a parameter."""
    _write(
        tmp_path / "m.py",
        "def go():\n    rank: int  # epitaxy:param\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


def test_unmarked_assignment_skipped(tmp_path: Path) -> None:
    """Plain assignment without marker AND not ADR-claimed → no parameter."""
    _write(tmp_path / "m.py", "def go():\n    rank = 128\n")
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


def test_semicolon_stacked_marker_attributes_to_rightmost(tmp_path: Path) -> None:
    """Codex code-time Med-2: `a = 1; b = 2  # epitaxy:param` → marker
    applies to `b` only (rightmost on the line), NOT to `a`.

    Determined by `.col_offset`: the rightmost assignment on the marked
    line is closest to the trailing comment.
    """
    _write(
        tmp_path / "m.py",
        "def go():\n    a = 1; b = 2  # epitaxy:param\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = {p.name: p for p in nodes if isinstance(p, ParameterNode)}
    assert "b" in params
    assert "a" not in params
    assert params["b"].value == "2"
    assert params["b"].provenance == "ast+comment"


def test_semicolon_stacked_no_marker_emits_nothing(tmp_path: Path) -> None:
    """Sanity: semicolon-stacked with NO marker → no parameters."""
    _write(
        tmp_path / "m.py",
        "def go():\n    a = 1; b = 2\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


# --------------------------------------------------------------------------- #
# Gating: parameters_enabled=False → ZERO parameter nodes                     #
# --------------------------------------------------------------------------- #


def test_zero_parameters_when_flag_disabled(tmp_path: Path) -> None:
    """Codex round-1 High-1 regression guard: --parameters off means zero
    parameter extraction regardless of comments OR decides_claimed."""
    _write(
        tmp_path / "m.py",
        "def go():\n    rank = 128  # epitaxy:param\n",
    )
    nodes, _edges, _errors, _bodies = _parse(
        tmp_path,
        parameters_enabled=False,
        decides_claimed={"param:m.py::go::rank"},
    )
    assert [n for n in nodes if isinstance(n, ParameterNode)] == []


# --------------------------------------------------------------------------- #
# Multi-parameter file with mixed ML + domain signals                         #
# --------------------------------------------------------------------------- #


def test_mixed_ml_and_domain_parameters_in_one_file(tmp_path: Path) -> None:
    """Broader product framing: ML hyperparameters AND domain-constrained
    values are equally first-class. Per [[feedback_epitaxy_product_framing]]."""
    _write(
        tmp_path / "experiment.py",
        "DEFAULT_RANK = 64  # epitaxy:param\n"
        "sample_temperature_K = 77  # epitaxy:param\n"
        "chamber_pressure_Torr = 1e-6  # epitaxy:param\n"
        "class Ranker:\n"
        "    def fit(self):\n"
        "        rank = 128  # epitaxy:param\n"
        "        validation_threshold = 0.95  # epitaxy:param\n"
        "        return rank\n",
    )
    nodes, _edges, _errors, _bodies = _parse(tmp_path, parameters_enabled=True)
    params = [n for n in nodes if isinstance(n, ParameterNode)]
    names = sorted(p.name for p in params)
    assert names == [
        "DEFAULT_RANK",
        "chamber_pressure_Torr",
        "rank",
        "sample_temperature_K",
        "validation_threshold",
    ]
    # Mixed scopes
    scopes = {p.scope for p in params}
    assert scopes == {"<module>", "Ranker.fit"}
    # Value preservation across types
    by_name = {p.name: p for p in params}
    assert by_name["sample_temperature_K"].value == "77"
    assert by_name["chamber_pressure_Torr"].value == "1e-6"  # NOT 1e-06
    assert by_name["validation_threshold"].value == "0.95"
