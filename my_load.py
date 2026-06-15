"""Load selected data for an inspected dataset.

Args:
    dataset_info: DatasetInfo from inspect_file()
    variables: list of variable names to load (None = all)
    dimensions: dict with dimension selection, e.g.:
               {'particles': slice(0, 1000)}        # first 1000
               {'particles': slice(None, None, 10)} # every 10th
               {'particles': 5000}                  # random 5000 particles
               {'particles': 0.1}                   # random 10% of particles
               {'grid': 64}                         # stride to ~64 cells/axis
               {'grid': 0.25}                        # keep 25% of cells per axis

Returns:
    A new DatasetInfo with data populated.

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


def load(dataset_info, variables=None, dimensions=None):
    # Universal, format-blind orchestration. The only format-specific step is
    # the adapter's read_array (one array given a location token + selection);
    # everything else — variable resolution, the selection, selection_info — is
    # written once here and shared by every adapter.
    loaded = copy.deepcopy(dataset_info)
    adapter = get_adapter_for_info(loaded)

    variables = _resolve_variables(loaded, variables)
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
