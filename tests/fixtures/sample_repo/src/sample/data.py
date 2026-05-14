"""Sample data loader — supports happy-path parser tests."""


def load():
    """Return fake rows."""
    return [1, 2, 3]


def transform(rows):
    """Double each row."""
    return [r * 2 for r in rows]