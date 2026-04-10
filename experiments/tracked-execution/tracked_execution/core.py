"""core.py — re-exports DAG for backward compatibility.

DAG now lives in dispatch.py alongside the dispatch logic that uses it.
Existing imports of ``from .core import DAG`` continue to work.
"""

from .dispatch import DAG

__all__ = ["DAG"]
