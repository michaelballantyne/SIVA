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
import math
import numpy as np
from vtk.util.numpy_support import vtk_to_numpy


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


def get_rich_field_stats(data, max_sample=100000):
    """Compute rich per-field statistics for all arrays in a dataset.

    Returns a list of dicts, one per field, each containing:
      name, location ("point"/"cell"), components, dtype,
      min, max, p1, p25, p50, p75, p99, mean, std, shape,
      and for vectors: magnitude stats + per-component stats.

    Args:
        data: VTK data object.
        max_sample: Maximum number of values to sample for percentiles.
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
            ncomp = arr.GetNumberOfComponents()
            n = arr.GetNumberOfTuples()
            dtype = arr.GetDataTypeAsString()

            # Subsample for speed on large arrays
            np_arr = vtk_to_numpy(arr)
            if np_arr is None:
                continue

            step = max(1, n // max_sample)
            if step > 1:
                sample = np_arr[::step]
            else:
                sample = np_arr

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
                info["min"] = rng[0]
                info["max"] = rng[1]
                info["p1"] = float(np.percentile(vals, 1))
                info["p25"] = float(np.percentile(vals, 25))
                info["p50"] = float(np.percentile(vals, 50))
                info["p75"] = float(np.percentile(vals, 75))
                info["p99"] = float(np.percentile(vals, 99))
                info["mean"] = float(np.mean(vals))
                info["std"] = float(np.std(vals))
                info["shape"] = _classify_distribution(vals)
            else:
                # Vector field: magnitude + per-component
                sample_f = sample.astype(np.float64)
                mag = np.linalg.norm(sample_f, axis=1)
                info["magnitude"] = {
                    "min": float(mag.min()),
                    "max": float(mag.max()),
                    "p1": float(np.percentile(mag, 1)),
                    "p25": float(np.percentile(mag, 25)),
                    "p50": float(np.percentile(mag, 50)),
                    "p75": float(np.percentile(mag, 75)),
                    "p99": float(np.percentile(mag, 99)),
                    "mean": float(np.mean(mag)),
                    "std": float(np.std(mag)),
                    "shape": _classify_distribution(mag),
                }
                info["components_stats"] = []
                for c in range(ncomp):
                    cvals = sample_f[:, c]
                    rng = arr.GetRange(c)
                    info["components_stats"].append({
                        "component": c,
                        "min": rng[0],
                        "max": rng[1],
                        "p1": float(np.percentile(cvals, 1)),
                        "p25": float(np.percentile(cvals, 25)),
                        "p50": float(np.percentile(cvals, 50)),
                        "p75": float(np.percentile(cvals, 75)),
                        "p99": float(np.percentile(cvals, 99)),
                        "mean": float(np.mean(cvals)),
                        "std": float(np.std(cvals)),
                    })

            results.append(info)

    return results


def format_rich_field_stats(stats_list):
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


def get_array_info(data):
    """List all arrays with component counts, types, and value ranges."""
    if data is None:
        return "Error: No data available"

    lines = []

    # Point data
    pd = data.GetPointData()
    if pd.GetNumberOfArrays() > 0:
        lines.append(f"Point Data ({data.GetNumberOfPoints()} points):")
        for i in range(pd.GetNumberOfArrays()):
            arr = pd.GetArray(i)
            name = pd.GetArrayName(i)
            ncomp = arr.GetNumberOfComponents()
            dtype = arr.GetDataTypeAsString()
            if ncomp == 1:
                rng = arr.GetRange()
                lines.append(f"  {name}: {dtype}, range=[{rng[0]:.6g}, {rng[1]:.6g}]")
            else:
                lines.append(f"  {name}: {dtype}, {ncomp} components")
                for c in range(ncomp):
                    rng = arr.GetRange(c)
                    lines.append(f"    component {c}: [{rng[0]:.6g}, {rng[1]:.6g}]")

    # Cell data
    cd = data.GetCellData()
    if cd.GetNumberOfArrays() > 0:
        lines.append(f"Cell Data ({data.GetNumberOfCells()} cells):")
        for i in range(cd.GetNumberOfArrays()):
            arr = cd.GetArray(i)
            name = cd.GetArrayName(i)
            ncomp = arr.GetNumberOfComponents()
            dtype = arr.GetDataTypeAsString()
            if ncomp == 1:
                rng = arr.GetRange()
                lines.append(f"  {name}: {dtype}, range=[{rng[0]:.6g}, {rng[1]:.6g}]")
            else:
                lines.append(f"  {name}: {dtype}, {ncomp} components")

    bounds = data.GetBounds()
    lines.append(
        f"Bounds: x=[{bounds[0]:.1f}, {bounds[1]:.1f}], "
        f"y=[{bounds[2]:.1f}, {bounds[3]:.1f}], "
        f"z=[{bounds[4]:.1f}, {bounds[5]:.1f}]"
    )

    dims = [0, 0, 0]
    if hasattr(data, "GetDimensions"):
        data.GetDimensions(dims)
        lines.append(f"Dimensions: {dims[0]} x {dims[1]} x {dims[2]}")

    return "\n".join(lines)


def get_bounds(data):
    """Get spatial bounds of data."""
    if data is None:
        return "Error: No data available"
    bounds = data.GetBounds()
    return (
        f"Bounds:\n"
        f"  X: [{bounds[0]:.2f}, {bounds[1]:.2f}] (range: {bounds[1]-bounds[0]:.2f})\n"
        f"  Y: [{bounds[2]:.2f}, {bounds[3]:.2f}] (range: {bounds[3]-bounds[2]:.2f})\n"
        f"  Z: [{bounds[4]:.2f}, {bounds[5]:.2f}] (range: {bounds[5]-bounds[4]:.2f})"
    )


def get_statistics(data, field):
    """Get min, max, mean, std for a field."""
    if data is None:
        return "Error: No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        pd = data.GetPointData()
        cd = data.GetCellData()
        point_arrays = [pd.GetArrayName(i) for i in range(pd.GetNumberOfArrays())]
        cell_arrays = [cd.GetArrayName(i) for i in range(cd.GetNumberOfArrays())]
        msg = f"Error: Field '{field}' not found."
        if point_arrays:
            msg += f" Point arrays: {point_arrays}."
        if cell_arrays:
            msg += f" Cell arrays: {cell_arrays}."
        if not point_arrays and not cell_arrays:
            msg += " No arrays available."
        return msg

    n = arr.GetNumberOfTuples()
    ncomp = arr.GetNumberOfComponents()

    if n == 0:
        return f"Field '{field}' exists but contains no tuples (empty dataset)."

    np_arr = vtk_to_numpy(arr).astype(np.float64)
    if ncomp > 1:
        np_arr = np_arr.reshape(n, ncomp)

    results = []
    for comp in range(ncomp):
        rng = arr.GetRange(comp)
        vals = np_arr[:, comp] if ncomp > 1 else np_arr
        mean = float(np.mean(vals))
        std = float(np.std(vals))

        comp_label = f" (component {comp})" if ncomp > 1 else ""
        results.append(
            f"  {field}{comp_label}:\n"
            f"    min: {rng[0]:.6g}\n"
            f"    max: {rng[1]:.6g}\n"
            f"    mean: {mean:.6g}\n"
            f"    std: {std:.6g}\n"
            f"    count: {n}"
        )

    return f"Statistics for '{field}':\n" + "\n".join(results)


def get_histogram(data, field, bins=20):
    """Generate a text histogram with ASCII bars."""
    if data is None:
        return "Error: No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
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
    return (
        f"Spatial extent where {field} in [{min_val:.4g}, {max_val:.4g}]:\n"
        f"  {count} points ({pct_str} of total)\n"
        f"  X: [{xmin:.2f}, {xmax:.2f}]\n"
        f"  Y: [{ymin:.2f}, {ymax:.2f}]\n"
        f"  Z: [{zmin:.2f}, {zmax:.2f}]"
    )


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
        return f"No point found near ({x}, {y}, {z})"

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


def suggest_scalar_range(data, field, percentile_low=1, percentile_high=99):
    """Suggest a useful scalar range based on the field's distribution.

    Uses percentiles to exclude extreme outliers that would compress
    the colormap. Default: 1st to 99th percentile.
    """
    if data is None:
        return "Error: No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        available = []
        pd = data.GetPointData()
        for i in range(pd.GetNumberOfArrays()):
            available.append(pd.GetArrayName(i))
        return f"Error: Field '{field}' not found. Available: {available}"

    n = arr.GetNumberOfTuples()
    if n == 0:
        return f"Field '{field}' has no values"

    # Sample values: use every Nth value for large datasets to keep sorting fast
    step = max(1, n // 10000)
    values = []
    for i in range(0, n, step):
        values.append(arr.GetValue(i))
    values.sort()

    sample_size = len(values)

    def percentile(sorted_vals, pct):
        idx = (pct / 100.0) * (len(sorted_vals) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(sorted_vals) - 1)
        frac = idx - lo
        return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac

    p_low = percentile(values, percentile_low)
    p_high = percentile(values, percentile_high)
    p5 = percentile(values, 5)
    p25 = percentile(values, 25)
    p50 = percentile(values, 50)
    p75 = percentile(values, 75)
    p95 = percentile(values, 95)

    full_range = arr.GetRange()

    # Compute how skewed the distribution is
    iqr = p75 - p25
    full_span = full_range[1] - full_range[0]
    concentration = iqr / full_span if full_span > 0 else 1.0

    lines = [f"Scalar range analysis for '{field}':"]
    lines.append(f"  Full range: [{full_range[0]:.6g}, {full_range[1]:.6g}]")
    lines.append(f"  Suggested range (p{percentile_low}-p{percentile_high}): [{p_low:.6g}, {p_high:.6g}]")
    lines.append("")
    lines.append("  Percentiles:")
    lines.append(f"    1%: {percentile(values, 1):.6g}")
    lines.append(f"    5%: {p5:.6g}")
    lines.append(f"   25%: {p25:.6g}")
    lines.append(f"   50% (median): {p50:.6g}")
    lines.append(f"   75%: {p75:.6g}")
    lines.append(f"   95%: {p95:.6g}")
    lines.append(f"   99%: {percentile(values, 99):.6g}")
    lines.append("")
    lines.append(f"  IQR (25-75%): [{p25:.6g}, {p75:.6g}]")
    lines.append(f"  IQR/full_range ratio: {concentration:.4f}")

    if concentration < 0.1:
        lines.append("")
        lines.append(f"  WARNING: Highly skewed distribution. The IQR covers only"
                      f" {concentration*100:.1f}% of the full range.")
        lines.append(f"  Using the full range as scalar_range will compress most values"
                      f" into a tiny portion of the colormap.")
        lines.append(f"  Consider using scalar_range=({p_low:.6g}, {p_high:.6g})"
                      f" or a non-linear colormap.")

    lines.append(f"\n  (Based on {sample_size} sampled values out of {n} total)")

    return "\n".join(lines)


def suggest_opacity_function(data, field, scalar_range=None, num_points=6, max_opacity=0.8):
    """Suggest opacity transfer function control points for volume rendering.

    Analyzes the field histogram and creates control points that make common
    (ambient) values transparent and rare (feature) values opaque.
    """
    if data is None:
        return "Error: No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        available = [data.GetPointData().GetArrayName(i)
                     for i in range(data.GetPointData().GetNumberOfArrays())]
        return f"Error: Field '{field}' not found. Available: {available}"

    rng = arr.GetRange()
    if scalar_range is None:
        scalar_range = rng

    lo, hi = scalar_range
    if hi <= lo:
        return f"Error: Invalid scalar_range: [{lo}, {hi}]"

    # Build a histogram over the scalar range
    n = arr.GetNumberOfTuples()
    bins = 100
    bin_width = (hi - lo) / bins
    counts = [0] * bins
    total_in_range = 0

    step = max(1, n // 50000)
    for i in range(0, n, step):
        v = arr.GetValue(i)
        if lo <= v <= hi:
            idx = min(int((v - lo) / bin_width), bins - 1)
            counts[idx] += 1
            total_in_range += 1

    if total_in_range == 0:
        return (f"No values in range [{lo}, {hi}]. "
                f"Field range is [{rng[0]:.6g}, {rng[1]:.6g}]")

    # Find the "ambient peak" — the bin with the most values
    max_bin = max(range(bins), key=lambda i: counts[i])
    max_count = counts[max_bin]

    # Generate control points: make ambient (high-count) regions transparent,
    # rare (low-count) regions opaque
    points = []
    step_size = max(1, bins // (num_points - 1))
    for i in range(0, bins, step_size):
        val = lo + (i + 0.5) * bin_width
        # Opacity is inversely proportional to how common this value is
        fraction = counts[i] / max_count if max_count > 0 else 0
        opacity = max_opacity * (1.0 - fraction)
        # Clamp
        opacity = max(0.0, min(max_opacity, opacity))
        points.append((round(val, 4), round(opacity, 4)))

    # Ensure we have endpoint at hi
    if points[-1][0] < hi:
        last_bin_frac = counts[-1] / max_count if max_count > 0 else 0
        points.append((round(hi, 4), round(max_opacity * (1.0 - last_bin_frac), 4)))

    # Ensure first point starts at lo
    if points[0][0] > lo:
        first_bin_frac = counts[0] / max_count if max_count > 0 else 0
        points.insert(0, (round(lo, 4), round(max_opacity * (1.0 - first_bin_frac), 4)))

    lines = [f"Suggested opacity function for '{field}' in [{lo:.4g}, {hi:.4g}]:"]
    lines.append(f"  opacity_function={points}")
    lines.append("")
    lines.append("Paste this into your show() call, e.g.:")
    lines.append(f'  show(node, "name", representation="Volume", color_by="{field}",')
    lines.append(f"    scalar_range=({lo:.4g}, {hi:.4g}),")
    lines.append(f"    opacity_function={points})")
    lines.append("")
    lines.append(f"Based on {total_in_range * step} values sampled from {n} total.")
    lines.append(f"Ambient peak at value ~{lo + (max_bin + 0.5) * bin_width:.4g} "
                 f"({counts[max_bin] * 100 / total_in_range:.1f}% of values in range)")

    return "\n".join(lines)


def suggest_isosurface(data, field, num_values=3):
    """Suggest good isosurface values based on the field histogram.

    Finds values at histogram peaks (common values that form coherent
    surfaces) and valleys (transitions between regions).
    """
    if data is None:
        return "Error: No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        available = [data.GetPointData().GetArrayName(i)
                     for i in range(data.GetPointData().GetNumberOfArrays())]
        return f"Error: Field '{field}' not found. Available: {available}"

    rng = arr.GetRange()
    if rng[0] == rng[1]:
        return f"Field '{field}' is constant: {rng[0]}"

    # Build histogram
    n = arr.GetNumberOfTuples()
    bins = 100
    bin_width = (rng[1] - rng[0]) / bins
    counts = [0] * bins

    step = max(1, n // 50000)
    for i in range(0, n, step):
        v = arr.GetValue(i)
        idx = min(int((v - rng[0]) / bin_width), bins - 1)
        counts[idx] += 1

    total = sum(counts)
    if total == 0:
        return f"No values sampled for '{field}'"

    # Find significant gradient changes (transitions between regions)
    # These make good isosurface values
    gradients = []
    for i in range(1, bins - 1):
        grad = abs(counts[i + 1] - counts[i - 1])
        val = rng[0] + (i + 0.5) * bin_width
        # Skip values very close to the range edges
        if val < rng[0] + 0.05 * (rng[1] - rng[0]):
            continue
        if val > rng[0] + 0.95 * (rng[1] - rng[0]):
            continue
        gradients.append((grad, val, counts[i]))

    # Sort by gradient magnitude (steepest transitions first)
    gradients.sort(reverse=True)

    # Pick top values that are well-separated
    suggested = []
    min_separation = (rng[1] - rng[0]) / (num_values * 2)
    for grad, val, count in gradients:
        if len(suggested) >= num_values:
            break
        # Check separation from already selected values
        if all(abs(val - s) > min_separation for s in suggested):
            suggested.append(round(val, 6))

    suggested.sort()

    # Also find percentile-based values
    values = []
    for i in range(0, n, step):
        values.append(arr.GetValue(i))
    values.sort()

    def pct(p):
        idx = int(p / 100 * (len(values) - 1))
        return values[idx]

    lines = [f"Suggested isosurface values for '{field}':"]
    lines.append(f"  Range: [{rng[0]:.6g}, {rng[1]:.6g}]")
    lines.append("")
    lines.append(f"  Gradient-based (transition points): {suggested}")
    lines.append(f"  Percentile-based:")
    for p in [25, 50, 75, 90, 95, 99]:
        lines.append(f"    p{p}: {pct(p):.6g}")
    lines.append("")
    lines.append(f"  Usage: filter(\"vtkContourFilter\", input=node,")
    lines.append(f"    ContourBy=\"{field}\", Isosurfaces={suggested})")

    return "\n".join(lines)


def get_ground_z(data, x, y):
    """Find the z-coordinate at the ground level for a given x,y position.

    This is important for terrain-following grids where z-coordinates
    at the ground vary with x,y position.
    """
    if data is None:
        return "Error: No data available"

    dims = [0, 0, 0]
    if not hasattr(data, "GetDimensions"):
        return "Error: Data is not a structured grid"
    data.GetDimensions(dims)

    # Find the nearest grid indices for x, y
    bounds = data.GetBounds()
    nx, ny, nz = dims

    # Find closest point at ground level (iz=0)
    best_dist = float("inf")
    best_pt = None
    best_ix = best_iy = 0

    # Sample to find the nearest ground point
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
        return f"Could not find ground point near ({x}, {y})"

    # Get z-values at different heights above this xy location
    z_values = []
    for iz in range(min(nz, 10)):
        idx = iz * nx * ny + best_iy * nx + best_ix
        pt = data.GetPoint(idx)
        z_values.append((iz, pt[2]))

    lines = [
        f"Ground at ({x}, {y}):",
        f"  Nearest grid point: ({best_pt[0]:.1f}, {best_pt[1]:.1f})",
        f"  Ground z (iz=0): {best_pt[2]:.1f}",
        f"  Z values at increasing heights:",
    ]
    for iz, z in z_values:
        lines.append(f"    iz={iz}: z={z:.1f}")

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
    arr = data.GetPointData().GetArray(field)
    location = "point"
    if arr is None:
        arr = data.GetCellData().GetArray(field)
        location = "cell"
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
    mean_val = float(np.mean(matched))
    min_val = float(np.min(matched))
    max_val = float(np.max(matched))
    std_val = float(np.std(matched))
    p1 = float(np.percentile(matched, 1))
    p25 = float(np.percentile(matched, 25))
    p50 = float(np.percentile(matched, 50))
    p75 = float(np.percentile(matched, 75))
    p99 = float(np.percentile(matched, 99))

    return (
        f"Conditional statistics for '{field}' where {condition_field} {condition_op} {condition_value}:\n"
        f"  Matching points: {count:,} of {total:,} ({pct:.2f}%)\n"
        f"  min:  {_fmt(min_val)}\n"
        f"  max:  {_fmt(max_val)}\n"
        f"  mean: {_fmt(mean_val)}\n"
        f"  std:  {_fmt(std_val)}\n"
        f"  p1:   {_fmt(p1)}\n"
        f"  p25:  {_fmt(p25)}\n"
        f"  p50:  {_fmt(p50)}\n"
        f"  p75:  {_fmt(p75)}\n"
        f"  p99:  {_fmt(p99)}"
    )
