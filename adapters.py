"""Format adapters: one contract every supported file format implements.

Each adapter knows how to (a) recognize its format, (b) inspect a file into a
DatasetInfo, and (c) read selected data back out. The registry dispatches by
asking each adapter `can_handle`. Adding a format = adding one adapter here;
nothing downstream (the registry, load, future query/pushdown) changes.

Only formats with a trusted, installed reader live here (full trust). Unknown
formats raise UnsupportedFormatError — we do not guess at raw byte layouts.
"""

import os
import copy
import numpy as np

from datasetInfo import DatasetInfo


class UnsupportedFormatError(Exception):
    """Raised when no registered adapter recognizes a file."""


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------
class FormatAdapter:
    """Base class. Every format implements these four methods.

    can_handle / inspect run at discovery time; load runs after the caller has
    chosen what to pull into memory. `name` is the value stored in
    DatasetInfo.filetype and used to route load() back to this adapter.
    """

    name = None  # short format identifier, e.g. "HDF5"

    @classmethod
    def can_handle(cls, filepath):
        """Cheap structural check: is this file ours? (magic bytes / ext.)"""
        raise NotImplementedError

    def inspect(self, filepath):
        """Read metadata only; return an unloaded DatasetInfo."""
        raise NotImplementedError

    def load(self, dataset_info, variables=None, dimensions=None):
        """Populate dataset_info.data with the requested selection."""
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Shared selection helpers (used by load implementations)
# ---------------------------------------------------------------------------
def _get_particle_indices(dimensions, total_particles):
    """Convert a 'particles' dimension selection to indices.

    Returns None (load all), a slice, or a numpy array of indices.
    """
    if dimensions is None or 'particles' not in dimensions:
        return None

    selection = dimensions['particles']

    if isinstance(selection, slice):
        return selection

    elif isinstance(selection, float):
        if not 0 < selection <= 1:
            raise ValueError(f"Float selection must be between 0 and 1, got {selection}")
        n_select = int(total_particles * selection)
        return np.random.choice(total_particles, size=n_select, replace=False)

    elif isinstance(selection, int):
        if selection > total_particles:
            raise ValueError(f"Cannot select {selection} particles from {total_particles}")
        return np.random.choice(total_particles, size=selection, replace=False)

    else:
        raise ValueError(f"Invalid dimension selection type: {type(selection)}")


def _get_grid_step(dimensions, axis_size):
    """Convert a 'grid' dimension selection to a per-axis stride (int)."""
    if dimensions is None or 'grid' not in dimensions:
        return 1

    selection = dimensions['grid']

    if isinstance(selection, float):
        if not 0 < selection <= 1:
            raise ValueError(f"Float grid selection must be between 0 and 1, got {selection}")
        return max(1, int(round(1 / selection)))

    elif isinstance(selection, int):
        if selection <= 0:
            raise ValueError(f"Grid resolution must be positive, got {selection}")
        return max(1, axis_size // selection)

    elif isinstance(selection, slice):
        return selection.step or 1

    else:
        raise ValueError(f"Invalid grid selection type: {type(selection)}")


def _resolve_variables(dataset_info, variables):
    """Default to all variables; validate any explicit request."""
    if variables is None:
        return dataset_info.variables
    invalid = set(variables) - set(dataset_info.variables)
    if invalid:
        raise ValueError(f"Variables not found in file: {invalid}")
    return variables


# ---------------------------------------------------------------------------
# HDF5
# ---------------------------------------------------------------------------
class HDF5Adapter(FormatAdapter):
    name = "HDF5"

    _EXTENSIONS = ('.h5', '.hdf5', '.hdf')
    _MAGIC = b'\x89HDF\r\n\x1a\n'

    @classmethod
    def can_handle(cls, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        if ext in cls._EXTENSIONS:
            return True
        try:
            with open(filepath, 'rb') as f:
                return f.read(8) == cls._MAGIC
        except OSError:
            return False

    def inspect(self, filepath):
        import h5py

        variables = []
        attributes = {}
        dimensions = {}
        dataset_shapes = {}

        with h5py.File(filepath, 'r') as f:
            def collect_datasets(name, obj):
                if isinstance(obj, h5py.Dataset):
                    variables.append(name)
                    dataset_shapes[name] = obj.shape

            f.visititems(collect_datasets)
            for key in f.attrs:
                val = f.attrs[key]
                attributes[key] = val.item() if hasattr(val, 'item') else val

        for var, shape in dataset_shapes.items():
            attributes[f"{var}_shape"] = shape

        # Detect particle-like data: all 1D datasets with the same length
        if dataset_shapes:
            all_1d = all(len(s) == 1 for s in dataset_shapes.values())
            if all_1d:
                lengths = set(s[0] for s in dataset_shapes.values())
                if len(lengths) == 1:
                    dimensions['particles'] = lengths.pop()

        return DatasetInfo(filepath, self.name, variables,
                           dimensions=dimensions, attributes=attributes)

    def load(self, dataset_info, variables=None, dimensions=None):
        import h5py

        variables = _resolve_variables(dataset_info, variables)
        total_particles = dataset_info.dimensions.get('particles', 0)
        particle_indices = (_get_particle_indices(dimensions, total_particles)
                            if total_particles else None)

        with h5py.File(dataset_info.filepath, 'r') as f:
            for var in variables:
                dset = f[var]
                if dset.ndim == 3:
                    # Stride-read directly from file — full array never in memory
                    step = _get_grid_step(dimensions, dset.shape[0])
                    arr = dset[::step, ::step, ::step]
                else:
                    arr = dset[:]
                    if arr.ndim == 1 and particle_indices is not None:
                        arr = arr[particle_indices]
                dataset_info.data[var] = arr

        dataset_info.loaded = True

        first_arr = dataset_info.data[variables[0]]
        dataset_info.selection_info = {
            'variables_loaded': variables,
            'dimension_selection': dimensions,
        }
        if total_particles:
            dataset_info.selection_info['total_particles'] = total_particles
            if first_arr.ndim == 1:
                dataset_info.selection_info['particles_loaded'] = len(first_arr)
        if first_arr.ndim == 3:
            dataset_info.selection_info['grid_shape_loaded'] = first_arr.shape

        return dataset_info


# ---------------------------------------------------------------------------
# GenericIO (HACC)
# ---------------------------------------------------------------------------
class GenericIOAdapter(FormatAdapter):
    name = "GenericIO"

    @classmethod
    def can_handle(cls, filepath):
        # GenericIO files carry a "HACC" magic in their header.
        try:
            with open(filepath, 'rb') as f:
                head = f.read(64)
            return b'HACC' in head
        except OSError:
            return False

    @staticmethod
    def _read(filepath):
        os.environ['GENERICIO_NO_MPI'] = 'true'
        import pygio
        try:
            return pygio.read_genericio(filepath)
        except Exception:
            # Partitioned files use the #0 notation
            return pygio.read_genericio(f"{filepath}#0")

    def inspect(self, filepath):
        data = self._read(filepath)

        variables = list(data.keys())

        dimensions = {}
        if variables:
            dimensions['particles'] = len(data[variables[0]])

        attributes = {}
        if hasattr(data, 'phys_scale'):
            attributes['phys_scale'] = data.phys_scale
        if hasattr(data, 'phys_origin'):
            attributes['phys_origin'] = data.phys_origin

        for var in variables:
            arr = data[var]
            attributes[f"{var}_min"] = float(arr.min())
            attributes[f"{var}_max"] = float(arr.max())

        return DatasetInfo(filepath, self.name, variables,
                           dimensions=dimensions, attributes=attributes)

    def load(self, dataset_info, variables=None, dimensions=None):
        raw_data = self._read(dataset_info.filepath)

        variables = _resolve_variables(dataset_info, variables)
        total_particles = dataset_info.dimensions.get('particles', 0)
        particle_indices = _get_particle_indices(dimensions, total_particles)

        for var in variables:
            data = raw_data[var]
            if particle_indices is not None:
                data = data[particle_indices]
            dataset_info.data[var] = data

        dataset_info.loaded = True
        dataset_info.selection_info = {
            'variables_loaded': variables,
            'total_particles': total_particles,
            'particles_loaded': len(dataset_info.data[variables[0]]),
            'dimension_selection': dimensions,
        }
        return dataset_info


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Order matters only when two adapters could both claim a file; cheapest /
# most specific checks first.
REGISTRY = [HDF5Adapter, GenericIOAdapter]

_BY_NAME = {a.name: a for a in REGISTRY}


def get_adapter(filepath):
    """Return an adapter instance for filepath, or raise UnsupportedFormatError."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    for adapter_cls in REGISTRY:
        if adapter_cls.can_handle(filepath):
            return adapter_cls()
    raise UnsupportedFormatError(
        f"Unsupported file type (no adapter recognized it): {filepath}\n"
        f"Supported for now: {', '.join(a.name for a in REGISTRY)}."
    )


def get_adapter_for_info(dataset_info):
    """Return the adapter that produced a given DatasetInfo (route load back)."""
    adapter_cls = _BY_NAME.get(dataset_info.filetype)
    if adapter_cls is None:
        raise UnsupportedFormatError(
            f"No adapter for filetype {dataset_info.filetype!r}")
    return adapter_cls()
