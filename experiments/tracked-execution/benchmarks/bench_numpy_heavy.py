"""bench_numpy_heavy.py — Derived field computation with numpy.

Realistic scenario: computing derived quantities from raw simulation data.
Velocity magnitude from Vx/Vy/Vz components, then thresholding on the result.

Edit sequence:
  1. Full pipeline: magnitude = sqrt(Vx^2 + Vy^2 + Vz^2), threshold at 0.5
  2. Change threshold to 0.6 (velocity extraction + magnitude cached)
  3. Switch to threshold on Vz component only (read cached, simpler extraction)
  4. Back to magnitude threshold at 0.5 (if cache survived, instant)
  5. Use magnitude threshold at 0.6 (was cached in step 2 — verify hit)

Tests numpy proxy overhead and caching of array arithmetic operations.

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
        # 1. Compute magnitude and threshold at 0.5
        (
            "1: magnitude_thresh0.5",
            f"""\
mesh = read("{data_path}")
vx = mesh["Vx"]
vy = mesh["Vy"]
vz = mesh["Vz"]
mag_sq = vx * vx + vy * vy + vz * vz
magnitude = np.sqrt(mag_sq)
mesh["Magnitude"] = magnitude
thresholded = mesh.threshold(value=0.5, scalars="Magnitude")
surface = thresholded.extract_surface()
show(surface, scalars="Magnitude", cmap="hot")
print(f"mag_thresh=0.5  pts={{surface.n_points}}")
""",
        ),
        # 2. Change threshold to 0.6 (velocity extraction + magnitude cached)
        (
            "2: magnitude_thresh0.6",
            f"""\
mesh = read("{data_path}")
vx = mesh["Vx"]
vy = mesh["Vy"]
vz = mesh["Vz"]
mag_sq = vx * vx + vy * vy + vz * vz
magnitude = np.sqrt(mag_sq)
mesh["Magnitude"] = magnitude
thresholded = mesh.threshold(value=0.6, scalars="Magnitude")
surface = thresholded.extract_surface()
show(surface, scalars="Magnitude", cmap="hot")
print(f"mag_thresh=0.6  pts={{surface.n_points}}")
""",
        ),
        # 3. Switch to Vz component only (simpler, read cached)
        (
            "3: vz_component_only",
            f"""\
mesh = read("{data_path}")
thresholded = mesh.threshold(value=5.0, scalars="Vz")
surface = thresholded.extract_surface()
show(surface, scalars="Vz", cmap="coolwarm")
print(f"vz_thresh=5.0  pts={{surface.n_points}}")
""",
        ),
        # 4. Back to magnitude at 0.5 (check if still cached after step 3)
        (
            "4: back_to_mag0.5",
            f"""\
mesh = read("{data_path}")
vx = mesh["Vx"]
vy = mesh["Vy"]
vz = mesh["Vz"]
mag_sq = vx * vx + vy * vy + vz * vz
magnitude = np.sqrt(mag_sq)
mesh["Magnitude"] = magnitude
thresholded = mesh.threshold(value=0.5, scalars="Magnitude")
surface = thresholded.extract_surface()
show(surface, scalars="Magnitude", cmap="hot")
print(f"mag_thresh=0.5  pts={{surface.n_points}}")
""",
        ),
        # 5. Magnitude threshold at 0.6 again (should be cached from step 2)
        (
            "5: mag0.6_revisit",
            f"""\
mesh = read("{data_path}")
vx = mesh["Vx"]
vy = mesh["Vy"]
vz = mesh["Vz"]
mag_sq = vx * vx + vy * vy + vz * vz
magnitude = np.sqrt(mag_sq)
mesh["Magnitude"] = magnitude
thresholded = mesh.threshold(value=0.6, scalars="Magnitude")
surface = thresholded.extract_surface()
show(surface, scalars="Magnitude", cmap="hot")
print(f"mag_thresh=0.6_revisit  pts={{surface.n_points}}")
""",
        ),
    ]


ANALYSIS = [
    ("1",     "Cold run",             "All misses: read+vx+vy+vz+sqrt+threshold+surface"),
    ("2",     "thresh 0.5→0.6",       "read+vx+vy+vz+sqrt all cached; threshold+surface re-run"),
    ("3",     "Switch to Vz",         "read cached; no magnitude needed; threshold+surface new"),
    ("4",     "Back to mag@0.5",      "vx/vy/vz/sqrt re-run (different pipeline); threshold re-run"),
    ("5",     "mag@0.6 revisit",      "All ops cached (same as step 2 after step 4 restored cache)"),
]


def main():
    print("=" * 60)
    print("Benchmark: Numpy-Heavy Derived Field Computation")
    print("=" * 60)
    print("\nScenario: Scientist computes velocity magnitude from components.")
    print("Tests numpy proxy caching: sqrt(), array arithmetic, field assignment.")
    print("\nCreating 150x150x150 dataset (~3.4M points)...")

    # Use moderate size: 150^3 is ~3.4M points, sufficient for timing
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

        print("\nPer-edit details:")
        for e in result.edits:
            speedup_str = (
                f"{e.speedup:.1f}x" if e.speedup != float("inf") else "   inf"
            )
            print(
                f"  {e.name:<30}  "
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
