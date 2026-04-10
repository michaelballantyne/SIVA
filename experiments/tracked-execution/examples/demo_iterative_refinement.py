#!/usr/bin/env python3
"""Demo: simulated agent iterative refinement loop.

An AI agent often refines a visualization over several rounds: adjust the
threshold, change the colormap, tune smoothing, etc.  With content-addressed
caching, only the changed step (and everything downstream of it) re-executes.
Upstream results that are identical stay cached.

This demo simulates 6 agent iterations and prints a hit/miss/eviction table
to show which steps were reused.

Run:
    python3 experiments/tracked-execution/examples/demo_iterative_refinement.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracked_execution import DAG, execute_pipeline, inspect_pipeline

from utils import cleanup, create_test_dataset


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def build_pipeline(data_path, threshold, colormap, smooth_iterations):
    """Return a pipeline code string parameterized by the agent's choices."""
    return f"""
mesh = read("{data_path}")
filtered = mesh.threshold(value={threshold:.1f}, scalars="Temperature")
surface = filtered.extract_surface()
smoothed = surface.smooth(n_iter={smooth_iterations})
show(smoothed, scalars="Temperature", cmap="{colormap}")
print(f"threshold={threshold:.1f}  cmap={colormap}  smooth={smooth_iterations}")
print(f"surface points: {{smoothed.n_points}}")
"""


def fmt_stats(stats):
    return (
        f"H={stats['hits']:3d}  M={stats['misses']:3d}  E={stats['evictions']:3d}"
    )


# ---------------------------------------------------------------------------
# Simulated agent state
# ---------------------------------------------------------------------------

ITERATIONS = [
    # (threshold, colormap, smooth_iters, description)
    (500.0, "viridis",  0,  "Initial pipeline"),
    (600.0, "viridis",  0,  "Agent: threshold up to 600 (inspection found cluster above 600)"),
    (600.0, "plasma",   0,  "Agent: switch colormap to plasma"),
    (600.0, "plasma",  10,  "Agent: add 10 smooth iterations for cleaner surface"),
    (650.0, "plasma",  10,  "Agent: refine threshold to 650"),
    (650.0, "inferno", 10,  "Agent: try inferno colormap for publication"),
]


def main():
    print("=" * 60)
    print("Demo: Iterative Refinement — agent simulation")
    print("=" * 60)

    print("\nCreating synthetic dataset (80x80x80)...")
    data_path = create_test_dataset(dims=(80, 80, 80))

    dag = DAG()

    # Table header
    print(
        f"\n{'Iter':>4}  {'Threshold':>10}  {'Colormap':>8}  {'Smooth':>6}"
        f"  {'H':>4}  {'M':>4}  {'E':>4}  {'Time(s)':>8}  Description"
    )
    print("-" * 100)

    rows = []

    for i, (threshold, colormap, smooth, description) in enumerate(ITERATIONS, start=1):
        pipeline = build_pipeline(data_path, threshold, colormap, smooth)

        t0 = time.perf_counter()
        result = execute_pipeline(pipeline, dag)
        elapsed = time.perf_counter() - t0

        s = result.stats
        rows.append((i, threshold, colormap, smooth, s["hits"], s["misses"], s["evictions"], elapsed))

        print(
            f"{i:>4}  {threshold:>10.1f}  {colormap:>8}  {smooth:>6}"
            f"  {s['hits']:>4}  {s['misses']:>4}  {s['evictions']:>4}"
            f"  {elapsed:>8.4f}  {description}"
        )

        # After the first pipeline, let the agent inspect and decide next step
        if i == 1:
            insp = inspect_pipeline("""
arr = filtered["Temperature"]
t_mean = arr.mean()
t_max  = arr.max()
# Rough proxy for p75: midpoint between mean and max
p75_approx = (t_mean + t_max) / 2.0
print(f"Temperature  mean={t_mean:.1f}  max={t_max:.1f}")
print(f"Approx p75 = {p75_approx:.1f}")
print("Agent decision: raise threshold to just above approx p75")
""", dag)
            print(f"         [inspect] {insp.output.strip()}")

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------
    print("\n--- Analysis ---")
    print("Iter  What changed                       Cache behaviour")
    print("-" * 70)
    analyses = [
        ("1 → 2", "threshold 500 → 600",            "read() hit; threshold/surface/smooth re-run"),
        ("2 → 3", "colormap viridis → plasma",       "all mesh ops cached; only show() changes"),
        ("3 → 4", "smooth 0 → 10 iterations",        "read/threshold/surface cached; smooth re-run"),
        ("4 → 5", "threshold 600 → 650",             "read() hit; threshold/surface/smooth re-run"),
        ("5 → 6", "colormap plasma → inferno",       "all mesh ops cached; only show() changes"),
    ]
    for (iter_label, what, behaviour) in analyses:
        print(f"  {iter_label:<8}  {what:<35}  {behaviour}")

    print("\n--- Hit / Miss Summary Table ---")
    print(f"{'Iter':>4}  {'Hits':>6}  {'Misses':>7}  {'Evictions':>10}  {'Time(s)':>8}")
    print("-" * 45)
    for (i, threshold, colormap, smooth, hits, misses, evictions, elapsed) in rows:
        print(f"{i:>4}  {hits:>6}  {misses:>7}  {evictions:>10}  {elapsed:>8.4f}")

    total_hits   = sum(r[4] for r in rows)
    total_misses = sum(r[5] for r in rows)
    pct_cached   = 100.0 * total_hits / (total_hits + total_misses) if (total_hits + total_misses) else 0
    print(f"\nOverall: {total_hits} hits, {total_misses} misses across {len(rows)} iterations")
    print(f"Cache reuse rate: {pct_cached:.1f}%")
    print("\nKey insight:")
    print("  - Colormap changes (iters 3, 6) cost zero compute — fully cached upstream.")
    print("  - Threshold changes trigger re-execution from threshold downward.")
    print("  - smooth() changes only re-run smooth — surface and read stay cached.")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
