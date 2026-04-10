"""Escape hatch for raw VTK operations within a tracked pipeline.

Functions passed to vtk_escape MUST be pure: same input → same output.
The function is hashed via source inspection, then bytecode fallback, or an
explicit key string for lambdas/closures with unstable bytecode.
"""

from __future__ import annotations

import inspect
from typing import Any, Sequence

import pyvista as pv

from .dispatch import DAG, stable_hash, _dag_call
from .proxy import TrackedProxy


def _hash_function(func, key: str | None) -> str:
    """Hash func: use key if provided, else source, else bytecode."""
    if key is not None:
        return stable_hash(("vtk_escape_key", key))

    try:
        source = inspect.getsource(func)
        return stable_hash(("vtk_escape_source", source))
    except (OSError, TypeError):
        pass

    # Bytecode fallback — less stable across Python versions
    code = func.__code__
    return stable_hash((
        "vtk_escape_bytecode",
        code.co_code,
        code.co_consts,
        func.__qualname__,
    ))


def vtk_escape(input_proxy: TrackedProxy, func, *, key: str | None = None) -> TrackedProxy:
    """Run a pure function on a tracked proxy's data, with caching.

    func must be pure: same input mesh → same output mesh. Hashing uses source
    inspection or bytecode; pass key for lambdas with unstable bytecode.

    Raises:
        TypeError: If input_proxy is not a TrackedProxy.
    """
    if not isinstance(input_proxy, TrackedProxy):
        raise TypeError(
            f"input_proxy must be a TrackedProxy, got {type(input_proxy).__name__}"
        )

    dag: DAG = object.__getattribute__(input_proxy, "_dag")
    input_hash: str = object.__getattribute__(input_proxy, "_hash")
    func_hash = _hash_function(func, key)
    op_hash = stable_hash(("vtk_escape", input_hash, func_hash))

    def _execute():
        real_input = object.__getattribute__(input_proxy, "_real")
        result = func(real_input)
        return result if isinstance(result, pv.DataSet) else pv.wrap(result)

    return _dag_call(dag, op_hash, _execute)


def vtk_escape_multi(
    input_proxies: Sequence[TrackedProxy],
    func,
    *,
    key: str | None = None,
) -> TrackedProxy:
    """Like vtk_escape but accepts multiple input proxies.

    func receives unwrapped meshes in the same order as input_proxies.

    Raises:
        TypeError: If any element is not a TrackedProxy, or the sequence is empty.
    """
    if not input_proxies:
        raise TypeError("input_proxies must be a non-empty sequence")

    for i, p in enumerate(input_proxies):
        if not isinstance(p, TrackedProxy):
            raise TypeError(
                f"input_proxies[{i}] must be a TrackedProxy, "
                f"got {type(p).__name__}"
            )

    dag: DAG = object.__getattribute__(input_proxies[0], "_dag")

    input_hashes = tuple(
        object.__getattribute__(p, "_hash") for p in input_proxies
    )
    func_hash = _hash_function(func, key)
    op_hash = stable_hash(("vtk_escape_multi", input_hashes, func_hash))

    def _execute():
        real_inputs = [object.__getattribute__(p, "_real") for p in input_proxies]
        result = func(*real_inputs)
        return result if isinstance(result, pv.DataSet) else pv.wrap(result)

    return _dag_call(dag, op_hash, _execute)
