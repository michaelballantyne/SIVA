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
