"""bench_parameter_sweep.py — Parameter sweep benchmark (threshold values).

Realistic scenario: a scientist is adjusting a threshold value to isolate
a feature. Each edit changes only the threshold — the read() operation should
always be a cache hit.

Expected behavior:
- Edit 1 (cold): all misses — read + threshold + surface all execute
- Edits 2-7: read() is always a hit; threshold + surface are misses (new value)
- Speedup: 10-100x since only the threshold downstream re-executes, not read()

Run:
    python3 experiments/tracked-execution/benchmarks/bench_parameter_sweep.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running from anywhere
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_harness import (
    BenchmarkResult,
    cleanup,
    create_large_dataset,
    run_benchmark,
    print_results_table,
    write_csv,
)


# Threshold values representing a scientist narrowing in on a feature
THRESHOLD_VALUES = [400, 450, 500, 550, 600, 650, 700]


def make_pipeline(data_path: str, threshold: int) -> str:
    """Pipeline: read → threshold → extract_surface → show."""
    return f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value={threshold}, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="viridis")
print(f"threshold={threshold}  surface_points={{surface.n_points}}")
"""


def main():
    print("=" * 60)
    print("Benchmark: Parameter Sweep (threshold values)")
    print("=" * 60)
    print("\nScenario: Scientist adjusts threshold to isolate a feature.")
    print("Pipeline: read → threshold → extract_surface → show")
    print(f"Threshold values: {THRESHOLD_VALUES}")
    print("\nCreating 150x150x150 dataset (~3.4M points)...")

    data_path = create_large_dataset(dims=(150, 150, 150), seed=42)
    print(f"Dataset: {data_path}")

    try:
        edits = [
            (f"threshold={t}", make_pipeline(data_path, t))
            for t in THRESHOLD_VALUES
        ]

        result = run_benchmark(
            name="Parameter Sweep (threshold)",
            edits=edits,
            warmup=True,
        )

        print_results_table(result)

        print("\nAnalysis:")
        print("  - Edit 1 (cold): all operations execute (all misses)")
        print("  - Edits 2-7: read() is a cache hit; threshold+surface re-execute")
        print("  - Speedup comes from skipping the slow file read on each edit")

        # Show per-edit speedup trend
        print("\nPer-edit details:")
        for e in result.edits:
            read_saved = " (read cached)" if e.hits >= 1 else " (cold)"
            speedup_str = f"{e.speedup:.1f}x" if e.speedup != float("inf") else "inf"
            print(
                f"  {e.name:<16}  cached={e.cached_ms:7.1f}ms  "
                f"uncached={e.uncached_ms:7.1f}ms  speedup={speedup_str:>8}{read_saved}"
            )

        # Optionally write CSV
        csv_path = Path(__file__).parent / "results_parameter_sweep.csv"
        write_csv(result, str(csv_path))
        print(f"\nResults written to: {csv_path}")

    finally:
        cleanup(data_path)

    print("\nDone.")
    return result


if __name__ == "__main__":
    main()
