"""Materialize a dataset under a Narrowing.

Two entry points share one read core:
  - `load(info)` — the legacy/declarative path. Builds a Narrowing from whatever
    `subset()` recorded in `info.selected_dimensions` and reads it. Byte-identical
    to the pre-Narrowing loader.
  - `materialize(info, narrowing)` — the interpreter's path. Reads under a
    planner-built Narrowing that has fused region/subsample (and later
    filter/bbox/timestep) into one selection.

`indexer` narrowing (grid crop+stride, particle subsample) is pushed into the
read where the library supports it; bbox/value predicates are applied as a row
mask after the read (no pushdown today). The per-format work lives in
adapters.py; this module copies metadata and routes to the adapter.
"""

import copy

import numpy as np

from adapters import get_adapter_for_info, _resolve_variables, build_selection_info
from narrowing import narrowing_from_dimensions, build_row_mask


def load(dataset_info):
    """Materialize exactly what `info` describes, narrowed by the legacy
    `selected_dimensions` policy `subset()` recorded."""
    narrowing = narrowing_from_dimensions(
        getattr(dataset_info, 'selected_dimensions', None),
        dataset_info.dimensions.get('particles', 0),
        getattr(dataset_info, 'positions', None))
    return materialize(dataset_info, narrowing)


def materialize(dataset_info, narrowing):
    """Read `dataset_info` under a (planner-built) Narrowing."""
    loaded = copy.deepcopy(dataset_info)

    # Timestep (Layer A): a timestep is a separate file — rebind to the chosen
    # one before dispatch. The adapter routes by filetype, so this just reads a
    # different file of the same format/schema.
    if getattr(narrowing, 'timestep', None) is not None and getattr(loaded, 'timesteps', None):
        loaded.filepath = loaded.timesteps[narrowing.timestep]['source']

    adapter = get_adapter_for_info(loaded)

    variables = _resolve_variables(loaded, None)   # already trimmed by projection
    locations = getattr(loaded, 'variable_locations', None) or {}

    # A bbox/predicate may test a variable the user didn't keep — read it too,
    # then drop it after masking.
    extra = _mask_only_vars(narrowing, loaded) - set(variables)
    read_vars = variables + [v for v in extra if v in loaded.variables]

    read_all = getattr(adapter, 'read_all', None)
    if read_all is not None:
        wanted = {var: locations.get(var, var) for var in read_vars}
        for var, arr in read_all(loaded.filepath, wanted, narrowing).items():
            loaded.data[var] = np.asarray(arr)
    else:
        for var in read_vars:
            location = locations.get(var, var)
            loaded.data[var] = np.asarray(
                adapter.read_array(loaded.filepath, location, narrowing))

    # Row mask (bbox + value predicates): applied after the read. Inert until a
    # phase wires bbox/predicates onto the Narrowing.
    if narrowing.has_row_mask:
        mask = build_row_mask(narrowing, loaded.data, getattr(loaded, 'positions', None))
        if mask is not None:
            for var in list(loaded.data):
                arr = loaded.data[var]
                if arr.ndim >= 1 and arr.shape and arr.shape[0] == mask.shape[0]:
                    loaded.data[var] = arr[mask]

    positions = getattr(loaded, 'positions', None) or ()
    for v in extra:                       # drop mask-only vars, but keep coords (render needs them)
        if v not in positions:
            loaded.data.pop(v, None)

    loaded.loaded = True
    loaded.variables = variables
    loaded.selection_info = build_selection_info(loaded, variables, narrowing)
    return loaded


def _mask_only_vars(narrowing, info):
    """Variables a row mask needs to read (positions for a bbox, predicate vars)."""
    needed = set()
    if narrowing.bbox is not None and getattr(info, 'positions', None):
        needed.update(info.positions)
    needed.update(p.var for p in narrowing.predicates)
    return needed
