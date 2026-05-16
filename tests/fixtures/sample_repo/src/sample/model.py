"""---
goal: train ranker over the data layer
why: rank=128 chosen per [ADR 2026-04](../../decisions/2026-04-rank-dim.md)
prereqs: data loaded via [data.py](src/sample/data.py)
decisions:
  - adr:decisions/2026-04-rank-dim.md
---
Sample model that depends on data — exercises all 3 supported call shapes.

Uses PEP-conventional src-layout imports (`from sample.data import load`,
NOT `from src.sample.data import load`) — the natural form after
`pip install -e .` on a src-layout repo. The parser strips the `src/`
package-root prefix when computing dotted module names (parser/python.py
`_module_dotted_name`).
"""

from sample.data import load


DEFAULT_RANK = 64  # epitaxy:param
sample_temperature_K = 77  # epitaxy:param


class M:
    def fit(self):
        """Calls `load()` (imported-name) and `self.cleanup()` (self-method)."""
        rank = 128  # epitaxy:param
        learning_rate = 0.001  # NO marker — claimed by ADR 2026-04-rank-dim decides:
        rows = load()
        self.cleanup()
        return rows, rank, learning_rate

    def cleanup(self):
        cleanup_threshold = 0.95  # unmarked + not ADR-claimed — should NOT emit
        return cleanup_threshold


def top_level():
    """Calls `helper()` (same-module direct call)."""
    return helper()


def helper():
    return 42


def outer_with_nested_inner():
    """Nested-function attribution boundary: inner() calling helper() must
    NOT emit an edge from outer_with_nested_inner -> helper. See parser
    `_calls_in_function_body`. Codex review Medium-3 locks this."""

    def inner():
        return helper()  # this call belongs to inner, not outer

    return inner