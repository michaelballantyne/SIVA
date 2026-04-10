"""Core tests for tracked-execution library.

All tests are self-contained: they create synthetic PyVista meshes programmatically
and do not require a display, network access, or filesystem datasets.
"""

from __future__ import annotations

import sys
import os
import tempfile
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

# Ensure the package is importable from its source directory
_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.dispatch import DAG
from tracked_execution.dispatch import dispatch, stable_hash, _should_wrap
from tracked_execution.proxy import TrackedProxy
from tracked_execution.executor import execute_pipeline, tracked_read
from tracked_execution.executor import inspect_pipeline


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mesh(n: int = 10, seed: int = 42) -> pv.ImageData:
    """Create a small synthetic PyVista mesh for testing."""
    rng = np.random.default_rng(seed)
    mesh = pv.ImageData(dimensions=(n, n, n))
    mesh["Temperature"] = rng.random(mesh.n_points) * 1000
    mesh["Pressure"] = rng.random(mesh.n_points) * 100
    return mesh


def make_proxy(mesh=None, dag=None):
    """Wrap a mesh in a TrackedProxy with a fresh or given DAG."""
    if dag is None:
        dag = DAG()
    if mesh is None:
        mesh = make_mesh()
    h = stable_hash(("root", "test_mesh"))
    dag.cache[h] = mesh
    dag.current_run.add(h)
    return TrackedProxy(mesh, h, dag), dag


# ---------------------------------------------------------------------------
# 1. stable_hash determinism
# ---------------------------------------------------------------------------

class TestStableHash:
    def test_deterministic(self):
        """Same inputs → same hash."""
        h1 = stable_hash(("ImageData", "abc123", "threshold", ("0.5",), ()))
        h2 = stable_hash(("ImageData", "abc123", "threshold", ("0.5",), ()))
        assert h1 == h2

    def test_different_inputs(self):
        """Different inputs → different hashes."""
        h1 = stable_hash(("ImageData", "abc123", "threshold", ("0.5",), ()))
        h2 = stable_hash(("ImageData", "abc123", "threshold", ("0.9",), ()))
        assert h1 != h2

    def test_scalar_types(self):
        """Hashes of different scalar types are different."""
        assert stable_hash(1) != stable_hash("1")
        assert stable_hash(1) != stable_hash(1.0)
        assert stable_hash(True) != stable_hash(1)
        assert stable_hash(None) != stable_hash(0)

    def test_numpy_scalar_conversion(self):
        """numpy scalars produce same hash as equivalent Python scalars."""
        # np.float64(1.0) should hash the same as Python float 1.0
        # (they convert to item() first)
        h_np = stable_hash(np.float64(1.0))
        h_py = stable_hash(float(1.0))
        assert h_np == h_py

    def test_traced_proxy_hash(self):
        """TrackedProxy uses its ._hash, not a derived hash."""
        dag = DAG()
        mesh = make_mesh()
        expected_hash = "deadbeef" * 8  # synthetic 64-char hex
        proxy = TrackedProxy(mesh, expected_hash, dag)
        assert stable_hash(proxy) == expected_hash


# ---------------------------------------------------------------------------
# 2. TrackedProxy — method calls
# ---------------------------------------------------------------------------

class TestProxy:
    def test_proxy_method_call(self):
        """Calling a whitelisted method on a proxy returns a new proxy."""
        proxy, dag = make_proxy()
        dag.begin_run()
        # .copy() returns another mesh — should be wrapped
        result = proxy.copy()
        assert isinstance(result, TrackedProxy)

    def test_proxy_attribute_access(self):
        """Accessing .n_points returns a raw int (scalar escape)."""
        proxy, dag = make_proxy()
        dag.begin_run()
        n = proxy.n_points
        # n_points is an int — should NOT be a proxy
        assert isinstance(n, int)
        assert n == 1000  # 10×10×10

    def test_proxy_getitem(self):
        """mesh["Temperature"] works through proxy and returns a TrackedProxy."""
        proxy, dag = make_proxy()
        dag.begin_run()
        arr = proxy["Temperature"]
        # numpy array → should be wrapped
        assert isinstance(arr, TrackedProxy)

    def test_proxy_repr(self):
        """TrackedProxy has a readable repr."""
        proxy, _ = make_proxy()
        r = repr(proxy)
        assert "TrackedProxy" in r
        assert "ImageData" in r

    def test_proxy_blocks_setattr(self):
        """Setting attributes on a proxy raises AttributeError."""
        proxy, _ = make_proxy()
        with pytest.raises(AttributeError):
            proxy.some_attr = 42


# ---------------------------------------------------------------------------
# 3. Cache hit / miss
# ---------------------------------------------------------------------------

class TestCaching:
    def test_cache_hit(self):
        """Calling the same operation twice on the same proxy → second is hit."""
        proxy, dag = make_proxy()

        dag.begin_run()
        r1 = proxy.copy()
        dag.end_run()
        stats1 = dag.stats()

        # Re-create proxy with same hash (simulates re-execution)
        dag.begin_run()
        r2 = proxy.copy()
        dag.end_run()
        stats2 = dag.stats()

        assert stats1["misses"] >= 1
        assert stats2["hits"] >= 1
        assert stats2["misses"] == 0

    def test_cache_miss_on_param_change(self):
        """Changing threshold value produces a cache miss."""
        proxy, dag = make_proxy()

        dag.begin_run()
        r1 = proxy.threshold(value=500.0, scalars="Temperature")
        dag.end_run()

        dag.begin_run()
        r2 = proxy.threshold(value=600.0, scalars="Temperature")
        dag.end_run()
        stats2 = dag.stats()

        # threshold(600) is a different op from threshold(500) → miss
        assert stats2["misses"] >= 1

    def test_cache_hit_upstream_cached(self):
        """When upstream op is cached but downstream param changes, upstream is a hit."""
        proxy, dag = make_proxy()

        dag.begin_run()
        surface1 = proxy.extract_surface()
        clean1 = surface1.copy()
        dag.end_run()

        dag.begin_run()
        surface2 = proxy.extract_surface()  # same op → should hit
        # Accessing n_points is a new op on the same cached surface
        n = surface2.n_points
        dag.end_run()
        stats = dag.stats()

        # extract_surface should be a hit on second run
        assert stats["hits"] >= 1


# ---------------------------------------------------------------------------
# 4. GC / eviction
# ---------------------------------------------------------------------------

class TestGC:
    def test_gc_evicts_stale(self):
        """After end_run(), entries not touched during the run are evicted."""
        proxy, dag = make_proxy()

        dag.begin_run()
        r1 = proxy.copy()
        dag.end_run()

        copy_hash = object.__getattribute__(r1, "_hash")
        assert copy_hash in dag.cache

        # Second run doesn't call .copy() — entry should be evicted
        dag.begin_run()
        n = proxy.n_points  # only this op
        dag.end_run()
        stats = dag.stats()

        assert copy_hash not in dag.cache
        assert stats["evictions"] >= 1

    def test_gc_retains_current(self):
        """After end_run(), entries touched during the run are retained."""
        proxy, dag = make_proxy()

        dag.begin_run()
        surface = proxy.extract_surface()
        dag.end_run()

        surface_hash = object.__getattribute__(surface, "_hash")
        assert surface_hash in dag.cache

        # Second run also calls extract_surface with same params → hit + retained
        dag.begin_run()
        surface2 = proxy.extract_surface()
        dag.end_run()

        surface_hash2 = object.__getattribute__(surface2, "_hash")
        assert surface_hash2 == surface_hash
        assert surface_hash2 in dag.cache


# ---------------------------------------------------------------------------
# 5. Whitelist enforcement
# ---------------------------------------------------------------------------

class TestWhitelist:
    def test_whitelist_blocks_save(self):
        """Calling .save() on a mesh proxy raises AttributeError."""
        proxy, dag = make_proxy()
        dag.begin_run()
        with pytest.raises(AttributeError, match="blocked|blacklisted|not whitelisted"):
            proxy.save("/tmp/test_blocked.vtk")

    def test_whitelist_blocks_setitem(self):
        """proxy["NewField"] = arr raises AttributeError (in-place mutation)."""
        proxy, dag = make_proxy()
        dag.begin_run()
        arr = np.ones(1000)
        with pytest.raises(AttributeError, match="blocked|blacklisted|not whitelisted"):
            proxy["NewField"] = arr

    def test_whitelist_allows_threshold(self):
        """Calling .threshold() on a mesh proxy works (it's whitelisted)."""
        proxy, dag = make_proxy()
        dag.begin_run()
        result = proxy.threshold(value=500.0, scalars="Temperature")
        assert isinstance(result, TrackedProxy)


# ---------------------------------------------------------------------------
# 6. Scalar escape
# ---------------------------------------------------------------------------

class TestScalarEscape:
    def test_scalar_escape_n_points(self):
        """n_points is a plain int, not a proxy."""
        proxy, dag = make_proxy()
        dag.begin_run()
        n = proxy.n_points
        assert isinstance(n, (int, float))  # not TrackedProxy

    def test_mean_returns_raw_float(self):
        """arr.mean() returns a raw Python float (or numpy scalar → float)."""
        proxy, dag = make_proxy()
        dag.begin_run()
        arr_proxy = proxy["Temperature"]
        mean_val = arr_proxy.mean()
        # Should not be a TrackedProxy — it's a scalar
        # numpy mean returns np.float64, which dispatches return as raw
        assert not isinstance(mean_val, TrackedProxy)
        assert isinstance(mean_val, (int, float, np.floating))


# ---------------------------------------------------------------------------
# 7. Numpy operators through proxy
# ---------------------------------------------------------------------------

class TestNumpyOperators:
    def test_array_gt_operator(self):
        """arr > 500 returns a tracked boolean array proxy."""
        proxy, dag = make_proxy()
        dag.begin_run()
        arr_proxy = proxy["Temperature"]
        mask = arr_proxy > 500
        # numpy boolean array should be wrapped (it's not a simple scalar)
        assert isinstance(mask, TrackedProxy)

    def test_array_add_scalar(self):
        """arr + 100 returns a tracked array proxy."""
        proxy, dag = make_proxy()
        dag.begin_run()
        arr_proxy = proxy["Temperature"]
        result = arr_proxy + 100
        assert isinstance(result, TrackedProxy)

    def test_array_neg(self):
        """-arr returns a tracked array proxy."""
        proxy, dag = make_proxy()
        dag.begin_run()
        arr_proxy = proxy["Temperature"]
        result = -arr_proxy
        assert isinstance(result, TrackedProxy)


# ---------------------------------------------------------------------------
# 8. Full pipeline end-to-end
# ---------------------------------------------------------------------------

class TestFullPipeline:
    def test_full_pipeline_execute(self):
        """execute_pipeline runs read→threshold and returns stats."""
        import tempfile, os

        dag = DAG()

        # Write a small synthetic mesh to a temp file
        mesh = make_mesh(n=5)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            code = f"""
mesh = read("{tmp_path}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
n = filtered.n_points
print(f"filtered points: {{n}}")
"""
            result = execute_pipeline(code, dag)
            assert "filtered points:" in result.output
            assert result.stats["misses"] >= 1

        finally:
            os.unlink(tmp_path)

    def test_pipeline_cache_hit_on_rerun(self):
        """Re-executing same pipeline with same data → all hits on second run."""
        import tempfile, os

        dag = DAG()
        mesh = make_mesh(n=5)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            code = f"""
mesh = read("{tmp_path}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
n = filtered.n_points
print(f"n: {{n}}")
"""
            result1 = execute_pipeline(code, dag)
            result2 = execute_pipeline(code, dag)

            # Second run should have all hits (zero misses)
            assert result2.stats["hits"] > 0
            assert result2.stats["misses"] == 0

        finally:
            os.unlink(tmp_path)

    def test_pipeline_cache_miss_on_param_change(self):
        """Changing threshold value → threshold op is a miss on second run."""
        import tempfile, os

        dag = DAG()
        mesh = make_mesh(n=5)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            code1 = f"""
mesh = read("{tmp_path}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
"""
            code2 = f"""
mesh = read("{tmp_path}")
filtered = mesh.threshold(value=600.0, scalars="Temperature")
"""
            execute_pipeline(code1, dag)
            result2 = execute_pipeline(code2, dag)

            # threshold(600) should be a miss
            assert result2.stats["misses"] >= 1

        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# 9. inspect_pipeline
# ---------------------------------------------------------------------------

class TestInspectPipeline:
    def test_inspect_pipeline_named_proxies(self):
        """inspect_pipeline has access to named proxies from last pipeline run."""
        import tempfile, os

        dag = DAG()
        mesh = make_mesh(n=5)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            pipeline_code = f"""
fire = read("{tmp_path}")
"""
            execute_pipeline(pipeline_code, dag)

            inspect_code = """
n = fire.n_points
print(f"n_points: {n}")
"""
            result = inspect_pipeline(inspect_code, dag)
            assert "n_points:" in result.output

        finally:
            os.unlink(tmp_path)

    def test_inspect_pipeline_show_raises_permission_error(self):
        """inspect_pipeline raises PermissionError for show/read/screenshot calls."""
        dag = DAG()
        mesh = make_mesh(n=5)
        h = stable_hash(("root", "test_mesh"))
        dag.cache[h] = mesh
        dag.names = {"mymesh": h}

        code = """
try:
    show(mymesh)
    print("ERROR: show should not be callable")
except PermissionError as e:
    print(f"OK: PermissionError: {e}")
"""
        result = inspect_pipeline(code, dag)
        assert "OK: PermissionError" in result.output
        assert "not available" in result.output

    def test_inspect_pipeline_captures_print(self):
        """print() output in inspect_pipeline is captured in result.output."""
        dag = DAG()
        mesh = make_mesh(n=5)
        h = stable_hash(("root", "test_mesh"))
        dag.cache[h] = mesh
        dag.names = {"mymesh": h}

        code = """
n = mymesh.n_points
print(f"hello {n}")
"""
        result = inspect_pipeline(code, dag)
        assert "hello" in result.output
        assert "125" in result.output  # 5×5×5 = 125 points

    def test_inspect_pipeline_array_stats(self):
        """inspect_pipeline can compute array stats via proxy methods."""
        import tempfile, os

        dag = DAG()
        mesh = make_mesh(n=10)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            pipeline_code = f"""
fire = read("{tmp_path}")
"""
            execute_pipeline(pipeline_code, dag)

            inspect_code = """
arr = fire["Temperature"]
print(f"min: {arr.min():.1f}")
print(f"max: {arr.max():.1f}")
print(f"mean: {arr.mean():.1f}")
"""
            result = inspect_pipeline(inspect_code, dag)
            assert "min:" in result.output
            assert "max:" in result.output
            assert "mean:" in result.output

        finally:
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# 10. tracked_read caching
# ---------------------------------------------------------------------------

class TestTrackedRead:
    def test_tracked_read_returns_proxy(self):
        """tracked_read returns a TrackedProxy wrapping the loaded mesh."""
        import tempfile, os

        dag = DAG()
        mesh = make_mesh(n=5)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)
            dag.begin_run()
            proxy = tracked_read(tmp_path, dag)
            assert isinstance(proxy, TrackedProxy)
        finally:
            os.unlink(tmp_path)

    def test_tracked_read_cache_hit_same_mtime(self):
        """Reading the same file twice (same mtime) → second read is a hit."""
        import tempfile, os

        dag = DAG()
        mesh = make_mesh(n=5)
        with tempfile.NamedTemporaryFile(suffix=".vtk", delete=False) as f:
            tmp_path = f.name
        try:
            mesh.save(tmp_path)

            dag.begin_run()
            p1 = tracked_read(tmp_path, dag)
            dag.end_run()
            s1 = dag.stats()

            dag.begin_run()
            p2 = tracked_read(tmp_path, dag)
            dag.end_run()
            s2 = dag.stats()

            assert s1["misses"] >= 1
            assert s2["hits"] >= 1
            assert s2["misses"] == 0

        finally:
            os.unlink(tmp_path)
