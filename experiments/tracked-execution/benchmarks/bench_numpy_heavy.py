"""bench_numpy_heavy.py — Derived field computation with numpy.

Realistic scenario: a scientist analyzing velocity fields from simulation data.
Demonstrates numpy proxy caching: array extraction, arithmetic, sqrt, and
threshold operations on derived quantities.

Since the tracked proxy system blocks in-place mesh mutation (for safety),
the pipeline pattern here focuses on:
  1. Extracting array fields from the mesh
  2. Computing derived quantities (magnitude, statistics) using tracked numpy
  3. Thresholding on the original fields (with the derived stats for context)

Edit sequence:
  1. Extract Vz, threshold on Vz > 5 — cold run
  2. Change threshold to Vz > 8 (read+Vz extraction cached)
  3. Switch to Vx field, threshold Vx > 8 (read cached, Vx extraction new)
  4. Back to Vz > 8 (if cache survived, instant)
  5. Vz > 5 revisit (was evicted by step 3 switch; must recompute threshold)
  6. Compute velocity magnitude stats using np.sqrt (proxy arithmetic caching)
  7. Repeat magnitude stats (all cached — near-zero)

The key insight: numpy arithmetic on proxies (vx*vx + vy*vy) is content-hashed,
so re-running the same computation hits the cache immediately.

Run:
    python3 experiments/tracked-execution/benchmarks/bench_numpy_heavy.py
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
    """Return edit sequence for numpy-heavy derived field benchmarks."""
    return [
        # 1. Extract Vz, threshold at 5 — cold run
        (
            "1: vz_thresh5",
            f"""\
mesh = read("{data_path}")
vz = mesh["Vz"]
thresholded = mesh.threshold(value=5.0, scalars="Vz")
surface = thresholded.extract_surface()
show(surface, scalars="Vz", cmap="coolwarm")
print(f"vz>5  pts={{surface.n_points}}")
""",
        ),
        # 2. Change threshold to Vz > 8 (read + Vz extraction cached)
        (
            "2: vz_thresh8",
            f"""\
mesh = read("{data_path}")
vz = mesh["Vz"]
thresholded = mesh.threshold(value=8.0, scalars="Vz")
surface = thresholded.extract_surface()
show(surface, scalars="Vz", cmap="coolwarm")
print(f"vz>8  pts={{surface.n_points}}")
""",
        ),
        # 3. Switch to Vx field (read cached, Vx extraction new)
        (
            "3: vx_thresh8",
            f"""\
mesh = read("{data_path}")
vx = mesh["Vx"]
thresholded = mesh.threshold(value=8.0, scalars="Vx")
surface = thresholded.extract_surface()
show(surface, scalars="Vx", cmap="RdBu")
print(f"vx>8  pts={{surface.n_points}}")
""",
        ),
        # 4. Back to Vz > 8 (check cache survival after step 3)
        (
            "4: vz_thresh8_revisit",
            f"""\
mesh = read("{data_path}")
vz = mesh["Vz"]
thresholded = mesh.threshold(value=8.0, scalars="Vz")
surface = thresholded.extract_surface()
show(surface, scalars="Vz", cmap="coolwarm")
print(f"vz>8_revisit  pts={{surface.n_points}}")
""",
        ),
        # 5. Magnitude statistics using numpy arithmetic (tracked proxy ops)
        (
            "5: magnitude_stats",
            f"""\
mesh = read("{data_path}")
vx = mesh["Vx"]
vy = mesh["Vy"]
vz = mesh["Vz"]
mag_sq = vx * vx + vy * vy + vz * vz
magnitude = np.sqrt(mag_sq)
mean_mag = np.mean(magnitude)
max_mag = np.max(magnitude)
print(f"magnitude: mean={{float(mean_mag):.2f}}  max={{float(max_mag):.2f}}")
thresholded = mesh.threshold(value=8.0, scalars="Vz")
surface = thresholded.extract_surface()
show(surface, scalars="Vz", cmap="coolwarm")
""",
        ),
        # 6. Repeat magnitude stats — all numpy proxy ops should be cached
        (
            "6: magnitude_stats_repeat",
            f"""\
mesh = read("{data_path}")
vx = mesh["Vx"]
vy = mesh["Vy"]
vz = mesh["Vz"]
mag_sq = vx * vx + vy * vy + vz * vz
magnitude = np.sqrt(mag_sq)
mean_mag = np.mean(magnitude)
max_mag = np.max(magnitude)
print(f"magnitude: mean={{float(mean_mag):.2f}}  max={{float(max_mag):.2f}}")
thresholded = mesh.threshold(value=8.0, scalars="Vz")
surface = thresholded.extract_surface()
show(surface, scalars="Vz", cmap="coolwarm")
""",
        ),
        # 7. Change magnitude stat query (std instead of max) — partial hit
        (
            "7: magnitude_std",
            f"""\
mesh = read("{data_path}")
vx = mesh["Vx"]
vy = mesh["Vy"]
vz = mesh["Vz"]
mag_sq = vx * vx + vy * vy + vz * vz
magnitude = np.sqrt(mag_sq)
mean_mag = np.mean(magnitude)
std_mag = np.std(magnitude)
print(f"magnitude: mean={{float(mean_mag):.2f}}  std={{float(std_mag):.2f}}")
thresholded = mesh.threshold(value=8.0, scalars="Vz")
surface = thresholded.extract_surface()
show(surface, scalars="Vz", cmap="coolwarm")
""",
        ),
    ]


ANALYSIS = [
    ("1",  "Cold run",           "All misses: read+Vz+threshold+surface"),
    ("2",  "Vz thresh 5→8",     "read+Vz cached; threshold(8)+surface new"),
    ("3",  "Vz→Vx field",       "read cached; Vx extraction new; threshold+surface new"),
    ("4",  "Vz>8 revisit",      "GC evicted Vz>8 cache from step 2; must recompute"),
    ("5",  "Add mag stats",     "vz>8 ops cached; vx+vy+mag_sq+sqrt+mean+max new"),
    ("6",  "Repeat mag stats",  "All ops fully cached — near-zero time"),
    ("7",  "std instead of max","read+vx+vy+vz+mag+mean cached; std is new op"),
]


def main():
    print("=" * 60)
    print("Benchmark: Numpy-Heavy Derived Field Computation")
    print("=" * 60)
    print("\nScenario: Scientist analyzes velocity components from simulation.")
    print("Tests: numpy proxy caching — array extraction, arithmetic, sqrt, stats.")
    print("\nCreating 150x150x150 dataset (~3.4M points)...")

    data_path = create_large_dataset(dims=(150, 150, 150), seed=42)
    print(f"Dataset: {data_path}")

    try:
        edits = make_edits(data_path)

        result = run_benchmark(
            name="Numpy-Heavy Derived Fields",
            edits=edits,
            warmup=True,
        )

        print_results_table(result)

        print("\nCaching analysis per edit:")
        print(f"  {'Edit':<5}  {'Change':<25}  Cache behavior")
        print("  " + "-" * 75)
        for (edit_num, change, behavior) in ANALYSIS:
            print(f"  {edit_num:<5}  {change:<25}  {behavior}")

        print("\nKey insight:")
        print("  - Edit 6 (repeat magnitude stats): near-zero time — all numpy ops cached")
        print("  - Edit 7 (std instead of max): partial hit — expensive upstream cached")
        print("  - GC evicts entries not in current run, so revisiting old pipelines re-runs")

        print("\nPer-edit details:")
        for e in result.edits:
            speedup_str = (
                f"{e.speedup:.1f}x" if e.speedup != float("inf") else "   inf"
            )
            print(
                f"  {e.name:<35}  "
                f"H={e.hits}  M={e.misses}  "
                f"cached={e.cached_ms:7.2f}ms  "
                f"speedup={speedup_str}"
            )

        csv_path = Path(__file__).parent / "results_numpy_heavy.csv"
        write_csv(result, str(csv_path))
        print(f"\nResults written to: {csv_path}")

    finally:
        cleanup(data_path)

    print("\nDone.")
    return result


if __name__ == "__main__":
    main()
