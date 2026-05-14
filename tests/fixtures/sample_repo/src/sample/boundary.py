"""Boundary cases the PR1 parser must NOT emit edges for.

Each function below exercises one explicitly-unsupported call shape.
Tests in tests/parser/test_python.py assert zero edges originate from each.
"""

import json  # noqa: F401  module-qualified-use target below

import sample.data as aliased  # noqa: F401  alias-import target below

from os import path as os_path  # noqa: F401  alias on stdlib


def alias_call():
    """`aliased.load()` — alias import unsupported in PR1; no edge expected."""
    return aliased.load()


def module_qualified_call():
    """`json.dumps(...)` — module-qualified call unsupported in PR1; no edge expected."""
    return json.dumps({})


def dynamic_call():
    """`getattr(...)()` — dynamic dispatch unsupported in PR1; no edge expected."""
    func_name = "load"
    fn = getattr(globals(), func_name, lambda: None)
    return fn()


def third_party_call():
    """`print(...)` — builtin / third-party target unsupported in PR1; no edge expected."""
    return print("hi")