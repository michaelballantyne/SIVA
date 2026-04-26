#!/usr/bin/env python3
"""Benchmark incremental pipeline caching on the synthetic dataset."""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from vislang.build_cache import BuildCache
from vislang.dsl import interpret_build

SYNTHETIC_VTI = os.path.join(
    os.path.dirname(__file__), "..", "datasets", "synthetic", "data", "output.vti"
)

if not os.path.exists(SYNTHETIC_VTI):
    print("ERROR: Synthetic dataset not found. Run datasets/synthetic/generate.py first.")
    sys.exit(1)

# ------------------------------------------------------------------
# Pipeline templates
# ------------------------------------------------------------------

PIPELINE_FULL = f"""\
data = source("vtkXMLImageDataReader", FileName="{SYNTHETIC_VTI}")
thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[{{lo}}, 1000.0])
surf = filter("vtkDataSetSurfaceFilter", input=thresh)
normals = filter("vtkPolyDataNormals", input=surf)
smooth = filter("vtkSmoothPolyDataFilter", input=surf, NumberOfIterations=5)
"""

PIPELINE_CHANGED_COLORMAP = f"""\
data = source("vtkXMLImageDataReader", FileName="{SYNTHETIC_VTI}")
thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[100.0, 1000.0])
surf = filter("vtkDataSetSurfaceFilter", input=thresh)
normals = filter("vtkPolyDataNormals", input=surf)
smooth = filter("vtkSmoothPolyDataFilter", input=surf, NumberOfIterations=10)
"""


def build(code, cache=None):
    t0 = time.perf_counter()
    interpret_build(code, cache=cache)
    elapsed = time.perf_counter() - t0
    hits = cache.hits if cache else 0
    misses = cache.misses if cache else 0
    return elapsed, hits, misses


def main():
    print(f"{'Scenario':<45} {'Time (s)':>10} {'Hits':>6} {'Misses':>7}")
    print("-" * 72)

    code_base = PIPELINE_FULL.format(lo=100.0)
    code_mid = PIPELINE_FULL.format(lo=200.0)

    # 1. Cold build (no cache)
    t, h, m = build(code_base, cache=None)
    print(f"{'Cold build (no cache)':<45} {t:>10.4f} {h:>6} {m:>7}")

    # 2. Warm rebuild — same code, same cache → all hits
    cache = BuildCache()
    build(code_base, cache=cache)   # prime
    t, h, m = build(code_base, cache=cache)
    print(f"{'Warm rebuild (no changes)':<45} {t:>10.4f} {h:>6} {m:>7}")

    # 3. Change a downstream filter param (smooth iterations)
    cache = BuildCache()
    build(code_base, cache=cache)   # prime
    t, h, m = build(PIPELINE_CHANGED_COLORMAP, cache=cache)
    print(f"{'Change downstream param (smooth)':<45} {t:>10.4f} {h:>6} {m:>7}")

    # 4. Change threshold value mid-pipeline → upstream hit, downstream miss
    cache = BuildCache()
    build(code_base, cache=cache)   # prime
    t, h, m = build(code_mid, cache=cache)
    print(f"{'Change threshold (mid-pipeline)':<45} {t:>10.4f} {h:>6} {m:>7}")

    print()
    print("Notes: Hits/misses are per the *second* build in each scenario.")


if __name__ == "__main__":
    main()
