"""bench_harness.py — Reusable benchmark framework for tracked execution.

Runs a sequence of pipeline edits through execute_pipeline, measuring
wall-clock time for both cached (shared DAG) and uncached (fresh DAG per edit)
modes. Produces a markdown table and optionally a CSV file.

Usage as a module:
    from bench_harness import run_benchmark, print_results_table

Usage as a script:
    python3 bench_harness.py  (runs a built-in smoke test)
"""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

# Allow running from anywhere by inserting the package root on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tracked_execution import DAG, execute_pipeline


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class EditResult:
    """Timing and cache stats for a single edit step."""
    name: str
    cached_time: float       # seconds, shared DAG
    uncached_time: float     # seconds, fresh DAG per run
    hits: int
    misses: int
    evictions: int

    @property
    def speedup(self) -> float:
        if self.cached_time == 0.0:
            return float("inf")
        return self.uncached_time / self.cached_time

    @property
    def cached_ms(self) -> float:
        return self.cached_time * 1000.0

    @property
    def uncached_ms(self) -> float:
        return self.uncached_time * 1000.0


@dataclass
class BenchmarkResult:
    """Collected results from a full benchmark run."""
    name: str
    edits: List[EditResult] = field(default_factory=list)

    @property
    def avg_cached_ms(self) -> float:
        if not self.edits:
            return 0.0
        return sum(e.cached_ms for e in self.edits) / len(self.edits)

    @property
    def avg_uncached_ms(self) -> float:
        if not self.edits:
            return 0.0
        return sum(e.uncached_ms for e in self.edits) / len(self.edits)

    @property
    def avg_speedup(self) -> float:
        finite = [e.speedup for e in self.edits if e.speedup != float("inf")]
        if not finite:
            return float("inf")
        return sum(finite) / len(finite)

    @property
    def total_hits(self) -> int:
        return sum(e.hits for e in self.edits)

    @property
    def total_misses(self) -> int:
        return sum(e.misses for e in self.edits)


# ---------------------------------------------------------------------------
# Core benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(
    name: str,
    edits: List[Tuple[str, str]],
    dag: Optional[DAG] = None,
    data_setup: Optional[Callable[[], str]] = None,
    warmup: bool = True,
) -> BenchmarkResult:
    """Run a sequence of pipeline edits and measure caching performance.

    Each edit is executed twice:
    1. In "cached" mode: using a single shared DAG across all edits (accumulating
       cache entries). This represents the real-world incremental editing scenario.
    2. In "uncached" mode: each edit runs with a fresh DAG (no prior cache).
       This represents the baseline cost of building from scratch every time.

    Args:
        name:       Benchmark name (used in output headers).
        edits:      List of (edit_name, pipeline_code_string) tuples, in order.
        dag:        Optional pre-populated DAG for the cached run. If None, a
                    fresh DAG is created.
        data_setup: Optional callable. If provided, it is called before timing
                    begins and must return a data path string. The path is not
                    used directly by the harness but the callable may populate
                    files that the pipeline code references.
        warmup:     If True (default), run the first edit once before timing to
                    warm JIT caches, pyvista startup overhead, etc.

    Returns:
        BenchmarkResult with per-edit timing and aggregate stats.
    """
    result = BenchmarkResult(name=name)

    if data_setup is not None:
        data_setup()

    shared_dag = dag if dag is not None else DAG()

    # Optional warmup: run first edit once on a throwaway DAG to pay for
    # pyvista/numpy initialization costs before we start timing.
    if warmup and edits:
        _warmup_dag = DAG()
        try:
            execute_pipeline(edits[0][1], _warmup_dag)
        except Exception:
            pass  # warmup failure is non-fatal

    for edit_name, code in edits:
        # --- Cached run (shared DAG, accumulating cache) ---
        t0 = time.perf_counter()
        cached_exec_result = execute_pipeline(code, shared_dag)
        cached_time = time.perf_counter() - t0

        # --- Uncached run (fresh DAG, cold cache) ---
        fresh_dag = DAG()
        t0 = time.perf_counter()
        execute_pipeline(code, fresh_dag)
        uncached_time = time.perf_counter() - t0

        stats = cached_exec_result.stats
        result.edits.append(EditResult(
            name=edit_name,
            cached_time=cached_time,
            uncached_time=uncached_time,
            hits=stats.get("hits", 0),
            misses=stats.get("misses", 0),
            evictions=stats.get("evictions", 0),
        ))

    return result


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def print_results_table(result: BenchmarkResult) -> None:
    """Print a markdown-style results table to stdout."""
    col_widths = {
        "edit": max(len("Edit"), max((len(e.name) for e in result.edits), default=4)),
        "cached": 14,
        "uncached": 15,
        "speedup": 9,
        "hits": 6,
        "misses": 7,
    }

    def row(edit_name, cached, uncached, speedup, hits, misses):
        return (
            f"| {edit_name:<{col_widths['edit']}} "
            f"| {cached:>{col_widths['cached']}} "
            f"| {uncached:>{col_widths['uncached']}} "
            f"| {speedup:>{col_widths['speedup']}} "
            f"| {hits:>{col_widths['hits']}} "
            f"| {misses:>{col_widths['misses']}} |"
        )

    sep = (
        f"|-{'-' * col_widths['edit']}-"
        f"|-{'-' * col_widths['cached']}-"
        f"|-{'-' * col_widths['uncached']}-"
        f"|-{'-' * col_widths['speedup']}-"
        f"|-{'-' * col_widths['hits']}-"
        f"|-{'-' * col_widths['misses']}-|"
    )

    print(f"\n## {result.name}\n")
    print(row("Edit", "Cached (ms)", "Uncached (ms)", "Speedup", "Hits", "Misses"))
    print(sep)

    for e in result.edits:
        speedup_str = (
            f"{e.speedup:.1f}x" if e.speedup != float("inf") else "inf"
        )
        print(row(
            e.name,
            f"{e.cached_ms:.3f}",
            f"{e.uncached_ms:.3f}",
            speedup_str,
            str(e.hits),
            str(e.misses),
        ))

    print(sep)
    avg_speedup_str = (
        f"{result.avg_speedup:.1f}x"
        if result.avg_speedup != float("inf")
        else "inf"
    )
    print(row(
        "AVERAGE",
        f"{result.avg_cached_ms:.3f}",
        f"{result.avg_uncached_ms:.3f}",
        avg_speedup_str,
        str(result.total_hits),
        str(result.total_misses),
    ))


def write_csv(result: BenchmarkResult, path: str) -> None:
    """Write benchmark results to a CSV file.

    Args:
        result: BenchmarkResult to write.
        path:   Destination CSV file path.
    """
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "benchmark", "edit", "cached_ms", "uncached_ms",
            "speedup", "hits", "misses", "evictions",
        ])
        for e in result.edits:
            speedup = e.speedup if e.speedup != float("inf") else ""
            writer.writerow([
                result.name, e.name,
                f"{e.cached_ms:.4f}", f"{e.uncached_ms:.4f}",
                f"{speedup:.2f}" if speedup != "" else "inf",
                e.hits, e.misses, e.evictions,
            ])


# ---------------------------------------------------------------------------
# Synthetic data helper
# ---------------------------------------------------------------------------

def create_large_dataset(dims=(150, 150, 150), seed=42) -> str:
    """Create a large synthetic VTK dataset with multiple scalar fields.

    Saves to a temp file and returns the absolute path. Call
    ``os.unlink(path)`` when finished.

    Args:
        dims: Grid dimensions (nx, ny, nz).
        seed: Random seed for reproducibility.

    Returns:
        Absolute path to the saved .vtk file.
    """
    import numpy as np
    import pyvista as pv

    mesh = pv.ImageData(dimensions=dims)
    rng = np.random.RandomState(seed)
    n = mesh.n_points
    mesh["Temperature"] = rng.rand(n) * 1000
    mesh["Pressure"] = rng.rand(n) * 100
    mesh["Vx"] = rng.randn(n) * 10
    mesh["Vy"] = rng.randn(n) * 10
    mesh["Vz"] = rng.randn(n) * 10
    coords = mesh.points
    mesh["Gradient"] = np.sqrt(
        coords[:, 0] ** 2 + coords[:, 1] ** 2 + coords[:, 2] ** 2
    )
    path = tempfile.mktemp(suffix=".vtk")
    mesh.save(path)
    return path


def cleanup(path: str) -> None:
    """Remove a temporary dataset file."""
    try:
        os.unlink(path)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def _smoke_test():
    """Quick self-test: run 3 edits through the harness and print results."""
    print("Running bench_harness smoke test...")

    data_path = create_large_dataset(dims=(50, 50, 50))

    try:
        edits = [
            ("cold_run", f'mesh = read("{data_path}")\nthresholded = mesh.threshold(value=500.0, scalars="Temperature")\nshow(thresholded)'),
            ("threshold_change", f'mesh = read("{data_path}")\nthresholded = mesh.threshold(value=600.0, scalars="Temperature")\nshow(thresholded)'),
            ("repeat", f'mesh = read("{data_path}")\nthresholded = mesh.threshold(value=600.0, scalars="Temperature")\nshow(thresholded)'),
        ]

        result = run_benchmark("Smoke Test", edits, warmup=False)
        print_results_table(result)

        assert len(result.edits) == 3, "Expected 3 edit results"
        # Third edit should be a hit on the second edit (same threshold)
        assert result.edits[2].hits > 0, "Repeat edit should have cache hits"

        print("\nSmoke test PASSED.")
    finally:
        cleanup(data_path)


if __name__ == "__main__":
    _smoke_test()
