"""Pipeline execution in a restricted, tracked namespace.

Provides execute_pipeline (run a pipeline script with content-addressed caching),
inspect_exec (ad-hoc queries against cached DAG state), and tracked_read
(file loader whose cache key is path + mtime).
"""

from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pyvista as pv

from .dispatch import DAG, stable_hash, _arg_hash, _unwrap, _dag_call
from .proxy import TrackedProxy
from .vtk_escape import vtk_escape as _vtk_escape, vtk_escape_multi as _vtk_escape_multi

__all__ = [
    "execute_pipeline",
    "ExecutionResult",
    "inspect_pipeline",
    "InspectResult",
    # Backward-compatible alias
    "inspect_exec",
    # Lower-level helper (not part of primary API)
    "tracked_read",
]


# ---------------------------------------------------------------------------
# Safe builtins — shared by execute_pipeline and inspect_exec
# ---------------------------------------------------------------------------

def _blocked_open(*args, **kwargs):
    raise PermissionError(
        "open() is not available in pipeline code. "
        "Use read(path) to load data files. "
        "For other file operations, use vtk_escape() with an explicit function."
    )


def _blocked_import(name, *args, **kwargs):
    raise ImportError(
        f"import {name!r} is not allowed in pipeline code. "
        "The pipeline runs in a restricted namespace for reproducibility and safety. "
        "NumPy is available as 'np'. "
        "If you need a specific module's functionality, use vtk_escape() "
        "with an explicit function that imports inside the function body."
    )


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
    "PermissionError": PermissionError,
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
    # Blocked builtins — raise informative errors instead of NameError
    "open": _blocked_open,
    "__import__": _blocked_import,
    # NOT included: exec, eval, compile, globals, locals,
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
    return _dag_call(dag, read_hash, lambda: pv.read(abs_path))


# ---------------------------------------------------------------------------
# Tracked numpy namespace
# ---------------------------------------------------------------------------

class _TrackedNumpyNamespace:
    """Numpy namespace whose function calls are content-hashed and cached via the DAG.

    Exposed as ``np`` in pipeline scripts.  Explicit wrappers cover common
    functions with non-trivial argument shapes; everything else falls through to
    the real numpy module via ``__getattr__`` (constants, less-common functions).
    """

    def __init__(self, dag: DAG):
        self._np = np
        self._dag = dag

    def _call(self, func_name: str, args: tuple, kwargs: dict):
        """Hash and execute a numpy function call."""
        op_hash = stable_hash((
            "np",
            func_name,
            tuple(_arg_hash(a) for a in args),
            tuple((k, _arg_hash(v)) for k, v in sorted(kwargs.items())),
        ))

        def _execute():
            real_args = [_unwrap(a) for a in args]
            real_kwargs = {k: _unwrap(v) for k, v in kwargs.items()}
            return getattr(self._np, func_name)(*real_args, **real_kwargs)

        return _dag_call(self._dag, op_hash, _execute)

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
    """Return the restricted execution namespace (builtins + tracked numpy + print).

    Callers extend this with context-specific entries (read, show, named proxies).
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
    """Result returned by execute_pipeline.

    Attributes:
        output: Captured print() output from the pipeline script.
        actors: List of (mesh_proxy, kwargs) tuples recorded by show/add_mesh calls.
        stats:  Cache hit/miss/eviction counts from this run.
        names:  Variable names in the pipeline that resolved to TrackedProxy values.
        ok:     True — execute_pipeline only returns (not raises) on success.
    """

    ok = True  # class-level sentinel; always True on a returned result

    def __init__(
        self,
        output: str,
        actors: list,
        stats: dict[str, int],
        names: list[str] | None = None,
    ):
        self.output = output
        self.actors = actors
        self.stats = stats
        self.names = names or []

    def __repr__(self) -> str:
        s = self.stats
        output_preview = self.output[:60].replace("\n", "\\n") if self.output else ""
        if output_preview and len(self.output) > 60:
            output_preview += "…"
        parts = [
            f"hits={s.get('hits', 0)}",
            f"misses={s.get('misses', 0)}",
            f"evictions={s.get('evictions', 0)}",
            f"actors={len(self.actors)}",
            f"names={self.names}",
        ]
        if output_preview:
            parts.append(f"output={output_preview!r}")
        return f"ExecutionResult({', '.join(parts)})"


def execute_pipeline(
    code_or_path: str | Path,
    dag: DAG,
    show_callback: Callable | None = None,
) -> ExecutionResult:
    """Execute a pipeline script in a tracked, restricted namespace.

    The script sees these names:

    - ``read(path)`` — load a file, cached by path + mtime
    - ``show(mesh, **kw)`` / ``add_mesh(mesh, **kw)`` — record an actor
    - ``screenshot(path)`` — capture a screenshot (requires show_callback or Session)
    - ``np`` — tracked numpy namespace (caches numpy computations)
    - ``pv`` — the pyvista module (for ``pv.ImageData()`` etc. inside vtk_escape)
    - ``vtk_escape`` / ``vtk_escape_multi`` — raw VTK escape hatch with caching
    - ``print(...)`` — captured; available in ``result.output``

    Raises on pipeline errors (SyntaxError, NameError, etc.) after calling
    ``dag.end_run()`` to keep the cache in a consistent state.

    Args:
        code_or_path:  A pipeline code string, or a path to a ``.py`` file.
        dag:           The DAG providing the content-addressed cache.
        show_callback: Optional callable invoked for rendering events.
                       Signature: ``callback(event_type, *args, **kwargs)``.
                       ``event_type`` is ``"show"``, ``"add_mesh"``, or
                       ``"screenshot"``.  Used internally by ``Session``; most
                       callers don't need this.

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
        "vtk_escape": _vtk_escape,
        "vtk_escape_multi": _vtk_escape_multi,
    })

    try:
        exec(compile(code, "<pipeline>", "exec"), namespace)
    except Exception:
        # end_run() must always be called to keep the DAG in a consistent state.
        # On error, we still evict stale entries and record counters so that the
        # next execute_pipeline call starts from a clean slate.
        dag.end_run()
        raise

    # Capture named proxy variables for inspect_pipeline
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
    """Result returned by inspect_pipeline (also inspect_exec).

    Attributes:
        output: Captured print() output from the inspection snippet.
    """

    def __init__(self, output: str):
        self.output = output

    def __repr__(self) -> str:
        preview = self.output[:80].replace("\n", "\\n") if self.output else ""
        if preview and len(self.output) > 80:
            preview += "…"
        return f"InspectResult(output={preview!r})"


def _blocked_show(*args, **kwargs):
    raise NameError(
        "show() is not available in inspect_pipeline snippets. "
        "Inspection code is read-only — it cannot add actors to the scene. "
        "Call show() inside the main pipeline script instead."
    )


def _blocked_read(*args, **kwargs):
    raise NameError(
        "read() is not available in inspect_pipeline snippets. "
        "Data files are loaded in the main pipeline script. "
        "Named mesh variables from the last pipeline run are already available here."
    )


def _blocked_screenshot(*args, **kwargs):
    raise NameError(
        "screenshot() is not available in inspect_pipeline snippets. "
        "Screenshots are taken from the main pipeline script."
    )


def inspect_pipeline(code: str, dag: DAG) -> InspectResult:
    """Run a read-only inspection snippet against the cached DAG state.

    The snippet sees all named TrackedProxy variables from the last
    execute_pipeline() call, plus ``np`` (tracked numpy) and ``print()``
    (captured).  No show/add_mesh/screenshot/read access is provided.
    Does not call begin_run() or end_run() — works against the live
    post-pipeline cache.

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

    # Add blocked stubs with descriptive errors for operations not available here
    namespace.update({
        "show": _blocked_show,
        "add_mesh": _blocked_show,
        "screenshot": _blocked_screenshot,
        "read": _blocked_read,
    })

    # Provide a custom __missing__ on the namespace to improve NameError messages
    available_names = sorted(
        k for k in namespace if not k.startswith("__")
    )
    namespace["_inspect_available_names"] = available_names

    try:
        exec(compile(code, "<inspect>", "exec"), namespace)
    except NameError as exc:
        # Re-raise with context about what names ARE available
        missing = str(exc).replace("name ", "").replace(" is not defined", "").strip("'\"")
        pipeline_vars = sorted(dag.names.keys())
        hint = (
            f"Pipeline variables available: {pipeline_vars}. "
            f"Built-ins and 'np' are also available."
        ) if pipeline_vars else (
            "No pipeline variables are available (run execute_pipeline first). "
            "Built-ins and 'np' are available."
        )
        raise NameError(f"{exc}. {hint}") from None

    return InspectResult(output=buf.getvalue())


# Backward-compatible alias — inspect_exec was the original name.
inspect_exec = inspect_pipeline
