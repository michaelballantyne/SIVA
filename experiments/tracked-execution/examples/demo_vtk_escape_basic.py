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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracked_execution import DAG, execute_pipeline
from utils import cleanup, create_test_dataset


def fmt_stats(stats):
    return (
        f"hits={stats['hits']:3d}  misses={stats['misses']:3d}"
        f"  evictions={stats['evictions']:3d}"
    )


def main():
    print("=" * 60)
    print("Demo: vtk_escape — Windowed Sinc Smoother")
    print("=" * 60)

    print("\nCreating synthetic dataset (60x60x60)...")
    data_path = create_test_dataset(dims=(60, 60, 60))
    print(f"Dataset: {data_path}")

    dag = DAG()

    # The pipeline uses vtk_escape to call vtkWindowedSincPolyDataFilter.
    # PyVista has smooth() but does not directly expose the windowed sinc
    # variant with PassBand control via a single convenient call.
    pipeline = f"""
mesh = read("{data_path}")
thresholded = mesh.threshold(value=500, scalars="Temperature")
surface = thresholded.extract_surface()

def smooth_sinc(m):
    \"\"\"Windowed sinc smoothing — not directly in PyVista API.\"\"\"
    import vtk
    smoother = vtk.vtkWindowedSincPolyDataFilter()
    smoother.SetInputData(m)
    smoother.SetNumberOfIterations(15)
    smoother.SetPassBand(0.1)
    smoother.Update()
    import pyvista as pv
    return pv.wrap(smoother.GetOutput())

smoothed = vtk_escape(surface, smooth_sinc)
show(smoothed, colormap="viridis")
print(f"Surface points before smoothing : {{surface.n_points}}")
print(f"Surface points after smoothing  : {{smoothed.n_points}}")
"""

    # ------------------------------------------------------------------
    # Run 1: cold cache — vtk_escape executes the VTK filter
    # ------------------------------------------------------------------
    print("\n--- Run 1: Cold cache (all misses expected) ---")
    t0 = time.perf_counter()
    result1 = execute_pipeline(pipeline, dag)
    elapsed1 = time.perf_counter() - t0
    print(result1.output.strip())
    print(f"Stats : {fmt_stats(result1.stats)}")
    print(f"Time  : {elapsed1:.4f}s")
    assert result1.stats["hits"] == 0, "First run should have zero hits"
    assert result1.stats["misses"] > 0, "First run should have misses"

    # ------------------------------------------------------------------
    # Run 2: identical pipeline — vtk_escape is a cache hit
    # ------------------------------------------------------------------
    print("\n--- Run 2: Same pipeline (all hits expected, smooth_sinc cached) ---")
    t0 = time.perf_counter()
    result2 = execute_pipeline(pipeline, dag)
    elapsed2 = time.perf_counter() - t0
    print(result2.output.strip())
    print(f"Stats : {fmt_stats(result2.stats)}")
    print(f"Time  : {elapsed2:.4f}s")
    assert result2.stats["misses"] == 0, "Second run should have zero misses"
    assert result2.stats["hits"] > 0, "Second run should be all hits"

    if elapsed1 > 0 and elapsed2 > 0:
        speedup = elapsed1 / elapsed2
        print(f"Speedup (run 1 / run 2): {speedup:.1f}x")

    # ------------------------------------------------------------------
    # Explanation
    # ------------------------------------------------------------------
    print("\n--- How vtk_escape caching works ---")
    print("  vtk_escape hashes both the input mesh (via its content hash in the DAG)")
    print("  and the function source code (via inspect.getsource).")
    print("  The combined hash is the cache key for the escape result.")
    print("  On Run 2, both the input hash and the function source are identical,")
    print("  so the smoother's output is returned directly from cache —")
    print("  vtkWindowedSincPolyDataFilter.Update() is never called.")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
