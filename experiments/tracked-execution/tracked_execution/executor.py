"""Execute pipeline scripts in a restricted, tracked namespace with content-addressed caching."""

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
from tracked_core.executor import execute_in_namespace as _execute_in_namespace

__all__ = [
    "execute_pipeline",
    "ExecutionResult",
    "inspect_pipeline",
    "InspectResult",
    # Lower-level helper (not part of primary API)
    "tracked_read",
]


# ---------------------------------------------------------------------------
# Safe builtins — shared by execute_pipeline and inspect_pipeline
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
    # I/O (print is overridden in namespace, but included here as fallback)
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
    """Load a PyVista mesh, cached by absolute path + mtime."""
    path = Path(path)
    abs_path = str(path.resolve())
    mtime = os.path.getmtime(abs_path)

    read_hash = stable_hash(("tracked_read", abs_path, mtime))
    return _dag_call(dag, read_hash, lambda: pv.read(abs_path))


# ---------------------------------------------------------------------------
# Tracked numpy namespace
# ---------------------------------------------------------------------------

class _TrackedNumpyNamespace:
    """NumPy namespace exposed as ``np`` in pipelines; all calls are hashed and cached.

    Explicit wrappers handle multi-arg functions; __getattr__ handles the rest.
    """

    def __init__(self, dag: DAG):
        self._dag = dag

    def _call(self, func_name: str, args: tuple, kwargs: dict):
        op_hash = stable_hash((
            "np",
            func_name,
            tuple(_arg_hash(a) for a in args),
            tuple((k, _arg_hash(v)) for k, v in sorted(kwargs.items())),
        ))

        def _execute():
            real_args = [_unwrap(a) for a in args]
            real_kwargs = {k: _unwrap(v) for k, v in kwargs.items()}
            return getattr(np, func_name)(*real_args, **real_kwargs)

        return _dag_call(self._dag, op_hash, _execute)

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

    def __getattr__(self, name: str):
        """Handle single-arg functions (sqrt, log, …) and constants (pi, e, inf, nan)."""
        attr = getattr(np, name, None)
        if attr is None:
            raise AttributeError(f"numpy has no attribute '{name}'")
        if callable(attr):
            def wrapper(*args, **kw):
                return self._call(name, args, kw)
            return wrapper
        return attr


def _make_print_buffer() -> tuple[io.StringIO, Callable]:
    """Return a (buffer, print_fn) pair where print_fn writes to the buffer."""
    buf = io.StringIO()

    def _captured_print(*args, sep=" ", end="\n", **kwargs):
        buf.write(sep.join(str(a) for a in args) + end)

    return buf, _captured_print


def _base_namespace(dag: DAG, print_fn: Callable) -> dict:
    """Return the restricted execution namespace (builtins + tracked numpy + print)."""
    return {
        "__builtins__": _SAFE_BUILTINS,
        "np": _TrackedNumpyNamespace(dag),
        "print": print_fn,
    }


class ExecutionResult:
    """Result of execute_pipeline: captured output, actor list, cache stats, and variable names."""

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
    read_fn: Callable | None = None,
) -> ExecutionResult:
    """Execute a pipeline script in a tracked, restricted namespace.

    The script has access to: read, show, screenshot, np, pv, vtk_escape,
    vtk_escape_multi, and print (captured). Raises on errors after dag.end_run().

    Args:
        code_or_path:  Pipeline code string or path to a .py file.
        dag:           The content-addressed cache.
        show_callback: Optional callback for show/screenshot events.
        read_fn:       Optional replacement for read(path). Defaults to tracked_read.
    """
    # Load code from file if needed
    if isinstance(code_or_path, Path) or (
        isinstance(code_or_path, str) and os.path.exists(code_or_path)
    ):
        code = Path(code_or_path).read_text()
    else:
        code = str(code_or_path)

    buf, print_fn = _make_print_buffer()
    actors: list[tuple[Any, dict]] = []

    def _tracked_show(mesh, **kwargs):
        actors.append((mesh, kwargs))
        if show_callback is not None:
            show_callback("show", mesh, **kwargs)

    def _tracked_screenshot(path_or_none=None, **kwargs):
        if show_callback is not None:
            show_callback("screenshot", path_or_none, **kwargs)

    _read = read_fn if read_fn is not None else tracked_read
    namespace = _base_namespace(dag, print_fn)
    namespace.update({
        "read": lambda path: _read(path, dag),
        "show": _tracked_show,
        "screenshot": _tracked_screenshot,
        "pv": pv,
        "vtk_escape": _vtk_escape,
        "vtk_escape_multi": _vtk_escape_multi,
    })

    _execute_in_namespace(code, namespace, dag)

    # Capture named proxy variables for inspect_pipeline.
    # Names are captured after end_run() — entries still in the cache are guaranteed to survive.
    dag.names = {
        var: object.__getattribute__(val, "_hash")
        for var, val in namespace.items()
        if not var.startswith("__") and isinstance(val, TrackedProxy)
    }

    return ExecutionResult(
        output=buf.getvalue(),
        actors=actors,
        stats=dag.stats(),
        names=list(dag.names.keys()),
    )


class InspectResult:
    """Result of inspect_pipeline: captured print() output from the snippet."""

    def __init__(self, output: str):
        self.output = output

    def __repr__(self) -> str:
        preview = self.output[:80].replace("\n", "\\n") if self.output else ""
        if preview and len(self.output) > 80:
            preview += "…"
        return f"InspectResult(output={preview!r})"


def _make_blocked_stub(name: str, explanation: str):
    """Return a callable that raises PermissionError with a descriptive message."""
    def stub(*args, **kwargs):
        raise PermissionError(f"{name}() is not available here. {explanation}")
    stub.__name__ = name
    return stub


_blocked_show = _make_blocked_stub(
    "show",
    "inspect is read-only — use show() in the pipeline file.",
)
_blocked_read = _make_blocked_stub(
    "read",
    "inspect is read-only — data is already loaded.",
)
_blocked_screenshot = _make_blocked_stub(
    "screenshot",
    "inspect is read-only — use the screenshot tool.",
)


def inspect_pipeline(code: str, dag: DAG) -> InspectResult:
    """Run a read-only snippet against the cached DAG state.

    The snippet sees all named proxy variables from the last execute_pipeline()
    call, plus np and print(). Does not call begin_run()/end_run().
    """
    buf, print_fn = _make_print_buffer()
    namespace = _base_namespace(dag, print_fn)

    for var_name, content_hash in dag.names.items():
        if content_hash in dag.cache:
            namespace[var_name] = TrackedProxy(dag.cache[content_hash], content_hash, dag)
        # If evicted (shouldn't happen right after pipeline), omit so snippet gets NameError.

    namespace.update({
        "show": _blocked_show,
        "screenshot": _blocked_screenshot,
        "read": _blocked_read,
    })

    available_names = sorted(k for k in namespace if not k.startswith("__"))
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
