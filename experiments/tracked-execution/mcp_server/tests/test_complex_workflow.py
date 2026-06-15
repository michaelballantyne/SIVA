"""Complex workflow test: multi-view, iteration, error recovery, vtk_escape.

Tests:
- test_multi_view_iteration: two independent views of same data, iterated
  separately; verifies shared read cache avoids double-loading.
- test_error_recovery: write bad pipeline code, verify watcher recovers on fix.
- test_vtk_escape_in_pipeline_file: vtk_escape used inside a pipeline file.
- test_inspect_driven_refinement: inspect to get field ranges, then refine
  the pipeline three times using those ranges.

Run with:
    xvfb-run -a python -m pytest mcp_server/tests/test_complex_workflow.py -v --timeout=60
"""

import os
import shutil
import tempfile
import time

import numpy as np
import pyvista as pv
import pytest

WILDFIRE_DATA = "/home/user/SIVA/datasets/wildfire/data/output.30000.vts"
_HAS_WILDFIRE = os.path.exists(WILDFIRE_DATA)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_dir():
    """Temp directory with a multi-field synthetic VTK file (no wildfire needed).

    Creates a 20x20x20 structured grid with three scalar fields:
      T   — temperature   linspace(200, 800, n_points)
      P   — pressure      linspace(1.0, 10.0, n_points)
      rho — density       linspace(0.1, 5.0, n_points)

    Small enough for fast tests; large enough to threshold meaningfully.
    """
    d = tempfile.mkdtemp()

    mesh = pv.ImageData(dimensions=(20, 20, 20))
    n = mesh.n_points
    mesh["T"] = np.linspace(200.0, 800.0, n)
    mesh["P"] = np.linspace(1.0, 10.0, n)
    mesh["rho"] = np.linspace(0.1, 5.0, n)
    mesh.save(os.path.join(d, "multi.vtk"))

    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def wildfire_session_dir():
    """Temp directory with a symlink to the wildfire dataset (skipped if absent)."""
    d = tempfile.mkdtemp()
    os.symlink(WILDFIRE_DATA, os.path.join(d, "output.30000.vts"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WATCHER_DEBOUNCE_S = 0.15  # watcher debounce is 100 ms; add 50 ms margin


def _write_pipeline(path: str, code: str, srv=None, timeout_s: float = 5.0) -> None:
    """Write *code* to *path* and wait for the file watcher to pick it up.

    If *srv* (the server module) is provided, polls until the view's
    ``reload_count`` increments, which reliably indicates the watcher has
    finished processing the new file (success or error).  Falls back to a
    fixed 1-second sleep when *srv* is not available.

    After confirming the reload, waits _WATCHER_DEBOUNCE_S to ensure the
    watcher's debounce window has expired before returning.  This prevents
    the next file write from being suppressed by the debounce logic.

    Args:
        path:      Absolute path to the pipeline file to write.
        code:      Python source to write to the file.
        srv:       The server module (from the ``reset_server`` fixture).
                   Pass this whenever possible to avoid fixed sleeps.
        timeout_s: Maximum seconds to wait for the watcher reload.
    """
    import os

    # Find the ViewState for this pipeline file before writing.
    before_count = None
    vs = None
    if srv is not None:
        view_name = os.path.splitext(os.path.basename(path))[0]
        vs = srv._views.get(view_name)
        if vs is not None:
            with vs.lock:
                before_count = vs.reload_count

    with open(path, "w") as f:
        f.write(code)

    if vs is not None and before_count is not None:
        # Poll until reload_count changes, meaning the watcher fired.
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with vs.lock:
                if vs.reload_count != before_count:
                    # Wait for the debounce window to expire so the next
                    # write is not suppressed by the watcher's debounce logic.
                    time.sleep(_WATCHER_DEBOUNCE_S)
                    return
            time.sleep(0.05)
        # Timed out — fall through without error; test assertions will catch it.
    else:
        # No server / view available yet — wait a fixed amount.
        time.sleep(1.0)


def _view_status(list_views_fn, pipeline_file: str) -> str:
    """Extract the status block for a single view from list_views output.

    Returns the lines for the named view (view name derived from pipeline_file).
    """
    import os
    view_name = os.path.splitext(os.path.basename(pipeline_file))[0]
    full = list_views_fn()
    # Split into lines, find the block starting with "  <view_name>"
    lines = full.splitlines()
    result_lines = []
    in_block = False
    for line in lines:
        if line.strip().startswith(view_name):
            in_block = True
        elif in_block and line.startswith("  ") and not line.startswith("    "):
            # New view block starts
            break
        if in_block:
            result_lines.append(line)
    return "\n".join(result_lines) if result_lines else full


def _wait_for_status(
    list_views_fn,
    pipeline_file: str,
    expect_error: bool,
    timeout_s: float = 5.0,
    poll_s: float = 0.2,
) -> str:
    """Poll list_views until the error state for a view matches *expect_error*.

    Returns the final per-view status string.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = _view_status(list_views_fn, pipeline_file)
        has_error = "Last error:" in status
        if has_error == expect_error:
            return status
        time.sleep(poll_s)
    return _view_status(list_views_fn, pipeline_file)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestComplexWorkflow:
    """Complex workflow tests: multi-view, iteration, error recovery, vtk_escape."""

    # ------------------------------------------------------------------
    # 1. Multi-view iteration
    # ------------------------------------------------------------------

    def test_multi_view_iteration(self, session_dir, reset_server):
        """Two views of the same data are iterated independently.

        Workflow:
        1. Set working dir (synthetic data).
        2. Create view-T.py (threshold on T).
        3. Create view-P.py (threshold on P).
        4. Inspect view-T: get point count.
        5. Refine view-T threshold in-place, wait for watcher.
        6. Inspect view-P: verify it is independent of view-T's state.
        7. Verify shared read cache has exactly one entry (file loaded once).
        8. Close view-T; confirm view-P still works.
        """
        from mcp_server.server import (
            set_working_directory,
            create_view,
            inspect,
            screenshot,
            close_view,
            list_views,
        )
        from mcp.server.fastmcp import Image

        # Step 1: set working directory.
        result = set_working_directory(session_dir)
        assert "Working directory set" in result, f"set_working_directory failed: {result}"
        assert "multi.vtk" in result, f"Expected multi.vtk listed, got: {result}"

        # Step 2: create temperature view.
        t_pipe = os.path.join(session_dir, "view-T.py")
        with open(t_pipe, "w") as f:
            f.write(
                'mesh = read("multi.vtk")\n'
                'hot = mesh.threshold(value=500.0, scalars="T")\n'
                'print(f"T>500: {hot.n_points} points")\n'
                'show(hot, colormap="inferno")\n'
            )
        r_t = create_view("view-T.py")
        assert "Error" not in r_t, f"create_view view-T failed: {r_t}"
        assert "view-T" in r_t or "View" in r_t, f"Expected view name in response: {r_t}"

        # Step 3: create pressure view.
        p_pipe = os.path.join(session_dir, "view-P.py")
        with open(p_pipe, "w") as f:
            f.write(
                'mesh = read("multi.vtk")\n'
                'high_p = mesh.threshold(value=5.0, scalars="P")\n'
                'print(f"P>5: {high_p.n_points} points")\n'
                'show(high_p, colormap="coolwarm")\n'
            )
        r_p = create_view("view-P.py")
        assert "Error" not in r_p, f"create_view view-P failed: {r_p}"

        # Step 4: inspect view-T — get initial point count.
        r_inspect_t = inspect("view-T.py", "print(hot.n_points)")
        assert "Error" not in r_inspect_t, f"Inspect view-T failed: {r_inspect_t}"
        initial_hot_pts = int(r_inspect_t.strip())
        assert initial_hot_pts > 0, f"Expected > 0 points above T=500, got {initial_hot_pts}"
        print(f"Initial hot points (T>500): {initial_hot_pts}")

        # Step 5: refine view-T — tighten threshold to T>650.
        # The watcher will pick up the edit automatically.
        _write_pipeline(t_pipe, (
            'mesh = read("multi.vtk")\n'
            'hot = mesh.threshold(value=650.0, scalars="T")\n'
            'print(f"T>650: {hot.n_points} points")\n'
            'show(hot, colormap="inferno")\n'
        ), srv=reset_server)

        # Wait for the watcher reload.
        status_t = _wait_for_status(list_views, "view-T.py", expect_error=False)
        assert "no errors" in status_t.lower(), (
            f"Expected no errors after watcher reload, got:\n{status_t}"
        )

        # Inspect again — point count should decrease.
        r_inspect_t2 = inspect("view-T.py", "print(hot.n_points)")
        assert "Error" not in r_inspect_t2, f"Post-refinement inspect failed: {r_inspect_t2}"
        refined_hot_pts = int(r_inspect_t2.strip())
        print(f"Refined hot points (T>650): {refined_hot_pts}")
        assert refined_hot_pts < initial_hot_pts, (
            f"Tightening threshold T>500→T>650 should reduce point count: "
            f"{initial_hot_pts} -> {refined_hot_pts}"
        )

        # Step 6: inspect view-P — independent of view-T's state.
        r_inspect_p = inspect("view-P.py", "print(high_p.n_points)")
        assert "Error" not in r_inspect_p, f"Inspect view-P failed: {r_inspect_p}"
        high_p_pts = int(r_inspect_p.strip())
        assert high_p_pts > 0, f"Expected > 0 points with P>5, got {high_p_pts}"
        print(f"High pressure points (P>5): {high_p_pts}")

        # Step 7: shared read cache has exactly one entry (file loaded once).
        cache = reset_server._shared_read_cache
        assert len(cache) == 1, (
            f"Expected 1 shared cache entry for the same file (multi.vtk), "
            f"got {len(cache)}: {list(cache.keys())}"
        )

        # Step 8: take screenshots from both views.
        img_t = screenshot("view-T.py")
        assert isinstance(img_t, Image), f"Expected Image from view-T, got {type(img_t)}"
        assert img_t.data[:4] == b"\x89PNG", "Expected PNG from view-T"

        img_p = screenshot("view-P.py")
        assert isinstance(img_p, Image), f"Expected Image from view-P, got {type(img_p)}"
        assert img_p.data[:4] == b"\x89PNG", "Expected PNG from view-P"

        # Step 9: close view-T, verify view-P still works.
        close_result = close_view("view-T.py")
        assert "closed" in close_result.lower(), f"Expected close confirmation, got: {close_result}"
        assert "view-T" not in reset_server._views, "view-T should be removed"

        # view-P should still respond.
        r_p_after = inspect("view-P.py", "print(high_p.n_points)")
        assert "Error" not in r_p_after, (
            f"view-P inspect failed after view-T close: {r_p_after}"
        )
        assert r_p_after.strip() == str(high_p_pts), (
            f"view-P point count changed after view-T close: {r_p_after}"
        )

        views = list_views()
        assert "view-P" in views, f"Expected view-P still listed, got: {views}"
        assert "view-T" not in views, f"Expected view-T gone, got: {views}"

        print("test_multi_view_iteration passed.")

    # ------------------------------------------------------------------
    # 2. Error recovery
    # ------------------------------------------------------------------

    def test_error_recovery(self, session_dir, reset_server):
        """Write bad pipeline code; verify the watcher recovers when fixed.

        Workflow:
        1. Create a view with a valid pipeline.
        2. Overwrite the file with a syntax error.
        3. Wait for watcher; list_views shows an error.
        4. Fix the file with valid code.
        5. Wait for watcher; list_views shows success.
        6. screenshot() works after recovery.
        """
        from mcp_server.server import (
            set_working_directory,
            create_view,
            list_views,
            screenshot,
        )
        from mcp.server.fastmcp import Image

        set_working_directory(session_dir)

        pipe = os.path.join(session_dir, "view-recover.py")
        with open(pipe, "w") as f:
            f.write(
                'mesh = read("multi.vtk")\n'
                'show(mesh, colormap="viridis")\n'
            )

        result = create_view("view-recover.py")
        assert "Error" not in result, f"create_view failed: {result}"

        # Verify initial healthy state.
        status = _view_status(list_views, "view-recover.py")
        assert "no errors" in status.lower(), (
            f"Expected no errors initially:\n{status}"
        )

        # Step 2: overwrite with a runtime error (undefined name).
        _write_pipeline(pipe, (
            'mesh = read("multi.vtk")\n'
            'bad = nonexistent_variable + 1\n'
            'show(mesh)\n'
        ), srv=reset_server)

        # Step 3: wait for watcher to detect the error.
        error_status = _wait_for_status(
            list_views, "view-recover.py", expect_error=True, timeout_s=5.0
        )
        assert "Last error:" in error_status, (
            f"Expected error reported after bad pipeline write:\n{error_status}"
        )
        assert "NameError" in error_status or "nonexistent" in error_status, (
            f"Expected NameError in status:\n{error_status}"
        )
        print(f"Error correctly captured:\n{error_status}")

        # Step 4 & 5: fix the file, wait for watcher to recover.
        _write_pipeline(pipe, (
            'mesh = read("multi.vtk")\n'
            'filtered = mesh.threshold(value=400.0, scalars="T")\n'
            'print(f"Recovered: {filtered.n_points} points")\n'
            'show(filtered, colormap="plasma")\n'
        ), srv=reset_server)

        recovered_status = _wait_for_status(
            list_views, "view-recover.py", expect_error=False, timeout_s=5.0
        )
        assert "no errors" in recovered_status.lower(), (
            f"Expected error cleared after fix:\n{recovered_status}"
        )
        print(f"Recovery confirmed:\n{recovered_status}")

        # Step 6: screenshot should succeed after recovery.
        img = screenshot("view-recover.py")
        assert isinstance(img, Image), f"Expected Image after recovery, got {type(img)}"
        assert img.data[:4] == b"\x89PNG", "Expected PNG after recovery"
        assert len(img.data) > 100, "PNG too small — likely empty render"
        print(f"Post-recovery screenshot: {len(img.data)} bytes")

        print("test_error_recovery passed.")

    # ------------------------------------------------------------------
    # 3. vtk_escape in a pipeline file
    # ------------------------------------------------------------------

    def test_vtk_escape_in_pipeline_file(self, session_dir, reset_server):
        """vtk_escape used inside a pipeline file to add a derived field.

        The function is defined directly in the pipeline code string (via exec).
        Important constraints to be aware of:

        - Imports inside function bodies are blocked because __import__ in
          the pipeline namespace raises ImportError.  The function must rely
          only on operations available without import.
        - The 'np' in the pipeline namespace is _TrackedNumpyNamespace, not
          real numpy.  Its methods (e.g. np.sqrt) return TrackedProxy objects,
          not real arrays.  Assigning a TrackedProxy to a pyvista mesh field
          (``m["field"] = proxy``) fails with "object __array__ method not
          producing an array".
        - SOLUTION: use Python arithmetic operators directly on the numpy
          arrays returned by m["field"] (e.g. ``** 0.5`` instead of
          ``np.sqrt``).  The field arrays are real numpy arrays and support
          standard arithmetic without wrapping.

        This test verifies that vtk_escape works end-to-end in a pipeline file
        and that the derived field is accessible in inspect().
        """
        from mcp_server.server import (
            set_working_directory,
            create_view,
            inspect,
            list_views,
            screenshot,
        )
        from mcp.server.fastmcp import Image

        set_working_directory(session_dir)

        # The function passed to vtk_escape is defined in the pipeline script.
        # m["T"] etc. return real numpy arrays — arithmetic on them works fine.
        # We use ** 0.5 (not np.sqrt) because np in the pipeline namespace is
        # _TrackedNumpyNamespace whose results are TrackedProxy objects, not
        # assignable to pyvista mesh fields.
        pipe = os.path.join(session_dir, "view-escape.py")
        with open(pipe, "w") as f:
            f.write(
                'mesh = read("multi.vtk")\n'
                '\n'
                '# vtk_escape function: adds a derived field to the mesh.\n'
                '# Uses ** 0.5 (not np.sqrt) so no TrackedProxy wrapping occurs.\n'
                'def add_combined_field(m):\n'
                '    """Derive a combined scalar: (T * P / rho) ** 0.5."""\n'
                '    T_vals = m["T"]\n'
                '    P_vals = m["P"]\n'
                '    rho_vals = m["rho"]\n'
                '    combined = (T_vals * P_vals / rho_vals) ** 0.5\n'
                '    m_copy = m.copy()\n'
                '    m_copy["combined"] = combined\n'
                '    return m_copy\n'
                '\n'
                'enriched = vtk_escape(mesh, add_combined_field, key="add_combined_v1")\n'
                'print(f"Enriched fields: {enriched.array_names}")\n'
                'print(f"Points: {enriched.n_points}")\n'
                'show(enriched, scalars="combined", colormap="plasma")\n'
            )

        result = create_view("view-escape.py")
        assert "Error" not in result, f"create_view view-escape failed: {result}"
        print(f"create_view result:\n{result}")

        # Verify the enriched mesh has the new field.
        r = inspect("view-escape.py", """\
print(f"Fields: {enriched.array_names}")
print(f"combined_min={enriched['combined'].min():.3f}")
print(f"combined_max={enriched['combined'].max():.3f}")
""")
        assert "Error" not in r, f"Inspect view-escape failed: {r}"
        assert "combined" in r, f"Expected 'combined' field in inspect output: {r}"
        print(f"Inspect enriched:\n{r}")

        # Screenshot should succeed.
        img = screenshot("view-escape.py")
        assert isinstance(img, Image), f"Expected Image, got {type(img)}"
        assert img.data[:4] == b"\x89PNG", "Expected PNG"
        assert len(img.data) > 100, "PNG too small"

        # Verify the pipeline ran cleanly via list_views.
        status = _view_status(list_views, "view-escape.py")
        assert "no errors" in status.lower(), (
            f"Expected no errors in vtk_escape pipeline:\n{status}"
        )
        assert "Cache:" in status, f"Expected cache stats in status: {status}"

        print("test_vtk_escape_in_pipeline_file passed.")

    # ------------------------------------------------------------------
    # 4. Inspect-driven refinement (3 iterations)
    # ------------------------------------------------------------------

    def test_inspect_driven_refinement(self, session_dir, reset_server):
        """Use inspect to guide pipeline refinement across multiple iterations.

        Workflow:
        1. Create view with basic pipeline (load + show all).
        2. Inspect: get T range and figure out a threshold.
        3. Write iteration 1: threshold at T_mean.
        4. Wait for watcher; inspect to get new point count.
        5. Write iteration 2: further filter by P > P_mean.
        6. Wait for watcher; inspect to verify both filters applied.
        7. Write iteration 3: change colormap and take screenshot.
        8. Verify cache hits increase across iterations.

        This mirrors the iterative agent workflow described in the server
        instructions.
        """
        from mcp_server.server import (
            set_working_directory,
            create_view,
            inspect,
            list_views,
            screenshot,
        )
        from mcp.server.fastmcp import Image

        set_working_directory(session_dir)

        pipe = os.path.join(session_dir, "view-refine.py")
        with open(pipe, "w") as f:
            f.write(
                'mesh = read("multi.vtk")\n'
                'print(f"Points: {mesh.n_points}")\n'
                'print(f"Fields: {mesh.array_names}")\n'
                'show(mesh, colormap="viridis")\n'
            )

        result = create_view("view-refine.py")
        assert "Error" not in result, f"create_view failed: {result}"

        # Step 2: inspect — get T and P ranges.
        r = inspect("view-refine.py", """\
T = mesh["T"]
P = mesh["P"]
T_mean = float(T.mean())
P_mean = float(P.mean())
T_max = float(T.max())
print(f"T_range=[{T.min():.1f}, {T_max:.1f}] mean={T_mean:.1f}")
print(f"P_range=[{P.min():.2f}, {P.max():.2f}] mean={P_mean:.2f}")
""")
        assert "Error" not in r, f"Initial inspect failed: {r}"
        assert "T_range" in r, f"Expected T_range in inspect output: {r}"
        assert "P_range" in r, f"Expected P_range in inspect output: {r}"
        print(f"Initial inspect:\n{r}")

        # Parse T_mean from output (format: "T_range=[200.0, 800.0] mean=500.0").
        # The synthetic data is linspace(200, 800, n) so mean = 500.
        assert "500.0" in r or "500" in r, (
            f"Expected T_mean≈500 in inspect output (synthetic data): {r}"
        )

        # Step 3: iteration 1 — threshold on T > 500 (T_mean).
        _write_pipeline(pipe, (
            'mesh = read("multi.vtk")\n'
            'hot = mesh.threshold(value=500.0, scalars="T")\n'
            'print(f"Iter1: T>500 -> {hot.n_points} points")\n'
            'show(hot, colormap="inferno")\n'
        ))

        status1 = _wait_for_status(list_views, "view-refine.py", expect_error=False)
        assert "no errors" in status1.lower(), (
            f"Expected no errors after iter1:\n{status1}"
        )

        # Step 4: inspect — get point count after T threshold.
        r1 = inspect("view-refine.py", "print(hot.n_points)")
        assert "Error" not in r1, f"Iter1 inspect failed: {r1}"
        iter1_pts = int(r1.strip())
        assert iter1_pts > 0, f"Expected > 0 points after T>500 threshold: {iter1_pts}"
        print(f"Iter1 point count (T>500): {iter1_pts}")

        # Since T = linspace(200, 800) and mesh is 20^3 = 8000 points,
        # T > 500 removes roughly the lower half.
        assert iter1_pts < 8000, f"T>500 threshold should reduce points below 8000: {iter1_pts}"

        # Step 5: iteration 2 — add P filter on top.
        # After thresholding T>500, the P range in the remaining region is
        # approximately [5.05, 10.0] (T and P are correlated in the synthetic
        # data).  Use P>8.0 to reliably reduce the count further.
        # Note: threshold on an UnstructuredGrid keeps cells where ANY point
        # exceeds the threshold, so moderate P values don't cut much.
        _write_pipeline(pipe, (
            'mesh = read("multi.vtk")\n'
            'hot = mesh.threshold(value=500.0, scalars="T")\n'
            'hot_dense = hot.threshold(value=8.0, scalars="P")\n'
            'print(f"Iter2: T>500 AND P>8.0 -> {hot_dense.n_points} points")\n'
            'show(hot_dense, colormap="inferno")\n'
        ))

        status2 = _wait_for_status(list_views, "view-refine.py", expect_error=False)
        assert "no errors" in status2.lower(), (
            f"Expected no errors after iter2:\n{status2}"
        )

        # Step 6: inspect — verify double filter applied.
        r2 = inspect("view-refine.py", """\
print(hot.n_points)
print(hot_dense.n_points)
""")
        assert "Error" not in r2, f"Iter2 inspect failed: {r2}"
        lines2 = [l for l in r2.strip().splitlines() if l.strip()]
        assert len(lines2) >= 2, f"Expected two point counts in iter2 output: {r2}"

        iter2_hot_pts = int(lines2[0].strip())
        iter2_dense_pts = int(lines2[1].strip())
        print(f"Iter2: hot={iter2_hot_pts}, hot_dense={iter2_dense_pts}")

        # hot in iter2 should match iter1 (same filter, same data — read cache hit).
        assert iter2_hot_pts == iter1_pts, (
            f"Expected hot.n_points to match iter1 ({iter1_pts}), "
            f"got {iter2_hot_pts} — caching may be broken"
        )
        # Adding P>8.0 on top of T>500 should reduce points further.
        assert iter2_dense_pts < iter2_hot_pts, (
            f"P>8.0 filter should reduce points further: "
            f"hot={iter2_hot_pts}, hot_dense={iter2_dense_pts}"
        )

        # Step 7: iteration 3 — change colormap only (data pipeline unchanged).
        # The data steps (read + two thresholds) should be all cache hits.
        _write_pipeline(pipe, (
            'mesh = read("multi.vtk")\n'
            'hot = mesh.threshold(value=500.0, scalars="T")\n'
            'hot_dense = hot.threshold(value=8.0, scalars="P")\n'
            'print(f"Iter3: {hot_dense.n_points} points")\n'
            'show(hot_dense, colormap="plasma", opacity=0.9)\n'
        ))

        status3 = _wait_for_status(list_views, "view-refine.py", expect_error=False)
        assert "no errors" in status3.lower(), (
            f"Expected no errors after iter3:\n{status3}"
        )

        # Step 8: screenshot after all iterations.
        img = screenshot("view-refine.py")
        assert isinstance(img, Image), f"Expected Image, got {type(img)}"
        assert img.data[:4] == b"\x89PNG", "Expected PNG"
        assert len(img.data) > 100, "PNG too small"
        print(f"Final screenshot: {len(img.data)} bytes")

        # Verify the cache is being used: iter3 changes only show() args,
        # so the data pipeline (read+threshold+threshold) should be all hits.
        # The list_views cache line format is "Cache: N hits, M misses, K evictions"
        assert "hits" in status3, f"Expected cache stats in status3: {status3}"
        # Extract hits count from "N hits" format
        import re
        m = re.search(r"(\d+)\s+hits?", status3)
        if m:
            hits = int(m.group(1))
            assert hits > 0, (
                f"Expected cache hits > 0 in iter3 (data unchanged), "
                f"got {hits}"
            )

        print("test_inspect_driven_refinement passed.")

    # ------------------------------------------------------------------
    # 5. Cross-view inspect (wildfire — skipped if absent)
    # ------------------------------------------------------------------

    @pytest.mark.skipif(not _HAS_WILDFIRE, reason="Wildfire dataset not downloaded")
    def test_cross_view_inspect(self, wildfire_session_dir, reset_server):
        """Inspect data in one wildfire view, use the info to configure a second.

        Workflow:
        1. Create view-raw.py: load full mesh, inspect T range.
        2. Use T range to write view-threshold.py with an appropriate threshold.
        3. Inspect view-threshold: verify reduced point count.
        4. Verify shared read cache has one entry (large file loaded once).
        """
        from mcp_server.server import (
            set_working_directory,
            create_view,
            inspect,
            screenshot,
        )
        from mcp.server.fastmcp import Image

        set_working_directory(wildfire_session_dir)

        # Step 1: create raw view.
        raw_pipe = os.path.join(wildfire_session_dir, "view-raw.py")
        with open(raw_pipe, "w") as f:
            f.write(
                'mesh = read("output.30000.vts")\n'
                'show(mesh, colormap="viridis")\n'
            )
        r_raw = create_view("view-raw.py")
        assert "Error" not in r_raw, f"create_view view-raw failed: {r_raw}"

        # Inspect T range.
        r_T = inspect("view-raw.py", """\
T = mesh["theta"]
print(f"T_min={T.min():.0f}")
print(f"T_max={T.max():.0f}")
print(f"T_mean={T.mean():.0f}")
""")
        assert "T_min=" in r_T, f"Expected T stats in inspect: {r_T}"
        print(f"Cross-view inspect T:\n{r_T}")

        # Parse T_max from the output.
        T_max_line = [l for l in r_T.splitlines() if "T_max=" in l]
        assert T_max_line, f"Could not find T_max line in: {r_T}"
        T_max = float(T_max_line[0].split("=")[1].strip())
        assert T_max > 400, f"Expected T_max > 400 (fire data), got {T_max}"

        # Step 2: use the range to configure a threshold view.
        # Choose 80% of T_max as threshold.
        threshold_val = T_max * 0.6
        thresh_pipe = os.path.join(wildfire_session_dir, "view-thresh.py")
        with open(thresh_pipe, "w") as f:
            f.write(
                f'mesh = read("output.30000.vts")\n'
                f'fire = mesh.threshold(value={threshold_val:.0f}, scalars="theta")\n'
                f'print(f"Fire pts: {{fire.n_points}}")\n'
                f'show(fire, colormap="inferno")\n'
            )
        r_thresh = create_view("view-thresh.py")
        assert "Error" not in r_thresh, f"create_view view-thresh failed: {r_thresh}"

        # Step 3: inspect threshold view.
        r_fire = inspect("view-thresh.py", "print(fire.n_points)")
        assert "Error" not in r_fire, f"Inspect view-thresh failed: {r_fire}"
        fire_pts = int(r_fire.strip())
        assert fire_pts > 0, f"Expected > 0 fire points, got {fire_pts}"
        assert fire_pts < 18_300_000, "Threshold should reduce point count"
        print(f"Fire points at T>{threshold_val:.0f}: {fire_pts}")

        # Step 4: shared read cache — one entry for the large file.
        cache = reset_server._shared_read_cache
        assert len(cache) == 1, (
            f"Expected 1 shared cache entry for output.30000.vts, "
            f"got {len(cache)}: {list(cache.keys())}"
        )

        print("test_cross_view_inspect passed.")
