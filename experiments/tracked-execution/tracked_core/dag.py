"""Content-addressed cache (DAG) for tracked execution."""

from __future__ import annotations

from typing import Any


class DAG:
    """Content-addressed cache: maps content hashes to live Python objects.

    Call begin_run() before each execution and end_run() after to evict stale
    entries. Hit/miss/eviction counts are available via stats() after end_run().
    """

    def __init__(self):
        self.cache: dict[str, Any] = {}
        self.current_run: set[str] = set()
        self.names: dict[str, str] = {}  # variable_name → hash
        self.hits: int = 0
        self.misses: int = 0
        self.evictions: int = 0
        self.timings: dict[str, float] = {}  # op_hash → seconds

    def begin_run(self) -> None:
        """Reset tracking set and counters for a new execution run."""
        self.current_run = set()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def end_run(self) -> None:
        """Evict cache entries not touched during the current run."""
        stale = set(self.cache.keys()) - self.current_run
        for key in stale:
            del self.cache[key]
            self.timings.pop(key, None)
            self.evictions += 1

    def stats(self) -> dict:
        """Return hit/miss/eviction counts and total compute time for the last run."""
        total_compute_time = sum(
            self.timings.get(h, 0.0) for h in self.current_run
        )
        return {
            "hits": self.hits,
            "misses": self.misses,
            "evictions": self.evictions,
            "total_compute_time": total_compute_time,
        }
