"""The generalized narrowing model — the one structure the planner fuses every
narrowing form into, and the one the physical read paths consult.

It supersedes the old modality-wide `Selection` (grid stride / particle
subsample) while staying backward compatible: `narrowing_from_dimensions`
rebuilds the exact old behavior from a legacy `selected_dimensions` dict, so
`load()` reads byte-identically to before. The planner builds a `Narrowing`
directly for the new per-axis forms (region crop + per-axis subsample now;
bbox/filter/timestep wired in later phases).

Every read funnels through `Narrowing.indexer` (pushdown: HDF5/FITS hyperslab)
or `apply_selection` (read-full-then-slice fallback). Build the capability here
and every adapter — including future LLM-generated ones — inherits it.
"""

from dataclasses import dataclass, field

import numpy as np

# Sentinel: arr[TAKE_ALL] / dset[TAKE_ALL] is a full read, so callers index
# unconditionally.
TAKE_ALL = slice(None)

_OPS = ("<", "<=", ">", ">=", "==", "!=")
_OP_FN = {"<": np.less, "<=": np.less_equal, ">": np.greater,
          ">=": np.greater_equal, "==": np.equal, "!=": np.not_equal}


# ---------------------------------------------------------------------------
# Structured pieces a Narrowing is built from
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class AxisRange:
    """One grid axis: index-space crop [start:stop] with an optional stride.
    None start/stop mean open (full extent). Interpretation-agnostic so a future
    world-space region can convert to indices before building this."""
    start: int = None
    stop: int = None
    step: int = 1

    def to_slice(self):
        return slice(self.start, self.stop, self.step or 1)


@dataclass(frozen=True)
class Predicate:
    """A value filter: keep where `var` `op` `value` (scalar rhs)."""
    var: str
    op: str
    value: float


@dataclass(frozen=True)
class BBox:
    """A coordinate-VALUE box on the position variables (info.positions)."""
    lo: tuple = (None, None, None)
    hi: tuple = (None, None, None)


# ---------------------------------------------------------------------------
# Legacy dict -> indices (verbatim from the old adapters.Selection helpers, so
# the back-compat path reproduces prior reads exactly)
# ---------------------------------------------------------------------------
def _get_particle_indices(dimensions, total_particles):
    """Convert a 'particles' dimension selection to indices.
    Returns None (load all), a slice, or a numpy array of indices."""
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


# ---------------------------------------------------------------------------
# The Narrowing
# ---------------------------------------------------------------------------
class Narrowing:
    """A resolved, format-blind narrowing, built ONCE per materialize() call.

    `indexer(ndim, leading_len)` returns a numpy index object (or TAKE_ALL) for
    grid crop+stride and particle subsample — the part expressible as a basic
    index. `bbox`/`predicates` are NOT a basic index (they depend on other
    variables' values), so load applies them afterward as a row mask via
    build_row_mask. `dimensions` is a free-form echo for selection_info.
    """

    def __init__(self, *, grid_ranges=None, particle_index=None, bbox=None,
                 predicates=(), timestep=None, dimensions=None,
                 total_particles=0, positions=None):
        self.grid_ranges = grid_ranges          # list[AxisRange] (len == ndim) or None
        self.particle_index = particle_index     # slice | np.ndarray | None
        self.bbox = bbox                          # BBox or None  (applied as a mask)
        self.predicates = tuple(predicates)       # tuple[Predicate]  (applied as a mask)
        self.timestep = timestep                  # resolved int index or None
        self.dimensions = dimensions or {}        # echo for selection_info
        self.total_particles = total_particles or 0
        self.positions = positions

    def indexer(self, ndim, leading_len):
        # NEW: per-axis grid crop + stride (region / per-axis subsample).
        if ndim == 3 and self.grid_ranges:
            return tuple(r.to_slice() for r in self.grid_ranges)
        # LEGACY: modality-wide grid stride from the dimensions dict.
        if ndim == 3 and self.dimensions and 'grid' in self.dimensions:
            step = _get_grid_step(self.dimensions, leading_len)
            if step <= 1:
                return TAKE_ALL
            s = slice(None, None, step)
            return (s, s, s)
        # Particle / table rows: same index for every aligned variable.
        if (ndim in (1, 2) and self.particle_index is not None
                and self.total_particles and leading_len == self.total_particles):
            return self.particle_index if ndim == 1 else (self.particle_index, slice(None))
        return TAKE_ALL

    @property
    def has_row_mask(self):
        return self.bbox is not None or bool(self.predicates)


def narrowing_from_dimensions(dimensions, total_particles, positions=None):
    """Back-compat builder: a Narrowing equivalent to the old Selection built
    from a legacy `selected_dimensions` dict. Keeps load() byte-identical."""
    total = total_particles or 0
    pidx = _get_particle_indices(dimensions, total) if total else None
    return Narrowing(grid_ranges=None, particle_index=pidx, dimensions=dimensions,
                     total_particles=total, positions=positions)


# ---------------------------------------------------------------------------
# Read-full-then-slice fallback + row mask
# ---------------------------------------------------------------------------
def apply_selection(arr, selection):
    """Apply a Narrowing's basic index to a materialized array (used by adapters
    whose library can't push the selection into the read)."""
    arr = np.asarray(arr)
    idx = selection.indexer(arr.ndim, arr.shape[0] if arr.ndim else 0)
    return arr if idx is TAKE_ALL else arr[idx]


def build_row_mask(narrowing, data, positions):
    """1-D boolean mask over particle/table rows from bbox + ANDed predicates.

    `data` holds already-read (and subsample-applied) variable arrays. Returns
    None if no mask applies. Used by load after the read (no pushdown today)."""
    mask = None
    if narrowing.bbox is not None and positions:
        for axis, vname in enumerate(positions):
            lo, hi = narrowing.bbox.lo[axis], narrowing.bbox.hi[axis]
            col = np.asarray(data[vname])
            m = np.ones(len(col), dtype=bool)
            if lo is not None:
                m &= col >= lo
            if hi is not None:
                m &= col <= hi
            mask = m if mask is None else (mask & m)
    for p in narrowing.predicates:
        m = _OP_FN[p.op](np.asarray(data[p.var]), p.value)
        mask = m if mask is None else (mask & m)
    return mask


# ---------------------------------------------------------------------------
# Static check (metadata only — runs before any bulk read)
# ---------------------------------------------------------------------------
def validate_narrowing(info, n):
    """Validate a planner-built Narrowing against an inspected schema. Raises a
    clear error before any data is read. Generalizes _validate_dimension_selection."""
    dims = info.dimensions or {}

    if n.grid_ranges is not None:
        grid = dims.get('grid')
        if not (isinstance(grid, (tuple, list)) and len(grid) == len(n.grid_ranges)):
            raise ValueError(f"region/subsample: dataset grid {grid!r} does not have "
                             f"{len(n.grid_ranges)} axes")
        for ax, (r, size) in enumerate(zip(n.grid_ranges, grid)):
            a = 0 if r.start is None else r.start
            b = size if r.stop is None else r.stop
            if not (0 <= a < b <= size):
                raise ValueError(f"region axis {ax}: [{a}:{b}] out of bounds 0..{size}")
            if r.step < 1:
                raise ValueError(f"subsample axis {ax}: step {r.step} must be >= 1")

    if n.bbox is not None:
        if not info.positions:
            raise ValueError("region on points needs coordinate variables "
                             "(info.positions); set inspect(positions=('x','y','z'))")
        for v in info.positions:
            if v not in info.variables:
                raise ValueError(f"position variable {v!r} not in dataset")

    for p in n.predicates:
        if p.var not in info.variables:
            raise ValueError(f"filter variable {p.var!r} not in {info.variables}")
        if p.op not in _OPS:
            raise ValueError(f"filter operator {p.op!r} invalid; one of {list(_OPS)}")

    if n.timestep is not None:
        steps = getattr(info, 'timesteps', None)
        if not steps:
            raise ValueError("timestep selected but this source has a single timestep; "
                             "inspect a glob/series to expose multiple timesteps")
        if not (0 <= n.timestep < len(steps)):
            raise ValueError(f"timestep {n.timestep} out of range 0..{len(steps) - 1}")
