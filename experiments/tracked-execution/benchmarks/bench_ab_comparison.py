"""bench_ab_comparison.py — A/B comparison switching between two pipeline views.

Realistic scenario: comparing two different visual representations of the same
underlying mesh computation. Both pipelines share the same read → threshold →
extract_surface chain; only the show() kwargs differ.

This is the key insight: if two views share expensive upstream computation, the
cache makes switching between them instant — you only pay for the unique parts.

Pipeline A: read → threshold(Temperature>500) → surface → show(inferno, opacity=1.0)
Pipeline B: read → threshold(Temperature>500) → surface → show(viridis, opacity=0.5)

(Same underlying mesh, different display parameters)

Edit sequence: A, B, A, B, A, B

After A1 (cold), all subsequent runs should show high hit rates because the
mesh computation is shared and cached. Only show() kwargs differ.

For contrast, we also include "hard A/B" — two views with different thresholds
where GC evicts the other's results on each switch — to show when caching doesn't help.

Run:
    python3 experiments/tracked-execution/benchmarks/bench_ab_comparison.py
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


def make_soft_ab_edits(data_path: str):
    """A/B with same mesh, different display params — cache wins strongly."""
    # Both A and B share the same read+threshold+surface chain
    pipeline_a = f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=500, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="inferno", opacity=1.0)
print(f"[A] inferno pts={{surface.n_points}}")
"""
    pipeline_b = f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=500, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="viridis", opacity=0.5)
print(f"[B] viridis pts={{surface.n_points}}")
"""
    return [
        ("A1: inferno (cold)",   pipeline_a),
        ("B1: viridis",          pipeline_b),
        ("A2: inferno",          pipeline_a),
        ("B2: viridis",          pipeline_b),
        ("A3: inferno",          pipeline_a),
        ("B3: viridis",          pipeline_b),
    ]


def make_hard_ab_edits(data_path: str):
    """A/B with different thresholds — GC evicts each on switch (no caching benefit)."""
    pipeline_a = f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=400, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="inferno")
print(f"[A] temp>400 pts={{surface.n_points}}")
"""
    pipeline_b = f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=700, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="viridis")
print(f"[B] temp>700 pts={{surface.n_points}}")
"""
    return [
        ("A1: temp>400 (cold)",  pipeline_a),
        ("B1: temp>700 (cold)",  pipeline_b),
        ("A2: temp>400 (re-run)",pipeline_a),
        ("B2: temp>700 (re-run)",pipeline_b),
        ("A3: temp>400 (re-run)",pipeline_a),
        ("B3: temp>700 (re-run)",pipeline_b),
    ]


def main():
    print("=" * 60)
    print("Benchmark: A/B Comparison Switching")
    print("=" * 60)
    print("\nScenario: Scientist toggles between two pipeline views.")

    print("\nCreating 150x150x150 dataset (~3.4M points)...")
    data_path = create_large_dataset(dims=(150, 150, 150), seed=42)
    print(f"Dataset: {data_path}")

    try:
        # --- Soft A/B: shared mesh, different display params ---
        print("\n" + "-" * 60)
        print("Soft A/B: Same threshold, different colormaps/opacity")
        print("-" * 60)
        print("  Pipeline A: threshold(T>500) → surface → show(inferno, opacity=1.0)")
        print("  Pipeline B: threshold(T>500) → surface → show(viridis, opacity=0.5)")
        print("  (Mesh computation shared — only display params differ)")
        soft_edits = make_soft_ab_edits(data_path)

        soft_result = run_benchmark(
            name="A/B Comparison — Soft (shared mesh)",
            edits=soft_edits,
            warmup=True,
        )
        print_results_table(soft_result)

        print("\n  Analysis:")
        print("  A1: Cold — all misses (read+threshold+surface)")
        print("  B1-B3, A2-A3: Mesh ops fully cached; only show() kwargs differ")
        print("  Result: instant A/B switching after first build")

        print("\n  Per-edit details:")
        for e in soft_result.edits:
            speedup_str = (
                f"{e.speedup:.1f}x" if e.speedup != float("inf") else "   inf"
            )
            print(
                f"    {e.name:<28}  H={e.hits}  M={e.misses}  "
                f"cached={e.cached_ms:7.2f}ms  speedup={speedup_str}"
            )

        # --- Hard A/B: different thresholds — GC evicts ---
        print("\n" + "-" * 60)
        print("Hard A/B: Different thresholds (GC evicts on each switch)")
        print("-" * 60)
        print("  Pipeline A: threshold(T>400) → surface → show(inferno)")
        print("  Pipeline B: threshold(T>700) → surface → show(viridis)")
        print("  (Different mesh results — GC cannot keep both alive)")
        hard_edits = make_hard_ab_edits(data_path)

        hard_result = run_benchmark(
            name="A/B Comparison — Hard (different thresholds)",
            edits=hard_edits,
            warmup=True,
        )
        print_results_table(hard_result)

        print("\n  Analysis:")
        print("  Each switch evicts the other's threshold+surface (GC)")
        print("  Only read() stays cached across all runs (shared upstream)")
        print("  Shows the GC trade-off: safety vs cross-pipeline reuse")

        print("\n  Per-edit details:")
        for e in hard_result.edits:
            speedup_str = (
                f"{e.speedup:.1f}x" if e.speedup != float("inf") else "   inf"
            )
            print(
                f"    {e.name:<30}  H={e.hits}  M={e.misses}  "
                f"cached={e.cached_ms:7.2f}ms  speedup={speedup_str}"
            )

        # --- Summary comparison ---
        print("\n" + "=" * 60)
        print("Comparison: Soft vs Hard A/B Switching")
        print("=" * 60)
        print(f"  Soft A/B (shared mesh):       avg cached = {soft_result.avg_cached_ms:.1f}ms")
        print(f"  Hard A/B (different thresh):  avg cached = {hard_result.avg_cached_ms:.1f}ms")
        speedup_diff = hard_result.avg_cached_ms / soft_result.avg_cached_ms if soft_result.avg_cached_ms > 0 else float("inf")
        print(f"  Shared-mesh speedup:          {speedup_diff:.1f}x faster than distinct-mesh A/B")
        print()
        print("  Design implication: For fastest A/B switching, structure pipelines")
        print("  to maximize shared upstream computation (common read + filters).")
        print("  Only the display parameters (show() kwargs) need to differ.")

        # Write CSVs
        soft_csv = Path(__file__).parent / "results_ab_soft.csv"
        hard_csv = Path(__file__).parent / "results_ab_hard.csv"
        write_csv(soft_result, str(soft_csv))
        write_csv(hard_result, str(hard_csv))
        print(f"\nResults written to: {soft_csv}")
        print(f"                    {hard_csv}")

    finally:
        cleanup(data_path)

    print("\nDone.")
    return soft_result  # return soft for run_all summary


if __name__ == "__main__":
    main()
