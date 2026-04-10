"""dag.py — content-addressed cache (DAG) for tracked execution.

DAG is domain-independent: it works for PyVista pipelines, numpy pipelines,
or any other compute graph where content-addressed caching is useful.
"""

from __future__ import annotations

from typing import Any


class DAG:
    """Content-addressed cache for pipeline execution.

    Stores a mapping from content hashes to live Python objects.
    Call begin_run() before each execution and end_run() after to evict stale
    entries.  Hit/miss/eviction counts are available via stats() after end_run().

    Attributes:
        cache:       content_hash → real object.
        current_run: Set of hashes touched during the current execution.
        names:       variable_name → content_hash, populated by execute_pipeline.
    """

    def __init__(self):
        self.cache: dict[str, Any] = {}
        self.current_run: set[str] = set()
        self.names: dict[str, str] = {}  # variable_name → hash

        # Stats from the last completed run (read via stats())
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0

    def begin_run(self) -> None:
        """Start a new execution run, resetting the tracking set and counters."""
        self.current_run = set()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def end_run(self) -> None:
        """Finish the current run: evict entries not touched this run.

        After this call:
        - cache only contains entries in current_run
        - stats() reflects the completed run
        """
        stale = set(self.cache.keys()) - self.current_run
        for key in stale:
            del self.cache[key]
            self.evictions += 1

    def stats(self) -> dict[str, int]:
        """Return hit/miss/eviction counts from the last completed run."""
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
        }
