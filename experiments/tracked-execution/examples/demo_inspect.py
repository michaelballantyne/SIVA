#!/usr/bin/env python3
"""Demo: one-off inspection with inspect_exec.

Shows how an agent can explore cached pipeline results without re-executing
the pipeline.  inspect_exec() provides a read-only view into the DAG.

Run:
    python3 experiments/tracked-execution/examples/demo_inspect.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracked_execution import DAG, execute_pipeline, inspect_exec

from utils import cleanup, create_test_dataset


def main():
    print("=" * 60)
    print("Demo: One-off Inspection via inspect_exec")
    print("=" * 60)

    print("\nCreating synthetic dataset (50x50x50)...")
    data_path = create_test_dataset(dims=(50, 50, 50))

    dag = DAG()

    # Run a pipeline that produces several named intermediate results
    pipeline = f"""
mesh = read("{data_path}")
low_temp   = mesh.threshold(value=200.0, scalars="Temperature")
high_temp  = mesh.threshold(value=800.0, scalars="Temperature")
low_surf   = low_temp.extract_surface()
high_surf  = high_temp.extract_surface()
print("Pipeline complete.")
print(f"  mesh       : {{mesh.n_points}} points")
print(f"  low_temp   : {{low_temp.n_points}} points (T > 200)")
print(f"  high_temp  : {{high_temp.n_points}} points (T > 800)")
"""

    print("\n--- Running pipeline ---")
    result = execute_pipeline(pipeline, dag)
    print(result.output.strip())
    print(f"Cache after pipeline: {len(dag.cache)} entries")
    print(f"Named proxies: {result.names}")

    # ------------------------------------------------------------------
    # Inspection 1: basic field ranges
    # ------------------------------------------------------------------
    print("\n--- Inspection 1: Field ranges on raw mesh ---")
    insp1 = inspect_exec("""
arr_t = mesh["Temperature"]
arr_p = mesh["Pressure"]
arr_g = mesh["Gradient"]
print(f"Temperature : min={arr_t.min():.1f}  max={arr_t.max():.1f}  mean={arr_t.mean():.1f}")
print(f"Pressure    : min={arr_p.min():.1f}  max={arr_p.max():.1f}  mean={arr_p.mean():.1f}")
print(f"Gradient    : min={arr_g.min():.1f}  max={arr_g.max():.1f}  mean={arr_g.mean():.1f}")
""", dag)
    print(insp1.output.strip())

    # ------------------------------------------------------------------
    # Inspection 2: percentile thresholds
    # ------------------------------------------------------------------
    print("\n--- Inspection 2: Percentile statistics ---")
    insp2 = inspect_exec("""
arr = mesh["Temperature"]
p10 = np.percentile(arr, 10)
p50 = np.percentile(arr, 50)
p90 = np.percentile(arr, 90)
p99 = np.percentile(arr, 99)
print(f"Temperature percentiles:")
print(f"  p10 = {p10:.1f}")
print(f"  p50 = {p50:.1f}  (median)")
print(f"  p90 = {p90:.1f}")
print(f"  p99 = {p99:.1f}")
""", dag)
    print(insp2.output.strip())

    # ------------------------------------------------------------------
    # Inspection 3: compare filtered views
    # ------------------------------------------------------------------
    print("\n--- Inspection 3: Compare filtered views ---")
    insp3 = inspect_exec("""
mesh_total   = mesh.n_points
low_total    = low_temp.n_points
high_total   = high_temp.n_points
pct_low  = 100.0 * low_total  / mesh_total
pct_high = 100.0 * high_total / mesh_total
print(f"Total mesh points : {mesh_total}")
print(f"T > 200 points    : {low_total}  ({pct_low:.1f}% of total)")
print(f"T > 800 points    : {high_total}  ({pct_high:.1f}% of total)")
""", dag)
    print(insp3.output.strip())

    # ------------------------------------------------------------------
    # Inspection 4: surface area proxy (point count comparison)
    # ------------------------------------------------------------------
    print("\n--- Inspection 4: Surface statistics ---")
    insp4 = inspect_exec("""
print(f"low_surf  : {low_surf.n_points} surface points")
print(f"high_surf : {high_surf.n_points} surface points")
rng_lo = low_surf.get_data_range("Temperature")
rng_hi = high_surf.get_data_range("Temperature")
print(f"low_surf  Temperature range  : {rng_lo[0]:.1f} - {rng_lo[1]:.1f}")
print(f"high_surf Temperature range  : {rng_hi[0]:.1f} - {rng_hi[1]:.1f}")
""", dag)
    print(insp4.output.strip())

    print(f"\nCache size after all inspections: {len(dag.cache)} entries")
    print("(Inspection adds cached results for new sub-operations it triggered)")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
