"""tracked_core — domain-independent core for content-addressed proxy execution.

This package contains the generic components of the tracked execution system
that are independent of any specific domain (PyVista, numpy, etc.):

    DAG           — content-addressed cache with per-run GC
    TrackedProxy  — transparent proxy that routes calls through a dispatch fn
    stable_hash   — deterministic content hashing
    _dag_call     — shared cache-check / execute / store pattern
    _should_wrap  — predicate: should this object be wrapped in a proxy?
    dispatch      — generic dispatch: whitelist check, hash, cache, execute

Domain-specific code (e.g. tracked_execution) imports from here and provides
the whitelist/blacklist, dispatch function, and namespace contents.
"""

from .dag import DAG
from .proxy import TrackedProxy
from .dispatch import stable_hash, _dag_call, _should_wrap, dispatch

__all__ = [
    "DAG",
    "TrackedProxy",
    "stable_hash",
    "_dag_call",
    "_should_wrap",
    "dispatch",
]
