#!/usr/bin/env python3
"""Demo: content-addressed caching with tracked execution.

Shows how a DAG transparently caches pipeline results so that re-running
the same pipeline is free, and only changed steps re-execute.

Run:
    python3 experiments/tracked-execution/examples/demo_caching.py
"""

import sys
import time
from pathlib import Path

# Allow import without pip-installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracked_execution import DAG, execute_pipeline

from utils import cleanup, create_test_dataset

sys.path.insert(0, str(Path(__file__).resolve().parent))


def fmt_stats(stats):
    return (
        f"hits={stats['hits']:3d}  misses={stats['misses']:3d}"
        f"  evictions={stats['evictions']:3d}"
    )


def main():
    print("=" * 60)
    print("Demo: Content-Addressed Caching")
    print("=" * 60)

    # Create a 100x100x100 mesh (1M points) so caching speedup is visible
    print("\nCreating synthetic dataset (100x100x100, ~1M points)...")
    data_path = create_test_dataset(dims=(100, 100, 100))
    print(f"Dataset saved to: {data_path}")

    dag = DAG()

    # Pipeline: read -> threshold -> extract_surface
    pipeline = f"""
mesh = read("{data_path}")
thresholded = mesh.threshold(value=500.0, scalars="Temperature")
surface = thresholded.extract_surface()
print(f"Run complete: {{surface.n_points}} surface points")
"""

    # ------------------------------------------------------------------
    # Run 1: cold cache — all misses
    # ------------------------------------------------------------------
    print("\n--- Run 1: Cold cache (all misses expected) ---")
    t0 = time.perf_counter()
    result1 = execute_pipeline(pipeline, dag)
    t1 = time.perf_counter()
    print(result1.output.strip())
    print(f"Stats : {fmt_stats(result1.stats)}")
    print(f"Time  : {t1 - t0:.3f}s")
    assert result1.stats["hits"] == 0, "First run should have zero hits"
    assert result1.stats["misses"] > 0, "First run should have misses"

    # ------------------------------------------------------------------
    # Run 2: identical pipeline — all hits
    # ------------------------------------------------------------------
    print("\n--- Run 2: Same pipeline (all hits expected) ---")
    t0 = time.perf_counter()
    result2 = execute_pipeline(pipeline, dag)
    t1 = time.perf_counter()
    print(result2.output.strip())
    print(f"Stats : {fmt_stats(result2.stats)}")
    print(f"Time  : {t1 - t0:.3f}s")
    assert result2.stats["misses"] == 0, "Second run should have zero misses"
    assert result2.stats["hits"] > 0, "Second run should be all hits"

    speedup_1to2 = (t1 - t0)
    print(f"(cached run is typically 10-100x faster than cold)")

    # ------------------------------------------------------------------
    # Run 3: changed threshold — read cached, threshold misses
    # ------------------------------------------------------------------
    pipeline_changed = f"""
mesh = read("{data_path}")
thresholded = mesh.threshold(value=700.0, scalars="Temperature")
surface = thresholded.extract_surface()
print(f"Run complete: {{surface.n_points}} surface points")
"""
    print("\n--- Run 3: Changed threshold=700 (read cached, threshold misses) ---")
    t0 = time.perf_counter()
    result3 = execute_pipeline(pipeline_changed, dag)
    t1 = time.perf_counter()
    print(result3.output.strip())
    print(f"Stats : {fmt_stats(result3.stats)}")
    print(f"Time  : {t1 - t0:.3f}s")
    assert result3.stats["hits"] >= 1, "read() should be a cache hit"
    assert result3.stats["misses"] >= 1, "threshold(700) should be a miss"
    print("(read() was a hit — file not re-read; threshold re-computed)")

    # ------------------------------------------------------------------
    # Run 4: repeat of Run 3 — all hits again
    # ------------------------------------------------------------------
    print("\n--- Run 4: Repeat of changed pipeline (all hits) ---")
    t0 = time.perf_counter()
    result4 = execute_pipeline(pipeline_changed, dag)
    t1 = time.perf_counter()
    print(result4.output.strip())
    print(f"Stats : {fmt_stats(result4.stats)}")
    print(f"Time  : {t1 - t0:.3f}s")
    assert result4.stats["misses"] == 0

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("\n--- Summary ---")
    print(f"Run 1 (cold)              : {fmt_stats(result1.stats)}")
    print(f"Run 2 (same pipeline)     : {fmt_stats(result2.stats)}")
    print(f"Run 3 (threshold changed) : {fmt_stats(result3.stats)}")
    print(f"Run 4 (same as Run 3)     : {fmt_stats(result4.stats)}")
    print("\nKey insight:")
    print("  - Run 2 is entirely from cache (zero misses).")
    print("  - Run 3 re-uses the cached read() but re-computes threshold+surface.")
    print("  - Run 4 caches the new threshold+surface immediately.")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
