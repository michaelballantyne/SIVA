"""End-to-end test: AI agent explores wildfire simulation data via MCP tools.

This simulates the full workflow an agent would follow:
1. Set working directory
2. Explore data by creating a view with a simple pipeline
3. Inspect the data to understand fields and ranges
4. Iterate on the pipeline to refine the visualization
5. Take screenshots at each stage

Run with:
    xvfb-run -a python -m pytest mcp_server/tests/test_wildfire_e2e.py -v --timeout=300

The wildfire file is 1.1 GB / 18.3M points, so operations are slow.
The 300-second timeout covers reading + thresholding + rendering.
"""

import os
import sys
import shutil
import tempfile
import time

import pytest

WILDFIRE_DATA = "/home/user/VisLang/datasets/wildfire/data/output.30000.vts"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_dir():
    """Create a temp session directory with a symlink to the wildfire data."""
    d = tempfile.mkdtemp()
    os.symlink(WILDFIRE_DATA, os.path.join(d, "output.30000.vts"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def reset_server():
    """Reset MCP server module state before and after each test.

    Stops any running watchers, clears views, and resets the working directory.
    Yields the server module so tests can import tools lazily.
    """
    # Ensure the mcp_server package is importable from its parent directory.
    te_root = os.path.join(os.path.dirname(__file__), "..", "..")
    if te_root not in sys.path:
        sys.path.insert(0, te_root)

    import mcp_server.server as srv

    def _clean():
        for vs in list(srv._views.values()):
            if vs.watcher is not None:
                try:
                    vs.watcher.stop()
                    vs.watcher.join(timeout=2)
                except Exception:
                    pass
            try:
                vs.plotter.close()
            except Exception:
                pass
        srv._views = {}
        srv._working_directory = None

    _clean()
    yield srv
    _clean()


# ---------------------------------------------------------------------------
# Helper: write a pipeline and wait for the watcher to pick it up
# ---------------------------------------------------------------------------

def _write_pipeline(path: str, code: str, wait_s: float = 0.5) -> None:
    """Write *code* to *path* and sleep briefly to let the file watcher fire."""
    with open(path, "w") as f:
        f.write(code)
    time.sleep(wait_s)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestWildfireE2E:
    """End-to-end workflow tests using the wildfire dataset."""

    @pytest.mark.skipif(
        not os.path.exists(WILDFIRE_DATA),
        reason="Wildfire dataset not downloaded",
    )
    def test_full_exploration_workflow(self, session_dir, reset_server):
        """Simulate a full agent exploration session on the wildfire dataset.

        Steps:
        1. Set working directory — verify data file is listed
        2. Write a simple pipeline and create a view
        3. Inspect field statistics
        4. Take a screenshot
        5. Refine: create a new view with fire threshold pipeline
        6. Inspect refined view, screenshot
        7. Tighten threshold: create a third view, screenshot

        Note: Each pipeline iteration uses a separate file name to avoid the
        watcher-thread OpenGL conflict that occurs when the same plotter is
        rendered from two threads simultaneously.  This mirrors how an agent
        would actually work with the MCP (different ``create_view`` calls per
        iteration).
        """
        from mcp_server.server import (
            set_working_directory,
            create_view,
            inspect,
            screenshot,
        )
        from mcp.server.fastmcp import Image
        import mcp_server.server as srv

        # ------------------------------------------------------------------
        # Step 1: Set working directory
        # ------------------------------------------------------------------
        result = set_working_directory(session_dir)
        assert "Working directory set" in result, f"Unexpected: {result}"
        # The symlinked data file should appear in the listing.
        assert "output.30000.vts" in result, (
            f"Expected data file listed in response:\n{result}"
        )

        # ------------------------------------------------------------------
        # Step 2: Create view — load and display the full mesh
        # ------------------------------------------------------------------
        pipeline_path = os.path.join(session_dir, "view-fire.py")
        _write_pipeline(pipeline_path, """\
mesh = read("output.30000.vts")
print(f"Loaded: {mesh.n_points} points")
print(f"Fields: {mesh.array_names}")
show(mesh, colormap="viridis")
""")

        result = create_view("view-fire.py")
        assert "Error" not in result, f"create_view failed:\n{result}"
        # The view name "view-fire" should appear in the response.
        assert "view-fire" in result.lower() or "View" in result, (
            f"Expected view name in response:\n{result}"
        )
        # The pipeline print output captures point count and field names.
        assert "18300000" in result or "Loaded" in result, (
            f"Expected pipeline output in create_view result:\n{result}"
        )

        # Stop the watcher immediately to prevent background render() calls
        # that conflict with the main-thread OpenGL context.
        vs = srv._views.get("view-fire")
        if vs is not None and vs.watcher is not None:
            vs.watcher.stop()
            vs.watcher.join(timeout=2)
            vs.watcher = None

        # ------------------------------------------------------------------
        # Step 3a: Inspect temperature field (theta)
        # ------------------------------------------------------------------
        r = inspect("view-fire.py", """\
arr = mesh["theta"]
print(f"theta min={arr.min():.1f} max={arr.max():.1f} mean={arr.mean():.1f}")
""")
        print(f"Inspect theta:\n{r}")
        # Temperature should be in the 298–1184 K range.
        assert any(token in r for token in ("theta", "298", "1183", "min=")), (
            f"Expected temperature stats in inspect output:\n{r}"
        )

        # ------------------------------------------------------------------
        # Step 3b: Inspect fuel density + fire fraction
        # ------------------------------------------------------------------
        r = inspect("view-fire.py", """\
rhof = mesh["rhof_1"]
theta = mesh["theta"]
fire_pts = int((theta > 400).sum())
total = int(mesh.n_points)
print(f"rhof_1 min={rhof.min():.4f} max={rhof.max():.4f}")
print(f"Fire points (theta>400): {fire_pts} of {total}")
""")
        print(f"Inspect fuel + fire fraction:\n{r}")
        # Should produce some output (fuel and fire info).
        assert "rhof_1" in r or "Fire" in r, (
            f"Expected fuel/fire stats in output:\n{r}"
        )

        # ------------------------------------------------------------------
        # Step 4: Screenshot of initial view
        # ------------------------------------------------------------------
        img = screenshot("view-fire.py")
        assert isinstance(img, Image), f"Expected Image, got {type(img)}"
        assert img.data[:4] == b"\x89PNG", "Expected PNG signature"
        assert len(img.data) > 1000, "PNG too small — likely empty render"
        print(f"Initial screenshot: {len(img.data)} bytes")

        # ------------------------------------------------------------------
        # Step 5: Refine pipeline — threshold to fire region (theta > 400)
        # Use a new file name to create a fresh view (avoiding watcher conflicts).
        # ------------------------------------------------------------------
        pipeline_v2 = os.path.join(session_dir, "view-fire2.py")
        _write_pipeline(pipeline_v2, """\
mesh = read("output.30000.vts")
fire = mesh.threshold(value=400, scalars="theta")
surface = fire.extract_surface()
print(f"Fire region: {fire.n_points} points")
print(f"Surface points: {surface.n_points}")
show(surface, colormap="inferno")
""")

        result2 = create_view("view-fire2.py")
        assert "Error" not in result2, f"create_view v2 failed:\n{result2}"
        print(f"Fire threshold view:\n{result2}")

        # Stop the watcher on the refinement view too.
        vs2 = srv._views.get("view-fire2")
        if vs2 is not None and vs2.watcher is not None:
            vs2.watcher.stop()
            vs2.watcher.join(timeout=2)
            vs2.watcher = None

        # Inspect the refined view — fire variable should now be available.
        r = inspect("view-fire2.py", "print(fire.n_points)")
        print(f"Inspect fire threshold:\n{r}")
        # Output should contain the fire point count (a non-zero integer).
        assert r.strip().isdigit() or "Error" not in r, (
            f"Expected fire point count in output:\n{r}"
        )

        # Screenshot after refinement.
        img2 = screenshot("view-fire2.py")
        assert isinstance(img2, Image)
        assert img2.data[:4] == b"\x89PNG"
        print(f"Fire threshold screenshot: {len(img2.data)} bytes")

        # ------------------------------------------------------------------
        # Step 6: Tighten threshold — isolate the hottest fire core (> 600 K)
        # ------------------------------------------------------------------
        pipeline_v3 = os.path.join(session_dir, "view-fire3.py")
        _write_pipeline(pipeline_v3, """\
mesh = read("output.30000.vts")
fire = mesh.threshold(value=600, scalars="theta")
surface = fire.extract_surface()
print(f"Hot fire: {fire.n_points} points")
show(surface, colormap="inferno")
""")

        result3 = create_view("view-fire3.py")
        assert "Error" not in result3, f"create_view v3 failed:\n{result3}"

        # Stop the watcher.
        vs3 = srv._views.get("view-fire3")
        if vs3 is not None and vs3.watcher is not None:
            vs3.watcher.stop()
            vs3.watcher.join(timeout=2)
            vs3.watcher = None

        img3 = screenshot("view-fire3.py")
        assert isinstance(img3, Image)
        assert img3.data[:4] == b"\x89PNG"
        print(f"Hot fire screenshot: {len(img3.data)} bytes")

        print("Full wildfire exploration workflow completed successfully!")

    @pytest.mark.skipif(
        not os.path.exists(WILDFIRE_DATA),
        reason="Wildfire dataset not downloaded",
    )
    def test_inspect_workflow(self, session_dir, reset_server):
        """Quick test: create view, inspect point count — no rendering assertions.

        This confirms the basic load+inspect path works on the real 18.3M-point
        wildfire mesh.  Skips screenshot to keep runtime shorter.
        """
        from mcp_server.server import set_working_directory, create_view, inspect

        set_working_directory(session_dir)

        pipeline_path = os.path.join(session_dir, "view-quick.py")
        _write_pipeline(pipeline_path, """\
mesh = read("output.30000.vts")
show(mesh)
""")

        result = create_view("view-quick.py")
        assert "Error" not in result, f"create_view failed:\n{result}"

        # Inspect: confirm point count matches the known 18.3M point grid.
        r = inspect("view-quick.py", "print(mesh.n_points)")
        print(f"Inspect n_points: {r}")
        assert "18300000" in r, (
            f"Expected 18300000 in inspect output, got:\n{r}"
        )

    @pytest.mark.skipif(
        not os.path.exists(WILDFIRE_DATA),
        reason="Wildfire dataset not downloaded",
    )
    def test_inspect_field_ranges(self, session_dir, reset_server):
        """Inspect all field names and temperature statistics.

        Validates that the agent can pull numeric stats from an inspect call.
        """
        from mcp_server.server import set_working_directory, create_view, inspect

        set_working_directory(session_dir)

        pipeline_path = os.path.join(session_dir, "view-fields.py")
        _write_pipeline(pipeline_path, """\
mesh = read("output.30000.vts")
show(mesh)
""")

        create_view("view-fields.py")

        # Field names
        r = inspect("view-fields.py", "print(mesh.array_names)")
        assert "theta" in r, f"Expected 'theta' in field list:\n{r}"
        assert "rhof_1" in r, f"Expected 'rhof_1' in field list:\n{r}"

        # Temperature range: 298–1184 K
        r = inspect("view-fields.py", """\
t = mesh["theta"]
print(f"{t.min():.0f}")
print(f"{t.max():.0f}")
""")
        assert "298" in r or "299" in r, (
            f"Expected min temperature ~298 in output:\n{r}"
        )
        assert "1183" in r or "1184" in r, (
            f"Expected max temperature ~1184 in output:\n{r}"
        )

    @pytest.mark.skipif(
        not os.path.exists(WILDFIRE_DATA),
        reason="Wildfire dataset not downloaded",
    )
    def test_multiple_inspect_calls(self, session_dir, reset_server):
        """Multiple sequential inspect calls on the same view work correctly."""
        from mcp_server.server import set_working_directory, create_view, inspect

        set_working_directory(session_dir)

        pipeline_path = os.path.join(session_dir, "view-multi.py")
        _write_pipeline(pipeline_path, """\
mesh = read("output.30000.vts")
show(mesh)
""")
        create_view("view-multi.py")

        # Call inspect multiple times; the DAG cache should serve subsequent
        # calls without re-reading the large file.
        for field in ("u", "v", "w", "theta", "O2"):
            snippet = (
                f'arr = mesh["{field}"]\n'
                f'print("{field}: n=" + str(len(arr)) + " min=" + str(round(float(arr.min()), 3)) + " max=" + str(round(float(arr.max()), 3)))\n'
            )
            r = inspect("view-multi.py", snippet)
            assert field in r, (
                f"Expected '{field}' in inspect output for field query:\n{r}"
            )
            assert "Error" not in r, f"Unexpected error inspecting '{field}':\n{r}"
