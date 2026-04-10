"""bench_pipeline_refinement.py — Gamma-style incremental pipeline edits.

Realistic scenario: a scientist building up a visualization step by step,
refining the pipeline with each iteration. Demonstrates how the cache makes
each incremental edit pay only for what changed.

Edit sequence:
  1. Just load and show raw data
  2. Add threshold on Temperature > 500
  3. Change threshold to Temperature > 600 (upstream cached)
  4. Add extract_surface after threshold
  5. Change colormap (everything cached except show)
  6. Add a clip plane (threshold cached, clip is new)
  7. Remove the clip plane, back to step 4's pipeline
  8. Change back to threshold > 500 (was cached earlier — evicted, must recompute)

Expected behavior:
- Each edit re-uses cached results from upstream unchanged steps
- Step 5 (colormap change): mesh ops fully cached, only show() changes
- Step 7 (remove clip): threshold+surface cached, clip evicted
- Step 8 (old threshold revisited): evicted from GC, must recompute

Run:
    python3 experiments/tracked-execution/benchmarks/bench_pipeline_refinement.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_harness import (
    cleanup,
    create_large_dataset,
    run_benchmark,
    print_results_table,
    write_csv,
)


def make_edits(data_path: str):
    """Return the sequence of pipeline edits for incremental refinement."""
    return [
        # 1. Just load and show raw data
        (
            "1: load+show_raw",
            f"""\
mesh = read("{data_path}")
show(mesh, scalars="Temperature", cmap="viridis")
print(f"raw pts={{mesh.n_points}}")
""",
        ),
        # 2. Add threshold on Temperature > 500
        (
            "2: add_threshold500",
            f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=500, scalars="Temperature")
show(filtered, scalars="Temperature", cmap="viridis")
print(f"threshold=500  pts={{filtered.n_points}}")
""",
        ),
        # 3. Change threshold to > 600 (read cached)
        (
            "3: change_threshold600",
            f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=600, scalars="Temperature")
show(filtered, scalars="Temperature", cmap="viridis")
print(f"threshold=600  pts={{filtered.n_points}}")
""",
        ),
        # 4. Add extract_surface after threshold
        (
            "4: add_surface",
            f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=600, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="viridis")
print(f"threshold=600  surface pts={{surface.n_points}}")
""",
        ),
        # 5. Change colormap (mesh ops fully cached)
        (
            "5: change_cmap_plasma",
            f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=600, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="plasma")
print(f"threshold=600  cmap=plasma  pts={{surface.n_points}}")
""",
        ),
        # 6. Add a clip plane (threshold cached, clip is new)
        (
            "6: add_clip_plane",
            f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=600, scalars="Temperature")
surface = filtered.extract_surface()
clipped = surface.clip(normal="x", origin=(50, 0, 0))
show(clipped, scalars="Temperature", cmap="plasma")
print(f"threshold=600  clip=x50  pts={{clipped.n_points}}")
""",
        ),
        # 7. Remove the clip plane (back to step 4/5 pipeline)
        (
            "7: remove_clip",
            f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=600, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="plasma")
print(f"threshold=600  no_clip  pts={{surface.n_points}}")
""",
        ),
        # 8. Change back to threshold > 500 (was evicted when we moved to 600)
        (
            "8: back_to_threshold500",
            f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=500, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="plasma")
print(f"threshold=500  pts={{surface.n_points}}")
""",
        ),
    ]


ANALYSIS = [
    ("1→2", "Add threshold",       "read cached; threshold+show re-run"),
    ("2→3", "threshold 500→600",   "read hit; threshold+show re-run"),
    ("3→4", "Add extract_surface", "read+threshold cached; surface+show new"),
    ("4→5", "Change colormap",     "all mesh ops cached; only show() changes"),
    ("5→6", "Add clip plane",      "read+threshold+surface cached; clip+show new"),
    ("6→7", "Remove clip",         "read+threshold+surface cached again; clip evicted"),
    ("7→8", "threshold 600→500",   "read hit; threshold 500 evicted — must recompute"),
]


def main():
    print("=" * 60)
    print("Benchmark: Gamma-style Pipeline Refinement")
    print("=" * 60)
    print("\nScenario: Scientist builds up a visualization step by step.")
    print("Pipeline grows and changes incrementally.")
    print("\nCreating 150x150x150 dataset (~3.4M points)...")

    data_path = create_large_dataset(dims=(150, 150, 150), seed=42)
    print(f"Dataset: {data_path}")

    try:
        edits = make_edits(data_path)

        result = run_benchmark(
            name="Pipeline Refinement (Gamma-style)",
            edits=edits,
            warmup=True,
        )

        print_results_table(result)

        print("\nCaching analysis per transition:")
        print(f"  {'Transition':<8}  {'Change':<25}  Cache behavior")
        print("  " + "-" * 75)
        for (trans, change, behavior) in ANALYSIS:
            print(f"  {trans:<8}  {change:<25}  {behavior}")

        print("\nPer-edit details:")
        for e in result.edits:
            speedup_str = (
                f"{e.speedup:.1f}x" if e.speedup != float("inf") else "   inf"
            )
            print(
                f"  {e.name:<28}  "
                f"H={e.hits}  M={e.misses}  "
                f"cached={e.cached_ms:7.2f}ms  "
                f"uncached={e.uncached_ms:7.2f}ms  "
                f"speedup={speedup_str}"
            )

        csv_path = Path(__file__).parent / "results_pipeline_refinement.csv"
        write_csv(result, str(csv_path))
        print(f"\nResults written to: {csv_path}")

    finally:
        cleanup(data_path)

    print("\nDone.")
    return result


if __name__ == "__main__":
    main()
