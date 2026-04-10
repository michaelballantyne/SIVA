"""bench_visual_params.py — Display-only parameter changes (colormaps, opacity).

Realistic scenario: a scientist trying different colormaps and opacity settings
to find the best visual representation. The underlying mesh computation should
be fully cached — only show() parameters change.

Expected behavior:
- Edit 1 (cold): read + threshold + surface compute (misses)
- Edits 2-10: read + threshold + surface are ALL cache hits.
  Only show() is called with different kwargs — no new mesh computation.
- Speedup: near-infinite for the mesh pipeline; only show() recording overhead.

Note: show() itself is not a "compute" operation in the DAG — it records
actors for reconciliation. So "cached" runs for display-only changes approach
zero computation time.

Run:
    python3 experiments/tracked-execution/benchmarks/bench_visual_params.py
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


COLORMAPS = ["viridis", "plasma", "inferno", "magma", "coolwarm"]
OPACITIES = [0.3, 0.5, 0.7, 0.9]


def make_pipeline(data_path: str, colormap: str, opacity: float) -> str:
    """Pipeline: read → threshold(500) → show(colormap, opacity)."""
    return f"""\
mesh = read("{data_path}")
filtered = mesh.threshold(value=500, scalars="Temperature")
surface = filtered.extract_surface()
show(surface, scalars="Temperature", cmap="{colormap}", opacity={opacity})
print(f"cmap={colormap}  opacity={opacity}  pts={{surface.n_points}}")
"""


def main():
    print("=" * 60)
    print("Benchmark: Visual Parameter Changes (colormaps, opacity)")
    print("=" * 60)
    print("\nScenario: Scientist iterates on colormap and opacity.")
    print("Pipeline: read → threshold(500) → extract_surface → show(cmap, opacity)")
    print(f"Colormaps: {COLORMAPS}")
    print(f"Opacities: {OPACITIES}")
    print("\nCreating 150x150x150 dataset (~3.4M points)...")

    data_path = create_large_dataset(dims=(150, 150, 150), seed=42)
    print(f"Dataset: {data_path}")

    try:
        edits = []

        # First batch: vary colormaps (opacity fixed at 1.0)
        for cmap in COLORMAPS:
            edits.append((f"cmap={cmap}", make_pipeline(data_path, cmap, 1.0)))

        # Second batch: vary opacity (colormap fixed at viridis)
        for opacity in OPACITIES:
            edits.append(
                (f"opacity={opacity}", make_pipeline(data_path, "viridis", opacity))
            )

        result = run_benchmark(
            name="Visual Parameter Changes",
            edits=edits,
            warmup=True,
        )

        print_results_table(result)

        print("\nAnalysis:")
        print("  - Edit 1 (viridis, cold): full pipeline executes — all misses")
        print("  - Edits 2-9: mesh pipeline fully cached; only show() kwargs change")
        print("  - Mesh ops (read/threshold/surface) are hits on every subsequent edit")
        print("  - Speedup is limited only by Python overhead, not VTK compute")

        # Show hits/misses for each edit to confirm caching behavior
        print("\nCache behavior per edit:")
        for e in result.edits:
            status = "CACHED" if e.hits > e.misses else "MISS  "
            speedup_str = f"{e.speedup:.1f}x" if e.speedup != float("inf") else "   inf"
            print(
                f"  [{status}] {e.name:<22}  "
                f"H={e.hits}  M={e.misses}  "
                f"cached={e.cached_ms:6.2f}ms  speedup={speedup_str}"
            )

        csv_path = Path(__file__).parent / "results_visual_params.csv"
        write_csv(result, str(csv_path))
        print(f"\nResults written to: {csv_path}")

    finally:
        cleanup(data_path)

    print("\nDone.")
    return result


if __name__ == "__main__":
    main()
