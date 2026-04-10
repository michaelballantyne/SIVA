"""executor.py — execute pandas pipelines with tracked caching."""

from __future__ import annotations

import io
import os

import pandas as pd

from tracked_core.dag import DAG
from tracked_core.dispatch import stable_hash, _dag_call
from tracked_core.proxy import TrackedProxy

from .dispatch import dispatch


def tracked_read_csv(path: str, dag: DAG) -> TrackedProxy:
    """Read a CSV file into a tracked DataFrame.

    The result is cached by (absolute path, mtime). If the file hasn't
    changed since the last run, the cached DataFrame is returned without
    re-reading from disk.

    Args:
        path: Path to the CSV file.
        dag:  The active DAG for caching.

    Returns:
        A TrackedProxy wrapping a pandas DataFrame.
    """
    abs_path = os.path.abspath(path)
    mtime = os.path.getmtime(abs_path)
    op_hash = stable_hash(("read_csv", abs_path, mtime))

    def _load():
        return pd.read_csv(abs_path)

    return _dag_call(dag, op_hash, _load, dispatch)


def execute_data_pipeline(code: str, dag: DAG):
    """Execute a pandas pipeline in a restricted namespace with caching.

    Runs the given Python code string in a sandboxed environment where
    ``read_csv`` is the tracked version and ``pd`` is pandas. All
    intermediate DataFrames created by the pipeline are cached in the DAG.

    After execution, stale cache entries (not touched this run) are evicted.

    Args:
        code: Python source code for the pipeline.
        dag:  The DAG to use for caching.

    Returns:
        A result object with:
          - ``output``: captured stdout from the pipeline (str)
          - ``stats``:  hit/miss/eviction counts (dict)
          - ``names``:  list of top-level variable names bound to DataFrames
    """
    dag.begin_run()

    buf = io.StringIO()

    def _print(*args, sep=" ", end="\n", **kw):
        buf.write(sep.join(str(a) for a in args) + end)

    namespace = {
        "__builtins__": {
            "None": None, "True": True, "False": False,
            "range": range, "len": len, "int": int, "float": float,
            "str": str, "bool": bool, "list": list, "print": _print,
        },
        "read_csv": lambda path: tracked_read_csv(path, dag),
        "pd": pd,
    }

    exec(compile(code, "<pipeline>", "exec"), namespace)

    dag.names = {
        k: v._hash
        for k, v in namespace.items()
        if isinstance(v, TrackedProxy) and not k.startswith("_")
    }
    dag.end_run()

    output = buf.getvalue()
    stats = dag.stats()
    names = list(dag.names.keys())

    return type("Result", (), {"output": output, "stats": stats, "names": names})()


def inspect_data(code: str, dag: DAG):
    """Run read-only inspection code against the currently cached DataFrames.

    Makes each named DataFrame from the previous pipeline run available in
    the inspection namespace as a TrackedProxy. The code can read, filter,
    and print, but cannot mutate the cached state.

    Args:
        code: Python source code for the inspection.
        dag:  The DAG holding cached results.

    Returns:
        A result object with ``output``: captured stdout (str).
    """
    buf = io.StringIO()

    def _print(*args, sep=" ", end="\n", **kw):
        buf.write(sep.join(str(a) for a in args) + end)

    namespace = {
        "__builtins__": {
            "None": None, "True": True, "False": False,
            "range": range, "len": len, "print": _print,
        },
        "pd": pd,
    }

    for name, h in dag.names.items():
        if h in dag.cache:
            namespace[name] = TrackedProxy(dag.cache[h], h, dag, dispatch_fn=dispatch)

    exec(compile(code, "<inspect>", "exec"), namespace)

    return type("InspectResult", (), {"output": buf.getvalue()})()
