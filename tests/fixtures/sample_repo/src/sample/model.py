"""Sample model that depends on data — exercises all 3 supported call shapes."""

from src.sample.data import load


class M:
    def fit(self):
        """Calls `load()` (imported-name) and `self.cleanup()` (self-method)."""
        rows = load()
        self.cleanup()
        return rows

    def cleanup(self):
        pass


def top_level():
    """Calls `helper()` (same-module direct call)."""
    return helper()


def helper():
    return 42