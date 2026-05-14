"""Parser tests — happy path + resolver boundary contract (locked per plan)."""

from __future__ import annotations

from pathlib import Path

import pytest

from epitaxy.parser.python import parse_repo


FIXTURE = Path(__file__).parent.parent / "fixtures" / "sample_repo"


def _collect_py(root: Path) -> list[Path]:
    return sorted(root.glob("src/**/*.py"))


@pytest.fixture(scope="module")
def parsed():
    nodes, edges = parse_repo(FIXTURE, _collect_py(FIXTURE))
    return nodes, edges


# --------------------------------------------------------------------------- #
# Happy path: module + function nodes are emitted with correct IDs            #
# --------------------------------------------------------------------------- #


def test_emits_module_for_each_python_file(parsed):
    nodes, _ = parsed
    module_ids = {n.id for n in nodes if n.type == "module"}
    assert "module:src/sample/__init__.py" in module_ids
    assert "module:src/sample/data.py" in module_ids
    assert "module:src/sample/model.py" in module_ids
    assert "module:src/sample/boundary.py" in module_ids


def test_emits_function_nodes_with_correct_qualname(parsed):
    nodes, _ = parsed
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
    nodes, _ = parsed
    transform = next(n for n in nodes if n.id == "function:src/sample/data.py::transform")
    assert "def transform" in transform.signature
    assert "rows" in transform.signature


# --------------------------------------------------------------------------- #
# Happy path: depends-on edges for supported call shapes                      #
# --------------------------------------------------------------------------- #


def test_module_to_module_depends_on_from_import(parsed):
    _, edges = parsed
    pairs = {(e.from_, e.to) for e in edges if e.type == "depends-on" and e.source == "import"}
    assert ("module:src/sample/model.py", "module:src/sample/data.py") in pairs


def test_function_to_function_for_imported_name_call(parsed):
    """M.fit() → load() via `from src.sample.data import load`."""
    _, edges = parsed
    pairs = {(e.from_, e.to) for e in edges if e.type == "depends-on" and e.source == "call"}
    assert (
        "function:src/sample/model.py::M.fit",
        "function:src/sample/data.py::load",
    ) in pairs


def test_function_to_function_for_self_method_call(parsed):
    """M.fit() → M.cleanup() via `self.cleanup()`."""
    _, edges = parsed
    pairs = {(e.from_, e.to) for e in edges if e.type == "depends-on" and e.source == "call"}
    assert (
        "function:src/sample/model.py::M.fit",
        "function:src/sample/model.py::M.cleanup",
    ) in pairs


def test_function_to_function_for_same_module_direct_call(parsed):
    """top_level() → helper() — both defined in model.py."""
    _, edges = parsed
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
    _, edges = parsed
    assert _outgoing_call_edges_from(edges, "::alias_call") == []


def test_no_edge_for_module_qualified_call(parsed):
    """`import x; x.foo()` — module-qualified call unsupported in PR1."""
    _, edges = parsed
    assert _outgoing_call_edges_from(edges, "::module_qualified_call") == []


def test_no_edge_for_dynamic_dispatch(parsed):
    """`getattr(...)()` — dynamic dispatch unsupported in PR1."""
    _, edges = parsed
    assert _outgoing_call_edges_from(edges, "::dynamic_call") == []


def test_no_edge_for_third_party_call(parsed):
    """`print(...)` — third-party / builtin call unsupported in PR1."""
    _, edges = parsed
    assert _outgoing_call_edges_from(edges, "::third_party_call") == []