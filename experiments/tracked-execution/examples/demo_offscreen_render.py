#!/usr/bin/env python3
"""Demo: SceneReconciler with real offscreen PyVista rendering.

Shows the full loop:
  1. Create synthetic data
  2. Run pipeline through execute_pipeline
  3. Apply actors to offscreen plotter via SceneReconciler
  4. Take screenshot
  5. Modify pipeline (change threshold)
  6. Re-run — shows caching (read() is a hit, threshold is a miss)
  7. Re-render — takes second screenshot
  8. Compare image sizes to verify scenes differ

Run with:
    xvfb-run -a python3 experiments/tracked-execution/examples/demo_offscreen_render.py
"""

import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pyvista as pv

# Allow import without pip-installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from tracked_execution import DAG, SceneReconciler, Session, execute_pipeline
from utils import cleanup, create_test_dataset


def fmt_stats(stats):
    return (
        f"hits={stats['hits']:3d}  misses={stats['misses']:3d}"
        f"  evictions={stats['evictions']:3d}"
    )


def fmt_reconcile(rec):
    return (
        f"added={rec.added}  updated={rec.updated}"
        f"  removed={rec.removed}  unchanged={rec.unchanged}"
    )


def main():
    print("=" * 60)
    print("Demo: Offscreen Rendering with SceneReconciler")
    print("=" * 60)

    # ------------------------------------------------------------------
    # 1. Create synthetic dataset
    # ------------------------------------------------------------------
    print("\n[1] Creating synthetic dataset (40x40x40)...")
    data_path = create_test_dataset(dims=(40, 40, 40), seed=42)
    tmpdir = tempfile.mkdtemp()
    print(f"    Dataset: {data_path}")
    print(f"    Screenshots: {tmpdir}/")

    # ------------------------------------------------------------------
    # 2. Set up offscreen plotter and reconciler
    # ------------------------------------------------------------------
    print("\n[2] Setting up offscreen plotter and SceneReconciler...")
    plotter = pv.Plotter(off_screen=True)
    dag = DAG()
    reconciler = SceneReconciler(plotter=plotter)
    print("    Plotter: off_screen=True")
    print("    Reconciler: plotter attached")

    # ------------------------------------------------------------------
    # 3. Run pipeline and apply actors via reconciler
    # ------------------------------------------------------------------
    print("\n[3] Running pipeline (threshold=500, colormap=inferno)...")
    pipeline1 = f"""
mesh = read("{data_path}")
hot = mesh.threshold(value=500, scalars="Temperature")
surface = hot.extract_surface()
show(surface, colormap="inferno", name="surface")
"""
    t0 = time.perf_counter()
    result1 = execute_pipeline(pipeline1, dag)
    t1 = time.perf_counter()
    print(f"    Stats : {fmt_stats(result1.stats)}")
    print(f"    Actors: {len(result1.actors)}")
    print(f"    Time  : {t1 - t0:.3f}s")

    rec1 = reconciler.reconcile(result1.actors)
    print(f"    Reconcile: {fmt_reconcile(rec1)}")
    assert rec1.added >= 1, f"Expected at least 1 added actor, got {rec1.added}"

    # ------------------------------------------------------------------
    # 4. Take first screenshot
    # ------------------------------------------------------------------
    img1 = os.path.join(tmpdir, "render1_inferno_500.png")
    plotter.screenshot(img1)
    size1 = os.path.getsize(img1)
    print(f"\n[4] Screenshot 1: {img1} ({size1:,} bytes)")
    assert size1 > 1000, f"Screenshot 1 is unexpectedly small: {size1} bytes"

    # ------------------------------------------------------------------
    # 5. Modify pipeline: change threshold (shows partial cache hit)
    # ------------------------------------------------------------------
    print("\n[5] Modifying pipeline (threshold=700, colormap=viridis)...")
    pipeline2 = f"""
mesh = read("{data_path}")
hot = mesh.threshold(value=700, scalars="Temperature")
surface = hot.extract_surface()
show(surface, colormap="viridis", name="surface")
"""
    t0 = time.perf_counter()
    result2 = execute_pipeline(pipeline2, dag)
    t1 = time.perf_counter()
    print(f"    Stats : {fmt_stats(result2.stats)}")
    print(f"    Actors: {len(result2.actors)}")
    print(f"    Time  : {t1 - t0:.3f}s")
    # read() should be a cache hit (same file, same mtime)
    assert result2.stats["hits"] >= 1, "read() should be a cache hit"
    assert result2.stats["misses"] >= 1, "threshold(700) should be a cache miss"
    print("    Cache behavior: read() hit, threshold+surface recomputed")

    rec2 = reconciler.reconcile(result2.actors)
    print(f"    Reconcile: {fmt_reconcile(rec2)}")
    # Same actor name "surface" — should be updated (mesh or params changed)
    assert rec2.updated >= 1, f"Expected actor update, got {fmt_reconcile(rec2)}"

    # ------------------------------------------------------------------
    # 6. Take second screenshot
    # ------------------------------------------------------------------
    img2 = os.path.join(tmpdir, "render2_viridis_700.png")
    plotter.screenshot(img2)
    size2 = os.path.getsize(img2)
    print(f"\n[6] Screenshot 2: {img2} ({size2:,} bytes)")
    assert size2 > 1000, f"Screenshot 2 is unexpectedly small: {size2} bytes"

    # ------------------------------------------------------------------
    # 7. Compare images — they should differ (different threshold + colormap)
    # ------------------------------------------------------------------
    print("\n[7] Comparing screenshots...")
    with open(img1, "rb") as f1, open(img2, "rb") as f2:
        data1, data2 = f1.read(), f2.read()

    if data1 != data2:
        print("    Images differ (as expected — different threshold and colormap)")
    else:
        print("    WARNING: Images are identical (unexpected — check rendering)")

    print(f"    Image 1 size: {size1:,} bytes")
    print(f"    Image 2 size: {size2:,} bytes")

    # ------------------------------------------------------------------
    # 8. Session API demo
    # ------------------------------------------------------------------
    print("\n[8] Session API: execute + screenshot (convenience wrapper)...")
    plotter2 = pv.Plotter(off_screen=True)
    session = Session(dag=dag, plotter=plotter2)

    session_pipeline = f"""
mesh = read("{data_path}")
show(mesh, colormap="plasma", name="full_mesh")
"""
    session_result = session.execute(session_pipeline)
    print(f"    Stats : {fmt_stats(session_result.stats)}")

    img3 = os.path.join(tmpdir, "session_plasma.png")
    session.screenshot(img3)
    size3 = os.path.getsize(img3)
    print(f"    Screenshot 3: {img3} ({size3:,} bytes)")
    assert size3 > 1000, f"Session screenshot is unexpectedly small: {size3} bytes"

    plotter2.close()

    # ------------------------------------------------------------------
    # 9. Re-run with identical code — all cache hits, identical image
    # ------------------------------------------------------------------
    print("\n[9] Re-running identical session pipeline (all hits expected)...")
    plotter3 = pv.Plotter(off_screen=True)
    session2 = Session(dag=dag, plotter=plotter3)
    result_cached = session2.execute(session_pipeline)
    print(f"    Stats : {fmt_stats(result_cached.stats)}")
    assert result_cached.stats["misses"] == 0, "Second run should have zero misses"
    assert result_cached.stats["hits"] > 0, "Second run should have cache hits"
    print("    All cache hits confirmed")
    plotter3.close()

    # ------------------------------------------------------------------
    # Cleanup and summary
    # ------------------------------------------------------------------
    plotter.close()
    cleanup(data_path)

    print("\n" + "=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Pipeline 1 (cold):            {fmt_stats(result1.stats)}")
    print(f"  Pipeline 2 (threshold change): {fmt_stats(result2.stats)}")
    print(f"  Session (plasma, cold):        {fmt_stats(session_result.stats)}")
    print(f"  Session re-run (cached):       {fmt_stats(result_cached.stats)}")
    print()
    print("  Screenshots written to:")
    print(f"    {img1}")
    print(f"    {img2}")
    print(f"    {img3}")
    print()
    print("All assertions passed. Offscreen rendering works correctly.")


if __name__ == "__main__":
    main()
