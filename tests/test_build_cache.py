"""Tests for structural-hash-based incremental pipeline caching."""

import os
import time
import pytest

from vislang.build_cache import BuildCache, stable_hash, _file_fingerprint
from vislang.dsl import interpret_build, PipelineBuilder


SYNTHETIC_VTI = os.path.join(
    os.path.dirname(__file__), "..", "datasets", "synthetic", "data", "output.vti"
)


def _ensure_synthetic():
    if not os.path.exists(SYNTHETIC_VTI):
        pytest.skip("Synthetic dataset not present — run datasets/synthetic/generate.py")


CODE_SIMPLE = """\
data = source("vtkXMLImageDataReader", FileName="{f}")
thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[100.0, 1000.0])
surf = filter("vtkDataSetSurfaceFilter", input=thresh)
""".format(f=SYNTHETIC_VTI)

CODE_CHANGED_THRESH = """\
data = source("vtkXMLImageDataReader", FileName="{f}")
thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[200.0, 1000.0])
surf = filter("vtkDataSetSurfaceFilter", input=thresh)
""".format(f=SYNTHETIC_VTI)

CODE_EXTRA_FILTER = """\
data = source("vtkXMLImageDataReader", FileName="{f}")
thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[100.0, 1000.0])
surf = filter("vtkDataSetSurfaceFilter", input=thresh)
smooth = filter("vtkSmoothPolyDataFilter", input=surf)
""".format(f=SYNTHETIC_VTI)


# ---------------------------------------------------------------------------
# 1. Hash determinism
# ---------------------------------------------------------------------------

def test_hash_determinism():
    _ensure_synthetic()
    cache1 = BuildCache()
    cache2 = BuildCache()
    _, vtk1, _, statuses1 = interpret_build(CODE_SIMPLE, cache=cache1)
    _, vtk2, _, statuses2 = interpret_build(CODE_SIMPLE, cache=cache2)
    # Both builds have the same node ids and same set of objects
    assert set(vtk1.keys()) == set(vtk2.keys())
    # Neither cache has hits on a first cold build (both start empty)
    assert cache1.hits == 0
    assert cache2.hits == 0


def test_same_pipeline_same_hashes():
    """Two builds of the same code into the same cache → all hits on second run."""
    _ensure_synthetic()
    cache = BuildCache()
    interpret_build(CODE_SIMPLE, cache=cache)
    # Second build with same code — all nodes should hit
    interpret_build(CODE_SIMPLE, cache=cache)
    assert cache.hits == 3   # data, thresh, surf
    assert cache.misses == 0


# ---------------------------------------------------------------------------
# 2. Hash sensitivity
# ---------------------------------------------------------------------------

def test_changed_param_changes_hash_and_descendants():
    """Changing a threshold value changes that node + surf hash, not data hash."""
    _ensure_synthetic()
    cache = BuildCache()
    # Cold build
    interpret_build(CODE_SIMPLE, cache=cache)
    # Rebuild with changed threshold
    interpret_build(CODE_CHANGED_THRESH, cache=cache)
    # data node → hit; thresh + surf → miss
    assert cache.hits == 1    # data
    assert cache.misses == 2  # thresh, surf


# ---------------------------------------------------------------------------
# 3. File-mtime fingerprint
# ---------------------------------------------------------------------------

def test_file_mtime_fingerprint_changes():
    """Touching the data file changes the file fingerprint."""
    _ensure_synthetic()
    fp1 = _file_fingerprint(SYNTHETIC_VTI)
    os.utime(SYNTHETIC_VTI, None)  # touch — updates mtime
    fp2 = _file_fingerprint(SYNTHETIC_VTI)
    # mtime_ns changes on touch
    assert fp1 != fp2


def test_missing_file_fingerprint_stable():
    fp = _file_fingerprint("/nonexistent/path/file.vti")
    assert len(fp) == 64   # sha256 hex always 64 chars


# ---------------------------------------------------------------------------
# 4. Cache hit on rebuild
# ---------------------------------------------------------------------------

def test_cache_hit_on_rebuild():
    _ensure_synthetic()
    cache = BuildCache()
    interpret_build(CODE_SIMPLE, cache=cache)
    interpret_build(CODE_SIMPLE, cache=cache)
    stats = {"hits": cache.hits, "misses": cache.misses, "evictions": cache.evictions}
    assert stats["evictions"] == 0
    assert stats["hits"] >= 2  # at least thresh + surf (source may also hit)


# ---------------------------------------------------------------------------
# 5. Partial change: upstream hits, downstream misses
# ---------------------------------------------------------------------------

def test_partial_change_upstream_hit_downstream_miss():
    _ensure_synthetic()
    cache = BuildCache()
    interpret_build(CODE_SIMPLE, cache=cache)
    interpret_build(CODE_CHANGED_THRESH, cache=cache)
    assert cache.hits >= 1    # data node hit
    assert cache.misses >= 2  # thresh and surf miss


# ---------------------------------------------------------------------------
# 6. Cache eviction on smaller pipeline
# ---------------------------------------------------------------------------

def test_cache_eviction_on_smaller_pipeline():
    _ensure_synthetic()
    cache = BuildCache()
    # Build larger pipeline first
    interpret_build(CODE_EXTRA_FILTER, cache=cache)
    # Rebuild smaller — 'smooth' node not touched → evicted
    interpret_build(CODE_SIMPLE, cache=cache)
    assert cache.evictions >= 1


# ---------------------------------------------------------------------------
# 7. BuildCache unit tests (no VTK)
# ---------------------------------------------------------------------------

def test_build_cache_get_put():
    cache = BuildCache()
    cache.begin_run()
    assert cache.get("abc") is None
    sentinel = object()
    cache.put("abc", sentinel)
    assert cache.get("abc") is sentinel


def test_build_cache_end_run_evicts_untouched():
    cache = BuildCache()
    cache.begin_run()
    cache.put("keep", object())
    cache.put("evict", object())
    cache.touch("keep")
    stats = cache.end_run()
    assert stats["evictions"] == 1
    assert cache.get("keep") is not None
    assert cache.get("evict") is None


def test_build_cache_counters_reset_on_begin_run():
    cache = BuildCache()
    cache.begin_run()
    cache.hits = 5
    cache.misses = 3
    cache.begin_run()
    assert cache.hits == 0
    assert cache.misses == 0


# ---------------------------------------------------------------------------
# 8. stable_hash edge cases
# ---------------------------------------------------------------------------

def test_stable_hash_primitives():
    assert stable_hash(42) == stable_hash(42)
    assert stable_hash(42) != stable_hash(43)
    assert stable_hash("hello") == stable_hash("hello")
    assert stable_hash(None) == stable_hash(None)
    assert stable_hash(True) != stable_hash(1)  # bool vs int differ by type


def test_stable_hash_nested():
    h1 = stable_hash({"a": [1, 2, 3], "b": "x"})
    h2 = stable_hash({"a": [1, 2, 3], "b": "x"})
    h3 = stable_hash({"a": [1, 2, 4], "b": "x"})
    assert h1 == h2
    assert h1 != h3


def test_stable_hash_numpy():
    pytest.importorskip("numpy")
    import numpy as np
    a = np.array([1.0, 2.0, 3.0])
    b = np.array([1.0, 2.0, 3.0])
    c = np.array([1.0, 2.0, 4.0])
    assert stable_hash(a) == stable_hash(b)
    assert stable_hash(a) != stable_hash(c)
