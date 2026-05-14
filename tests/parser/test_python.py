"""Parser tests — happy path + resolver boundary contract (locked per plan)."""

from __future__ import annotations

from pathlib import Path

import pytest

from epitaxy.parser.python import ParseError, parse_repo


FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_repo"
PACKAGE_ROOTS = ["src/"]


def _collect_py(root: Path) -> list[Path]:
    return sorted(root.glob("src/**/*.py"))


@pytest.fixture(scope="module")
def parsed():
    nodes, edges, errors = parse_repo(
        FIXTURE, _collect_py(FIXTURE), package_roots=PACKAGE_ROOTS
    )
    return nodes, edges, errors


# --------------------------------------------------------------------------- #
# Happy path: module + function nodes are emitted with correct IDs            #
# --------------------------------------------------------------------------- #


def test_emits_module_for_each_python_file(parsed):
    nodes, _, _ = parsed
    module_ids = {n.id for n in nodes if n.type == "module"}
    assert "module:src/sample/__init__.py" in module_ids
    assert "module:src/sample/data.py" in module_ids
    assert "module:src/sample/model.py" in module_ids
    assert "module:src/sample/boundary.py" in module_ids


def test_emits_function_nodes_with_correct_qualname(parsed):
    nodes, _, _ = parsed
    fn_ids = {n.id for n in nodes if n.type == "function"}
    # data.py
    assert "function:src/sample/data.py::load" in fn_ids
    assert "function:src/sample/data.py::transform" in fn_ids
    # model.py — class methods use `Class.method` qualname
    assert "function:src/sample/model.py::M.fit" in fn_ids
    assert "function:src/sample/model.py::M.cleanup" in fn_ids
    assert "function:src/sample/model.py::top_level" in fn_ids
    assert "function:src/sample/model.py::helper" in fn_ids


def test_function_signature_is_source_rendered(parsed):
    nodes, _, _ = parsed
    transform = next(n for n in nodes if n.id == "function:src/sample/data.py::transform")
    assert "def transform" in transform.signature
    assert "rows" in transform.signature


# --------------------------------------------------------------------------- #
# Happy path: depends-on edges for supported call shapes                      #
# --------------------------------------------------------------------------- #


def test_module_to_module_depends_on_from_import(parsed):
    _, edges, _ = parsed
    pairs = {(e.from_, e.to) for e in edges if e.type == "depends-on" and e.source == "import"}
    assert ("module:src/sample/model.py", "module:src/sample/data.py") in pairs


def test_function_to_function_for_imported_name_call(parsed):
    """M.fit() → load() via PEP-conventional `from sample.data import load`."""
    _, edges, _ = parsed
    pairs = {(e.from_, e.to) for e in edges if e.type == "depends-on" and e.source == "call"}
    assert (
        "function:src/sample/model.py::M.fit",
        "function:src/sample/data.py::load",
    ) in pairs


def test_function_to_function_for_self_method_call(parsed):
    """M.fit() → M.cleanup() via `self.cleanup()`."""
    _, edges, _ = parsed
    pairs = {(e.from_, e.to) for e in edges if e.type == "depends-on" and e.source == "call"}
    assert (
        "function:src/sample/model.py::M.fit",
        "function:src/sample/model.py::M.cleanup",
    ) in pairs


def test_function_to_function_for_same_module_direct_call(parsed):
    """top_level() → helper() — both defined in model.py."""
    _, edges, _ = parsed
    pairs = {(e.from_, e.to) for e in edges if e.type == "depends-on" and e.source == "call"}
    assert (
        "function:src/sample/model.py::top_level",
        "function:src/sample/model.py::helper",
    ) in pairs


# --------------------------------------------------------------------------- #
# Resolver boundary contract — these MUST NOT emit edges in PR1               #
# --------------------------------------------------------------------------- #


def _outgoing_call_edges_from(edges, qualname_id_suffix):
    """Helper: return call-source edges originating from a given qualname."""
    return [
        e for e in edges
        if e.type == "depends-on"
        and e.source == "call"
        and e.from_.endswith(qualname_id_suffix)
    ]


def test_no_edge_for_alias_import(parsed):
    """`import x as y; y.foo()` — alias import unsupported in PR1."""
    _, edges, _ = parsed
    assert _outgoing_call_edges_from(edges, "::alias_call") == []


def test_no_edge_for_module_qualified_call(parsed):
    """`import x; x.foo()` — module-qualified call unsupported in PR1."""
    _, edges, _ = parsed
    assert _outgoing_call_edges_from(edges, "::module_qualified_call") == []


def test_no_edge_for_dynamic_dispatch(parsed):
    """`getattr(...)()` — dynamic dispatch unsupported in PR1."""
    _, edges, _ = parsed
    assert _outgoing_call_edges_from(edges, "::dynamic_call") == []


def test_no_edge_for_third_party_call(parsed):
    """`print(...)` — third-party / builtin call unsupported in PR1."""
    _, edges, _ = parsed
    assert _outgoing_call_edges_from(edges, "::third_party_call") == []


# --------------------------------------------------------------------------- #
# Codex review fixes: src-layout, nested function attribution, ParseError     #
# --------------------------------------------------------------------------- #


def test_src_layout_import_resolves_via_package_roots(tmp_path: Path) -> None:
    """`from pkg.x import y` in a `src/pkg/x.py` repo must resolve.

    Without `package_roots=["src/"]`, `_module_dotted_name` keys files under
    `src.pkg.x` and the natural `from pkg.x import y` fails to resolve
    (Codex review High-2). Locks the fix.
    """
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("")
    (repo / "src" / "pkg" / "lib.py").write_text("def util(): pass\n")
    (repo / "src" / "pkg" / "main.py").write_text(
        "from pkg.lib import util\n\ndef run():\n    util()\n"
    )

    files = sorted(repo.glob("src/**/*.py"))
    nodes, edges, errors = parse_repo(repo, files, package_roots=["src/"])

    assert errors == []
    call_edges = [(e.from_, e.to) for e in edges if e.source == "call"]
    assert (
        "function:src/pkg/main.py::run",
        "function:src/pkg/lib.py::util",
    ) in call_edges


def test_nested_function_call_does_not_pollute_outer_edges(parsed) -> None:
    """`def outer(): def inner(): helper()` must NOT emit `outer -> helper`.

    Codex review Medium-3: ast.walk descends into inner; _calls_in_function_body
    locks the boundary.
    """
    _, edges, _ = parsed
    pairs = {(e.from_, e.to) for e in edges if e.type == "depends-on" and e.source == "call"}
    assert (
        "function:src/sample/model.py::outer_with_nested_inner",
        "function:src/sample/model.py::helper",
    ) not in pairs


def test_syntax_error_file_returned_as_parse_error_not_raised(tmp_path: Path) -> None:
    """One bad file shouldn't abort parse; surface in `list[ParseError]` so
    CLI can exit 3 per CLI.md §7 (Codex review Medium-4)."""
    repo = tmp_path / "repo"
    (repo / "src" / "pkg").mkdir(parents=True)
    (repo / "src" / "pkg" / "__init__.py").write_text("")
    (repo / "src" / "pkg" / "ok.py").write_text("def good(): pass\n")
    (repo / "src" / "pkg" / "bad.py").write_text("def broken(:  # syntax error\n")

    files = sorted(repo.glob("src/**/*.py"))
    nodes, _edges, errors = parse_repo(repo, files, package_roots=["src/"])

    # Index still gets a node for the good file
    assert any(n.id == "module:src/pkg/ok.py" for n in nodes)
    # Bad file is surfaced as ParseError
    assert len(errors) == 1
    err = errors[0]
    assert isinstance(err, ParseError)
    assert err.path.name == "bad.py"
    assert "SyntaxError" in err.reason
