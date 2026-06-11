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

from adapters import get_adapter_for_info


def load(dataset_info, variables=None, dimensions=None):
    loaded_info = copy.deepcopy(dataset_info)
    adapter = get_adapter_for_info(loaded_info)
    return adapter.load(loaded_info, variables=variables, dimensions=dimensions)
