#!/usr/bin/env python3
"""Demo: vtk_escape for a VTK filter not in PyVista's API.

Shows how to use vtk_escape to call a raw VTK filter (vtkWindowedSincPolyDataFilter)
within a tracked pipeline, with transparent caching on subsequent runs.

Run:
    python3 experiments/tracked-execution/examples/demo_vtk_escape_basic.py
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
# VTK filter function — defined at module scope so import works normally
# ---------------------------------------------------------------------------

def smooth_sinc(m):
    """Windowed sinc smoothing — not directly in PyVista API.

    vtkWindowedSincPolyDataFilter provides PassBand control that PyVista's
    smooth() method does not expose in the same way.
    """
    smoother = vtk.vtkWindowedSincPolyDataFilter()
    smoother.SetInputData(m)
    smoother.SetNumberOfIterations(15)
    smoother.SetPassBand(0.1)
    smoother.Update()
    return pv.wrap(smoother.GetOutput())


# ---------------------------------------------------------------------------
# Pipeline helpers
# ---------------------------------------------------------------------------

def run_pipeline(data_path, dag):
    """Read → threshold → extract_surface → vtk_escape(sinc smoother) → show."""
    dag.begin_run()

    mesh = tracked_read(data_path, dag)
    thresholded = mesh.threshold(value=500, scalars="Temperature")
    surface = thresholded.extract_surface()
    smoothed = vtk_escape(surface, smooth_sinc)

    # Unwrap to inspect results (doesn't touch cache)
    real_surface = object.__getattribute__(surface, "_real")
    real_smoothed = object.__getattribute__(smoothed, "_real")

    dag.end_run()

    return dag.stats(), real_surface.n_points, real_smoothed.n_points


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
    print("Demo: vtk_escape — Windowed Sinc Smoother")
    print("=" * 60)

    print("\nCreating synthetic dataset (60x60x60)...")
    data_path = create_test_dataset(dims=(60, 60, 60))
    print(f"Dataset: {data_path}")

    dag = DAG()

    # ------------------------------------------------------------------
    # Run 1: cold cache — vtk_escape executes the VTK filter
    # ------------------------------------------------------------------
    print("\n--- Run 1: Cold cache (all misses expected) ---")
    t0 = time.perf_counter()
    stats1, surface_pts, smoothed_pts = run_pipeline(data_path, dag)
    elapsed1 = time.perf_counter() - t0
    print(f"Surface points before smoothing : {surface_pts}")
    print(f"Surface points after smoothing  : {smoothed_pts}")
    print(f"Stats : {fmt_stats(stats1)}")
    print(f"Time  : {elapsed1:.4f}s")
    assert stats1["hits"] == 0, f"First run should have zero hits, got {stats1['hits']}"
    assert stats1["misses"] > 0, "First run should have misses"

    # ------------------------------------------------------------------
    # Run 2: identical pipeline — vtk_escape is a cache hit
    # ------------------------------------------------------------------
    print("\n--- Run 2: Same pipeline (all hits expected, smooth_sinc cached) ---")
    t0 = time.perf_counter()
    stats2, surface_pts2, smoothed_pts2 = run_pipeline(data_path, dag)
    elapsed2 = time.perf_counter() - t0
    print(f"Surface points before smoothing : {surface_pts2}")
    print(f"Surface points after smoothing  : {smoothed_pts2}")
    print(f"Stats : {fmt_stats(stats2)}")
    print(f"Time  : {elapsed2:.4f}s")
    assert stats2["misses"] == 0, f"Second run should have zero misses, got {stats2['misses']}"
    assert stats2["hits"] > 0, "Second run should be all hits"
    assert surface_pts == surface_pts2, "Point counts should be identical"
    assert smoothed_pts == smoothed_pts2, "Point counts should be identical"

    if elapsed1 > 0 and elapsed2 > 0:
        speedup = elapsed1 / elapsed2
        print(f"Speedup (run 1 / run 2): {speedup:.1f}x")

    # ------------------------------------------------------------------
    # Explanation
    # ------------------------------------------------------------------
    print("\n--- How vtk_escape caching works ---")
    print("  vtk_escape hashes both the input mesh (via its content hash in the DAG)")
    print("  and smooth_sinc's source code (via inspect.getsource).")
    print("  The combined hash is the cache key for the escape result.")
    print("  On Run 2, both the input hash and the function source are identical,")
    print("  so the smoother's output is returned directly from cache —")
    print("  vtkWindowedSincPolyDataFilter.Update() is never called.")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
