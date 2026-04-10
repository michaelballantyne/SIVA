"""executor.py — generic namespace execution for tracked pipelines.

Provides execute_in_namespace(): run a code string in a restricted, tracked
namespace. Domain-specific code (e.g. tracked_execution.executor) builds on
top of this by providing the namespace contents.
"""

from __future__ import annotations

from typing import Any

from .dag import DAG


def execute_in_namespace(code: str, namespace: dict, dag: DAG) -> None:
    """Execute *code* in *namespace* within a begin_run()/end_run() lifecycle.

    Calls dag.begin_run() before execution and dag.end_run() after, even if
    the code raises. This keeps the DAG in a consistent state across calls.

    The caller is responsible for populating *namespace* with all builtins,
    tracked helpers (np, read, show, etc.), and any domain-specific names
    before calling this function.

    Args:
        code:      Python source code string to execute.
        namespace: The execution namespace dict. Should include ``__builtins__``.
        dag:       The active DAG — begin_run() and end_run() are called here.

    Raises:
        Any exception raised by the executed code (after calling dag.end_run()).
    """
    dag.begin_run()
    try:
        exec(compile(code, "<pipeline>", "exec"), namespace)
    except Exception:
        dag.end_run()
        raise
    dag.end_run()
