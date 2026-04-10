"""Tests for Session and runner helpers.

All tests use plotter=None (no rendering) so they run without a display.
Tests verify cache stats, inspect, and re-execution caching behaviour.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

# Ensure package is importable from its source directory
_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.core import DAG
from tracked_execution.dispatch import stable_hash
from tracked_execution.executor import execute_pipeline
from tracked_execution.executor import inspect_pipeline, inspect_exec
from tracked_execution.reconciler import SceneReconciler
from tracked_execution.runner import Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_vtk_file(n: int = 5, seed: int = 42) -> str:
    """Create a synthetic mesh, save to a temp VTK file, and return the path."""
    rng = np.random.default_rng(seed)
    mesh = pv.ImageData(dimensions=(n, n, n))
    mesh["Temperature"] = rng.random(mesh.n_points) * 1000
    tmp = tempfile.mktemp(suffix=".vtk")
    mesh.save(tmp)
    return tmp


# ---------------------------------------------------------------------------
# 1. Session.execute — basic pipeline run
# ---------------------------------------------------------------------------

class TestSessionExecute:
    def test_execute_returns_result(self):
        """Session.execute returns an ExecutionResult."""
        from tracked_execution.executor import ExecutionResult

        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0)
n = filtered.n_points
print(f"n: {{n}}")
"""
            result = session.execute(code=code)
            assert isinstance(result, ExecutionResult)
        finally:
            os.unlink(tmp)

    def test_execute_captures_output(self):
        """Output from print() in the pipeline is captured in result.output."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            code = f"""
mesh = read("{tmp}")
print("hello from pipeline")
"""
            result = session.execute(code=code)
            assert "hello from pipeline" in result.output
        finally:
            os.unlink(tmp)

    def test_execute_stats_present(self):
        """Session stats after execute() returns hits/misses/evictions."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0)
"""
            session.execute(code=code)
            stats = session.stats()
            assert "hits" in stats
            assert "misses" in stats
            assert "evictions" in stats
            assert stats["misses"] >= 1
        finally:
            os.unlink(tmp)

    def test_execute_no_code_no_filepath_raises(self):
        """Calling execute() with neither code nor file_path raises ValueError."""
        session = Session(plotter=None)
        with pytest.raises(ValueError, match="file_path"):
            session.execute()

    def test_execute_from_file(self):
        """Session.execute reads from self.file_path when no code is given."""
        tmp_data = make_vtk_file()
        tmp_pipeline = tempfile.mktemp(suffix=".py")
        try:
            Path(tmp_pipeline).write_text(f"""
mesh = read("{tmp_data}")
print("from file")
""")
            session = Session(file_path=tmp_pipeline, plotter=None)
            result = session.execute()
            assert "from file" in result.output
        finally:
            os.unlink(tmp_data)
            os.unlink(tmp_pipeline)

    def test_last_result_updated(self):
        """session.last_result is updated after each execute() call."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            assert session.last_result is None

            code = f'mesh = read("{tmp}")'
            session.execute(code=code)
            assert session.last_result is not None
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 2. Session.inspect — read-only inspection
# ---------------------------------------------------------------------------

class TestSessionInspect:
    def test_inspect_after_execute(self):
        """inspect() can access named proxies from the last execute() call."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            pipeline_code = f'fire = read("{tmp}")'
            session.execute(code=pipeline_code)

            result = session.inspect("n = fire.n_points\nprint(f'n: {n}')")
            assert "n:" in result.output
        finally:
            os.unlink(tmp)

    def test_inspect_returns_inspect_result(self):
        """inspect() returns an InspectResult object."""
        from tracked_execution.executor import InspectResult

        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            session.execute(code=f'fire = read("{tmp}")')
            result = session.inspect('print("hello inspect")')
            assert isinstance(result, InspectResult)
        finally:
            os.unlink(tmp)

    def test_inspect_array_stats(self):
        """inspect() can compute min/max/mean of named arrays."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            session.execute(code=f'fire = read("{tmp}")')
            result = session.inspect("""
arr = fire["Temperature"]
print(f"min: {arr.min():.1f}")
print(f"max: {arr.max():.1f}")
""")
            assert "min:" in result.output
            assert "max:" in result.output
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 3. Session re-execution caching
# ---------------------------------------------------------------------------

class TestSessionReExecuteCaching:
    def test_second_run_has_cache_hits(self):
        """Running the same pipeline code twice → hits > 0 on second run."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0)
"""
            session.execute(code=code)
            result2 = session.execute(code=code)

            assert result2.stats["hits"] > 0
        finally:
            os.unlink(tmp)

    def test_second_run_zero_misses(self):
        """Running the same pipeline code twice → zero misses on second run."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            code = f"""
mesh = read("{tmp}")
filtered = mesh.threshold(value=500.0)
n = filtered.n_points
"""
            session.execute(code=code)
            result2 = session.execute(code=code)

            assert result2.stats["misses"] == 0
        finally:
            os.unlink(tmp)

    def test_param_change_causes_miss(self):
        """Changing threshold value between executions → miss on second run."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            code_v1 = f'mesh = read("{tmp}")\nfiltered = mesh.threshold(value=500.0)'
            code_v2 = f'mesh = read("{tmp}")\nfiltered = mesh.threshold(value=600.0)'

            session.execute(code=code_v1)
            result2 = session.execute(code=code_v2)

            # read() should be a hit; threshold() should be a miss
            assert result2.stats["hits"] >= 1
            assert result2.stats["misses"] >= 1
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 4. Session.stats() with no prior execute
# ---------------------------------------------------------------------------

class TestSessionStats:
    def test_stats_empty_before_execute(self):
        """stats() returns empty dict when no execute() has been called."""
        session = Session(plotter=None)
        assert session.stats() == {}

    def test_stats_reflects_last_run(self):
        """stats() returns the stats from the most recent execute() call."""
        tmp = make_vtk_file()
        try:
            session = Session(plotter=None)
            code = f'mesh = read("{tmp}")\nfiltered = mesh.threshold(value=500.0)'

            # First run: misses expected
            session.execute(code=code)
            stats_run1 = session.stats()
            assert stats_run1["misses"] >= 1

            # Second run: hits expected
            session.execute(code=code)
            stats_run2 = session.stats()
            assert stats_run2["hits"] > 0
            assert stats_run2["misses"] == 0
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 5. Session as context manager
# ---------------------------------------------------------------------------

class TestSessionContextManager:
    def test_context_manager_cleans_up(self):
        """Session used as context manager exits without error (no plotter)."""
        tmp = make_vtk_file()
        try:
            with Session(plotter=None) as session:
                result = session.execute(code=f'mesh = read("{tmp}")')
                assert result is not None
            # After __exit__, session is cleaned up; watcher should be None
            assert session.watcher is None
        finally:
            os.unlink(tmp)


# ---------------------------------------------------------------------------
# 6. Session screenshot raises without plotter
# ---------------------------------------------------------------------------

class TestSessionScreenshot:
    def test_screenshot_raises_without_plotter(self):
        """screenshot() raises RuntimeError when plotter is None."""
        session = Session(plotter=None)
        with pytest.raises(RuntimeError, match="plotter"):
            session.screenshot("/tmp/test_no_plotter.png")


# ---------------------------------------------------------------------------
# 7. Session with custom DAG
# ---------------------------------------------------------------------------

class TestSessionCustomDAG:
    def test_custom_dag_used(self):
        """Session uses the provided DAG instance."""
        dag = DAG()
        session = Session(plotter=None, dag=dag)
        assert session.dag is dag

    def test_dag_populated_after_execute(self):
        """After execute(), dag.names contains variable names from the pipeline."""
        tmp = make_vtk_file()
        try:
            dag = DAG()
            session = Session(plotter=None, dag=dag)
            session.execute(code=f'fire = read("{tmp}")')
            assert "fire" in dag.names
        finally:
            os.unlink(tmp)
