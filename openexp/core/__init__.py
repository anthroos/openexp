"""OpenExp core — Q-learning memory engine."""
from .q_value import QCache, QValueUpdater, QValueScorer


def search_memories(*args, **kwargs):
    """Lazy import to avoid requiring fastembed at import time."""
    from .direct_search import search_memories as _search
    return _search(*args, **kwargs)


def add_memory(*args, **kwargs):
    """Lazy import to avoid requiring fastembed at import time."""
    from .direct_search import add_memory as _add
    return _add(*args, **kwargs)
