"""The interpreter/planner — it turns an AST chain into physical ops.

A spec builds AST nodes (dsl_forms); after exec, run_pipeline hands each
registered sink here. plan_pipeline walks the chain source->sink, inspects the
source for its schema, static-checks the request (raising BEFORE any bulk read),
lowers the forms onto the physical layer, and executes.

Form order is the promise; how we lower it is ours to choose — within what is
provably result-preserving. Every middle form is classified on two independent
axes:

  Pushdown (efficiency): STRUCTURAL cuts are knowable from metadata and fold
  into the one read (projection via `fields`; grid index crop via `region`;
  grid stride / leading particle row-stride via `subsample`). COMPUTED cuts
  need the data and run after the read (particle `region` bbox, `threshold`,
  demoted subsamples).

  Reordering (correctness): every form here is an ABSOLUTE cut (a fixed rule —
  ANDed cuts commute freely) EXCEPT `subsample`, the lone RELATIVE sampler
  ("every Nth of what's left"): its result depends on the element set at the
  moment it runs, so no cut may slide across it. Concretely: a particle
  subsample is pushed into the read ONLY if no computed cut precedes it in the
  spec; otherwise it is demoted to a post-read op at its written position.
  Grid subsample composes with grid crops into one [a:b:k] per axis, and grid
  `threshold` NaN-masks in place (shape-preserving), so on grids everything
  commutes and full pushdown is always safe.

The lowered plan is one Narrowing: the pushdown layer (project + grid_ranges /
particle_index) plus `post_ops`, the computed cuts in written order, applied by
materialize after the read.
"""

import os
import re

import numpy as np

from dsl_forms.nodes import SourceNode, upstream_of
from my_inspect import inspect_file
from my_load import materialize
from my_compress import compress as _compress
from my_render import render as _render
from adapters import adapter_capabilities
from narrowing import (Narrowing, AxisRange, Predicate, BBox,
                       RowMask, RowSample, VoxelMask,
                       narrowing_from_dimensions, validate_narrowing)

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
    returning the local path."""
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
    'grid'. Decides how region/subsample/threshold lower."""
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


def _grid_ranges(region_nodes, subsample_nodes, info):
    """Fuse grid region crops + subsample strides into one [a:b:k] per axis.
    Both are structural index-space directives on the ORIGINAL grid, so they
    compose order-free by convention (the stride anchors at the crop start)."""
    if not (region_nodes or subsample_nodes):
        return None
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
    return ranges


def _bbox_of(region_node):
    """A particle region: a world-coordinate box on the position variables."""
    lo, hi = [None, None, None], [None, None, None]
    for axis, a, b in region_node.ranges:
        if axis not in _AXIS_INDEX:
            raise ValueError(f"region axis {axis!r} not one of x, y, z")
        i = _AXIS_INDEX[axis]
        lo[i], hi[i] = a, b
    return BBox(lo=tuple(lo), hi=tuple(hi))


def plan_pipeline(terminal, dry_run=False):
    """Plan (and unless dry_run, execute) one pipeline ending at `terminal`.

    `terminal` is normally a sink (render/save). For the no-sink dry run it may
    be any dangling leaf node, in which case there is no action to run and the
    call only reports the inferred plan.

    A remote source (ssh:// or user@host:) dispatches by SITE: if the chain has
    a narrowing prefix and the remote is reachable, the prefix executes next to
    the data (via vislang_exec) and only the reduced result crosses the wire
    (see remote_reduce.py); otherwise we fall back to the whole-file fetch and
    plan locally as before. Render/save always run locally.

    Returns a result dict: {kind, uri, steps, output, materialized}.
    Raises on a bad spec (with a clear message) — the caller reports per-sink.
    """
    chain = _chain(terminal)
    src = chain[0]
    sink = terminal if terminal.is_sink else None
    middle = chain[1:-1] if sink is not None else chain[1:]

    if _REMOTE.match(src.uri):
        return _plan_remote(src, middle, sink, terminal, dry_run)
    return _plan_local(src, middle, sink, terminal, dry_run, src.uri, [])


def _plan_remote(src, middle, sink, terminal, dry_run):
    """Site dispatch for a remote source. v1 cost gate: any narrowing present +
    a reachable remote -> reduce remotely (a threshold can't run locally
    without the whole file anyway; a geometric cut ships fewer bytes by
    construction). No narrowing -> the whole file must cross the wire either
    way, so fetch it. VISLANG_REMOTE=off forces the fetch; =force errors
    instead of falling back (surfacing what broke)."""
    steps = []
    mode = os.environ.get("VISLANG_REMOTE", "auto")
    has_narrowing = any(n.kind in ("fields", "region", "subsample", "threshold")
                        for n in middle)

    if dry_run or sink is None:
        steps.append(f"remote source {src.uri}")
        steps.append("plan: reduce on the remote (narrowing prefix next to the "
                     "data), pull the result, run the sink locally"
                     if has_narrowing and mode != "off" else
                     "plan: fetch the whole file, then plan locally")
        steps.append("(dry run — nothing probed, nothing read; schema checks "
                     "run at execution)")
        for n in middle:
            steps.append(f"  … {n.kind}")
        steps.append(f"(no sink — chain ends at {terminal.kind}; nothing to materialize)"
                     if sink is None else f"-> {sink.kind}")
        return {"kind": (sink.kind if sink is not None else terminal.kind),
                "uri": src.uri, "steps": steps, "output": None, "materialized": False}

    if mode != "off" and has_narrowing:
        from remote_reduce import remote_reduce, RemoteUnavailable
        try:
            loaded, rsteps = remote_reduce(src, middle)
            steps += rsteps
            pending_compress = [n for n in middle if n.kind == "compress"]
            for c in pending_compress:
                steps.append(f"compress {list(c.variables)} "
                             f"(error_bound={c.error_bound}, local)")
            result = {"kind": sink.kind, "uri": src.uri, "steps": steps,
                      "output": None, "materialized": True}
            steps.append(f"-> {sink.kind} (local)")
            return _finish(loaded, pending_compress, sink, result)
        except RemoteUnavailable as e:
            if mode == "force":
                raise RuntimeError(f"VISLANG_REMOTE=force but remote reduce is "
                                   f"unavailable: {e}")
            steps.append(f"remote reduce unavailable ({e}) — falling back to "
                         f"whole-file fetch")
    elif not has_narrowing:
        steps.append("no narrowing forms — the whole file crosses the wire "
                     "either way (cost gate: fetch)")

    local = _fetch_remote(src.uri)               # establish_connection + transfer
    steps.append(f"fetch {src.uri} -> {local}")
    return _plan_local(src, middle, sink, terminal, dry_run, local, steps)


def _finish(loaded, pending_compress, sink, result):
    """The local suffix shared by both sites: compress, then the sink."""
    for c in pending_compress:
        loaded = _compress(loaded, list(c.variables), c.error_bound, c.mode)
    if sink.kind == "render":
        _render(loaded, cmap=sink.cmap, opacity=sink.opacity)   # prints its URL
        result["output"] = "rendered (URL in output above)"
    else:  # save
        result["output"] = _save(loaded, sink.path)
    return result


def _plan_local(src, middle, sink, terminal, dry_run, source_uri, steps):
    # Inspect (metadata only) — static checks below run before any bulk read.
    info = inspect_file(source_uri, positions=src.positions)
    steps.append(f"inspect {source_uri}  ({len(info.variables)} vars, "
                 f"dims={info.dimensions or {}})")

    modality = _modality(info)
    caps = adapter_capabilities(info)
    slab = "pushdown" if caps["strided_read"] else "in-memory slice"

    # --- classify + lower each middle form, in written order -----------------
    keep = None                    # which fields to project (None = keep all)
    grid_region_nodes = []         # grid crops, saved up to fuse into the read later
    grid_subsample_nodes = []      # grid strides, same idea
    post_ops = []                  # filters that must run AFTER reading, in order
    pushdown_subsample = None      # a particle subsample safe to push into the read
    seen_computed = False          # "have we hit an order-sensitive filter yet?"
    pending_compress = []          # compress form queued for the very end


    for node in middle:
        if pending_compress and node.kind != "compress":
            raise NotImplementedError("narrowing after compress is not supported; "
                                      "put fields/region/subsample/threshold before compress")
        if node.kind == "fields":
            base = keep if keep is not None else list(info.variables)
            invalid = set(node.keep) - set(base)
            if invalid:
                raise ValueError(f"fields: {sorted(invalid)} not available here; "
                                 f"have {sorted(base)}")
            keep = list(node.keep)
            steps.append(f"project fields={keep}")
        elif node.kind == "region":
            if modality == "grid":
                grid_region_nodes.append(node)   # structural index crop
                steps.append(f"region {dict((a, (lo, hi)) for a, lo, hi in node.ranges)} "
                             f"({slab})")
            else:                                # computed world-coord bbox
                post_ops.append(RowMask(bbox=_bbox_of(node)))
                seen_computed = True
                steps.append(f"region {dict((a, (lo, hi)) for a, lo, hi in node.ranges)} "
                             f"(bbox mask, post-read)")
        elif node.kind == "subsample":
            if modality == "grid":
                grid_subsample_nodes.append(node)  # structural stride
                what = (f"uniform={node.uniform}" if node.uniform is not None
                        else str(dict(node.per_axis)))
                steps.append(f"subsample {what} ({slab})")
            else:
                if node.uniform is None:
                    raise ValueError("per-axis subsample doesn't apply to point data; "
                                     "use a single factor, e.g. subsample(d, 0.1)")
                if not seen_computed and pushdown_subsample is None:
                    pushdown_subsample = node      # safe: no computed cut precedes it
                    steps.append(f"subsample uniform={node.uniform} (pushdown)")
                else:
                    # A computed cut (or an earlier sampler) precedes it: pushing
                    # it into the read would silently reorder. Run it post-read,
                    # at its written position.
                    post_ops.append(RowSample(factor=node.uniform))
                    steps.append(f"subsample uniform={node.uniform} "
                                 f"(post-read: follows a computed cut)")
        elif node.kind == "threshold":
            pred = Predicate(node.var, node.op, node.value)
            if modality == "grid":
                post_ops.append(VoxelMask(predicates=(pred,)))  # NaN-fill, commutes
                steps.append(f"threshold {node.var} {node.op} {node.value} "
                             f"(voxel NaN-mask)")
            else:
                post_ops.append(RowMask(predicates=(pred,)))
                seen_computed = True
                steps.append(f"threshold {node.var} {node.op} {node.value} (row mask)")
        elif node.kind == "compress":
            pending_compress.append(node)
            steps.append(f"compress {list(node.variables)} (error_bound={node.error_bound})")
        else:
            raise ValueError(f"unknown form {node.kind!r} in pipeline")

    # --- fuse the structural layer into one Narrowing ------------------------
    if modality == "grid":
        ranges = _grid_ranges(grid_region_nodes, grid_subsample_nodes, info)
        echo = {}
        if ranges:
            echo["grid_ranges"] = [(r.start, r.stop, r.step) for r in ranges]
        narrowing = Narrowing(grid_ranges=ranges, dimensions=echo,
                              positions=getattr(info, "positions", None))
    else:
        total = (info.dimensions or {}).get("particles", 0)
        dims = None
        if pushdown_subsample is not None:
            f = pushdown_subsample.uniform
            dims = {"particles": slice(None, None, f) if isinstance(f, int) else f}
        narrowing = narrowing_from_dimensions(dims, total,
                                              getattr(info, "positions", None))
        echo = dict(narrowing.dimensions or {})

    narrowing.post_ops = tuple(post_ops)
    narrowing.project = tuple(keep) if keep is not None else None
    if keep is not None:
        echo["project"] = keep
    if post_ops:
        echo["post_ops"] = [type(op).__name__ for op in post_ops]
    narrowing.dimensions = echo

    # Static check against the FULL schema (a threshold may read a var outside
    # the projection), before any bulk read.
    validate_narrowing(info, narrowing)

    fuse_bits = []
    if narrowing.grid_ranges:
        fuse_bits.append(f"grid_ranges={echo['grid_ranges']}")
    if narrowing.particle_index is not None:
        fuse_bits.append(f"particles={echo.get('particles')}")
    if narrowing.project is not None:
        fuse_bits.append(f"project={list(narrowing.project)}")
    if post_ops:
        fuse_bits.append(f"post_ops={echo['post_ops']}")
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
              "uri": src.uri, "steps": steps, "output": None, "materialized": False,
              "narrowing": narrowing}   # the lowered plan, for introspection (explain.py)
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
