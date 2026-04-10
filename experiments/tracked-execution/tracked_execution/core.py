"""Core DAG — stores cache, tracks current execution run, manages eviction.

The DAG class is the central store for content-addressed caching.
Each pipeline execution calls begin_run() at the start and end_run() at the end.
Entries not touched during the run are evicted (GC).
"""

from __future__ import annotations

from typing import Any


class DAG:
    """Content-addressed cache for pipeline execution.

    Attributes:
        cache: Maps content_hash (str) → real Python/VTK/numpy object.
        current_run: Set of hashes touched during the current execution.
        names: Maps variable_name (str) → content_hash, populated after exec.

    Lifecycle per execution:
        1. Call begin_run() to start a fresh tracking set.
        2. Execute the pipeline (TrackedProxy dispatches update current_run).
        3. Call end_run() to evict stale entries and collect stats.
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
