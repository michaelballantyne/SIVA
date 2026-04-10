"""executor.py — Pipeline execution and inspection in a restricted namespace.

Provides:
    tracked_read(path, dag)          — load a PyVista file with mtime-based identity
    execute_pipeline(code, dag, ...) — run pipeline code with tracked entry points
    inspect_exec(code, dag)          — run a read-only inspection snippet against cached DAG state
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Callable

import pyvista as pv

from .core import DAG
from .dispatch import stable_hash, _should_wrap, _arg_hash, _unwrap
from .proxy import TrackedProxy

# Re-export for backward-compat with `from tracked_execution.inspect import ...`
__all__ = [
    "tracked_read",
    "execute_pipeline",
    "ExecutionResult",
    "inspect_exec",
    "InspectResult",
]


# ---------------------------------------------------------------------------
# Safe builtins — shared by execute_pipeline and inspect_exec
# ---------------------------------------------------------------------------

_SAFE_BUILTINS = {
    # Type constructors
    "int": int,
    "float": float,
    "str": str,
    "bool": bool,
    "list": list,
    "tuple": tuple,
    "dict": dict,
    "set": set,
    "frozenset": frozenset,
    "bytes": bytes,
    "bytearray": bytearray,
    "type": type,
    # Functional
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "len": len,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "pow": pow,
    "divmod": divmod,
    "all": all,
    "any": any,
    # I/O (print is overridden in namespace, but include here as fallback)
    "print": print,
    # Introspection
    "isinstance": isinstance,
    "issubclass": issubclass,
    "hasattr": hasattr,
    "getattr": getattr,
    "callable": callable,
    "repr": repr,
    "id": id,
    # Iteration
    "iter": iter,
    "next": next,
    # Exceptions
    "Exception": Exception,
    "ValueError": ValueError,
    "TypeError": TypeError,
    "KeyError": KeyError,
    "IndexError": IndexError,
    "AttributeError": AttributeError,
    "NameError": NameError,
    "ImportError": ImportError,
    "ModuleNotFoundError": ModuleNotFoundError,
    "StopIteration": StopIteration,
    "RuntimeError": RuntimeError,
    "NotImplementedError": NotImplementedError,
    "OSError": OSError,
    "IOError": IOError,
    "FileNotFoundError": FileNotFoundError,
    "OverflowError": OverflowError,
    "ZeroDivisionError": ZeroDivisionError,
    "AssertionError": AssertionError,
    "ArithmeticError": ArithmeticError,
    "LookupError": LookupError,
    # Constants
    "True": True,
    "False": False,
    "None": None,
    # NOT included: __import__, open, exec, eval, compile, globals, locals,
    # vars, dir, delattr, setattr (on external objects)
}


# ---------------------------------------------------------------------------
# tracked_read
# ---------------------------------------------------------------------------

def tracked_read(path: str | Path, dag: DAG) -> TrackedProxy:
    """Load a PyVista mesh from *path*, with content identity = filename + mtime.

    The identity hash is computed from the absolute path and the file's last
    modification time (os.path.getmtime).  If the same hash is already in
    dag.cache (i.e. the file hasn't changed since the last run), the cached
    mesh is returned without re-reading the file from disk.

    Args:
        path: Path to the VTK/PyVista-readable file.
        dag:  The active DAG instance for this execution run.

    Returns:
        A TrackedProxy wrapping the loaded PyVista mesh.
    """
    path = Path(path)
    abs_path = str(path.resolve())
    mtime = os.path.getmtime(abs_path)

    read_hash = stable_hash(("tracked_read", abs_path, mtime))

    if read_hash in dag.cache:
        dag.current_run.add(read_hash)
        dag.hits += 1
        return TrackedProxy(dag.cache[read_hash], read_hash, dag)

    dag.misses += 1
    mesh = pv.read(abs_path)
    dag.cache[read_hash] = mesh
    dag.current_run.add(read_hash)
    return TrackedProxy(mesh, read_hash, dag)


# ---------------------------------------------------------------------------
# Tracked numpy namespace
# ---------------------------------------------------------------------------

class _TrackedNumpyNamespace:
    """A thin namespace that makes common numpy functions tracked via dispatch.

    Usage inside pipeline scripts:
        arr = np.percentile(mesh["Temperature"], 95)
    """

    def __init__(self, dag: DAG):
        import numpy as _np
        self._np = _np
        self._dag = dag

    def _call(self, func_name: str, args: tuple, kwargs: dict):
        """Hash and execute a numpy function call."""
        op_hash = stable_hash((
            "np",
            func_name,
            tuple(_arg_hash(a) for a in args),
            tuple((k, _arg_hash(v)) for k, v in sorted(kwargs.items())),
        ))

        if op_hash in self._dag.cache:
            self._dag.current_run.add(op_hash)
            self._dag.hits += 1
            cached = self._dag.cache[op_hash]
            if _should_wrap(cached):
                return TrackedProxy(cached, op_hash, self._dag)
            return cached

        self._dag.misses += 1

        real_args = [_unwrap(a) for a in args]
        real_kwargs = {k: _unwrap(v) for k, v in kwargs.items()}

        func = getattr(self._np, func_name)
        result = func(*real_args, **real_kwargs)

        self._dag.cache[op_hash] = result
        self._dag.current_run.add(op_hash)

        if _should_wrap(result):
            return TrackedProxy(result, op_hash, self._dag)
        return result

    # ---- Tracked numpy functions with non-trivial argument shapes ----

    def percentile(self, a, q, **kwargs):
        return self._call("percentile", (a, q), kwargs)

    def histogram(self, a, bins=10, **kwargs):
        return self._call("histogram", (a, bins), kwargs)

    def where(self, condition, x=None, y=None, **kwargs):
        args = (condition,) if x is None and y is None else (condition, x, y)
        return self._call("where", args, kwargs)

    def linspace(self, start, stop, num=50, **kwargs):
        return self._call("linspace", (start, stop, num), kwargs)

    def clip(self, a, a_min, a_max, **kwargs):
        return self._call("clip", (a, a_min, a_max), kwargs)

    def concatenate(self, arrays, **kwargs):
        return self._call("concatenate", (arrays,), kwargs)

    # Allow attribute access for constants like np.pi, np.inf, np.nan,
    # and any numpy functions not explicitly listed above.
    def __getattr__(self, name: str):
        return getattr(self._np, name)


# Single-argument numpy functions: auto-generate tracked wrappers.
# These all take one required positional arg and optional kwargs.
_NUMPY_SINGLE_ARG = (
    "sqrt", "abs", "mean", "std", "min", "max", "sum",
    "log", "log10", "exp", "sort", "unique",
    "array", "zeros", "ones",
)


def _make_np_method(name: str):
    def method(self, a, **kwargs):
        return self._call(name, (a,), kwargs)
    method.__name__ = name
    return method


for _np_name in _NUMPY_SINGLE_ARG:
    setattr(_TrackedNumpyNamespace, _np_name, _make_np_method(_np_name))


# ---------------------------------------------------------------------------
# Namespace helpers
# ---------------------------------------------------------------------------

def _make_print_buffer() -> tuple[io.StringIO, Callable]:
    """Return a (buffer, print_fn) pair where print_fn writes to the buffer."""
    buf = io.StringIO()

    def _captured_print(*args, sep=" ", end="\n", **kwargs):
        buf.write(sep.join(str(a) for a in args) + end)

    return buf, _captured_print


def _base_namespace(dag: DAG, print_fn: Callable) -> dict:
    """Return the restricted namespace shared by execute_pipeline and inspect_exec.

    Includes: safe builtins, tracked numpy, and captured print.
    The caller adds further entries (read, show, named proxies, etc.).
    """
    return {
        "__builtins__": _SAFE_BUILTINS,
        "np": _TrackedNumpyNamespace(dag),
        "print": print_fn,
    }


# ---------------------------------------------------------------------------
# execute_pipeline
# ---------------------------------------------------------------------------

class ExecutionResult:
    """Result object returned by execute_pipeline."""

    def __init__(
        self,
        output: str,
        actors: list,
        stats: dict[str, int],
        names: list[str] | None = None,
    ):
        self.output = output    # captured print() output
        self.actors = actors    # list of (mesh_proxy, kwargs) recorded by show/add_mesh
        self.stats = stats      # hit/miss/eviction counts
        self.names = names or []  # list of variable names assigned to TrackedProxy values


def execute_pipeline(
    code_or_path: str | Path,
    dag: DAG,
    show_callback: Callable | None = None,
) -> ExecutionResult:
    """Execute a pipeline script in a tracked, restricted namespace.

    The namespace provides:
        read(path)          — tracked_read entry point
        np                  — tracked numpy namespace
        show(mesh, **kw)    — records desired actor; calls show_callback if provided
        add_mesh(mesh, **kw)— alias for show
        screenshot(path)    — calls show_callback("screenshot", path)
        print(...)          — captured to a string buffer (available in result.output)
        pv                  — pyvista module (read-only dataset creation)

    Args:
        code_or_path: Either a code string or a Path to a .py file.
        dag:          The active DAG for this execution.
        show_callback: Optional callable invoked on show/add_mesh/screenshot calls.
                       Signature: show_callback(event_type, *args, **kwargs)

    Returns:
        ExecutionResult with captured output, actor list, and cache stats.
    """
    # Load code from file if needed
    if isinstance(code_or_path, Path) or (
        isinstance(code_or_path, str) and "\n" not in code_or_path and code_or_path.endswith(".py")
    ):
        code = Path(code_or_path).read_text()
    else:
        code = str(code_or_path)

    dag.begin_run()

    buf, print_fn = _make_print_buffer()
    actors: list[tuple[Any, dict]] = []

    def _tracked_show(mesh, **kwargs):
        actors.append((mesh, kwargs))
        if show_callback is not None:
            show_callback("show", mesh, **kwargs)

    def _tracked_screenshot(path_or_none=None, **kwargs):
        if show_callback is not None:
            show_callback("screenshot", path_or_none, **kwargs)

    namespace = _base_namespace(dag, print_fn)
    namespace.update({
        "read": lambda path: tracked_read(path, dag),
        "show": _tracked_show,
        "add_mesh": _tracked_show,
        "screenshot": _tracked_screenshot,
        "pv": pv,
    })

    exec(compile(code, "<pipeline>", "exec"), namespace)

    # Capture named proxy variables for inspect_exec
    dag.names = {
        var: object.__getattribute__(val, "_hash")
        for var, val in namespace.items()
        if not var.startswith("__") and isinstance(val, TrackedProxy)
    }

    dag.end_run()

    return ExecutionResult(
        output=buf.getvalue(),
        actors=actors,
        stats=dag.stats(),
        names=list(dag.names.keys()),
    )


# ---------------------------------------------------------------------------
# inspect_exec
# ---------------------------------------------------------------------------

class InspectResult:
    """Result object returned by inspect_exec."""

    def __init__(self, output: str):
        self.output = output  # captured print() output


def inspect_exec(code: str, dag: DAG) -> InspectResult:
    """Run a read-only inspection snippet against the cached DAG state.

    The snippet has access to:
    - All named TrackedProxy variables from the last pipeline execution
      (variable names captured in dag.names after execute_pipeline).
    - ``np``: the tracked numpy namespace.
    - ``print()``: captured to a string buffer; result is returned.

    The snippet may NOT call show/add_mesh/screenshot, read new files, import
    arbitrary modules, or mutate the DAG cache directly.

    Method calls on proxies go through dispatch() and are cached in dag.cache.
    inspect_exec() does NOT call begin_run() or end_run(); it works against the
    live post-pipeline state.

    Args:
        code: Python snippet to execute.
        dag:  The DAG populated by the most recent execute_pipeline() call.

    Returns:
        InspectResult with the captured print output.
    """
    buf, print_fn = _make_print_buffer()
    namespace = _base_namespace(dag, print_fn)

    # Populate named proxies from the last pipeline run
    for var_name, content_hash in dag.names.items():
        if content_hash in dag.cache:
            namespace[var_name] = TrackedProxy(dag.cache[content_hash], content_hash, dag)
        # If evicted (shouldn't happen right after pipeline), omit — snippet
        # gets NameError, which is the correct failure mode.

    exec(compile(code, "<inspect>", "exec"), namespace)

    return InspectResult(output=buf.getvalue())
