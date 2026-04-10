"""inspect.py — re-exports from executor.py for backward compatibility.

inspect_exec and InspectResult have moved to executor.py where they live
alongside the namespace setup they share with execute_pipeline.
"""

from .executor import InspectResult, inspect_exec  # noqa: F401

__all__ = ["InspectResult", "inspect_exec"]
