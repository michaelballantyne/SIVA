"""Query tools for inspecting VTK data objects."""

import vtk
import math


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
