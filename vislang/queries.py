"""Query tools for inspecting VTK data objects.

Error conventions
-----------------
All public functions that return a string on success also return a string on
failure.  Error strings are distinguishable from valid results because they
start with the prefix ``"Error: "``.  Callers that need to detect failures
programmatically can check ``result.startswith("Error: ")``.

Functions that return structured data (lists, dicts) on success return an
empty collection (``[]``, ``{}``) or a dict with an ``"error"`` key on
failure — not a string.  See ``sample_points`` and ``get_spatial_extent_dict``
for examples.
"""

import vtk
import numpy as np
from vtk.util.numpy_support import vtk_to_numpy

from vislang._vtk_introspect import find_field_array


def _classify_distribution(values):
    """Classify distribution shape as uniform, skewed, bimodal, or sparse.

    Args:
        values: 1-D numpy array of sampled values.

    Returns:
        One of "uniform", "skewed", "bimodal", "sparse".
    """
    if len(values) == 0:
        return "sparse"

    # Check for sparse: >50% zeros (or near-zero relative to range)
    vmin, vmax = values.min(), values.max()
    span = vmax - vmin
    if span == 0:
        return "uniform"
    zero_fraction = np.mean(np.abs(values) < span * 1e-6)
    if zero_fraction > 0.5:
        return "sparse"

    # Skewness check: compare mean vs median
    mean = np.mean(values)
    median = np.median(values)
    std = np.std(values)
    if std > 0:
        skewness = (mean - median) / std
        if abs(skewness) > 0.3:
            return "skewed"

    # Bimodal check: build a coarse histogram and look for two peaks
    # with a valley between them
    counts, _ = np.histogram(values, bins=30)
    # Smooth to reduce noise
    smoothed = np.convolve(counts, [0.2, 0.6, 0.2], mode="same")
    # Find local maxima
    peak_indices = []
    threshold = smoothed.max() * 0.2
    for i in range(1, len(smoothed) - 1):
        if smoothed[i] > smoothed[i - 1] and smoothed[i] > smoothed[i + 1] and smoothed[i] > threshold:
            peak_indices.append(i)
    # Check that there is a clear valley between at least two peaks
    if len(peak_indices) >= 2:
        for a in range(len(peak_indices)):
            for b in range(a + 1, len(peak_indices)):
                valley = min(smoothed[peak_indices[a]:peak_indices[b] + 1])
                peak_min = min(smoothed[peak_indices[a]], smoothed[peak_indices[b]])
                if valley < peak_min * 0.6:
                    return "bimodal"

    return "uniform"


def _fmt(val, precision=6):
    """Format a numeric value concisely."""
    return f"{val:.{precision}g}"


def _scalar_percentile_stats(vals):
    """Compute percentile stats dict for a 1-D float64 array."""
    return {
        "min": float(vals.min()),
        "max": float(vals.max()),
        "p1":  float(np.percentile(vals, 1)),
        "p25": float(np.percentile(vals, 25)),
        "p50": float(np.percentile(vals, 50)),
        "p75": float(np.percentile(vals, 75)),
        "p99": float(np.percentile(vals, 99)),
        "mean": float(np.mean(vals)),
        "std":  float(np.std(vals)),
    }


def get_rich_field_stats(data, max_sample=100000, field=None):
    """Compute rich per-field statistics for all arrays in a dataset.

    Returns a list of dicts, one per field, each containing:
      name, location ("point"/"cell"), components, dtype,
      min, max, p1, p25, p50, p75, p99, mean, std, shape,
      and for vectors: magnitude stats + per-component stats.

    Args:
        data: VTK data object.
        max_sample: Maximum number of values to sample for percentiles.
        field: If given, compute stats for this field only (faster).
    """
    if data is None:
        return []

    results = []

    for location, field_data, n_items in [
        ("point", data.GetPointData(), data.GetNumberOfPoints()),
        ("cell", data.GetCellData(), data.GetNumberOfCells()),
    ]:
        for i in range(field_data.GetNumberOfArrays()):
            arr = field_data.GetArray(i)
            name = field_data.GetArrayName(i)
            if arr is None or name is None:
                continue
            if field is not None and name != field:
                continue

            ncomp = arr.GetNumberOfComponents()
            n = arr.GetNumberOfTuples()
            dtype = arr.GetDataTypeAsString()

            if n == 0:
                continue

            np_arr = vtk_to_numpy(arr)
            if np_arr is None:
                continue

            step = max(1, n // max_sample)
            sample = np_arr[::step] if step > 1 else np_arr

            info = {
                "name": name,
                "location": location,
                "components": ncomp,
                "dtype": dtype,
                "tuples": n,
            }

            if ncomp == 1:
                vals = sample.astype(np.float64).ravel()
                rng = arr.GetRange()
                stats = _scalar_percentile_stats(vals)
                stats["min"] = rng[0]
                stats["max"] = rng[1]
                info.update(stats)
                info["shape"] = _classify_distribution(vals)
            else:
                sample_f = sample.astype(np.float64)
                mag = np.linalg.norm(sample_f, axis=1)
                mag_stats = _scalar_percentile_stats(mag)
                mag_stats["shape"] = _classify_distribution(mag)
                info["magnitude"] = mag_stats
                info["components_stats"] = []
                for c in range(ncomp):
                    cvals = sample_f[:, c]
                    rng = arr.GetRange(c)
                    cs = _scalar_percentile_stats(cvals)
                    cs["min"] = rng[0]
                    cs["max"] = rng[1]
                    cs["component"] = c
                    info["components_stats"].append(cs)

            results.append(info)
            if field is not None:
                return results  # found the one we wanted

    return results


def format_rich_field_stats(stats_list, data=None):
    """Format the output of get_rich_field_stats into a readable string."""
    if not stats_list:
        return "No fields found."

    lines = []
    for s in stats_list:
        name = s["name"]
        loc = s["location"]
        ncomp = s["components"]

        if ncomp == 1:
            shape_flag = s["shape"]
            lines.append(
                f"  {name} ({loc}, {s['dtype']}): "
                f"[{_fmt(s['min'])}, {_fmt(s['max'])}]  "
                f"shape={shape_flag}"
            )
            lines.append(
                f"    p1={_fmt(s['p1'])}  p25={_fmt(s['p25'])}  "
                f"p50={_fmt(s['p50'])}  p75={_fmt(s['p75'])}  "
                f"p99={_fmt(s['p99'])}"
            )
            lines.append(
                f"    mean={_fmt(s['mean'])}  std={_fmt(s['std'])}"
            )
            # Flag potentially surface-confined sparse fields
            if shape_flag == "sparse" and data is not None:
                if (hasattr(data, "GetDimensions")
                        and data.GetClassName() == "vtkStructuredGrid"):
                    dims = [0, 0, 0]
                    data.GetDimensions(dims)
                    nz = dims[2]
                    if nz > 1 and s.get("tuples", 0) > 0:
                        # Estimate non-zero fraction from percentiles
                        # If p75=0 but p99 > 0, most values are zero
                        if s["p75"] == 0 and s["p99"] != 0:
                            lines.append(
                                f"    [Sparse: may be surface-confined — "
                                f"p75=0 but p99≠0 on a {dims[2]}-layer grid. "
                                f"Try describe_data(node='ground_node', field='{name}') "
                                f"after extracting ground with extract_grid.]"
                            )
        else:
            lines.append(
                f"  {name} ({loc}, {s['dtype']}, {ncomp} components):"
            )
            mag = s.get("magnitude", {})
            if mag:
                lines.append(
                    f"    |magnitude|: [{_fmt(mag['min'])}, {_fmt(mag['max'])}]  "
                    f"shape={mag['shape']}"
                )
                lines.append(
                    f"      p1={_fmt(mag['p1'])}  p25={_fmt(mag['p25'])}  "
                    f"p50={_fmt(mag['p50'])}  p75={_fmt(mag['p75'])}  "
                    f"p99={_fmt(mag['p99'])}"
                )
                lines.append(
                    f"      mean={_fmt(mag['mean'])}  std={_fmt(mag['std'])}"
                )
            for cs in s.get("components_stats", []):
                lines.append(
                    f"    component {cs['component']}: "
                    f"[{_fmt(cs['min'])}, {_fmt(cs['max'])}]  "
                    f"p50={_fmt(cs['p50'])}"
                )

    return "\n".join(lines)


def get_histogram(data, field, bins=20):
    """Generate a text histogram with ASCII bars."""
    if data is None:
        return "Error: No data available"

    arr, _loc = find_field_array(data, field)
    if arr is None:
        return f"Error: Field '{field}' not found"

    rng = arr.GetRange()
    if rng[0] == rng[1]:
        return f"Field '{field}' is constant: {rng[0]}"

    n = arr.GetNumberOfTuples()
    vals = vtk_to_numpy(arr).astype(np.float64).ravel()
    counts_arr, bin_edges = np.histogram(vals, bins=bins, range=(rng[0], rng[1]))
    counts = counts_arr.tolist()

    max_count = max(counts)
    bar_width = 40

    lines = [f"Histogram of '{field}' ({n} values, {bins} bins):"]
    lines.append(f"Range: [{rng[0]:.6g}, {rng[1]:.6g}]")
    lines.append("")

    for i in range(bins):
        lo = bin_edges[i]
        hi = bin_edges[i + 1]
        bar_len = int(counts[i] / max_count * bar_width) if max_count > 0 else 0
        bar = "█" * bar_len
        pct = counts[i] / n * 100
        lines.append(f"  [{lo:10.4g}, {hi:10.4g}) {bar:40s} {counts[i]:>8d} ({pct:5.1f}%)")

    # Detect sparse distribution: >60% of mass in first or last 20% of bins
    edge_bins = max(1, bins // 5)
    low_mass = sum(counts[:edge_bins]) / n if n > 0 else 0
    high_mass = sum(counts[-edge_bins:]) / n if n > 0 else 0
    if low_mass > 0.6 or high_mass > 0.6:
        lines.append("")
        lines.append(
            "Tip: this field appears sparse (most mass at one end). "
            "For suggest_isosurface, consider threshold()ing "
            "to the feature region first."
        )

    return "\n".join(lines)


def get_spatial_extent_dict(data, field, min_val, max_val):
    """Return structured bounding box where field is within given range.

    Returns a dict with keys: 'xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax',
    'count', 'total', or an 'error' key if the computation could not be done.
    """
    if data is None:
        return {"error": "Error: No data available"}

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        return {"error": f"Error: Field '{field}' not found"}

    n = arr.GetNumberOfTuples()
    vals = vtk_to_numpy(arr).astype(np.float64).ravel()
    mask = (vals >= min_val) & (vals <= max_val)
    count = int(mask.sum())

    if count == 0:
        return {"error": f"No points where {field} is in [{min_val}, {max_val}]"}

    pts_np = vtk_to_numpy(data.GetPoints().GetData()).reshape(-1, 3)
    matching_pts = pts_np[mask]
    xmin, ymin, zmin = matching_pts.min(axis=0)
    xmax, ymax, zmax = matching_pts.max(axis=0)

    return {
        "xmin": float(xmin), "xmax": float(xmax),
        "ymin": float(ymin), "ymax": float(ymax),
        "zmin": float(zmin), "zmax": float(zmax),
        "count": count,
        "total": int(n),
    }


def get_spatial_extent(data, field, min_val, max_val):
    """Find bounding box where field is within given range."""
    result = get_spatial_extent_dict(data, field, min_val, max_val)
    if "error" in result:
        return result["error"]

    xmin, xmax = result["xmin"], result["xmax"]
    ymin, ymax = result["ymin"], result["ymax"]
    zmin, zmax = result["zmin"], result["zmax"]
    count = result["count"]
    n = result["total"]

    pct = count / n * 100
    pct_str = f"{pct:.4f}%" if pct < 0.1 else f"{pct:.1f}%"
    lines = [
        f"Spatial extent where {field} in [{min_val:.4g}, {max_val:.4g}]:",
        f"  {count} points ({pct_str} of total)",
        f"  X: [{xmin:.2f}, {xmax:.2f}]",
        f"  Y: [{ymin:.2f}, {ymax:.2f}]",
        f"  Z: [{zmin:.2f}, {zmax:.2f}]",
    ]

    # For structured grids, also report grid index bounds
    class_name = data.GetClassName()
    if class_name in ("vtkStructuredGrid", "vtkImageData", "vtkRectilinearGrid"):
        arr, _loc = find_field_array(data, field)
        if arr is not None:
            vals = vtk_to_numpy(arr).astype(np.float64).ravel()
            mask = (vals >= min_val) & (vals <= max_val)
            matching_ids = np.where(mask)[0]
            i0, i1, j0, j1, k0, k1 = data.GetExtent()
            nx = i1 - i0 + 1
            ny = j1 - j0 + 1
            loc_i = matching_ids % nx
            loc_j = (matching_ids // nx) % ny
            loc_k = matching_ids // (nx * ny)
            abs_i = loc_i + i0
            abs_j = loc_j + j0
            abs_k = loc_k + k0
            lines.append(
                f"  Grid indices (for extract_grid VOI): "
                f"i=[{abs_i.min()}, {abs_i.max()}], "
                f"j=[{abs_j.min()}, {abs_j.max()}], "
                f"k=[{abs_k.min()}, {abs_k.max()}]"
            )

    return "\n".join(lines)


def sample_point(data, x, y, z, fields=None):
    """Sample field values at the nearest point to (x, y, z).

    Returns values of all fields (or specified fields) at the closest grid point.
    """
    if data is None:
        return "Error: No data available"

    # Find closest point
    locator = None
    try:
        import vtk
        locator = vtk.vtkPointLocator()
        locator.SetDataSet(data)
        locator.BuildLocator()
        pt_id = locator.FindClosestPoint([x, y, z])
    except Exception:
        # Fallback: brute force on a subset
        pt_id = 0
        best_dist = float("inf")
        n = data.GetNumberOfPoints()
        step = max(1, n // 100000)
        for i in range(0, n, step):
            pt = data.GetPoint(i)
            d = (pt[0] - x) ** 2 + (pt[1] - y) ** 2 + (pt[2] - z) ** 2
            if d < best_dist:
                best_dist = d
                pt_id = i

    if pt_id < 0:
        return f"Error: No point found near ({x}, {y}, {z})"

    actual_pt = data.GetPoint(pt_id)
    lines = [
        f"Sample at ({x}, {y}, {z}):",
        f"  Nearest point: ({actual_pt[0]:.2f}, {actual_pt[1]:.2f}, {actual_pt[2]:.2f})",
        f"  Point ID: {pt_id}",
    ]

    pd = data.GetPointData()
    target_fields = fields if fields else [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]

    for field_name in target_fields:
        arr = pd.GetArray(field_name)
        if arr is None:
            lines.append(f"  {field_name}: not found")
            continue
        ncomp = arr.GetNumberOfComponents()
        if ncomp == 1:
            lines.append(f"  {field_name}: {arr.GetValue(pt_id):.6g}")
        else:
            vals = arr.GetTuple(pt_id)
            lines.append(f"  {field_name}: ({', '.join(f'{v:.6g}' for v in vals)})")

    return "\n".join(lines)


def sample_points(data, points, fields=None):
    """Sample field values at multiple points at once.

    Builds a vtkPointLocator once and probes all points efficiently.
    Returns a list of dicts, one per input point, with coordinates and
    field values. Points outside the dataset bounds are flagged with
    ``outside_bounds=True`` and field values set to None.

    Args:
        data: VTK dataset to probe.
        points: Sequence of (x, y, z) tuples.
        fields: Optional list of field names to return. If None, all
                point-data arrays are returned.

    Returns:
        List of dicts with keys:
          - ``query``: the requested (x, y, z)
          - ``nearest``: the actual closest grid point (x, y, z)
          - ``point_id``: integer index into the dataset
          - ``outside_bounds``: True when the query point is outside the
            dataset bounding box
          - one key per field with scalar or tuple value (or None)
    """
    if data is None:
        return []

    # Build locator once for all points
    try:
        locator = vtk.vtkPointLocator()
        locator.SetDataSet(data)
        locator.BuildLocator()
        use_locator = True
    except Exception:
        use_locator = False
        locator = None

    # Determine dataset bounding box for out-of-bounds detection
    bounds = data.GetBounds()  # (xmin, xmax, ymin, ymax, zmin, zmax)

    pd = data.GetPointData()
    if fields is not None:
        target_fields = list(fields)
    else:
        target_fields = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]

    # Cache array objects so we don't look them up per point
    arrays = {}
    for fname in target_fields:
        arrays[fname] = pd.GetArray(fname)

    results = []
    for qpt in points:
        x, y, z = float(qpt[0]), float(qpt[1]), float(qpt[2])

        outside = (
            x < bounds[0] or x > bounds[1]
            or y < bounds[2] or y > bounds[3]
            or z < bounds[4] or z > bounds[5]
        )

        if use_locator:
            pt_id = locator.FindClosestPoint([x, y, z])
        else:
            # Brute-force fallback (sampled subset)
            pt_id = 0
            best_dist = float("inf")
            n = data.GetNumberOfPoints()
            step = max(1, n // 100000)
            for i in range(0, n, step):
                pt = data.GetPoint(i)
                d = (pt[0] - x) ** 2 + (pt[1] - y) ** 2 + (pt[2] - z) ** 2
                if d < best_dist:
                    best_dist = d
                    pt_id = i

        entry = {
            "query": (x, y, z),
            "outside_bounds": outside,
        }

        if pt_id < 0:
            entry["nearest"] = None
            entry["point_id"] = -1
            for fname in target_fields:
                entry[fname] = None
        else:
            actual = data.GetPoint(pt_id)
            entry["nearest"] = (actual[0], actual[1], actual[2])
            entry["point_id"] = int(pt_id)

            for fname in target_fields:
                arr = arrays[fname]
                if arr is None:
                    entry[fname] = None
                else:
                    ncomp = arr.GetNumberOfComponents()
                    if ncomp == 1:
                        entry[fname] = arr.GetValue(pt_id)
                    else:
                        entry[fname] = tuple(arr.GetTuple(pt_id))

        results.append(entry)

    return results


def format_sample_points(results):
    """Format the output of sample_points() as a human-readable string."""
    if not results:
        return "Error: No results"

    lines = [f"Batch point sample: {len(results)} point(s)"]
    for i, r in enumerate(results):
        qx, qy, qz = r["query"]
        lines.append(f"\nPoint {i + 1}: query=({qx}, {qy}, {qz})")
        if r.get("outside_bounds"):
            lines.append("  [outside dataset bounds]")
        if r.get("nearest") is None:
            lines.append("  No nearest point found")
            continue
        nx, ny, nz = r["nearest"]
        lines.append(f"  Nearest grid point: ({nx:.4g}, {ny:.4g}, {nz:.4g})")
        lines.append(f"  Point ID: {r['point_id']}")
        for key, val in r.items():
            if key in ("query", "nearest", "point_id", "outside_bounds"):
                continue
            if val is None:
                lines.append(f"  {key}: not found")
            elif isinstance(val, tuple):
                lines.append(f"  {key}: ({', '.join(f'{v:.6g}' for v in val)})")
            else:
                lines.append(f"  {key}: {val:.6g}")

    return "\n".join(lines)


def suggest_isosurface(data, field, num_values=3):
    """Suggest good isosurface values based on the field histogram.

    Finds values at histogram peaks (common values that form coherent
    surfaces) and valleys (transitions between regions).

    Note:
        For sparse fields (e.g. a fire plume that is a tiny fraction of the domain),
        results will be poor on the full dataset because gradient-based analysis is
        dominated by the background.  Use get_histogram() to check: if most histogram
        mass is concentrated in the first or last few bins, threshold() to the feature
        region first, then call suggest_isosurface on the thresholded node.
    """
    if data is None:
        return "Error: No data available"

    arr, _loc = find_field_array(data, field)
    if arr is None:
        available = [data.GetPointData().GetArrayName(i)
                     for i in range(data.GetPointData().GetNumberOfArrays())]
        return f"Error: Field '{field}' not found. Available: {available}"

    rng = arr.GetRange()
    if rng[0] == rng[1]:
        return f"Field '{field}' is constant: {rng[0]}"

    # Build histogram using numpy
    n = arr.GetNumberOfTuples()
    all_values = vtk_to_numpy(arr)
    step = max(1, n // 50000)
    values = all_values[::step]

    bins = 100
    counts, bin_edges = np.histogram(values, bins=bins, range=(rng[0], rng[1]))
    bin_centers = 0.5 * (bin_edges[:-1] + bin_edges[1:])

    total = counts.sum()
    if total == 0:
        return f"Error: No values sampled for '{field}'"

    # Find significant gradient changes (transitions between regions)
    # These make good isosurface values
    lo_edge = rng[0] + 0.05 * (rng[1] - rng[0])
    hi_edge = rng[0] + 0.95 * (rng[1] - rng[0])
    gradients = []
    for i in range(1, bins - 1):
        val = float(bin_centers[i])
        if val < lo_edge or val > hi_edge:
            continue
        grad = abs(int(counts[i + 1]) - int(counts[i - 1]))
        gradients.append((grad, val, int(counts[i])))

    # Sort by gradient magnitude (steepest transitions first)
    gradients.sort(reverse=True)

    # Pick top values that are well-separated
    suggested = []
    min_separation = (rng[1] - rng[0]) / (num_values * 2)
    for grad, val, count in gradients:
        if len(suggested) >= num_values:
            break
        if all(abs(val - s) > min_separation for s in suggested):
            suggested.append(round(val, 6))

    suggested.sort()

    lines = [f"Suggested isosurface values for '{field}':"]
    lines.append(f"  Range: [{rng[0]:.6g}, {rng[1]:.6g}]")
    lines.append("")
    lines.append(f"  Gradient-based (transition points): {suggested}")
    lines.append(f"  Percentile-based:")
    for p in [25, 50, 75, 90, 95, 99]:
        lines.append(f"    p{p}: {np.percentile(values, p):.6g}")
    lines.append("")
    lines.append(f"  Usage: filter(\"vtkContourFilter\", input=node,")
    lines.append(f"    ContourBy=\"{field}\", Isosurfaces={suggested})")

    return "\n".join(lines)


def get_ground_z(data, x, y, layers=True):
    """Return the Z coordinate at (x, y) for the lowest layer of a structured grid.

    Finds the point in the iz=0 layer of the structured grid that is nearest
    to (x, y) in the XY plane, then reports its Z coordinate and the Z values
    at the first few layers above it.

    This is useful for any 3D structured grid where the Z coordinate of the
    bottom layer varies with (x, y) — for example terrain-following grids,
    curvilinear meshes, or layered volume data.

    For non-structured-grid data (e.g. vtkPolyData, vtkUnstructuredGrid),
    returns an informative error message.

    Args:
        data: VTK structured grid or image data object.
        x: X coordinate to query.
        y: Y coordinate to query.
        layers: If True (default), include z-values for the first 10 layers.
            If False, return only the ground z value (faster to parse).
    """
    if data is None:
        return "Error: No data available"

    dims = [0, 0, 0]
    if not hasattr(data, "GetDimensions"):
        data_type = type(data).__name__
        return (
            f"Error: get_ground_z requires a structured grid (vtkStructuredGrid "
            f"or vtkImageData), but got {data_type}. "
            "Use get_spatial_extent() to find the Z range of your data instead."
        )
    data.GetDimensions(dims)
    nx, ny, nz = dims

    if nx == 0 or ny == 0 or nz == 0:
        return "Error: Structured grid has zero-size dimension(s)"

    # Find closest point at ground level (iz=0)
    best_dist = float("inf")
    best_pt = None
    best_ix = best_iy = 0

    # Coarse pass to find the nearest ground point
    for iy in range(0, ny, max(1, ny // 50)):
        for ix in range(0, nx, max(1, nx // 50)):
            idx = iy * nx + ix  # iz=0
            pt = data.GetPoint(idx)
            d = (pt[0] - x) ** 2 + (pt[1] - y) ** 2
            if d < best_dist:
                best_dist = d
                best_pt = pt
                best_ix = ix
                best_iy = iy

    # Refine search around the best coarse point
    for iy in range(max(0, best_iy - 15), min(ny, best_iy + 15)):
        for ix in range(max(0, best_ix - 15), min(nx, best_ix + 15)):
            idx = iy * nx + ix
            pt = data.GetPoint(idx)
            d = (pt[0] - x) ** 2 + (pt[1] - y) ** 2
            if d < best_dist:
                best_dist = d
                best_pt = pt
                best_ix = ix
                best_iy = iy

    if best_pt is None:
        return f"Error: Could not find a grid point near ({x}, {y})"

    ground_z = best_pt[2]

    if not layers:
        return f"Ground z = {ground_z:.1f}"

    # Get z-values at increasing layers above this xy location
    z_values = []
    for iz in range(min(nz, 10)):
        idx = iz * nx * ny + best_iy * nx + best_ix
        pt = data.GetPoint(idx)
        z_values.append((iz, pt[2]))

    lines = [
        f"Ground z = {ground_z:.1f}",
        f"",
        f"Z at ({x}, {y}):",
        f"  Nearest grid point (iz=0): ({best_pt[0]:.1f}, {best_pt[1]:.1f})",
        f"  Z at iz=0 (lowest layer): {ground_z:.1f}",
        f"  Z values at increasing layers:",
    ]
    for iz, z in z_values:
        lines.append(f"    iz={iz}: z={z:.1f}")

    # Check for terrain-following grid
    sample_zs = [data.GetPoint(iy * nx + ix)[2]
                 for iy in range(0, ny, max(1, ny // 10))
                 for ix in range(0, nx, max(1, nx // 10))]
    if np.std(sample_zs) > 1.0:
        lines.append("")
        lines.append(f"Note: Ground z varies significantly (std={np.std(sample_zs):.1f}) — "
                     "this is a terrain-following grid.")
        lines.append("Extract the ground layer by grid index, not by spatial z bound:")
        lines.append(f"  extract_grid(input=data, VOI=[0, {nx-1}, 0, {ny-1}, 0, 0])")

    return "\n".join(lines)


def sample_line(data, point1, point2, resolution=100):
    """Sample a dataset along a line between two points using vtkProbeFilter.

    Args:
        data: VTK data object to sample from.
        point1: (x, y, z) start point.
        point2: (x, y, z) end point.
        resolution: Number of sample points along the line.

    Returns:
        The vtkPolyData output of the probe filter with sampled field values.
    """
    line = vtk.vtkLineSource()
    line.SetPoint1(*point1)
    line.SetPoint2(*point2)
    line.SetResolution(resolution)
    line.Update()

    probe = vtk.vtkProbeFilter()
    probe.SetInputConnection(line.GetOutputPort())
    probe.SetSourceData(data)
    probe.Update()

    return probe.GetOutput()


def get_line_probe_data(probe_output, fields, max_rows=50):
    """Extract sampled values from a line probe output as formatted text.

    Args:
        probe_output: vtkPolyData output from sample_line().
        fields: List of field names to extract.
        max_rows: Maximum number of rows to include in the table output.
            If the probe has more points, it will be downsampled for display.

    Returns:
        A formatted string with a table of values and summary statistics.
    """
    if probe_output is None:
        return "Error: No probe data available"

    n_points = probe_output.GetNumberOfPoints()
    if n_points == 0:
        return "Error: Probe returned 0 points. The line may be outside the dataset bounds."

    pd = probe_output.GetPointData()

    # Check which fields are available
    available = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
    valid_fields = []
    missing_fields = []
    for f in fields:
        if pd.GetArray(f) is not None:
            valid_fields.append(f)
        else:
            missing_fields.append(f)

    if not valid_fields:
        return (
            f"Error: None of the requested fields {fields} found in probe output.\n"
            f"Available fields: {available}"
        )

    # Compute distance along the line from point1
    points = probe_output.GetPoints()
    p0 = np.array(points.GetPoint(0))
    distances = np.zeros(n_points)
    for i in range(n_points):
        pt = np.array(points.GetPoint(i))
        distances[i] = np.linalg.norm(pt - p0)

    total_distance = distances[-1] if n_points > 1 else 0.0

    # Collect field data as numpy arrays
    field_arrays = {}
    for f in valid_fields:
        arr = pd.GetArray(f)
        np_arr = vtk_to_numpy(arr)
        field_arrays[f] = np_arr

    # Check validity mask if available (vtkProbeFilter sets vtkValidPointMask)
    valid_mask_arr = pd.GetArray("vtkValidPointMask")
    if valid_mask_arr is not None:
        valid_mask = vtk_to_numpy(valid_mask_arr).astype(bool)
        n_valid = int(np.sum(valid_mask))
    else:
        valid_mask = np.ones(n_points, dtype=bool)
        n_valid = n_points

    # Build summary statistics for each field
    stats_lines = []
    for f in valid_fields:
        arr_data = field_arrays[f]
        ncomp = 1 if arr_data.ndim == 1 else arr_data.shape[1]

        if ncomp == 1:
            vals = arr_data[valid_mask].astype(np.float64)
            if len(vals) == 0:
                stats_lines.append(f"  {f}: no valid samples")
                continue
            vmin, vmax = float(vals.min()), float(vals.max())
            vmean = float(vals.mean())

            # Determine trend direction
            if len(vals) >= 2:
                first_quarter = vals[:max(1, len(vals) // 4)].mean()
                last_quarter = vals[max(0, 3 * len(vals) // 4):].mean()
                diff = last_quarter - first_quarter
                span = vmax - vmin
                if span > 0:
                    rel_change = diff / span
                    if rel_change > 0.1:
                        trend = "increasing"
                    elif rel_change < -0.1:
                        trend = "decreasing"
                    else:
                        trend = "flat"
                else:
                    trend = "constant"
            else:
                trend = "single point"

            stats_lines.append(
                f"  {f}: min={_fmt(vmin)}, max={_fmt(vmax)}, "
                f"mean={_fmt(vmean)}, trend={trend}"
            )
        else:
            # Vector field: report magnitude stats
            vals = arr_data[valid_mask].astype(np.float64)
            if len(vals) == 0:
                stats_lines.append(f"  {f}: no valid samples")
                continue
            mag = np.linalg.norm(vals, axis=1)
            stats_lines.append(
                f"  {f} (vector, {ncomp} components): "
                f"|mag| min={_fmt(float(mag.min()))}, max={_fmt(float(mag.max()))}, "
                f"mean={_fmt(float(mag.mean()))}"
            )

    # Build the table (downsample if too many rows)
    if n_points > max_rows:
        step = n_points / max_rows
        indices = [int(i * step) for i in range(max_rows)]
        # Always include last point
        if indices[-1] != n_points - 1:
            indices[-1] = n_points - 1
    else:
        indices = list(range(n_points))

    # Format header
    col_headers = ["dist"]
    for f in valid_fields:
        arr_data = field_arrays[f]
        ncomp = 1 if arr_data.ndim == 1 else arr_data.shape[1]
        if ncomp == 1:
            col_headers.append(f)
        else:
            for c in range(ncomp):
                col_headers.append(f"{f}[{c}]")
    col_headers.append("valid")

    header_line = "  ".join(f"{h:>12s}" for h in col_headers)

    table_lines = [header_line]
    table_lines.append("  ".join("-" * 12 for _ in col_headers))

    for idx in indices:
        row_vals = [f"{distances[idx]:12.4f}"]
        for f in valid_fields:
            arr_data = field_arrays[f]
            ncomp = 1 if arr_data.ndim == 1 else arr_data.shape[1]
            if ncomp == 1:
                row_vals.append(f"{float(arr_data[idx]):12.6g}")
            else:
                for c in range(ncomp):
                    row_vals.append(f"{float(arr_data[idx, c]):12.6g}")
        is_valid = "yes" if valid_mask[idx] else "no"
        row_vals.append(f"{is_valid:>12s}")
        table_lines.append("  ".join(row_vals))

    # Assemble the full output
    lines = [
        f"Line probe: {n_points} samples over distance {total_distance:.4f}",
        f"  Valid samples: {n_valid}/{n_points}",
    ]
    if missing_fields:
        lines.append(f"  Missing fields: {missing_fields}")
        lines.append(f"  Available fields: {available}")
    lines.append("")
    lines.append("Summary statistics:")
    lines.extend(stats_lines)
    lines.append("")
    if n_points > max_rows:
        lines.append(f"Data table (showing {len(indices)} of {n_points} samples):")
    else:
        lines.append("Data table:")
    lines.extend(table_lines)

    return "\n".join(lines)


# Supported condition operators
_CONDITION_OPS = {
    ">": lambda a, b: a > b,
    "<": lambda a, b: a < b,
    ">=": lambda a, b: a >= b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


def _get_scalar_array(data, field):
    """Return a 1-D numpy float64 array for a scalar field, or None if not found.

    Searches point data first, then cell data.  Raises ValueError for
    multi-component (vector) fields because masked statistics on vectors are
    ambiguous.
    """
    arr, location = find_field_array(data, field)
    if arr is None:
        return None, None, None
    if arr.GetNumberOfComponents() != 1:
        raise ValueError(
            f"Field '{field}' has {arr.GetNumberOfComponents()} components. "
            "query_stats only supports scalar (1-component) fields."
        )
    return vtk_to_numpy(arr).astype(np.float64), location, arr


def query_stats(data, field, condition_field, condition_op, condition_value):
    """Compute statistics for *field* at points where *condition_field* satisfies the condition.

    Args:
        data: VTK dataset (vtkDataSet subclass).
        field: Name of the scalar field to compute statistics on.
        condition_field: Name of the scalar field used as the filter condition.
        condition_op: One of ">", "<", ">=", "<=", "==", "!=".
        condition_value: Numeric threshold value for the condition.

    Returns:
        A human-readable string with count, mean, min, max, std, and
        percentiles (p1, p25, p50, p75, p99) of *field* over matching points.
    """
    if data is None:
        return "Error: No data available."

    if condition_op not in _CONDITION_OPS:
        ops = ", ".join(sorted(_CONDITION_OPS.keys()))
        return f"Error: Unknown operator '{condition_op}'. Supported: {ops}"

    # Fetch target field array
    try:
        target_vals, target_loc, _ = _get_scalar_array(data, field)
    except ValueError as exc:
        return f"Error: {exc}"
    if target_vals is None:
        available = [
            data.GetPointData().GetArrayName(i)
            for i in range(data.GetPointData().GetNumberOfArrays())
        ]
        return f"Error: Field '{field}' not found. Available point fields: {available}"

    # Fetch condition field array
    try:
        cond_vals, cond_loc, _ = _get_scalar_array(data, condition_field)
    except ValueError as exc:
        return f"Error: {exc}"
    if cond_vals is None:
        available = [
            data.GetPointData().GetArrayName(i)
            for i in range(data.GetPointData().GetNumberOfArrays())
        ]
        return f"Error: Condition field '{condition_field}' not found. Available point fields: {available}"

    # Both arrays must be the same length for direct comparison
    if len(target_vals) != len(cond_vals):
        return (
            f"Error: Field '{field}' ({target_loc} data, {len(target_vals)} tuples) and "
            f"condition field '{condition_field}' ({cond_loc} data, {len(cond_vals)} tuples) "
            "have different lengths. Both must be the same data location (point or cell)."
        )

    # Apply condition mask
    op_fn = _CONDITION_OPS[condition_op]
    mask = op_fn(cond_vals, float(condition_value))
    matched = target_vals[mask]
    count = int(mask.sum())
    total = len(target_vals)

    if count == 0:
        pct = 0.0
        return (
            f"Conditional statistics for '{field}' where {condition_field} {condition_op} {condition_value}:\n"
            f"  No points satisfy the condition (0 of {total} points)."
        )

    pct = count / total * 100
    s = _scalar_percentile_stats(matched)

    return (
        f"Conditional statistics for '{field}' where {condition_field} {condition_op} {condition_value}:\n"
        f"  Matching points: {count:,} of {total:,} ({pct:.2f}%)\n"
        f"  min:  {_fmt(s['min'])}\n"
        f"  max:  {_fmt(s['max'])}\n"
        f"  mean: {_fmt(s['mean'])}\n"
        f"  std:  {_fmt(s['std'])}\n"
        f"  p1:   {_fmt(s['p1'])}\n"
        f"  p25:  {_fmt(s['p25'])}\n"
        f"  p50:  {_fmt(s['p50'])}\n"
        f"  p75:  {_fmt(s['p75'])}\n"
        f"  p99:  {_fmt(s['p99'])}"
    )
