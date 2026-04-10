#!/usr/bin/env python3
"""Demo: garbage collection — eviction of stale cache entries.

After each pipeline execution, the DAG runs GC: entries not touched by the
current run are evicted.  Shared upstream entries (e.g. the read() result)
are retained as long as at least one pipeline still references them.

Scenario:
  Pipeline A: read -> threshold(500) -> contour
  Pipeline B: read -> clip (completely different downstream)

  After B runs, A's threshold/contour are evicted, but the shared read()
  stays alive.  Running A again forces threshold/contour to re-execute.

Run:
    python3 experiments/tracked-execution/examples/demo_gc.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracked_execution import DAG, execute_pipeline

from utils import cleanup, create_test_dataset


def sep(title):
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


def show_cache(dag, label):
    print(f"{label}: {len(dag.cache)} cache entries")


def fmt(stats):
    return f"hits={stats['hits']}  misses={stats['misses']}  evictions={stats['evictions']}"


def main():
    print("=" * 60)
    print("Demo: Garbage Collection — Eviction of Stale Entries")
    print("=" * 60)

    print("\nCreating synthetic dataset (60x60x60)...")
    data_path = create_test_dataset(dims=(60, 60, 60))

    dag = DAG()

    # ------------------------------------------------------------------
    # Pipeline A: read -> threshold(500) -> contour
    # ------------------------------------------------------------------
    pipeline_a = f"""
mesh = read("{data_path}")
thresholded = mesh.threshold(value=500.0, scalars="Temperature")
surface = thresholded.extract_surface()
print(f"Pipeline A: {{surface.n_points}} surface points")
"""

    sep("Step 1: Run Pipeline A  [read -> threshold(500) -> surface]")
    show_cache(dag, "Before")
    result_a1 = execute_pipeline(pipeline_a, dag)
    print(result_a1.output.strip())
    print(f"Stats : {fmt(result_a1.stats)}")
    show_cache(dag, "After ")
    cache_after_a = len(dag.cache)

    # ------------------------------------------------------------------
    # Pipeline B: read (same file) -> clip -> extract_surface
    # ------------------------------------------------------------------
    pipeline_b = f"""
mesh = read("{data_path}")
clipped = mesh.clip(normal="z")
clipped_surf = clipped.extract_surface()
print(f"Pipeline B: {{clipped_surf.n_points}} surface points")
"""

    sep("Step 2: Run Pipeline B  [read -> clip -> surface]")
    print("Pipeline B shares the same file (read() will be a HIT).")
    print("Threshold and contour from Pipeline A will be EVICTED.")
    show_cache(dag, "Before")
    result_b = execute_pipeline(pipeline_b, dag)
    print(result_b.output.strip())
    print(f"Stats : {fmt(result_b.stats)}")
    show_cache(dag, "After ")
    cache_after_b = len(dag.cache)

    assert result_b.stats["hits"] >= 1, "read() should be a hit"
    assert result_b.stats["misses"] >= 1, "clip/surface should be misses"
    assert result_b.stats["evictions"] >= 1, "threshold/contour should be evicted"

    print(f"\nCache shrank from {cache_after_a} → {cache_after_b} entries")
    print("(A's threshold + surface evicted; read() survived because B uses it)")

    # ------------------------------------------------------------------
    # Run Pipeline A again — read is still cached, but threshold/surface must re-run
    # ------------------------------------------------------------------
    sep("Step 3: Re-run Pipeline A  [read cached; threshold/surface re-execute]")
    show_cache(dag, "Before")
    result_a2 = execute_pipeline(pipeline_a, dag)
    print(result_a2.output.strip())
    print(f"Stats : {fmt(result_a2.stats)}")
    show_cache(dag, "After ")

    assert result_a2.stats["hits"] >= 1, "read() should still be a hit"
    assert result_a2.stats["misses"] >= 1, "threshold/surface must re-execute"

    print(f"\nread() hit  : yes (file not re-read — mtime unchanged)")
    print(f"threshold() : miss (evicted by Pipeline B's run)")
    print(f"surface()   : miss (evicted by Pipeline B's run)")

    # ------------------------------------------------------------------
    # Run Pipeline A a second time — now fully cached again
    # ------------------------------------------------------------------
    sep("Step 4: Re-run Pipeline A again (now fully cached)")
    result_a3 = execute_pipeline(pipeline_a, dag)
    print(result_a3.output.strip())
    print(f"Stats : {fmt(result_a3.stats)}")
    assert result_a3.stats["misses"] == 0

    # ------------------------------------------------------------------
    # Run both pipelines alternating — demonstrate shared read()
    # ------------------------------------------------------------------
    sep("Step 5: Alternating A / B — shared read() never re-reads")
    for run_idx, (label, pipeline) in enumerate(
        [("A", pipeline_a), ("B", pipeline_b), ("A", pipeline_a), ("B", pipeline_b)],
        start=1,
    ):
        r = execute_pipeline(pipeline, dag)
        print(f"  Run {run_idx} (Pipeline {label}): {fmt(r.stats)}")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    sep("Summary")
    print("Run        Pipeline  Hits  Misses  Evictions  Notes")
    print("-" * 70)
    rows = [
        ("1", "A (cold)",    result_a1.stats["hits"], result_a1.stats["misses"], result_a1.stats["evictions"], "cold cache"),
        ("2", "B",           result_b.stats["hits"],  result_b.stats["misses"],  result_b.stats["evictions"],  "read hit; A's entries evicted"),
        ("3", "A (re-run)",  result_a2.stats["hits"], result_a2.stats["misses"], result_a2.stats["evictions"], "read hit; threshold/surface re-run"),
        ("4", "A (cached)",  result_a3.stats["hits"], result_a3.stats["misses"], result_a3.stats["evictions"], "fully cached"),
    ]
    for (run, pipeline, hits, misses, evictions, note) in rows:
        print(f"  {run:<3}  {pipeline:<12}  {hits:<5}  {misses:<7}  {evictions:<10}  {note}")

    print("\nKey insights:")
    print("  - GC runs at end_run(): entries not in current_run are evicted.")
    print("  - read() is shared: both A and B reference the same hash.")
    print("  - Switching pipelines alternately causes re-execution of unique ops.")
    print("  - Running any pipeline twice in a row is always fully cached.")

    cleanup(data_path)
    print("\nDone.")


if __name__ == "__main__":
    main()
