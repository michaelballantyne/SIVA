"""vtk_escape — escape hatch for raw VTK operations within a tracked pipeline.

Provides:
    vtk_escape(input_proxy, func, *, key=None)
        Run a raw VTK/Python function on a tracked proxy's data, with caching.
    vtk_escape_multi(input_proxies, func, *, key=None)
        Like vtk_escape but accepts multiple input proxies.

The purity contract
-------------------
Functions passed to vtk_escape MUST be pure: given the same input mesh they
must always produce the same output mesh. If a function reads from files,
uses random numbers, or depends on global state, the cache will serve stale
results. This contract cannot be enforced automatically — it is a requirement
on the caller.

Hashing strategy
----------------
The function is hashed in order of preference:
1. If an explicit ``key`` string is provided, it is used directly.
2. Otherwise ``inspect.getsource(func)`` is attempted for source-based hashing.
3. If source is unavailable (e.g. lambdas defined in an interactive session),
   fall back to the function's bytecode (``co_code`` + ``co_consts``) combined
   with ``__qualname__``.

Use explicit ``key`` values for lambdas, closures, or dynamically generated
functions whose bytecode may not be stable across interpreter restarts.
"""

from __future__ import annotations

import inspect
from typing import Any, Sequence

import pyvista as pv

from .core import DAG
from .dispatch import stable_hash
from .proxy import TrackedProxy


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _hash_function(func, key: str | None) -> str:
    """Compute a stable hash for *func*.

    Uses ``key`` directly if given, otherwise tries source, then bytecode.
    """
    if key is not None:
        return stable_hash(("vtk_escape_key", key))

    try:
        source = inspect.getsource(func)
        return stable_hash(("vtk_escape_source", source))
    except (OSError, TypeError):
        pass

    # Bytecode fallback — less stable across Python versions but better than nothing
    code = func.__code__
    return stable_hash((
        "vtk_escape_bytecode",
        code.co_code,
        code.co_consts,
        func.__qualname__,
    ))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def vtk_escape(input_proxy: TrackedProxy, func, *, key: str | None = None) -> TrackedProxy:
    """Run a raw VTK/Python function on a tracked proxy's data.

    The function must be pure: same input mesh → same output mesh.

    This is an escape hatch for VTK filters that PyVista does not wrap or does
    not expose conveniently. Within a tracked pipeline, arbitrary code normally
    breaks caching because the system cannot hash it. ``vtk_escape`` solves
    this by hashing the function itself (via source inspection or bytecode) and
    combining that hash with the input hash to form a cache key.

    Args:
        input_proxy: A TrackedProxy wrapping a PyVista mesh.
        func: A callable(mesh) -> mesh. Receives the unwrapped PyVista mesh and
              must return a PyVista mesh or a VTK object that ``pv.wrap()``
              can handle.
        key: Optional explicit cache key string. Use this when ``func`` is a
             lambda, closure, or dynamically generated function whose source or
             bytecode may not be stable across runs. Must be changed whenever
             the function's behaviour changes.

    Returns:
        A new TrackedProxy wrapping the function's output.

    Raises:
        TypeError: If ``input_proxy`` is not a TrackedProxy.

    Example::

        import vtk

        def smooth_filter(m):
            f = vtk.vtkWindowedSincPolyDataFilter()
            f.SetInputData(m)
            f.SetNumberOfIterations(20)
            f.Update()
            return pv.wrap(f.GetOutput())

        smoothed = vtk_escape(surface_proxy, smooth_filter)
        show(smoothed, colormap="viridis")
    """
    if not isinstance(input_proxy, TrackedProxy):
        raise TypeError(
            f"input_proxy must be a TrackedProxy, got {type(input_proxy).__name__}"
        )

    dag: DAG = object.__getattribute__(input_proxy, "_dag")
    input_hash: str = object.__getattribute__(input_proxy, "_hash")

    func_hash = _hash_function(func, key)
    op_hash = stable_hash(("vtk_escape", input_hash, func_hash))

    if op_hash in dag.cache:
        dag.current_run.add(op_hash)
        dag.hits += 1
        return TrackedProxy(dag.cache[op_hash], op_hash, dag)

    # Cache miss — execute the function
    dag.misses += 1
    real_input = object.__getattribute__(input_proxy, "_real")
    result = func(real_input)

    # Wrap VTK output if needed
    if not isinstance(result, pv.DataSet):
        result = pv.wrap(result)

    dag.cache[op_hash] = result
    dag.current_run.add(op_hash)
    return TrackedProxy(result, op_hash, dag)


def vtk_escape_multi(
    input_proxies: Sequence[TrackedProxy],
    func,
    *,
    key: str | None = None,
) -> TrackedProxy:
    """Like vtk_escape but accepts multiple input proxies.

    The function must be pure: same set of input meshes → same output mesh.

    Args:
        input_proxies: A sequence of TrackedProxy instances.
        func: A callable(mesh1, mesh2, ...) -> mesh. Receives unwrapped PyVista
              meshes in the same order as ``input_proxies``.
        key: Optional explicit cache key string (see vtk_escape for guidance).

    Returns:
        A new TrackedProxy wrapping the function's output.

    Raises:
        TypeError: If any element of ``input_proxies`` is not a TrackedProxy,
                   or if ``input_proxies`` is empty.

    Example::

        def merge_meshes(a, b):
            import pyvista as pv
            return a.merge(b)

        merged = vtk_escape_multi([proxy_a, proxy_b], merge_meshes)
    """
    if not input_proxies:
        raise TypeError("input_proxies must be a non-empty sequence")

    for i, p in enumerate(input_proxies):
        if not isinstance(p, TrackedProxy):
            raise TypeError(
                f"input_proxies[{i}] must be a TrackedProxy, "
                f"got {type(p).__name__}"
            )

    # All proxies must share the same DAG
    dag: DAG = object.__getattribute__(input_proxies[0], "_dag")

    input_hashes = tuple(
        object.__getattribute__(p, "_hash") for p in input_proxies
    )
    func_hash = _hash_function(func, key)
    op_hash = stable_hash(("vtk_escape_multi", input_hashes, func_hash))

    if op_hash in dag.cache:
        dag.current_run.add(op_hash)
        dag.hits += 1
        return TrackedProxy(dag.cache[op_hash], op_hash, dag)

    # Cache miss — execute
    dag.misses += 1
    real_inputs = [object.__getattribute__(p, "_real") for p in input_proxies]
    result = func(*real_inputs)

    if not isinstance(result, pv.DataSet):
        result = pv.wrap(result)

    dag.cache[op_hash] = result
    dag.current_run.add(op_hash)
    return TrackedProxy(result, op_hash, dag)
