#!/usr/bin/env python3
"""Demo: vtk_escape cache behaviour across parameter changes.

Shows that vtk_escape participates correctly in the DAG:
  - Its cache key depends on the input hash, so upstream changes propagate.
  - Changing only downstream parameters (colormap, show kwargs) leaves vtk_escape cached.
  - Hit/miss stats are printed after each simulated pipeline run.

Run:
    python3 experiments/tracked-execution/examples/demo_vtk_escape_caching.py
"""

import sys
import time
from pathlib import Path

import vtk
import pyvista as pv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracked_execution import DAG, tracked_read, vtk_escape
from utils import cleanup, create_test_dataset


# ---------------------------------------------------------------------------
# VTK filter function — defined at module scope so import works normally.
# ---------------------------------------------------------------------------

def apply_custom_filter(m):
    """Triangulate then decimate the surface — a post-processing step via raw VTK.

    vtkDecimatePro is used here as a representative 'custom VTK filter'.
    vtkTriangleFilter ensures the mesh is fully triangulated first, which
    is required by vtkDecimatePro.
    """
    # Step 1: triangulate (vtkDecimatePro requires triangles)
    tri = vtk.vtkTriangleFilter()
    tri.SetInputData(m)
    tri.Update()

    # Step 2: decimate
    dec = vtk.vtkDecimatePro()
    dec.SetInputConnection(tri.GetOutputPort())
    dec.SetTargetReduction(0.3)
    dec.PreserveTopologyOn()
    dec.Update()
    return pv.wrap(dec.GetOutput())


# ---------------------------------------------------------------------------
# Pipeline runner
# ---------------------------------------------------------------------------

def run_pipeline(data_path, dag, threshold, colormap):
    """Read → threshold → extract_surface → vtk_escape(decimator) → show.

    'colormap' is recorded but not used for caching (it's a display-only param).
    """
    dag.begin_run()

    mesh = tracked_read(data_path, dag)
    thresholded = mesh.threshold(value=threshold, scalars="Temperature")
    surface = thresholded.extract_surface()
    decimated = vtk_escape(surface, apply_custom_filter)

    # Record actor for show (not caching-relevant)
    real_decimated = object.__getattribute__(decimated, "_real")
    actors = [(real_decimated, {"scalars": "Temperature", "cmap": colormap})]

    dag.end_run()

    n_in = object.__getattribute__(thresholded, "_real").n_cells
    n_out = real_decimated.n_cells
    return dag.stats(), n_in, n_out


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
    print("Demo: vtk_escape — Cache Behaviour Across Parameter Changes")
    print("=" * 60)

    print("\nCreating synthetic dataset (70x70x70)...")
    data_path = create_test_dataset(dims=(70, 70, 70))
    print(f"Dataset: {data_path}")

    dag = DAG()

    runs = [
        # (threshold, colormap, description)
        (500.0, "viridis",  "Run 1: cold cache — all ops are misses"),
        (500.0, "viridis",  "Run 2: identical pipeline — all ops are hits"),
        (700.0, "viridis",  "Run 3: threshold changed — read cached; threshold + vtk_escape miss"),
        (700.0, "plasma",   "Run 4: colormap only — mesh ops (incl. vtk_escape) all hit"),
        (700.0, "plasma",   "Run 5: repeat of Run 4 — all hits"),
    ]

    results = []
    for threshold, colormap, description in runs:
        t0 = time.perf_counter()
        stats, n_in, n_out = run_pipeline(data_path, dag, threshold, colormap)
        elapsed = time.perf_counter() - t0
        results.append((description, stats, elapsed, n_in, n_out))

    # ------------------------------------------------------------------
    # Print table
    # ------------------------------------------------------------------
    print(f"\n{'Run':<6}  {'Hits':>5}  {'Misses':>7}  {'Evictions':>10}  {'Time(s)':>8}  Description")
    print("-" * 90)
    for i, (description, stats, elapsed, n_in, n_out) in enumerate(results, 1):
        print(
            f"{i:<6}  {stats['hits']:>5}  {stats['misses']:>7}"
            f"  {stats['evictions']:>10}  {elapsed:>8.4f}  {description}"
        )
        print(f"         cells before decimation={n_in}  after={n_out}")

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------
    print("\n--- Assertions ---")

    # Run 1: cold — no hits
    s1 = results[0][1]
    assert s1["hits"] == 0, f"Run 1 should have 0 hits, got {s1['hits']}"
    assert s1["misses"] > 0
    print("Run 1 PASS: cold cache, all misses as expected")

    # Run 2: identical — all hits
    s2 = results[1][1]
    assert s2["misses"] == 0, f"Run 2 should have 0 misses, got {s2['misses']}"
    assert s2["hits"] > 0
    print("Run 2 PASS: same pipeline, all hits as expected")

    # Run 3: threshold changed — read should hit, vtk_escape should miss
    s3 = results[2][1]
    assert s3["hits"] >= 1, f"Run 3: read() should be a hit (got {s3['hits']} hits)"
    assert s3["misses"] >= 1, f"Run 3: vtk_escape should miss (got {s3['misses']} misses)"
    print("Run 3 PASS: threshold change causes vtk_escape miss, read stays cached")

    # Run 4: colormap only — vtk_escape should be a hit (threshold=700 is cached from Run 3)
    s4 = results[3][1]
    assert s4["misses"] == 0, f"Run 4 should have 0 misses, got {s4['misses']}"
    assert s4["hits"] > 0
    print("Run 4 PASS: colormap-only change, vtk_escape is a full cache hit")

    # Run 5: repeat of Run 4 — all hits
    s5 = results[4][1]
    assert s5["misses"] == 0, f"Run 5 should have 0 misses, got {s5['misses']}"
    print("Run 5 PASS: all hits")

    # ------------------------------------------------------------------
    # Key insight
    # ------------------------------------------------------------------
    print("\n--- Key insight ---")
    print("  vtk_escape participates in the DAG like any other operation.")
    print("  Its cache key = hash(input_mesh) + hash(function_source).")
    print("  When the threshold changes (Run 3), the input to vtk_escape changes,")
    print("  so the DAG cache misses and the VTK decimator re-runs.")
    print("  When only the colormap changes (Run 4), the mesh pipeline is identical,")
    print("  so vtk_escape is a cache hit — vtkDecimatePro is never called.")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
