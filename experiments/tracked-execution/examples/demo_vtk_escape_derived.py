#!/usr/bin/env python3
"""Demo: vtk_escape for derived field computation.

Shows using vtk_escape to compute a velocity magnitude field from
Vx, Vy, Vz vector components — a common derived field that PyVista's
high-level API does not make convenient in a tracked pipeline context.

The derived-field computation is cached by vtk_escape. Iterating over
different threshold values shows the derived field stays cached while
only the downstream threshold re-executes.

Run:
    python3 experiments/tracked-execution/examples/demo_vtk_escape_derived.py
"""

import sys
import os
import tempfile
import time
from pathlib import Path

import numpy as np
import pyvista as pv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracked_execution import DAG, execute_pipeline


# ---------------------------------------------------------------------------
# Dataset creation
# ---------------------------------------------------------------------------

def create_velocity_dataset(dims=(80, 80, 80), seed=42):
    """Synthetic mesh with Temperature and Vx/Vy/Vz velocity components."""
    mesh = pv.ImageData(dimensions=dims)
    rng = np.random.RandomState(seed)
    n = mesh.n_points
    mesh["Temperature"] = rng.rand(n) * 1000
    mesh["Vx"] = rng.randn(n) * 10
    mesh["Vy"] = rng.randn(n) * 10
    mesh["Vz"] = rng.randn(n) * 10
    path = tempfile.mktemp(suffix=".vtk")
    mesh.save(path)
    return path


# ---------------------------------------------------------------------------
# Pipeline builder
# ---------------------------------------------------------------------------

def build_pipeline(data_path, mag_threshold):
    """Pipeline: read → derive velocity magnitude → threshold on magnitude."""
    return f"""
mesh = read("{data_path}")

def compute_velocity_magnitude(m):
    \"\"\"Compute velocity magnitude from Vx, Vy, Vz components.\"\"\"
    import numpy as np
    vx = m["Vx"]
    vy = m["Vy"]
    vz = m["Vz"]
    mag = np.sqrt(vx**2 + vy**2 + vz**2)
    result = m.copy()
    result["VelocityMagnitude"] = mag
    return result

enriched = vtk_escape(mesh, compute_velocity_magnitude)
fast_flow = enriched.threshold(value={mag_threshold:.1f}, scalars="VelocityMagnitude")
show(fast_flow, colormap="plasma")
print(f"mag_threshold={mag_threshold:.1f}: {{fast_flow.n_points}} points above threshold")
"""


def fmt_stats(stats):
    return (
        f"hits={stats['hits']:3d}  misses={stats['misses']:3d}"
        f"  evictions={stats['evictions']:3d}"
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("=" * 60)
    print("Demo: vtk_escape — Derived Field (Velocity Magnitude)")
    print("=" * 60)

    print("\nCreating synthetic velocity dataset (80x80x80)...")
    data_path = create_velocity_dataset(dims=(80, 80, 80))
    print(f"Dataset: {data_path}")
    print("Fields: Temperature, Vx, Vy, Vz")

    dag = DAG()

    iterations = [
        # (mag_threshold, description)
        (10.0, "Run 1: cold cache — derive VelocityMagnitude, then threshold at 10"),
        (10.0, "Run 2: same pipeline — all cached"),
        (15.0, "Run 3: threshold raised to 15 — derivation cached, threshold misses"),
        (20.0, "Run 4: threshold raised to 20 — derivation still cached"),
        (20.0, "Run 5: repeat of Run 4 — all hits"),
    ]

    results = []
    for mag_threshold, description in iterations:
        pipeline = build_pipeline(data_path, mag_threshold)
        t0 = time.perf_counter()
        result = execute_pipeline(pipeline, dag)
        elapsed = time.perf_counter() - t0
        results.append((description, result.stats, elapsed, result.output.strip()))

    # ------------------------------------------------------------------
    # Print table
    # ------------------------------------------------------------------
    print(f"\n{'Run':<6}  {'Hits':>5}  {'Misses':>7}  {'Evictions':>10}  {'Time(s)':>8}")
    print("-" * 60)
    for i, (description, stats, elapsed, output) in enumerate(results, 1):
        print(
            f"{i:<6}  {stats['hits']:>5}  {stats['misses']:>7}"
            f"  {stats['evictions']:>10}  {elapsed:>8.4f}"
        )
        print(f"       {description}")
        if output:
            print(f"       -> {output}")

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------
    print("\n--- Assertions ---")

    # Run 1: cold — no hits
    s1 = results[0][1]
    assert s1["hits"] == 0, f"Run 1 should have 0 hits, got {s1['hits']}"
    assert s1["misses"] > 0
    print("Run 1 PASS: cold cache, all misses")

    # Run 2: identical — all hits
    s2 = results[1][1]
    assert s2["misses"] == 0, f"Run 2 should have 0 misses, got {s2['misses']}"
    assert s2["hits"] > 0
    print("Run 2 PASS: identical pipeline, all hits")

    # Run 3: threshold changed — read + vtk_escape should hit, threshold should miss
    s3 = results[2][1]
    # read() is 1 hit, vtk_escape is another hit; threshold is a miss
    assert s3["hits"] >= 2, (
        f"Run 3: read() and vtk_escape should both hit (got {s3['hits']} hits)"
    )
    assert s3["misses"] >= 1, (
        f"Run 3: threshold(15) should miss (got {s3['misses']} misses)"
    )
    print("Run 3 PASS: vtk_escape cached, only threshold re-ran")

    # Run 4: threshold changed again — derivation still cached
    s4 = results[3][1]
    assert s4["hits"] >= 2, f"Run 4: read() and vtk_escape should hit ({s4['hits']} hits)"
    assert s4["misses"] >= 1, f"Run 4: threshold(20) should miss ({s4['misses']} misses)"
    print("Run 4 PASS: vtk_escape still cached across second threshold change")

    # Run 5: all hits
    s5 = results[4][1]
    assert s5["misses"] == 0, f"Run 5 should have 0 misses, got {s5['misses']}"
    print("Run 5 PASS: all hits")

    # ------------------------------------------------------------------
    # Key insight
    # ------------------------------------------------------------------
    print("\n--- Key insight ---")
    print("  compute_velocity_magnitude reads all three vector arrays and produces")
    print("  a derived scalar field. This is a potentially expensive computation.")
    print("  vtk_escape caches it: as long as the raw mesh is unchanged, the derived")
    print("  field is never recomputed — even as the downstream threshold changes.")
    print("  The derivation is computed once and reused across all threshold iterations.")

    try:
        os.unlink(data_path)
    except OSError:
        pass

    print("\nDone.")


if __name__ == "__main__":
    main()
