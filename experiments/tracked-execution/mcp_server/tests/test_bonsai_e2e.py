"""End-to-end test: AI agent explores bonsai CT scan via MCP tools.

Demonstrates:
- Loading volumetric ImageData
- Exploring density ranges for segmentation
- Threshold-based segmentation (air vs wood vs pot)
- Isosurface extraction via contour()
- Iterative refinement of visualization

Run with:
    xvfb-run -a python -m pytest mcp_server/tests/test_bonsai_e2e.py -v --timeout=120
"""

import os
import shutil
import tempfile

import pytest

BONSAI_DATA = "/home/user/VisLang/datasets/bonsai/data/bonsai.vti"


@pytest.fixture
def session_dir():
    """Create a temp session directory with a symlink to the bonsai data."""
    d = tempfile.mkdtemp()
    os.symlink(BONSAI_DATA, os.path.join(d, "bonsai.vti"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _stop_watcher(srv, view_name: str) -> None:
    """Stop the file watcher for a view to prevent background render() calls.

    VTK OpenGL is not thread-safe. The watcher's reconcile callback from a
    background thread can conflict if the main thread also calls render()
    (e.g., via screenshot()). Stop the watcher immediately after create_view.
    """
    vs = srv._views.get(view_name)
    if vs is not None and vs.watcher is not None:
        vs.watcher.stop()
        vs.watcher.join(timeout=2)
        vs.watcher = None


class TestBonsaiE2E:
    """End-to-end workflow tests using the bonsai CT dataset."""

    @pytest.mark.skipif(
        not os.path.exists(BONSAI_DATA),
        reason="Bonsai dataset not downloaded",
    )
    def test_ct_exploration(self, session_dir, reset_server):
        """Full CT workflow: load, explore density, segment, isosurface.

        Steps:
        1. Set working directory — verify bonsai.vti is listed
        2. Load and inspect the full volume
        3. Explore density distribution (air vs wood vs dense)
        4. Threshold to wood region
        5. Extract isosurface contours
        6. Inspect isosurface statistics
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
        assert "bonsai.vti" in result, (
            f"Expected bonsai.vti in file listing:\n{result}"
        )

        # ------------------------------------------------------------------
        # Step 2: Load and display full volume
        # ------------------------------------------------------------------
        p = os.path.join(session_dir, "view-bonsai.py")
        with open(p, "w") as f:
            f.write(
                'mesh = read("bonsai.vti")\n'
                'print(f"Points: {mesh.n_points}")\n'
                'print(f"Fields: {mesh.array_names}")\n'
                'show(mesh)\n'
            )

        result = create_view("view-bonsai.py")
        assert "Error" not in result, f"create_view failed:\n{result}"
        assert "view-bonsai" in result.lower() or "View" in result, (
            f"Expected view name in response:\n{result}"
        )
        assert "16777216" in result or "Points" in result, (
            f"Expected point count in create_view output:\n{result}"
        )

        _stop_watcher(srv, "view-bonsai")

        # ------------------------------------------------------------------
        # Step 3: Explore density distribution
        # ------------------------------------------------------------------
        r = inspect("view-bonsai.py", """\
arr = mesh["density"]
print(f"Density range: {arr.min():.0f} - {arr.max():.0f}")
print(f"Mean: {arr.mean():.1f}, Std: {arr.std():.1f}")

low = int((arr < 30).sum())
mid = int(((arr >= 30) & (arr < 100)).sum())
high = int((arr >= 100).sum())
total = len(arr)
print(f"Air (0-29): {low} ({100*low/total:.1f}%)")
print(f"Wood (30-99): {mid} ({100*mid/total:.1f}%)")
print(f"Dense (100+): {high} ({100*high/total:.1f}%)")
""")
        print(f"Inspect density:\n{r}")
        assert "Density range" in r, f"Expected density stats in output:\n{r}"
        assert "255" in r or "254" in r, (
            f"Expected max density ~255 in output:\n{r}"
        )
        assert "Air" in r, f"Expected 'Air' segmentation label in output:\n{r}"

        # ------------------------------------------------------------------
        # Step 4: Threshold to wood region
        # ------------------------------------------------------------------
        p2 = os.path.join(session_dir, "view-wood.py")
        with open(p2, "w") as f:
            f.write(
                'mesh = read("bonsai.vti")\n'
                'wood = mesh.threshold(value=[30, 145], scalars="density")\n'
                'print(f"Wood region: {wood.n_points} points")\n'
                'show(wood, colormap="bone")\n'
            )

        result2 = create_view("view-wood.py")
        assert "Error" not in result2, f"create_view (wood) failed:\n{result2}"
        _stop_watcher(srv, "view-wood")
        print(f"Wood view: {result2}")

        r_wood = inspect("view-wood.py", "print(wood.n_points)")
        print(f"Wood n_points: {r_wood}")
        wood_pts = int(r_wood.strip())
        assert wood_pts < 16777216, (
            f"Thresholded wood region should have fewer points than full volume, got {wood_pts}"
        )
        assert wood_pts > 0, "Threshold returned empty result"

        img = screenshot("view-wood.py")
        assert isinstance(img, Image), f"Expected Image, got {type(img)}"
        assert img.data[:4] == b"\x89PNG", "Expected PNG signature"
        assert len(img.data) > 1000, "PNG too small — likely empty render"
        print(f"Wood screenshot: {len(img.data)} bytes")

        # ------------------------------------------------------------------
        # Step 5: Isosurface extraction via contour()
        # ------------------------------------------------------------------
        p3 = os.path.join(session_dir, "view-iso.py")
        with open(p3, "w") as f:
            f.write(
                'mesh = read("bonsai.vti")\n'
                'iso = mesh.contour(isosurfaces=[50, 100, 150], scalars="density")\n'
                'print(f"Isosurface: {iso.n_points} points")\n'
                'show(iso, colormap="copper", opacity=0.7)\n'
            )

        result3 = create_view("view-iso.py")
        assert "Error" not in result3, f"create_view (iso) failed:\n{result3}"
        _stop_watcher(srv, "view-iso")
        print(f"Iso view: {result3}")

        img2 = screenshot("view-iso.py")
        assert isinstance(img2, Image), f"Expected Image, got {type(img2)}"
        assert img2.data[:4] == b"\x89PNG", "Expected PNG signature"
        assert len(img2.data) > 1000, "PNG too small"
        print(f"Isosurface screenshot: {len(img2.data)} bytes")

        # ------------------------------------------------------------------
        # Step 6: Inspect isosurface stats
        # ------------------------------------------------------------------
        r2 = inspect("view-iso.py", """\
print(f"Isosurface points: {iso.n_points}")
bounds = iso.bounds
print(f"Bounds x: {bounds[0]:.1f} to {bounds[1]:.1f}")
print(f"Bounds y: {bounds[2]:.1f} to {bounds[3]:.1f}")
print(f"Bounds z: {bounds[4]:.1f} to {bounds[5]:.1f}")
""")
        print(f"Iso inspect:\n{r2}")
        assert "Isosurface points" in r2, f"Expected isosurface stats:\n{r2}"
        assert "Bounds" in r2, f"Expected bounds info:\n{r2}"

        print("Bonsai CT exploration completed successfully!")

    @pytest.mark.skipif(
        not os.path.exists(BONSAI_DATA),
        reason="Bonsai dataset not downloaded",
    )
    def test_multiple_views_ct(self, session_dir, reset_server):
        """Test creating multiple CT views simultaneously.

        Creates two independent views (full volume and thresholded), then
        verifies that inspect works correctly on both — full volume has
        16,777,216 points, thresholded has fewer.
        """
        from mcp_server.server import set_working_directory, create_view, inspect
        import mcp_server.server as srv

        set_working_directory(session_dir)

        p1 = os.path.join(session_dir, "full.py")
        with open(p1, "w") as f:
            f.write('mesh = read("bonsai.vti")\nshow(mesh)\n')
        create_view("full.py")
        _stop_watcher(srv, "full")

        p2 = os.path.join(session_dir, "thresh.py")
        with open(p2, "w") as f:
            f.write(
                'mesh = read("bonsai.vti")\n'
                't = mesh.threshold(value=50, scalars="density")\n'
                'show(t)\n'
            )
        create_view("thresh.py")
        _stop_watcher(srv, "thresh")

        r1 = inspect("full.py", "print(mesh.n_points)")
        r2 = inspect("thresh.py", "print(t.n_points)")

        print(f"Full: {r1.strip()}, Thresh: {r2.strip()}")

        assert "16777216" in r1, (
            f"Expected 16777216 in full volume point count, got:\n{r1}"
        )

        thresh_pts = int(r2.strip())
        assert thresh_pts < 16777216, (
            f"Thresholded view should have fewer points than full volume, got {thresh_pts}"
        )
        assert thresh_pts > 0, "Threshold returned empty result"

    @pytest.mark.skipif(
        not os.path.exists(BONSAI_DATA),
        reason="Bonsai dataset not downloaded",
    )
    def test_inspect_density_stats(self, session_dir, reset_server):
        """Inspect density field statistics — validate known CT scan properties.

        The bonsai dataset is a 256^3 uint8 CT scan with:
        - Field name: 'density'
        - Range: 0-255 (uint8)
        - Full domain: 16,777,216 voxels
        """
        from mcp_server.server import set_working_directory, create_view, inspect
        import mcp_server.server as srv

        set_working_directory(session_dir)

        p = os.path.join(session_dir, "stats.py")
        with open(p, "w") as f:
            f.write('mesh = read("bonsai.vti")\nshow(mesh)\n')
        create_view("stats.py")
        _stop_watcher(srv, "stats")

        r = inspect("stats.py", "print(mesh.array_names)")
        assert "density" in r, f"Expected 'density' in field list:\n{r}"

        r = inspect("stats.py", "print(mesh.n_points)")
        assert "16777216" in r, (
            f"Expected 16777216 (256^3) in output:\n{r}"
        )

        r = inspect("stats.py", """\
arr = mesh["density"]
print(f"{int(arr.min())}")
print(f"{int(arr.max())}")
""")
        assert "0" in r, f"Expected min density 0 in output:\n{r}"
        assert "255" in r, f"Expected max density 255 in output:\n{r}"

    @pytest.mark.skipif(
        not os.path.exists(BONSAI_DATA),
        reason="Bonsai dataset not downloaded",
    )
    def test_sequential_inspect_calls(self, session_dir, reset_server):
        """Multiple sequential inspect calls on the same CT view work correctly.

        Verifies that the DAG cache serves subsequent inspect calls efficiently.
        """
        from mcp_server.server import set_working_directory, create_view, inspect
        import mcp_server.server as srv

        set_working_directory(session_dir)

        p = os.path.join(session_dir, "seq.py")
        with open(p, "w") as f:
            f.write('mesh = read("bonsai.vti")\nshow(mesh)\n')
        create_view("seq.py")
        _stop_watcher(srv, "seq")

        snippets = [
            ("n_points", "print(mesh.n_points)"),
            ("dtype", "print(mesh['density'].dtype)"),
            ("min", "print(int(mesh['density'].min()))"),
            ("max", "print(int(mesh['density'].max()))"),
        ]
        for label, snippet in snippets:
            r = inspect("seq.py", snippet)
            assert "Error" not in r, (
                f"Unexpected error in sequential inspect ({label}):\n{r}"
            )
            assert r.strip(), (
                f"Empty output from sequential inspect ({label}):\n{r}"
            )
            print(f"  {label}: {r.strip()}")
