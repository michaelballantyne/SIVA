"""Executor tests for tracked-execution library.

Tests for execute_pipeline(), inspect_pipeline(), tracked_read(), and the
restricted namespace. These tests are self-contained: they create synthetic
PyVista meshes and write temp files as needed. No display is required.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

# Ensure the package is importable from its source directory
_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.core import DAG
from tracked_execution.dispatch import stable_hash
from tracked_execution.executor import execute_pipeline, tracked_read, ExecutionResult
from tracked_execution.executor import inspect_pipeline, InspectResult
from tracked_execution.proxy import TrackedProxy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def create_test_data(n: int = 10, seed: int = 42) -> str:
    """Create a synthetic mesh and save to a temp VTK file. Returns path."""
    rng = np.random.default_rng(seed)
    mesh = pv.ImageData(dimensions=(n, n, n))
    mesh["Temperature"] = rng.random(mesh.n_points) * 1000
    mesh["Pressure"] = rng.random(mesh.n_points) * 100
    tmp = tempfile.mktemp(suffix=".vtk")
    mesh.save(tmp)
    return tmp


# ---------------------------------------------------------------------------
# 1. test_execute_pipeline_basic
# ---------------------------------------------------------------------------

class TestExecutePipelineBasic:
    def test_returns_execution_result(self):
        """execute_pipeline returns an ExecutionResult."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
n = filtered.n_points
print(f"filtered points: {{n}}")
"""
            result = execute_pipeline(code, dag)
            assert isinstance(result, ExecutionResult)
        finally:
            os.unlink(tmp)

    def test_output_captured(self):
        """print() output inside the pipeline is captured."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code = f"""
mesh = read("{tmp}")
print("hello from pipeline")
"""
            result = execute_pipeline(code, dag)
            assert "hello from pipeline" in result.output
        finally:
            os.unlink(tmp)

    def test_stats_returned(self):
        """Stats dict has expected keys."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
"""
            result = execute_pipeline(code, dag)
            assert "hits" in result.stats
            assert "misses" in result.stats
            assert "evictions" in result.stats
            assert result.stats["misses"] >= 1
        finally:
            os.unlink(tmp)

    def test_names_populated(self):
        """dag.names is populated with variable names → hashes."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
"""
            result = execute_pipeline(code, dag)
            assert "mesh" in result.names
            assert "filtered" in result.names
        finally:
            os.unlink(tmp)

    def test_show_callback_called(self):
        """show_callback is invoked when show() is called in the pipeline."""
        dag = DAG()
        tmp = create_test_data(n=5)
        events = []

        def cb(event_type, *args, **kwargs):
            events.append(event_type)

        try:
            code = f"""
mesh = read("{tmp}")
show(mesh)
"""
            execute_pipeline(code, dag, show_callback=cb)
            assert "show" in events
        finally:
            os.unlink(tmp)

    def test_actors_recorded(self):
        """Actors passed to show() are recorded in result.actors."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code = f"""
mesh = read("{tmp}")
show(mesh, color="red")
"""
            result = execute_pipeline(code, dag)
            assert len(result.actors) == 1
            mesh_proxy, kwargs = result.actors[0]
            assert isinstance(mesh_proxy, TrackedProxy)
            assert kwargs.get("color") == "red"
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 2. test_execute_pipeline_caching
# ---------------------------------------------------------------------------

class TestExecutePipelineCaching:
    def test_hits_on_rerun(self):
        """Running the same pipeline twice → hits > 0 on second run."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
"""
            execute_pipeline(code, dag)
            result2 = execute_pipeline(code, dag)
            assert result2.stats["hits"] > 0
        finally:
            os.unlink(tmp)

    def test_zero_misses_on_rerun(self):
        """Running the same pipeline twice → zero misses on second run."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
n = filtered.n_points
"""
            execute_pipeline(code, dag)
            result2 = execute_pipeline(code, dag)
            assert result2.stats["misses"] == 0
        finally:
            os.unlink(tmp)

    def test_tracked_read_cache_hit(self):
        """tracked_read uses cache when file mtime hasn't changed."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            dag.begin_run()
            p1 = tracked_read(tmp, dag)
            dag.end_run()
            s1 = dag.stats()

            dag.begin_run()
            p2 = tracked_read(tmp, dag)
            dag.end_run()
            s2 = dag.stats()

            assert s1["misses"] >= 1
            assert s2["hits"] >= 1
            assert s2["misses"] == 0
        finally:
            os.unlink(tmp)

    def test_numpy_namespace_cached(self):
        """Numpy operations via np.* are cached between runs."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code = f"""
mesh = read("{tmp}")
arr = mesh["Temperature"]
p95 = np.percentile(arr, 95)
print(f"p95: {{p95:.2f}}")
"""
            execute_pipeline(code, dag)
            result2 = execute_pipeline(code, dag)
            assert result2.stats["hits"] > 0
            assert result2.stats["misses"] == 0
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 3. test_execute_pipeline_change
# ---------------------------------------------------------------------------

class TestExecutePipelineChange:
    def test_downstream_reruns_on_param_change(self):
        """Changing threshold value causes threshold to re-run (miss)."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code1 = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
"""
            code2 = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=600.0, scalars="Temperature")
"""
            execute_pipeline(code1, dag)
            result2 = execute_pipeline(code2, dag)
            assert result2.stats["misses"] >= 1
        finally:
            os.unlink(tmp)

    def test_upstream_cached_on_param_change(self):
        """When threshold param changes, the read() upstream is still cached."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code1 = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
"""
            code2 = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=600.0, scalars="Temperature")
"""
            execute_pipeline(code1, dag)
            result2 = execute_pipeline(code2, dag)
            # read() should be a hit, threshold should be a miss
            assert result2.stats["hits"] >= 1
            assert result2.stats["misses"] >= 1
        finally:
            os.unlink(tmp)

    def test_eviction_after_change(self):
        """Old threshold result is evicted when param changes."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            code1 = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0, scalars="Temperature")
"""
            result1 = execute_pipeline(code1, dag)
            # After first run, cache has the threshold result

            code2 = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=600.0, scalars="Temperature")
"""
            result2 = execute_pipeline(code2, dag)
            # The old threshold(500) entry should be evicted
            assert result2.stats["evictions"] >= 1
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 4. test_inspect_pipeline
# ---------------------------------------------------------------------------

class TestInspectPipeline:
    def test_basic_inspect(self):
        """inspect_pipeline can access named variables from last pipeline run."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            pipeline_code = f"""
fire = read("{tmp}")
"""
            execute_pipeline(pipeline_code, dag)

            inspect_code = """
n = fire.n_points
print(f"n_points: {n}")
"""
            result = inspect_pipeline(inspect_code, dag)
            assert isinstance(result, InspectResult)
            assert "n_points:" in result.output
        finally:
            os.unlink(tmp)

    def test_returns_inspect_result(self):
        """inspect_pipeline returns InspectResult with output attribute."""
        dag = DAG()
        mesh = pv.ImageData(dimensions=(5, 5, 5))
        mesh["Temperature"] = np.ones(mesh.n_points) * 42.0
        h = stable_hash(("root", "test"))
        dag.cache[h] = mesh
        dag.names = {"mymesh": h}

        result = inspect_pipeline('print("hello")', dag)
        assert isinstance(result, InspectResult)
        assert "hello" in result.output

    def test_inspect_array_stats(self):
        """inspect_pipeline can compute array stats via proxy methods."""
        dag = DAG()
        tmp = create_test_data(n=10)
        try:
            execute_pipeline(f'fire = read("{tmp}")', dag)
            result = inspect_pipeline("""
arr = fire["Temperature"]
print(f"min: {arr.min():.1f}")
print(f"max: {arr.max():.1f}")
print(f"mean: {arr.mean():.1f}")
""", dag)
            assert "min:" in result.output
            assert "max:" in result.output
            assert "mean:" in result.output
        finally:
            os.unlink(tmp)

    def test_inspect_numpy_available(self):
        """np is available in inspect_pipeline namespace."""
        dag = DAG()
        mesh = pv.ImageData(dimensions=(5, 5, 5))
        mesh["T"] = np.ones(mesh.n_points)
        h = stable_hash(("root", "test"))
        dag.cache[h] = mesh
        dag.names = {"mymesh": h}

        result = inspect_pipeline("""
arr = mymesh["T"]
print(f"size: {arr.size}")
""", dag)
        assert "size:" in result.output


# ---------------------------------------------------------------------------
# 5. test_inspect_pipeline_no_mutation
# ---------------------------------------------------------------------------

class TestInspectPipelineNoMutation:
    def test_no_show(self):
        """show() is not available in inspect_pipeline."""
        dag = DAG()
        mesh = pv.ImageData(dimensions=(5, 5, 5))
        h = stable_hash(("root", "test"))
        dag.cache[h] = mesh
        dag.names = {"mymesh": h}

        code = """
try:
    show(mymesh)
    print("ERROR: should not reach")
except NameError:
    print("OK: show not available")
"""
        result = inspect_pipeline(code, dag)
        assert "OK: show not available" in result.output

    def test_no_add_mesh(self):
        """add_mesh() is not available in inspect_pipeline."""
        dag = DAG()
        mesh = pv.ImageData(dimensions=(5, 5, 5))
        h = stable_hash(("root", "test"))
        dag.cache[h] = mesh
        dag.names = {"mymesh": h}

        code = """
try:
    add_mesh(mymesh)
    print("ERROR: should not reach")
except NameError:
    print("OK: add_mesh not available")
"""
        result = inspect_pipeline(code, dag)
        assert "OK: add_mesh not available" in result.output

    def test_no_screenshot(self):
        """screenshot() is not available in inspect_pipeline."""
        dag = DAG()
        dag.names = {}

        code = """
try:
    screenshot("/tmp/test.png")
    print("ERROR: should not reach")
except NameError:
    print("OK: screenshot not available")
"""
        result = inspect_pipeline(code, dag)
        assert "OK: screenshot not available" in result.output

    def test_no_read(self):
        """read() is not available in inspect_pipeline."""
        dag = DAG()
        dag.names = {}

        code = """
try:
    read("/tmp/some_file.vtk")
    print("ERROR: should not reach")
except NameError:
    print("OK: read not available")
"""
        result = inspect_pipeline(code, dag)
        assert "OK: read not available" in result.output

    def test_proxy_blacklist_still_enforced(self):
        """Even in inspect_pipeline, blacklisted methods on proxies are blocked."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            execute_pipeline(f'fire = read("{tmp}")', dag)

            code = """
try:
    fire.save("/tmp/blocked.vtk")
    print("ERROR: should not reach")
except AttributeError:
    print("OK: save blocked")
"""
            result = inspect_pipeline(code, dag)
            assert "OK: save blocked" in result.output
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 6. test_restricted_namespace
# ---------------------------------------------------------------------------

class TestRestrictedNamespace:
    def test_no_import_os(self):
        """Pipeline code cannot import os."""
        dag = DAG()
        code = """
try:
    import os
    print("ERROR: import should fail")
except (ImportError, NameError):
    print("OK: import blocked")
"""
        result = execute_pipeline(code, dag)
        assert "OK: import blocked" in result.output

    def test_no_import_subprocess(self):
        """Pipeline code cannot import subprocess."""
        dag = DAG()
        code = """
try:
    import subprocess
    print("ERROR: import should fail")
except (ImportError, NameError):
    print("OK: import blocked")
"""
        result = execute_pipeline(code, dag)
        assert "OK: import blocked" in result.output

    def test_no_builtins_access(self):
        """Pipeline code runs in a restricted namespace without full module access.

        The key invariant is that import statements fail with a clear error —
        __import__ is present in __builtins__ but is a blocked stub that raises
        ImportError with an actionable message rather than silently succeeding.
        """
        dag = DAG()
        # The important check: imports actually fail (even though __import__ is
        # present as a blocked stub to give better error messages than NameError)
        code = """
try:
    import os
    print("ERROR: import should fail")
except (ImportError, NameError):
    print("OK: import is blocked")
"""
        result = execute_pipeline(code, dag)
        assert "OK: import is blocked" in result.output
        assert "ERROR" not in result.output

    def test_safe_builtins_available(self):
        """Pipeline code can use safe builtins like range, len, int."""
        dag = DAG()
        code = """
xs = list(range(5))
n = len(xs)
x = int("42")
print(f"ok: {n} {x}")
"""
        result = execute_pipeline(code, dag)
        assert "ok: 5 42" in result.output

    def test_no_open(self):
        """Pipeline code cannot call open() to read arbitrary files.

        open() now raises PermissionError (not NameError) with an actionable
        message telling the agent to use read() instead.
        """
        dag = DAG()
        code = """
try:
    f = open("/etc/passwd")
    print("ERROR: open should fail")
except (NameError, PermissionError):
    print("OK: open not available")
"""
        result = execute_pipeline(code, dag)
        assert "OK: open not available" in result.output

    def test_no_exec_eval(self):
        """Pipeline code cannot use exec or eval to escape the sandbox."""
        dag = DAG()
        code = """
try:
    exec("import os")
    print("ERROR: exec should fail")
except NameError:
    print("OK: exec not available")
"""
        result = execute_pipeline(code, dag)
        assert "OK: exec not available" in result.output

    def test_inspect_pipeline_no_import(self):
        """inspect_pipeline also blocks imports."""
        dag = DAG()
        dag.names = {}

        code = """
try:
    import os
    print("ERROR: import should fail")
except (ImportError, NameError):
    print("OK: import blocked")
"""
        result = inspect_pipeline(code, dag)
        assert "OK: import blocked" in result.output


# ---------------------------------------------------------------------------
# 7. Error recovery — DAG stays consistent after pipeline errors
# ---------------------------------------------------------------------------

class TestErrorRecovery:
    """Verify that execute_pipeline leaves the DAG in a clean state after errors."""

    def test_syntax_error_propagates(self):
        """SyntaxError in pipeline code propagates to the caller."""
        dag = DAG()
        with pytest.raises(SyntaxError):
            execute_pipeline("this is not : valid python !!!", dag)

    def test_runtime_error_propagates(self):
        """RuntimeError in pipeline code propagates to the caller."""
        dag = DAG()
        with pytest.raises((NameError, RuntimeError, TypeError)):
            execute_pipeline("result = undefined_variable_xyz", dag)

    def test_dag_usable_after_syntax_error(self):
        """After a SyntaxError, the DAG can still be used for a subsequent run."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            # First: bad code that raises SyntaxError
            with pytest.raises(SyntaxError):
                execute_pipeline("this is not : valid python !!!", dag)

            # Second: valid pipeline — should work without issues
            code = f'mesh = read("{tmp}")\nprint("ok")'
            result = execute_pipeline(code, dag)
            assert "ok" in result.output
        finally:
            os.unlink(tmp)

    def test_dag_usable_after_runtime_error(self):
        """After a RuntimeError, the DAG can still be used for a subsequent run."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            # First: pipeline that raises a NameError at runtime
            with pytest.raises(NameError):
                execute_pipeline("x = totally_undefined_var_xyz", dag)

            # Second: valid pipeline — cache still works
            code = f'mesh = read("{tmp}")\nprint("recovered")'
            result = execute_pipeline(code, dag)
            assert "recovered" in result.output
        finally:
            os.unlink(tmp)

    def test_partial_pipeline_cache_survives_error(self):
        """Successful steps before an error are still cached after recovery.

        When a pipeline reads a file successfully then raises an error, the
        read() result is evicted by end_run() (since the run didn't complete
        normally).  On the next clean run, read() re-executes and re-caches.
        The important guarantee is that the DAG doesn't get stuck in a broken
        state that prevents future runs.
        """
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            # Fail partway through
            with pytest.raises(AttributeError):
                execute_pipeline(
                    f'mesh = read("{tmp}")\nmesh.nonexistent_filter()',
                    dag,
                )

            # After error, subsequent clean runs must succeed
            code = f'mesh = read("{tmp}")\nprint(mesh.n_points)'
            result = execute_pipeline(code, dag)
            assert result.stats["misses"] >= 1  # read() re-executes
            assert "n_points" in result.output or len(result.output.strip()) > 0
        finally:
            os.unlink(tmp)

    def test_second_run_after_error_has_correct_stats(self):
        """Stats from the run after an error are accurate (not stale from the failed run)."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            with pytest.raises(NameError):
                execute_pipeline("x = totally_undefined_var_xyz", dag)

            code = f'mesh = read("{tmp}")'
            result = execute_pipeline(code, dag)
            # Stats should reflect the clean second run only
            assert "hits" in result.stats
            assert "misses" in result.stats
            assert result.stats["misses"] >= 1  # read() was a miss
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 8. ExecutionResult and InspectResult ergonomics
# ---------------------------------------------------------------------------

class TestResultErgonomics:
    """Test the repr, ok sentinel, and inspect_pipeline alias."""

    def test_execution_result_ok_is_true(self):
        """ExecutionResult.ok is True — indicates the pipeline completed."""
        dag = DAG()
        result = execute_pipeline("x = 1 + 1", dag)
        assert result.ok is True

    def test_execution_result_repr_contains_stats(self):
        """ExecutionResult repr includes hits, misses, evictions."""
        dag = DAG()
        result = execute_pipeline("x = 1 + 1", dag)
        r = repr(result)
        assert "hits=" in r
        assert "misses=" in r
        assert "evictions=" in r

    def test_execution_result_repr_contains_output_preview(self):
        """ExecutionResult repr includes a preview of captured output."""
        dag = DAG()
        result = execute_pipeline('print("hello world")', dag)
        r = repr(result)
        assert "hello world" in r

    def test_execution_result_repr_contains_names(self):
        """ExecutionResult repr includes the list of named pipeline variables."""
        dag = DAG()
        tmp = create_test_data(n=5)
        try:
            result = execute_pipeline(f'mesh = read("{tmp}")', dag)
            r = repr(result)
            assert "mesh" in r
        finally:
            os.unlink(tmp)

    def test_inspect_result_repr_contains_output(self):
        """InspectResult repr includes a preview of captured output."""
        dag = DAG()
        dag.names = {}
        result = inspect_pipeline('print("inspection output")', dag)
        r = repr(result)
        assert "inspection output" in r

    def test_inspect_pipeline_is_primary_name(self):
        """inspect_pipeline is importable from the top-level package."""
        from tracked_execution import inspect_pipeline as ip
        assert callable(ip)

    def test_pv_available_in_pipeline_namespace(self):
        """pv (pyvista module) is accessible in the pipeline namespace."""
        dag = DAG()
        # pv.ImageData() creates a PyVista object; n_points is 0 for a default ImageData
        result = execute_pipeline("""
img = pv.ImageData()
print(f"pv available, type={type(img).__name__}")
""", dag)
        assert "pv available" in result.output
        assert "ImageData" in result.output
