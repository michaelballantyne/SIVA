"""run_all.py — Run all tracked-execution benchmarks and produce a summary.

Executes each benchmark in sequence and collects results into a single
summary table showing average cached time, uncached time, and speedup.

Usage:
    python3 experiments/tracked-execution/benchmarks/run_all.py

Options (environment variables):
    BENCH_QUICK=1    Use smaller datasets for faster iteration (dims=80^3)
    BENCH_CSV=path   Write aggregate CSV to this path
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench_harness import BenchmarkResult, write_csv


def run_all_benchmarks() -> List[BenchmarkResult]:
    """Run every benchmark module and collect results.

    Returns:
        List of BenchmarkResult, one per benchmark.
    """
    import bench_parameter_sweep
    import bench_visual_params
    import bench_pipeline_refinement
    import bench_numpy_heavy
    import bench_ab_comparison

    modules = [
        ("Parameter Sweep",       bench_parameter_sweep),
        ("Visual Params",         bench_visual_params),
        ("Pipeline Refinement",   bench_pipeline_refinement),
        ("Numpy Heavy",           bench_numpy_heavy),
        ("A/B Comparison",        bench_ab_comparison),
    ]

    results = []
    total_t0 = time.perf_counter()

    for bench_name, module in modules:
        print("\n" + "=" * 70)
        print(f"Running: {bench_name}")
        print("=" * 70)
        t0 = time.perf_counter()
        result = module.main()
        elapsed = time.perf_counter() - t0
        print(f"[{bench_name}] completed in {elapsed:.1f}s")
        results.append(result)

    total_elapsed = time.perf_counter() - total_t0
    print(f"\nAll benchmarks completed in {total_elapsed:.1f}s")
    return results


def print_summary_table(results: List[BenchmarkResult]) -> None:
    """Print a combined summary table across all benchmarks."""
    col_bench = max(len("Benchmark"), max(len(r.name) for r in results))
    col_cached = 17
    col_uncached = 18
    col_speedup = 13

    def hdr(bench, cached, uncached, speedup):
        return (
            f"| {bench:<{col_bench}} "
            f"| {cached:>{col_cached}} "
            f"| {uncached:>{col_uncached}} "
            f"| {speedup:>{col_speedup}} |"
        )

    sep = (
        f"|-{'-'*col_bench}-"
        f"|-{'-'*col_cached}-"
        f"|-{'-'*col_uncached}-"
        f"|-{'-'*col_speedup}-|"
    )

    print("\n\n" + "=" * 80)
    print("SUMMARY: Tracked Execution Benchmark Results")
    print("=" * 80)
    print(hdr("Benchmark", "Avg Cached (ms)", "Avg Uncached (ms)", "Avg Speedup"))
    print(sep)

    for r in results:
        speedup_str = (
            f"{r.avg_speedup:.1f}x"
            if r.avg_speedup != float("inf")
            else "inf"
        )
        print(hdr(
            r.name,
            f"{r.avg_cached_ms:.1f}",
            f"{r.avg_uncached_ms:.1f}",
            speedup_str,
        ))

    print(sep)

    # Overall aggregate
    all_cached = [e.cached_ms for r in results for e in r.edits]
    all_uncached = [e.uncached_ms for r in results for e in r.edits]
    overall_cached = sum(all_cached) / len(all_cached) if all_cached else 0
    overall_uncached = sum(all_uncached) / len(all_uncached) if all_uncached else 0
    overall_speedup = overall_uncached / overall_cached if overall_cached > 0 else float("inf")
    overall_speedup_str = f"{overall_speedup:.1f}x" if overall_speedup != float("inf") else "inf"

    print(hdr("OVERALL AVERAGE", f"{overall_cached:.1f}", f"{overall_uncached:.1f}", overall_speedup_str))

    total_hits = sum(r.total_hits for r in results)
    total_misses = sum(r.total_misses for r in results)
    total_ops = total_hits + total_misses
    hit_rate = 100.0 * total_hits / total_ops if total_ops > 0 else 0

    print(f"\nTotal operations: {total_ops}  ({total_hits} hits, {total_misses} misses)")
    print(f"Overall cache hit rate: {hit_rate:.1f}%")


def write_summary_csv(results: List[BenchmarkResult], path: str) -> None:
    """Write aggregate CSV with one row per benchmark."""
    import csv

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "benchmark", "edits", "avg_cached_ms", "avg_uncached_ms",
            "avg_speedup", "total_hits", "total_misses",
        ])
        for r in results:
            speedup = r.avg_speedup if r.avg_speedup != float("inf") else ""
            writer.writerow([
                r.name,
                len(r.edits),
                f"{r.avg_cached_ms:.4f}",
                f"{r.avg_uncached_ms:.4f}",
                f"{speedup:.2f}" if speedup != "" else "inf",
                r.total_hits,
                r.total_misses,
            ])
    print(f"Summary CSV written to: {path}")


def main():
    print("Tracked Execution Benchmark Suite")
    print("===================================")
    print("Running all benchmarks...\n")

    results = run_all_benchmarks()
    print_summary_table(results)

    csv_path = os.environ.get("BENCH_CSV")
    if csv_path:
        write_summary_csv(results, csv_path)
    else:
        # Default: write to benchmarks directory
        default_csv = Path(__file__).parent / "results_summary.csv"
        write_summary_csv(results, str(default_csv))

    print("\nDone.")


if __name__ == "__main__":
    main()
