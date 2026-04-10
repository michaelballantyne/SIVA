"""bench_ab_comparison.py — A/B comparison switching between two pipeline views.

Realistic scenario: comparing two different views of the same data. Each view
uses different filters and colormaps. After the first pass, switching between
views should be instant (fully cached).

Pipeline A: threshold Temperature > 500 → extract_surface → show(inferno)
Pipeline B: threshold Pressure > 50    → extract_surface → show(viridis)

Edit sequence: A, B, A, B, A, B

Expected behavior:
- A1: cold run, all misses
- B1: read cached; threshold(Pressure>50) + surface new
- A2: read cached + threshold(Temp>500) + surface cached — all hits from A1
- B2: read cached + threshold(Pressure>50) + surface cached — all hits from B1
- A3, B3: all hits

After the first pass through A and B, every subsequent switch is free.
This shows the cache makes A/B toggling instant.

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


def make_pipeline_a(data_path: str) -> str:
    """Pipeline A: threshold Temperature > 500, inferno colormap."""
    return f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=500, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="inferno")
print(f"[A] Temperature>500  pts={{surface.n_points}}")
"""


def make_pipeline_b(data_path: str) -> str:
    """Pipeline B: threshold Pressure > 50, viridis colormap."""
    return f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=50, scalars="Pressure")
surface = filtered.extract_surface()
show(surface, scalars="Pressure", cmap="viridis")
print(f"[B] Pressure>50  pts={{surface.n_points}}")
"""


def main():
    print("=" * 60)
    print("Benchmark: A/B Comparison Switching")
    print("=" * 60)
    print("\nScenario: Scientist toggles between two pipeline views.")
    print("Pipeline A: threshold(Temperature>500) → surface → show(inferno)")
    print("Pipeline B: threshold(Pressure>50)     → surface → show(viridis)")
    print("Edit sequence: A, B, A, B, A, B")
    print("\nCreating 150x150x150 dataset (~3.4M points)...")

    data_path = create_large_dataset(dims=(150, 150, 150), seed=42)
    print(f"Dataset: {data_path}")

    try:
        pipeline_a = make_pipeline_a(data_path)
        pipeline_b = make_pipeline_b(data_path)

        edits = [
            ("A1: Temp>500 (cold)",   pipeline_a),
            ("B1: Pres>50 (cold)",    pipeline_b),
            ("A2: Temp>500 (cached)", pipeline_a),
            ("B2: Pres>50 (cached)",  pipeline_b),
            ("A3: Temp>500 (cached)", pipeline_a),
            ("B3: Pres>50 (cached)",  pipeline_b),
        ]

        result = run_benchmark(
            name="A/B Comparison Switching",
            edits=edits,
            warmup=True,
        )

        print_results_table(result)

        print("\nAnalysis:")
        print("  A1: Cold run — all misses (read + threshold + surface)")
        print("  B1: read cached; threshold(Pressure) + surface new")
        print("  A2: All cached from A1 — instant switch back to A")
        print("  B2: All cached from B1 — instant switch back to B")
        print("  A3, B3: All cached — toggling is free after first pass")

        print("\nPer-edit timing:")
        for e in result.edits:
            speedup_str = (
                f"{e.speedup:.1f}x" if e.speedup != float("inf") else "   inf"
            )
            is_cached_run = "CACHED" if "(cached)" in e.name else "COLD  "
            print(
                f"  [{is_cached_run}] {e.name:<28}  "
                f"H={e.hits}  M={e.misses}  "
                f"cached={e.cached_ms:7.2f}ms  "
                f"speedup={speedup_str}"
            )

        # Compute first-pass vs second-pass comparison
        first_pass_ms = result.edits[0].cached_ms + result.edits[1].cached_ms
        second_pass_ms = result.edits[2].cached_ms + result.edits[3].cached_ms
        if second_pass_ms > 0:
            ab_speedup = first_pass_ms / second_pass_ms
            print(f"\n  First A+B pass:   {first_pass_ms:.1f}ms")
            print(f"  Second A+B pass:  {second_pass_ms:.1f}ms")
            print(f"  Toggle speedup:   {ab_speedup:.1f}x")

        csv_path = Path(__file__).parent / "results_ab_comparison.csv"
        write_csv(result, str(csv_path))
        print(f"\nResults written to: {csv_path}")

    finally:
        cleanup(data_path)

    print("\nDone.")
    return result


if __name__ == "__main__":
    main()
