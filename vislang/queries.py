"""Query tools for inspecting VTK data objects."""

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
    counts, _ = np.histogram(values, bins=30)
    # Smooth slightly
    smoothed = np.convolve(counts, [0.25, 0.5, 0.25], mode="same")
    # Count local maxima
    peaks = 0
    threshold = smoothed.max() * 0.15
    for i in range(1, len(smoothed) - 1):
        if smoothed[i] > smoothed[i - 1] and smoothed[i] > smoothed[i + 1] and smoothed[i] > threshold:
            peaks += 1
    if peaks >= 2:
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
        return "No data available"

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
        return "No data available"
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
        return "No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        available = []
        pd = data.GetPointData()
        for i in range(pd.GetNumberOfArrays()):
            available.append(pd.GetArrayName(i))
        return f"Field '{field}' not found. Available: {available}"

    n = arr.GetNumberOfTuples()
    ncomp = arr.GetNumberOfComponents()

    results = []
    for comp in range(ncomp):
        rng = arr.GetRange(comp)
        total = 0.0
        total_sq = 0.0
        for i in range(n):
            v = arr.GetComponent(i, comp) if ncomp > 1 else arr.GetValue(i)
            total += v
            total_sq += v * v
        mean = total / n
        variance = (total_sq / n) - (mean * mean)
        std = math.sqrt(max(0, variance))

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
        return "No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        return f"Field '{field}' not found"

    rng = arr.GetRange()
    if rng[0] == rng[1]:
        return f"Field '{field}' is constant: {rng[0]}"

    n = arr.GetNumberOfTuples()
    bin_width = (rng[1] - rng[0]) / bins
    counts = [0] * bins

    for i in range(n):
        v = arr.GetValue(i)
        idx = int((v - rng[0]) / bin_width)
        idx = min(idx, bins - 1)
        counts[idx] += 1

    max_count = max(counts)
    bar_width = 40

    lines = [f"Histogram of '{field}' ({n} values, {bins} bins):"]
    lines.append(f"Range: [{rng[0]:.6g}, {rng[1]:.6g}]")
    lines.append("")

    for i in range(bins):
        lo = rng[0] + i * bin_width
        hi = lo + bin_width
        bar_len = int(counts[i] / max_count * bar_width) if max_count > 0 else 0
        bar = "█" * bar_len
        pct = counts[i] / n * 100
        lines.append(f"  [{lo:10.4g}, {hi:10.4g}) {bar:40s} {counts[i]:>8d} ({pct:5.1f}%)")

    return "\n".join(lines)


def get_spatial_extent(data, field, min_val, max_val):
    """Find bounding box where field is within given range."""
    if data is None:
        return "No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        return f"Field '{field}' not found"

    n = arr.GetNumberOfTuples()
    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    count = 0

    for i in range(n):
        v = arr.GetValue(i)
        if min_val <= v <= max_val:
            pt = data.GetPoint(i)
            xmin = min(xmin, pt[0])
            xmax = max(xmax, pt[0])
            ymin = min(ymin, pt[1])
            ymax = max(ymax, pt[1])
            zmin = min(zmin, pt[2])
            zmax = max(zmax, pt[2])
            count += 1

    if count == 0:
        return f"No points where {field} is in [{min_val}, {max_val}]"

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
        return "No data available"

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


def suggest_scalar_range(data, field, percentile_low=1, percentile_high=99):
    """Suggest a useful scalar range based on the field's distribution.

    Uses percentiles to exclude extreme outliers that would compress
    the colormap. Default: 1st to 99th percentile.
    """
    if data is None:
        return "No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        available = []
        pd = data.GetPointData()
        for i in range(pd.GetNumberOfArrays()):
            available.append(pd.GetArrayName(i))
        return f"Field '{field}' not found. Available: {available}"

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
        return "No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        available = [data.GetPointData().GetArrayName(i)
                     for i in range(data.GetPointData().GetNumberOfArrays())]
        return f"Field '{field}' not found. Available: {available}"

    rng = arr.GetRange()
    if scalar_range is None:
        scalar_range = rng

    lo, hi = scalar_range
    if hi <= lo:
        return f"Invalid scalar_range: [{lo}, {hi}]"

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
        return "No data available"

    arr = data.GetPointData().GetArray(field)
    if arr is None:
        arr = data.GetCellData().GetArray(field)
    if arr is None:
        available = [data.GetPointData().GetArrayName(i)
                     for i in range(data.GetPointData().GetNumberOfArrays())]
        return f"Field '{field}' not found. Available: {available}"

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
        return "No data available"

    dims = [0, 0, 0]
    if not hasattr(data, "GetDimensions"):
        return "Data is not a structured grid"
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
