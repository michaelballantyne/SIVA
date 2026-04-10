#!/usr/bin/env python3
"""Demo: vtk_escape_multi — combining multiple tracked meshes.

Shows using vtk_escape_multi to merge two separately-thresholded regions
(hot and cold) into a single mesh. The merge result is cached, and only
re-runs when one of its inputs changes.

Run:
    python3 experiments/tracked-execution/examples/demo_vtk_escape_multi.py
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


def build_pipeline(data_path, hot_threshold, cold_threshold, colormap):
    """Pipeline that thresholds two regions and merges them with vtk_escape_multi."""
    return f"""
mesh = read("{data_path}")

# Isolate hot region (above hot_threshold)
hot = mesh.threshold(value={hot_threshold:.1f}, scalars="Temperature")

# Isolate cold region (below cold_threshold, using invert=True)
cold = mesh.threshold(value={cold_threshold:.1f}, scalars="Temperature", invert=True)

def combine_regions(hot_mesh, cold_mesh):
    \"\"\"Merge hot and cold regions into one mesh for combined visualization.\"\"\"
    return hot_mesh.merge(cold_mesh)

combined = vtk_escape_multi([hot, cold], combine_regions)
show(combined, scalars="Temperature", cmap="{colormap}")
print(
    f"hot(>{hot_threshold:.0f})={{hot.n_points}} pts  "
    f"cold(<{cold_threshold:.0f})={{cold.n_points}} pts  "
    f"combined={{combined.n_points}} pts"
)
"""


def main():
    print("=" * 60)
    print("Demo: vtk_escape_multi — Merging Two Tracked Meshes")
    print("=" * 60)

    print("\nCreating synthetic dataset (65x65x65)...")
    data_path = create_test_dataset(dims=(65, 65, 65))
    print(f"Dataset: {data_path}")

    dag = DAG()

    runs = [
        # (hot_threshold, cold_threshold, colormap, description)
        (700.0, 300.0, "coolwarm",
         "Run 1: cold cache — both thresholds and vtk_escape_multi all miss"),
        (700.0, 300.0, "coolwarm",
         "Run 2: same pipeline — all hits"),
        (700.0, 300.0, "plasma",
         "Run 3: colormap only — all mesh ops (incl. vtk_escape_multi) hit"),
        (750.0, 300.0, "coolwarm",
         "Run 4: hot threshold raised — hot misses, vtk_escape_multi misses, cold hits"),
        (750.0, 250.0, "coolwarm",
         "Run 5: cold threshold lowered — cold misses, vtk_escape_multi misses, hot hits"),
        (750.0, 250.0, "coolwarm",
         "Run 6: repeat of Run 5 — all hits"),
    ]

    results = []
    for hot_threshold, cold_threshold, colormap, description in runs:
        pipeline = build_pipeline(data_path, hot_threshold, cold_threshold, colormap)
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

    # Run 3: colormap only — mesh pipeline unchanged, vtk_escape_multi is a hit
    s3 = results[2][1]
    assert s3["misses"] == 0, f"Run 3 should have 0 misses, got {s3['misses']}"
    print("Run 3 PASS: colormap-only change, vtk_escape_multi is a cache hit")

    # Run 4: hot threshold changed — hot misses, vtk_escape_multi misses, cold hits
    s4 = results[3][1]
    # read() + cold = at least 2 hits; hot + vtk_escape_multi = at least 2 misses
    assert s4["hits"] >= 2, (
        f"Run 4: read() and cold region should hit (got {s4['hits']} hits)"
    )
    assert s4["misses"] >= 2, (
        f"Run 4: hot threshold and vtk_escape_multi should miss (got {s4['misses']} misses)"
    )
    print("Run 4 PASS: hot change propagates into vtk_escape_multi miss")

    # Run 5: cold threshold changed — cold misses, vtk_escape_multi misses, hot hits
    s5 = results[4][1]
    assert s5["hits"] >= 2, (
        f"Run 5: read() and hot region should hit (got {s5['hits']} hits)"
    )
    assert s5["misses"] >= 2, (
        f"Run 5: cold threshold and vtk_escape_multi should miss (got {s5['misses']} misses)"
    )
    print("Run 5 PASS: cold change propagates into vtk_escape_multi miss")

    # Run 6: all hits
    s6 = results[5][1]
    assert s6["misses"] == 0, f"Run 6 should have 0 misses, got {s6['misses']}"
    print("Run 6 PASS: all hits")

    # ------------------------------------------------------------------
    # Key insight
    # ------------------------------------------------------------------
    print("\n--- Key insight ---")
    print("  vtk_escape_multi hashes ALL of its input proxies, not just one.")
    print("  Cache key = hash(hot_input) + hash(cold_input) + hash(function_source).")
    print("  If either input changes, the combined hash changes and the merge re-runs.")
    print("  Run 4: only the hot region changed -> vtk_escape_multi re-ran.")
    print("  Run 5: only the cold region changed -> vtk_escape_multi re-ran again.")
    print("  In each case, the unchanged upstream branch stayed cached.")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
