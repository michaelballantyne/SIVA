"""
Extract a y-z cross-section of the wind field at a fixed x position
just behind the ridgeline where the fire is burning.

Outputs a JSON file suitable for Vega-Lite vector field visualization,
with columns: y, z, v, w, speed, theta
"""

import vtk
import json
import math

INPUT_FILE = "output.30000.vts"
OUTPUT_FILE = "yz_wind_slice.json"
TARGET_X = 70.0  # just behind ridge, in the active fire zone
SUBSAMPLE_Y = 10  # take every Nth point in y
SUBSAMPLE_Z = 2   # take every Nth point in z
MAX_Z_INDEX = 30   # only go up ~half the domain height (lower atmosphere)


def main():
    reader = vtk.vtkXMLStructuredGridReader()
    reader.SetFileName(INPUT_FILE)
    reader.Update()
    grid = reader.GetOutput()

    dims = [0, 0, 0]
    grid.GetDimensions(dims)
    nx, ny, nz = dims

    pd = grid.GetPointData()
    v_arr = pd.GetArray("v")
    w_arr = pd.GetArray("w")
    theta_arr = pd.GetArray("theta")
    rhof_arr = pd.GetArray("rhof_1")

    # Find the i-index closest to TARGET_X along the centerline at ground level
    best_i = 0
    best_dist = float("inf")
    j_mid = ny // 2
    for i in range(nx):
        idx = i + j_mid * nx + 0 * nx * ny
        pt = grid.GetPoint(idx)
        dist = abs(pt[0] - TARGET_X)
        if dist < best_dist:
            best_dist = dist
            best_i = i

    # Verify
    check_idx = best_i + j_mid * nx
    check_pt = grid.GetPoint(check_idx)
    print(f"Selected i={best_i}, actual x={check_pt[0]:.1f} (target was {TARGET_X})")

    # Extract the slice
    records = []
    for k in range(0, min(nz, MAX_Z_INDEX), SUBSAMPLE_Z):
        for j in range(0, ny, SUBSAMPLE_Y):
            idx = best_i + j * nx + k * nx * ny
            pt = grid.GetPoint(idx)
            y = pt[1]
            z = pt[2]
            v = v_arr.GetValue(idx)
            w = w_arr.GetValue(idx)
            theta = theta_arr.GetValue(idx)
            rhof = rhof_arr.GetValue(idx)
            speed = math.sqrt(v * v + w * w)

            records.append({
                "y": round(y, 1),
                "z": round(z, 1),
                "v": round(v, 2),
                "w": round(w, 2),
                "speed": round(speed, 2),
                "theta": round(theta, 1),
                "rhof_1": round(rhof, 4),
            })

    with open(OUTPUT_FILE, "w") as f:
        json.dump(records, f)

    print(f"Wrote {len(records)} records to {OUTPUT_FILE}")
    print(f"y range: [{records[0]['y']}, {records[-1]['y']}]")
    print(f"z range: [{min(r['z'] for r in records)}, {max(r['z'] for r in records)}]")
    print(f"speed range: [{min(r['speed'] for r in records):.2f}, {max(r['speed'] for r in records):.2f}]")


if __name__ == "__main__":
    main()
