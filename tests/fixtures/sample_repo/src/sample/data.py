"""---
goal: load training rows for the ranker
why: kept dead-simple so PR1 + PR2 parser tests have a stable shape
effects: returns an in-memory list; no I/O in v0
---
Sample data loader — supports happy-path parser tests."""


def load():
    """---
    goal: return fake rows for testing
    prereqs: none
    ---
    Return fake rows."""
    return [1, 2, 3]


def transform(rows):
    """Double each row."""
    return [r * 2 for r in rows]