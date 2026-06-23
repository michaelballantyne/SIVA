"""Materialize a dataset — purely.

`load(info)` reads exactly what `info` describes: every variable still listed on
it, sliced by whatever dimension policy `subset()` recorded. It takes no
selection arguments of its own — narrowing is `subset()`'s job (see
`my_subset.py`). This keeps `load` an honest "materialize what's described" with
no hidden optimization, which is what makes pushdown via `subset()` sound.

In this (shallow, eager) embedding `load` is explicit: render/compress operate on
already-loaded data and raise otherwise. Once the DSL grows an interpreter +
computation graph, materialization becomes the executor's job and `load` moves
out of the spec grammar into a tool — but not yet.

Returns:
    A new DatasetInfo with `data` populated.

The per-format work lives in adapters.py; load() copies the metadata and
routes to the adapter that produced it.
"""

import copy

import numpy as np

from adapters import (
    get_adapter_for_info,
    _resolve_variables,
    Selection,
    build_selection_info,
)


def load(dataset_info):
    # Universal, format-blind orchestration. The only format-specific step is
    # the adapter's read_array (one array given a location token + selection);
    # everything else — variable resolution, the selection, selection_info — is
    # written once here and shared by every adapter.
    loaded = copy.deepcopy(dataset_info)
    adapter = get_adapter_for_info(loaded)

    # The selection lives on the info: `variables` was trimmed by subset() (or
    # is the full list), and `selected_dimensions` is its recorded slice policy.
    variables = _resolve_variables(loaded, None)
    dimensions = getattr(loaded, 'selected_dimensions', None)
    selection = Selection(dimensions, loaded.dimensions.get('particles', 0))
    locations = getattr(loaded, 'variable_locations', None) or {}

    # Column-store formats (GenericIO) expose read_all so the file is read once
    # rather than once per variable.
    read_all = getattr(adapter, 'read_all', None)
    if read_all is not None:
        wanted = {var: locations.get(var, var) for var in variables}
        for var, arr in read_all(loaded.filepath, wanted, selection).items():
            loaded.data[var] = np.asarray(arr)
    else:
        for var in variables:
            location = locations.get(var, var)
            loaded.data[var] = np.asarray(
                adapter.read_array(loaded.filepath, location, selection))

    loaded.loaded = True
    loaded.selection_info = build_selection_info(loaded, variables, selection)
    return loaded
