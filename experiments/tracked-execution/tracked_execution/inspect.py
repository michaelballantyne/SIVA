"""inspect.py — One-off inspection of a pipeline's cached DAG state.

Provides inspect_exec(), which lets an agent run ad-hoc Python snippets against
the named proxies populated by the last execute_pipeline() call, without
modifying the pipeline or triggering any rendering.

Example:
    result = inspect_exec('''
    arr = fire["Temperature"]
    print(f"points: {fire.n_points}")
    print(f"temp range: {arr.min():.1f} - {arr.max():.1f}")
    print(f"mean: {arr.mean():.1f}")
    ''', dag)
    # result.output == "points: 50000\\ntemp range: 23.4 - 891.2\\nmean: 445.3\\n"
"""

from __future__ import annotations

import io

from .core import DAG
from .proxy import TrackedProxy


class InspectResult:
    """Result object returned by inspect_exec."""

    def __init__(self, output: str):
        self.output = output  # captured print() output


def inspect_exec(code: str, dag: DAG) -> InspectResult:
    """Run a read-only inspection snippet against the cached DAG state.

    The snippet has access to:
    - All named TrackedProxy variables from the last pipeline execution
      (variable names captured in dag.names after execute_pipeline).
    - ``np``: the real numpy module (read-only use, not tracked through DAG).
    - ``print()``: captured to a string buffer; result is returned.

    The snippet may NOT:
    - Call show/add_mesh/screenshot (not in namespace).
    - Read new files (no ``read`` in namespace).
    - Import arbitrary modules (__import__ not available).
    - Mutate the pipeline or the DAG cache directly.

    Method calls on the proxies go through dispatch() and ARE cached in dag.cache
    — they are added to dag.current_run if they match cached hashes. This means
    inspect_exec() adds entries to current_run but does NOT call begin_run() or
    end_run(); it works against the live post-pipeline state.

    Args:
        code: Python snippet to execute.
        dag:  The DAG populated by the most recent execute_pipeline() call.

    Returns:
        InspectResult with the captured print output.
    """
    buf = io.StringIO()

    def _captured_print(*args, sep=" ", end="\n", **kwargs):
        buf.write(sep.join(str(a) for a in args) + end)

    # Build namespace: start with safe builtins, add captured proxies by name
    from .executor import _SAFE_BUILTINS, _TrackedNumpyNamespace

    namespace: dict = {
        "__builtins__": _SAFE_BUILTINS,
        "np": _TrackedNumpyNamespace(dag),
        "print": _captured_print,
    }

    # Populate named proxies from the last pipeline run
    for var_name, content_hash in dag.names.items():
        if content_hash in dag.cache:
            real_obj = dag.cache[content_hash]
            namespace[var_name] = TrackedProxy(real_obj, content_hash, dag)
        # If hash was evicted (shouldn't happen if called right after pipeline),
        # we simply omit the variable. The snippet will get a NameError if it
        # references it, which is the correct failure mode.

    exec(compile(code, "<inspect>", "exec"), namespace)

    return InspectResult(output=buf.getvalue())
