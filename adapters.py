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
# FITS (observational astronomy) via astropy
# ---------------------------------------------------------------------------
def _fits_field_map(hdulist):
    """Map variable name -> (hdu_index, column_name_or_None) for readable data.

    Table HDUs contribute one variable per column; image HDUs (NAXIS > 0)
    contribute one variable for the image array. Uses headers only, so it does
    not pull pixel/row data into memory.
    """
    fmap = {}
    for i, hdu in enumerate(hdulist):
        cols = getattr(hdu, 'columns', None)
        if cols is not None:  # table HDU
            for c in cols.names:
                fmap[c] = (i, c)
            continue
        if hdu.header.get('NAXIS', 0) > 0:  # image HDU with data
            name = hdu.name if (hdu.name and hdu.name != 'PRIMARY') else f"image_{i}"
            fmap[name] = (i, None)
    return fmap


class AstropyAdapter(FormatAdapter):
    name = "FITS"

    _EXTENSIONS = ('.fits', '.fit', '.fts')
    _MAGIC = b'SIMPLE  ='  # every FITS primary header starts here

    @classmethod
    def can_handle(cls, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        if ext in cls._EXTENSIONS:
            return True
        try:
            with open(filepath, 'rb') as f:
                return f.read(len(cls._MAGIC)) == cls._MAGIC
        except OSError:
            return False

    def inspect(self, filepath):
        from astropy.io import fits

        variables = []
        dimensions = {}
        attributes = {}

        with fits.open(filepath, memmap=True) as hdul:
            fmap = _fits_field_map(hdul)
            for var, (idx, col) in fmap.items():
                hdu = hdul[idx]
                variables.append(var)
                if col is None:  # image
                    shape = tuple(hdu.shape)
                    attributes[f"{var}_shape"] = shape
                    if len(shape) == 3:
                        dimensions['grid'] = shape
                else:  # table column
                    attributes[f"{var}_length"] = hdu.header.get('NAXIS2', 0)

            # If all table columns share a length, treat rows like particles
            table_lengths = {hdul[idx].header.get('NAXIS2', 0)
                             for (idx, col) in fmap.values() if col is not None}
            if len(table_lengths) == 1:
                dimensions['particles'] = table_lengths.pop()

            for k in ('TELESCOP', 'INSTRUME', 'OBJECT', 'DATE-OBS'):
                if k in hdul[0].header:
                    attributes[k] = hdul[0].header[k]

        return DatasetInfo(filepath, self.name, variables,
                           dimensions=dimensions, attributes=attributes)

    def load(self, dataset_info, variables=None, dimensions=None):
        from astropy.io import fits

        variables = _resolve_variables(dataset_info, variables)
        total_particles = dataset_info.dimensions.get('particles', 0)
        particle_indices = (_get_particle_indices(dimensions, total_particles)
                            if total_particles else None)

        with fits.open(dataset_info.filepath, memmap=True) as hdul:
            fmap = _fits_field_map(hdul)
            for var in variables:
                idx, col = fmap[var]
                hdu = hdul[idx]
                if col is None:  # image
                    arr = np.asarray(hdu.data)
                    if arr.ndim == 3:
                        step = _get_grid_step(dimensions, arr.shape[0])
                        arr = arr[::step, ::step, ::step]
                else:  # table column
                    arr = np.asarray(hdu.data[col])
                    if arr.ndim == 1 and particle_indices is not None:
                        arr = arr[particle_indices]
                dataset_info.data[var] = arr

        dataset_info.loaded = True
        dataset_info.selection_info = {
            'variables_loaded': variables,
            'dimension_selection': dimensions,
        }
        if total_particles:
            dataset_info.selection_info['total_particles'] = total_particles
        return dataset_info


# ---------------------------------------------------------------------------
# yt — near-universal simulation reader (auto-detects across frontends)
# ---------------------------------------------------------------------------
class YTAdapter(FormatAdapter):
    """Broad fallback for simulation data. yt.load() sniffs the file against its
    frontends (Enzo, FLASH, Gadget, RAMSES, AREPO, Athena, ...) and returns the
    right dataset object. Registered last: specific adapters win first; yt
    catches everything else it recognizes.
    """

    name = "yt"

    @staticmethod
    def _quiet_yt():
        import yt
        try:
            yt.set_log_level("error")
        except Exception:
            pass
        return yt

    @classmethod
    def can_handle(cls, filepath):
        try:
            yt = cls._quiet_yt()
        except ImportError:
            return False
        try:
            yt.load(filepath)  # cheap: reads metadata, raises if unrecognized
            return True
        except Exception:
            return False

    def inspect(self, filepath):
        yt = self._quiet_yt()
        ds = yt.load(filepath)

        # field_list entries are (ftype, fname) tuples
        variables, seen = [], set()
        for _ftype, fname in ds.field_list:
            if fname not in seen:
                variables.append(str(fname))
                seen.add(fname)

        dimensions = {}
        attributes = {"yt_dataset_class": type(ds).__name__}

        try:
            dims = [int(d) for d in ds.domain_dimensions]
            attributes['domain_dimensions'] = dims
            if any(d > 1 for d in dims):
                dimensions['grid'] = tuple(dims)
        except Exception:
            pass
        try:
            attributes['domain_left_edge'] = [float(x) for x in ds.domain_left_edge]
            attributes['domain_right_edge'] = [float(x) for x in ds.domain_right_edge]
        except Exception:
            pass
        try:
            counts = dict(ds.particle_type_counts)
            if counts:
                dimensions['particles'] = int(sum(counts.values()))
                attributes['particle_type_counts'] = {k: int(v) for k, v in counts.items()}
        except Exception:
            pass
        try:
            attributes['current_time'] = float(ds.current_time)
        except Exception:
            pass

        return DatasetInfo(filepath, self.name, variables,
                           dimensions=dimensions, attributes=attributes)

    def load(self, dataset_info, variables=None, dimensions=None):
        yt = self._quiet_yt()
        ds = yt.load(dataset_info.filepath)
        variables = _resolve_variables(dataset_info, variables)

        # map short field name -> full (ftype, fname) yt field key
        field_lookup = {}
        for ftype, fname in ds.field_list:
            field_lookup.setdefault(str(fname), (ftype, fname))

        total_particles = dataset_info.dimensions.get('particles', 0)
        particle_indices = (_get_particle_indices(dimensions, total_particles)
                            if total_particles else None)

        want_grid = (dimensions or {}).get('grid') is not None or \
                    'grid' in dataset_info.dimensions
        covering = None
        if want_grid:
            try:
                covering = ds.covering_grid(level=0,
                                            left_edge=ds.domain_left_edge,
                                            dims=ds.domain_dimensions)
            except Exception:
                covering = None

        all_data = ds.all_data()
        for var in variables:
            field = field_lookup.get(var, var)
            arr = None
            if covering is not None:
                try:
                    arr = np.asarray(covering[field])
                    if arr.ndim == 3:
                        step = _get_grid_step(dimensions, arr.shape[0])
                        arr = arr[::step, ::step, ::step]
                except Exception:
                    arr = None
            if arr is None:
                arr = np.asarray(all_data[field])
                if arr.ndim == 1 and particle_indices is not None:
                    arr = arr[particle_indices]
            dataset_info.data[var] = arr

        dataset_info.loaded = True
        dataset_info.selection_info = {
            'variables_loaded': variables,
            'dimension_selection': dimensions,
        }
        if total_particles:
            dataset_info.selection_info['total_particles'] = total_particles
        return dataset_info


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# yt first: it recognizes most simulation formats (incl. sim-HDF5 like AREPO/
# Gadget/SWIFT) and reads them with proper fields/units. The specific magic-byte
# adapters below are the fallback for files yt does not claim (plain HDF5,
# observational FITS, GenericIO).
REGISTRY = [YTAdapter, HDF5Adapter, AstropyAdapter, GenericIOAdapter]

_BY_NAME = {a.name: a for a in REGISTRY}

# Tier-1 adapters learned at runtime (LLM-generated, then frozen). These are
# instances, keyed by the format name they report.
_GENERATED = []
_GENERATED_BY_NAME = {}
_generated_cache_loaded = False


def register_generated_adapter(adapter):
    """Add a learned adapter so future files route to it as Tier 0."""
    if adapter.name not in _GENERATED_BY_NAME:
        _GENERATED.append(adapter)
        _GENERATED_BY_NAME[adapter.name] = adapter


def _llm_module():
    """The Tier-1 module, or None if its deps (dspy) aren't importable."""
    try:
        import llm_adapter
        return llm_adapter
    except Exception:
        return None


def _ensure_generated_loaded():
    """Register previously-frozen generated adapters from disk, once. No LLM."""
    global _generated_cache_loaded
    if _generated_cache_loaded:
        return
    _generated_cache_loaded = True
    mod = _llm_module()
    if mod is not None:
        try:
            mod.load_cached_adapters()
        except Exception:
            pass


def get_adapter(filepath):
    """Return an adapter for filepath.

    Tier 0: registered readers (yt, HDF5, FITS, GenericIO).
    Tier 1: a previously-frozen LLM-generated adapter, else one generated now.
    Raises UnsupportedFormatError if nothing can read it.
    """
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")

    # Tier 0 — yt first, then the specific magic-byte readers
    for adapter_cls in REGISTRY:
        if adapter_cls.can_handle(filepath):
            return adapter_cls()

    # Tier 1 — adapters already learned and frozen
    _ensure_generated_loaded()
    for adapter in _GENERATED:
        if adapter.can_handle(filepath):
            return adapter

    # Tier 1 — generate, validate, and freeze a new adapter
    mod = _llm_module()
    if mod is not None:
        adapter = mod.try_generate_adapter(filepath)  # registers on success
        if adapter is not None:
            return adapter

    raise UnsupportedFormatError(
        f"Unsupported file type (no reader recognized it, and no adapter "
        f"could be generated): {filepath}"
    )


def get_adapter_for_info(dataset_info):
    """Return the adapter that produced a given DatasetInfo (route load back)."""
    adapter_cls = _BY_NAME.get(dataset_info.filetype)
    if adapter_cls is not None:
        return adapter_cls()
    _ensure_generated_loaded()
    inst = _GENERATED_BY_NAME.get(dataset_info.filetype)
    if inst is not None:
        return inst
    raise UnsupportedFormatError(
        f"No adapter for filetype {dataset_info.filetype!r}")
