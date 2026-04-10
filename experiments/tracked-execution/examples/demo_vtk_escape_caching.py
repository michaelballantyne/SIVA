#!/usr/bin/env python3
"""Demo: vtk_escape cache behaviour across parameter changes.

Shows that vtk_escape participates correctly in the DAG:
  - Its cache key depends on the input hash, so upstream changes propagate.
  - Changing only downstream parameters (colormap) leaves vtk_escape cached.
  - Hit/miss stats are printed after each simulated pipeline run.

Run:
    python3 experiments/tracked-execution/examples/demo_vtk_escape_caching.py
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


def build_pipeline(data_path, threshold, colormap):
    """Return a pipeline that thresholds, then applies a VTK custom filter."""
    return f"""
mesh = read("{data_path}")
thresholded = mesh.threshold(value={threshold:.1f}, scalars="Temperature")
surface = thresholded.extract_surface()

def apply_custom_filter(m):
    \"\"\"Decimate the surface — a post-processing step via VTK.\"\"\"
    import vtk
    dec = vtk.vtkDecimatePro()
    dec.SetInputData(m)
    dec.SetTargetReduction(0.3)
    dec.PreserveTopologyOn()
    dec.Update()
    import pyvista as pv
    return pv.wrap(dec.GetOutput())

decimated = vtk_escape(surface, apply_custom_filter)
show(decimated, scalars="Temperature", cmap="{colormap}")
print(f"threshold={{thresholded.n_cells}} cells -> decimated={{decimated.n_cells}} cells")
"""


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
        (700.0, "viridis",  "Run 3: threshold changed — read cached; threshold/vtk_escape miss"),
        (700.0, "plasma",   "Run 4: colormap only — mesh ops (incl. vtk_escape) all hit"),
        (700.0, "plasma",   "Run 5: repeat of Run 4 — all hits"),
    ]

    results = []
    for threshold, colormap, description in runs:
        pipeline = build_pipeline(data_path, threshold, colormap)
        t0 = time.perf_counter()
        result = execute_pipeline(pipeline, dag)
        elapsed = time.perf_counter() - t0
        results.append((description, result.stats, elapsed))

    # ------------------------------------------------------------------
    # Print table
    # ------------------------------------------------------------------
    print(f"\n{'Run':<6}  {'Hits':>5}  {'Misses':>7}  {'Evictions':>10}  {'Time(s)':>8}")
    print("-" * 55)
    for i, (description, stats, elapsed) in enumerate(results, 1):
        print(
            f"{i:<6}  {stats['hits']:>5}  {stats['misses']:>7}"
            f"  {stats['evictions']:>10}  {elapsed:>8.4f}"
        )
        print(f"       {description}")

    # ------------------------------------------------------------------
    # Assertions and explanations
    # ------------------------------------------------------------------
    print("\n--- Assertions ---")

    # Run 1: cold — no hits
    s1 = results[0][1]
    assert s1["hits"] == 0, f"Run 1 should have 0 hits, got {s1['hits']}"
    assert s1["misses"] > 0, "Run 1 should have misses"
    print("Run 1 PASS: cold cache, all misses as expected")

    # Run 2: identical — all hits
    s2 = results[1][1]
    assert s2["misses"] == 0, f"Run 2 should have 0 misses, got {s2['misses']}"
    assert s2["hits"] > 0, "Run 2 should have hits"
    print("Run 2 PASS: same pipeline, all hits as expected")

    # Run 3: threshold changed — read should hit, vtk_escape should miss
    s3 = results[2][1]
    assert s3["hits"] >= 1, f"Run 3: read() should be a hit (got {s3['hits']} hits)"
    assert s3["misses"] >= 1, f"Run 3: vtk_escape should miss (got {s3['misses']} misses)"
    print("Run 3 PASS: threshold change causes vtk_escape miss, read stays cached")

    # Run 4: colormap only — vtk_escape should be a hit (threshold=700 is cached from Run 3)
    s4 = results[3][1]
    assert s4["misses"] == 0, f"Run 4 should have 0 misses, got {s4['misses']}"
    assert s4["hits"] > 0, "Run 4 should have hits"
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
    print("  so its cache misses and the VTK filter re-runs.")
    print("  When only the colormap changes (Run 4), the mesh pipeline is identical,")
    print("  so vtk_escape is a cache hit — the VTK filter is never called.")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
