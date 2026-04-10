"""Generic namespace execution: run a code string within a DAG lifecycle."""

from __future__ import annotations

from typing import Any

from .dag import DAG


def execute_in_namespace(code: str, namespace: dict, dag: DAG) -> None:
    """Execute code in namespace, bracketed by dag.begin_run()/end_run().

    The caller must populate namespace with builtins and tracked helpers before
    calling this. dag.end_run() is always called, even if the code raises.
    """
    dag.begin_run()
    try:
        exec(compile(code, "<pipeline>", "exec"), namespace)
    except Exception:
        dag.end_run()
        raise
    dag.end_run()
