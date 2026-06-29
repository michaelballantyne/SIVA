"""The interpreter/planner — it turns an AST chain into physical ops.

A spec builds AST nodes (dsl_forms); after exec, run_pipeline hands each
registered sink here. plan_pipeline walks the chain source->sink, inspects the
source for its schema, static-checks the request (raising BEFORE any bulk read),
FUSES the narrowing forms into one Narrowing, lowers onto the physical layer
(inspect/subset/materialize/compress/render), and executes.

Form order is the promise; how we lower it is ours to choose. Projection
(`fields`) lowers via subset; region + subsample fuse into one Narrowing applied
by materialize (region crop + per-axis stride composed into one read). The
remaining new forms (filter/timestep, and region on point data) static-check but
raise NotImplementedError at lowering until their physical phases land.
"""

import os
import re

import numpy as np

from dsl_forms.nodes import SourceNode, upstream_of
from my_inspect import inspect_file
from my_subset import subset
from my_load import materialize
from my_compress import compress as _compress
from my_render import render as _render
from narrowing import (Narrowing, AxisRange, Predicate, BBox,
                       narrowing_from_dimensions, validate_narrowing)

# Forms whose physical backing is not wired yet (empty now; kept for future
# staged forms — e.g. grid value-filter / in-file timestep axis).
_NOT_YET = {}

_REMOTE = re.compile(r"^[a-z][a-z0-9+.-]*://|^[^/\s]+@[^/\s]+:")   # scheme:// or user@host:
_AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
_CACHE_DIR = "vislang_downloads"


def _normalize_remote(uri):
    """ssh://[user@]host/path -> scp-style [user@]host:/path that _parse_remote reads."""
    if uri.startswith("ssh://"):
        rest = uri[len("ssh://"):]
        if "/" not in rest:
            raise ValueError(f"ssh URL needs a path: {uri!r}")
        hostpart, path = rest.split("/", 1)
        return f"{hostpart}:/{path}"
    return uri


def _fetch_remote(uri):
    """Establish a connection and transfer a remote source to a local cache,
    returning the local path. (Remote globs/series are not supported yet.)"""
    from my_download import establish_connection, transfer, _parse_remote
    norm = _normalize_remote(uri)
    _, _, remote_path = _parse_remote(norm)
    os.makedirs(_CACHE_DIR, exist_ok=True)
    local = os.path.join(_CACHE_DIR, os.path.basename(remote_path.rstrip("/")) or "download")
    out = transfer(establish_connection(norm), remote_path, local)
    if out is None:
        raise RuntimeError(f"remote transfer of {uri} was cancelled")
    return out


# ---------------------------------------------------------------------------
def _chain(terminal):
    """Ordered [source, ..., terminal] by following `upstream`. Validates head."""
    nodes = []
    n = terminal
    while n is not None:
        nodes.append(n)
        n = upstream_of(n)
    nodes.reverse()
    if not isinstance(nodes[0], SourceNode):
        raise ValueError("pipeline does not begin at source(); every chain must "
                         "start from source(path)")
    return nodes


def _modality(info):
    """'particles' if the dataset has coordinate vars / a particle count, else
    'grid'. Decides how region/subsample lower."""
    if getattr(info, "positions", None):
        return "particles"
    if "particles" in (info.dimensions or {}):
        return "particles"
    return "grid"


def _step_from_factor(factor):
    """A subsample factor -> integer stride. int stride stays; float fraction ->
    nearest integer stride (legacy grid semantics: keep ~fraction per axis)."""
    if isinstance(factor, int):
        return factor
    return max(1, int(round(1 / factor)))


def _build_narrowing(region_nodes, subsample_nodes, filter_nodes, info):
    """Fuse the collected region + subsample + filter nodes into one Narrowing."""
    if _modality(info) == "grid":
        if filter_nodes:
            raise NotImplementedError("value filter on grid fields (voxel masking) is a "
                                      "follow-up; filter applies to point/table data today")
        return _grid_narrowing(region_nodes, subsample_nodes, info)
    return _particle_narrowing(region_nodes, subsample_nodes, filter_nodes, info)


def _grid_narrowing(region_nodes, subsample_nodes, info):
    # No narrowing requested -> full read; grid dims aren't needed at all.
    if not (region_nodes or subsample_nodes):
        return Narrowing(positions=getattr(info, "positions", None))
    grid = (info.dimensions or {}).get("grid")
    if not grid:
        raise ValueError("region/subsample needs grid dimensions, but none were "
                         "detected for this dataset (the HDF5 binding may have "
                         "fallen back to a generic listing)")
    ndim = len(grid)

    def axis_idx(axis):
        if axis not in _AXIS_INDEX or _AXIS_INDEX[axis] >= ndim:
            raise ValueError(f"unknown axis {axis!r}; this grid has {ndim} axes (x, y, z)")
        return _AXIS_INDEX[axis]

    ranges = [AxisRange(None, None, 1) for _ in range(ndim)]
    for rn in region_nodes:                       # crop [lo:hi] per named axis
        for axis, lo, hi in rn.ranges:
            i = axis_idx(axis)
            ranges[i] = AxisRange(lo, hi, ranges[i].step)
    for sn in subsample_nodes:                    # compose stride onto each axis
        if sn.uniform is not None:
            step = _step_from_factor(sn.uniform)
            ranges = [AxisRange(r.start, r.stop, step) for r in ranges]
        else:
            for axis, f in sn.per_axis:
                i = axis_idx(axis)
                ranges[i] = AxisRange(ranges[i].start, ranges[i].stop, _step_from_factor(f))

    echo = {"grid_ranges": [(r.start, r.stop, r.step) for r in ranges]}
    return Narrowing(grid_ranges=ranges, dimensions=echo,
                     positions=getattr(info, "positions", None))


def _particle_narrowing(region_nodes, subsample_nodes, filter_nodes, info):
    total = (info.dimensions or {}).get("particles", 0)
    positions = getattr(info, "positions", None)

    # subsample -> a shared row index (reuse the legacy particle logic).
    dims = None
    for sn in subsample_nodes:
        if sn.uniform is None:
            raise ValueError("per-axis subsample doesn't apply to point data; use a "
                             "single factor, e.g. subsample(d, 0.1)")
        f = sn.uniform
        dims = {"particles": slice(None, None, f) if isinstance(f, int) else f}
    base = narrowing_from_dimensions(dims, total, positions)

    # region -> a world-coordinate bounding box on the position variables.
    bbox = None
    if region_nodes:
        if not positions:
            raise ValueError("region on points needs coordinate variables; set "
                             "inspect(positions=('x','y','z'))")
        lo, hi = [None, None, None], [None, None, None]
        for rn in region_nodes:
            for axis, a, b in rn.ranges:
                if axis not in _AXIS_INDEX:
                    raise ValueError(f"region axis {axis!r} not one of x, y, z")
                i = _AXIS_INDEX[axis]
                lo[i], hi[i] = a, b
        bbox = BBox(lo=tuple(lo), hi=tuple(hi))

    # filter -> ANDed value predicates.
    preds = tuple(Predicate(fn.var, fn.op, fn.value) for fn in filter_nodes)

    echo = dict(base.dimensions or {})
    if bbox is not None:
        echo["bbox"] = {"lo": bbox.lo, "hi": bbox.hi}
    if preds:
        echo["filter"] = [(p.var, p.op, p.value) for p in preds]
    return Narrowing(particle_index=base.particle_index, bbox=bbox, predicates=preds,
                     dimensions=echo, total_particles=total, positions=positions)


def plan_pipeline(terminal, dry_run=False):
    """Plan (and unless dry_run, execute) one pipeline ending at `terminal`.

    `terminal` is normally a sink (render/save). For the no-sink dry run it may
    be any dangling leaf node, in which case there is no action to run and the
    call only reports the inferred plan.

    Returns a result dict: {kind, uri, steps, output, materialized}.
    Raises on a bad spec (with a clear message) — the caller reports per-sink.
    """
    chain = _chain(terminal)
    src = chain[0]
    sink = terminal if terminal.is_sink else None      # a dangling leaf has no sink
    middle = chain[1:-1] if sink is not None else chain[1:]
    steps = []

    source_uri = src.uri
    if _REMOTE.match(source_uri):
        local = _fetch_remote(source_uri)        # establish_connection + transfer
        steps.append(f"fetch {source_uri} -> {local}")
        source_uri = local

    # Inspect (metadata only) — static checks below run before any bulk read. A
    # glob uri makes info.timesteps (a series); timestep() selects one.
    info = inspect_file(source_uri, positions=src.positions)
    steps.append(f"inspect {source_uri}  ({len(info.variables)} vars, "
                 f"dims={info.dimensions or {}}"
                 f"{', %d timesteps' % len(info.timesteps) if info.timesteps else ''})")

    # Collect narrowing; projection lowers eagerly via subset (validates vars).
    region_nodes, subsample_nodes, filter_nodes = [], [], []
    timestep_nodes, pending_compress = [], []
    for node in middle:
        if node.kind in _NOT_YET:
            raise NotImplementedError(_NOT_YET[node.kind])
        if pending_compress and node.kind != "compress":
            raise NotImplementedError("narrowing after compress is not supported yet; "
                                      "put fields/region/subsample/filter/timestep before compress")
        if node.kind == "fields":
            info = subset(info, variables=list(node.keep))
            steps.append(f"project fields={list(node.keep)}")
        elif node.kind == "region":
            region_nodes.append(node)
            steps.append(f"region {dict((a, (lo, hi)) for a, lo, hi in node.ranges)}")
        elif node.kind == "subsample":
            subsample_nodes.append(node)
            steps.append(f"subsample {('uniform=' + str(node.uniform)) if node.uniform is not None else dict(node.per_axis)}")
        elif node.kind == "filter":
            filter_nodes.append(node)
            steps.append(f"filter {node.var} {node.op} {node.value}")
        elif node.kind == "timestep":
            timestep_nodes.append(node)
            steps.append(f"timestep {node.index}")
        elif node.kind == "compress":
            pending_compress.append(node)
            steps.append(f"compress {list(node.variables)} (error_bound={node.error_bound})")

    # Fuse region + subsample + filter into one Narrowing, add timestep, check it.
    narrowing = _build_narrowing(region_nodes, subsample_nodes, filter_nodes, info)
    if timestep_nodes:
        if len(timestep_nodes) > 1:
            raise ValueError("only one timestep() per pipeline")
        narrowing.timestep = timestep_nodes[0].index
    validate_narrowing(info, narrowing)
    fuse_bits = []
    if narrowing.grid_ranges:
        fuse_bits.append(f"grid_ranges={narrowing.dimensions['grid_ranges']}")
    if narrowing.particle_index is not None:
        fuse_bits.append(f"particles={narrowing.dimensions.get('particles')}")
    if narrowing.bbox is not None:
        fuse_bits.append(f"bbox={narrowing.dimensions.get('bbox')}")
    if narrowing.predicates:
        fuse_bits.append(f"filter={narrowing.dimensions.get('filter')}")
    if narrowing.timestep is not None:
        fuse_bits.append(f"timestep={narrowing.timestep}")
    if fuse_bits:
        steps.append("fuse -> " + ", ".join(fuse_bits))

    # The sink (the action). A dangling leaf has none — it can only dry-run.
    if sink is not None:
        sink_step = (f"render(cmap={sink.cmap})" if sink.kind == "render"
                     else f"save -> {sink.path}")
        steps.append(f"-> {sink_step}")
    else:
        steps.append(f"(no sink — chain ends at {terminal.kind}; nothing to materialize)")

    result = {"kind": (sink.kind if sink is not None else terminal.kind),
              "uri": src.uri, "steps": steps, "output": None, "materialized": False}
    if dry_run or sink is None:
        return result

    # Execute: materialize under the fused narrowing, compress, then the sink.
    loaded = materialize(info, narrowing)
    result["materialized"] = True
    for c in pending_compress:
        loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)

    if sink.kind == "render":
        _render(loaded, cmap=sink.cmap, opacity=sink.opacity)   # prints its URL
        result["output"] = "rendered (URL in output above)"
    else:  # save
        result["output"] = _save(loaded, sink.path)
    return result


def _save(loaded, path):
    """Minimal Phase-1 save: write the loaded arrays as an .npz. Persisting the
    compressed form (loaded.compressed_bytes) is a follow-up."""
    np.savez(path, **loaded.data)
    out = path if path.endswith(".npz") else path + ".npz"
    print(f"[save] wrote {len(loaded.data)} array(s) -> {out}")
    return out


def format_result(result):
    head = f"[{result['kind']}] {result['uri']}"
    body = "\n".join(f"    {s}" for s in result["steps"])
    tail = f"\n  output: {result['output']}" if result["output"] else ""
    mode = "" if result["materialized"] else "  (dry run — nothing materialized)"
    return f"{head}{mode}\n{body}{tail}"
