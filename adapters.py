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
    """Base class. Every format implements three things.

    can_handle / inspect run at discovery time. `read_array` is the one
    format-specific read primitive: given a per-variable `location` token (from
    inspect's variable_locations, default = the variable name) and a Selection,
    return ONE numpy array — pushing the selection into the read where the
    library supports partial reads, else reading full and finishing with
    apply_selection(). All orchestration (variable resolution, selection,
    selection_info) lives in the universal load() in my_load.py — adapters do
    NOT implement load.

    `name` is the value stored in DatasetInfo.filetype and used to route
    read_array back to this adapter via get_adapter_for_info.

    Column-store formats (GenericIO) may also implement an optional
    `read_all(filepath, locations, selection) -> {var: array}` batch hook so
    load() reads the file once instead of once per variable.
    """

    name = None  # short format identifier, e.g. "HDF5"

    @classmethod
    def can_handle(cls, filepath):
        """Cheap structural check: is this file ours? (magic bytes / ext.)"""
        raise NotImplementedError

    def inspect(self, filepath):
        """Read metadata only; return an unloaded DatasetInfo."""
        raise NotImplementedError

    def read_array(self, filepath, location, selection):
        """Return one selected numpy array for `location`."""
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
        return list(dataset_info.variables)
    invalid = set(variables) - set(dataset_info.variables)
    if invalid:
        raise ValueError(f"Variables not found in file: {invalid}")
    return list(variables)


_DIMENSION_KEYS = ('particles', 'grid')


def _validate_dimension_selection(dimensions):
    """Fail fast on a dimension selection at subset() time.

    Checks the keys and value types only; the actual index/stride resolution is
    deferred to load() (where the dataset's sizes are known and, for random
    subsamples, the draw should happen at read time). Mirrors the type contract
    of _get_particle_indices / _get_grid_step.
    """
    if not isinstance(dimensions, dict):
        raise ValueError(f"dimensions must be a dict, got {type(dimensions).__name__}")
    bad = set(dimensions) - set(_DIMENSION_KEYS)
    if bad:
        raise ValueError(f"Unknown dimension(s) {bad}; expected any of {_DIMENSION_KEYS}")
    for key, sel in dimensions.items():
        if isinstance(sel, slice):
            continue
        if isinstance(sel, float):
            if not 0 < sel <= 1:
                raise ValueError(f"Float {key} selection must be in (0, 1], got {sel}")
        elif isinstance(sel, int) and not isinstance(sel, bool):
            if sel <= 0:
                raise ValueError(f"{key} selection must be positive, got {sel}")
        else:
            raise ValueError(
                f"Invalid {key} selection {sel!r}: expected int, float, or slice")


def detect_positions(variables):
    """Best-effort ('x','y','z') coordinate variable names from a list of names.

    Returns a 3-tuple or None (no confident match). Works on names alone, so
    `inspect` can resolve it without reading data. Format-blind.
    """
    keys = list(variables)
    if 'x' in keys and 'y' in keys and 'z' in keys:
        return ('x', 'y', 'z')

    lower = {k.lower(): k for k in keys}
    if 'x' in lower and 'y' in lower and 'z' in lower:
        return (lower['x'], lower['y'], lower['z'])

    for px, py, pz in [('X', 'Y', 'Z'), ('pos_x', 'pos_y', 'pos_z'),
                       ('position_x', 'position_y', 'position_z'), ('px', 'py', 'pz')]:
        if px in keys and py in keys and pz in keys:
            return (px, py, pz)

    xs = [k for k in keys if 'x' in k.lower()]
    ys = [k for k in keys if 'y' in k.lower()]
    zs = [k for k in keys if 'z' in k.lower()]
    if len(xs) == len(ys) == len(zs) == 1:
        return (xs[0], ys[0], zs[0])

    return None


# Sentinel returned by Selection.indexer meaning "take the whole array/dataset".
# arr[TAKE_ALL] / dset[TAKE_ALL] is a full read, so callers index unconditionally.
TAKE_ALL = slice(None)


class Selection:
    """A resolved, format-blind selection, built ONCE per load() call.

    It owns the policy (what the caller asked for via `dimensions`) and resolves
    it to a concrete index object on demand through indexer(ndim, leading_len).
    The same branching is reused by pushdown reads (dset[idx]) and the read-full
    fallback (arr[idx] via apply_selection).
    """

    def __init__(self, dimensions, total_particles):
        self.dimensions = dimensions                  # raw dict, for selection_info echo
        self.total_particles = total_particles or 0
        # Resolve particle indices ONCE so every variable subsamples the SAME
        # rows — x[i], y[i], z[i] must stay aligned across the load() call.
        self.particle_index = (_get_particle_indices(dimensions, self.total_particles)
                               if self.total_particles else None)

    def indexer(self, ndim, leading_len):
        """Index object to apply to an array/dataset of this ndim whose leading
        axis has length `leading_len`. Returns TAKE_ALL when no slicing applies."""
        if ndim == 3 and self.dimensions and 'grid' in self.dimensions:
            step = _get_grid_step(self.dimensions, leading_len)
            if step <= 1:
                return TAKE_ALL
            s = slice(None, None, step)
            return (s, s, s)
        if (ndim in (1, 2) and self.particle_index is not None
                and self.total_particles and leading_len == self.total_particles):
            return self.particle_index if ndim == 1 else (self.particle_index, slice(None))
        return TAKE_ALL


def apply_selection(arr, selection):
    """Read-full-then-slice fallback: apply a Selection to a materialized array.
    Adapters use this when their library can't push the selection into the read."""
    arr = np.asarray(arr)
    idx = selection.indexer(arr.ndim, arr.shape[0] if arr.ndim else 0)
    return arr if idx is TAKE_ALL else arr[idx]


def build_selection_info(dataset_info, variables, selection):
    """Reproduce the exact selection_info contract shared by every adapter:
    variables_loaded, dimension_selection, and (when the dataset has particles)
    total_particles + particles_loaded, plus grid_shape_loaded for 3-D output."""
    info = {
        'variables_loaded': variables,
        'dimension_selection': selection.dimensions,
    }
    if selection.total_particles:
        info['total_particles'] = selection.total_particles
        for v in variables:
            if dataset_info.data[v].ndim == 1:
                info['particles_loaded'] = len(dataset_info.data[v])
                break
    first = dataset_info.data[variables[0]]
    if first.ndim == 3:
        info['grid_shape_loaded'] = first.shape
    return info


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
        # The container is known (h5py reads any HDF5), but the semantics are
        # not. Try the binding path — it fingerprints the schema and reuses or
        # LLM-derives a verified binding. Fall back to a flat generic listing if
        # binding is unavailable (no dspy / no API key / never validated).
        try:
            import schema_binding
            bound = schema_binding.bind_hdf5(filepath)
            if bound is not None:
                return bound
        except ImportError:
            pass  # dspy / h5py not available — expected; use generic listing
        except Exception as e:
            # A real error in the binding path — don't mask it silently.
            import sys
            print(f"[VisLang] binding path failed for {filepath}: "
                  f"{type(e).__name__}: {e}; falling back to generic HDF5 inspection.",
                  file=sys.stderr)
        return self._generic_inspect(filepath)

    def _generic_inspect(self, filepath):
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

    def read_array(self, filepath, location, selection):
        # `location` is either a dataset-path string (generic inspect, where the
        # variable name IS the path) or a binding token dict from
        # schema_binding: {"source", "component", "dim"}.
        import h5py

        if isinstance(location, dict):
            source = location["source"]
            comp = location.get("component")
        else:
            source, comp = location, None

        with h5py.File(filepath, 'r') as f:
            dset = f[source]
            if comp is not None:
                # 2-D (N, k) -> take one column via hyperslab (only that column
                # is read), then row-subsample via the shared fallback.
                return apply_selection(dset[:, comp], selection)
            if dset.ndim == 3:
                # Grid hyperslab pushdown: dset[(s,s,s)] reads only strided cells;
                # dset[slice(None)] reads the full cube.
                return dset[selection.indexer(3, dset.shape[0])]
            # 1-D / 2-D whole-array variable: read full, then fallback subsample.
            return apply_selection(dset[:], selection)


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
    def _read(filepath, variables=None):
        os.environ['GENERICIO_NO_MPI'] = 'true'
        import pygio

        def _do(path):
            if variables is not None:
                try:  # column pushdown: read only requested variables
                    return pygio.read_genericio(path, variables=list(variables))
                except TypeError:  # older pygio without a variables= kwarg
                    return pygio.read_genericio(path)
            return pygio.read_genericio(path)

        try:
            return _do(filepath)
        except Exception:
            # Partitioned files use the #0 notation
            return _do(f"{filepath}#0")

    def inspect(self, filepath):
        # Header-only metadata — NO bulk read. The previous implementation read
        # every variable's full array (~743s on an 8.9 GB HACC file) just to list
        # names and compute per-variable min/max; those min/max attributes were
        # unused, so we drop them and read only the header.
        os.environ['GENERICIO_NO_MPI'] = 'true'
        import pygio

        def _hdr(fn, path, default=None):
            try:
                return fn(path)
            except Exception:
                return default

        # Names/phys metadata are identical across partitions, so the #0 fallback
        # is safe for them (mirrors _read's partition handling).
        names = _hdr(pygio.read_variable_names, filepath)
        if not names:
            names = _hdr(pygio.read_variable_names, f"{filepath}#0", default=[])
        variables = list(names or [])

        # Total particle count must come from the base path — a #0 partition
        # reports only its own rows. read_num_elems is reliable where
        # read_total_num_elems returns -1.
        dimensions = {}
        n = _hdr(pygio.read_num_elems, filepath)
        if isinstance(n, int) and n >= 0:
            dimensions['particles'] = n

        attributes = {}
        scale = _hdr(pygio.read_phys_scale, filepath)
        if scale is not None:
            attributes['phys_scale'] = scale
        origin = _hdr(pygio.read_phys_origin, filepath)
        if origin is not None:
            attributes['phys_origin'] = origin

        return DatasetInfo(filepath, self.name, variables,
                           dimensions=dimensions, attributes=attributes)

    def read_array(self, filepath, location, selection):
        # Single-variable path (rarely used; load() prefers read_all). pygio has
        # no row-level partial read, so subsample in memory after the column read.
        raw = self._read(filepath, variables=[location])
        return apply_selection(raw[location], selection)

    def read_all(self, filepath, locations, selection):
        # Batch hook: one file read for all requested columns (column pushdown
        # where pygio supports it), so load() doesn't re-read the file per var.
        wanted = list(dict.fromkeys(locations.values()))
        raw = self._read(filepath, variables=wanted)
        return {var: apply_selection(raw[loc], selection)
                for var, loc in locations.items()}


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

        info = DatasetInfo(filepath, self.name, variables,
                           dimensions=dimensions, attributes=attributes)
        # Token per variable: (hdu_index, column_or_None) — how read_array
        # addresses it. Survives my_load's deepcopy.
        info.variable_locations = {var: fmap[var] for var in variables}
        return info

    def read_array(self, filepath, location, selection):
        from astropy.io import fits

        idx_hdu, col = location
        with fits.open(filepath, memmap=True) as hdul:
            hdu = hdul[idx_hdu]
            if col is None:  # image HDU
                data = hdu.data
                if data is None:
                    return np.asarray([])
                if data.ndim == 3:
                    # memmap strided slice -> only the strided planes are paged in
                    return np.asarray(data[selection.indexer(3, data.shape[0])])
                return np.asarray(data)
            # table column: materialize then fallback subsample
            return apply_selection(hdu.data[col], selection)


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

    def __init__(self):
        # Cache (ds, field_lookup, covering) per (filepath, want_grid) so load()
        # doesn't re-run yt.load() once per variable.
        self._ctx_cache = {}

    @staticmethod
    def _quiet_yt():
        import yt
        try:
            yt.set_log_level("error")
        except Exception:
            pass
        return yt

    def _ctx(self, filepath, want_grid):
        key = (filepath, want_grid)
        if key not in self._ctx_cache:
            yt = self._quiet_yt()
            ds = yt.load(filepath)
            field_lookup = {}
            for ftype, fname in ds.field_list:
                field_lookup.setdefault(str(fname), (ftype, fname))
            covering = None
            if want_grid:
                try:
                    covering = ds.covering_grid(level=0,
                                                left_edge=ds.domain_left_edge,
                                                dims=ds.domain_dimensions)
                except Exception:
                    covering = None
            self._ctx_cache[key] = (ds, field_lookup, covering)
        return self._ctx_cache[key]

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

        info = DatasetInfo(filepath, self.name, variables,
                           dimensions=dimensions, attributes=attributes)
        # Token per variable: the field name + whether this dataset is grid-
        # structured (so read_array knows to build a covering grid vs all_data).
        is_grid = 'grid' in dimensions
        info.variable_locations = {v: {"field": v, "grid": is_grid} for v in variables}
        return info

    def read_array(self, filepath, location, selection):
        if isinstance(location, dict):
            field_name = location.get("field")
            want_grid = location.get("grid", False)
        else:
            field_name, want_grid = location, False

        ds, field_lookup, covering = self._ctx(filepath, want_grid)
        field = field_lookup.get(field_name, field_name)

        arr = None
        if covering is not None:
            try:
                arr = np.asarray(covering[field])   # 3-D grid field
            except Exception:
                arr = None
        if arr is None:
            arr = np.asarray(ds.all_data()[field])  # particle / fallback field
        return apply_selection(arr, selection)


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

    print(f"[VisLang] No built-in reader (yt/HDF5/FITS/GenericIO) recognized \n"
          f"{os.path.basename(filepath)!r} — checking generated adapters.", flush=True)

    # Tier 1 — adapters already learned and frozen
    _ensure_generated_loaded()
    for adapter in _GENERATED: #checkcheck
        if adapter.can_handle(filepath):
            print(f"[VisLang] Using previously-generated adapter {adapter.name!r} "
                  f"(frozen — no LLM call).", flush=True)
            return adapter

    # Tier 1 — generate, validate, and freeze a new adapter
    mod = _llm_module()
    if mod is not None:
        print(f"[VisLang] No frozen adapter matches — asking the LLM to identify "
              f"the format and write a reader module.", flush=True)
        adapter = mod.try_generate_adapter(filepath)  # registers on success
        if adapter is not None:
            return adapter
    else:
        print(f"[VisLang] LLM fallback unavailable (dspy not importable).", flush=True)

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
