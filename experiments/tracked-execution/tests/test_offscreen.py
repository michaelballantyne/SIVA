"""Tests for SceneReconciler and Session with real offscreen PyVista rendering.

These tests require an X server for VTK's OpenGL context.  Run with:

    xvfb-run -a python3 -m pytest tests/test_offscreen.py -v

All tests use pv.Plotter(off_screen=True) so no window is created, but VTK
still needs an X server for context creation — hence the xvfb-run requirement.
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

from tracked_execution import DAG, execute_pipeline, SceneReconciler, Session


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_vtk_file(dims=(20, 20, 20), seed=42):
    """Create a synthetic ImageData mesh, save to a temp .vtk file, return path."""
    mesh = pv.ImageData(dimensions=dims)
    mesh["Temperature"] = np.random.RandomState(seed).rand(mesh.n_points) * 1000
    path = tempfile.mktemp(suffix=".vtk")
    mesh.save(path)
    return path


def png_is_nonempty(path, min_size=1000):
    """Return True if file exists and has more than min_size bytes."""
    return os.path.exists(path) and os.path.getsize(path) > min_size


# ---------------------------------------------------------------------------
# TestOffscreenRendering
# ---------------------------------------------------------------------------

class TestOffscreenRendering:
    """Full-loop tests that do real offscreen rendering via PyVista Plotter."""

    def setup_method(self):
        self.plotter = pv.Plotter(off_screen=True)
        self.data_path = make_vtk_file()
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        try:
            self.plotter.close()
        except Exception:
            pass
        try:
            os.unlink(self.data_path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 1. execute_pipeline → actors → plotter → screenshot
    # ------------------------------------------------------------------

    def test_execute_and_screenshot(self):
        """Full loop: execute pipeline -> apply actors -> screenshot."""
        dag = DAG()
        code = f"""
mesh = read("{self.data_path}")
hot = mesh.threshold(value=500, scalars="Temperature")
surface = hot.extract_surface()
show(surface, colormap="inferno")
"""
        result = execute_pipeline(code, dag)
        assert len(result.actors) >= 1

        # Apply actors to plotter using the (mesh_proxy, kwargs) tuple format
        for mesh_proxy, kwargs in result.actors:
            from tracked_execution.dispatch import _unwrap
            real_mesh = _unwrap(mesh_proxy)
            self.plotter.add_mesh(real_mesh, **kwargs)

        img_path = os.path.join(self.tmpdir, "shot1.png")
        self.plotter.screenshot(img_path)
        assert os.path.exists(img_path)
        assert os.path.getsize(img_path) > 0

    # ------------------------------------------------------------------
    # 2. SceneReconciler.reconcile() with a real plotter — basic add
    # ------------------------------------------------------------------

    def test_reconciler_with_plotter_adds_actor(self):
        """SceneReconciler applies a new actor to a real plotter (added >= 1)."""
        dag = DAG()
        reconciler = SceneReconciler(plotter=self.plotter)

        code = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="viridis")'
        r1 = execute_pipeline(code, dag)
        rec1 = reconciler.reconcile(r1.actors)

        assert rec1.added >= 1
        assert rec1.updated == 0
        assert rec1.removed == 0

        # Verify screenshot is producible after reconcile
        img1 = os.path.join(self.tmpdir, "r1.png")
        self.plotter.screenshot(img1)
        assert png_is_nonempty(img1)

    # ------------------------------------------------------------------
    # 3. Two successive reconciles with colormap change → different images
    # ------------------------------------------------------------------

    def test_reconciler_colormap_change_differs(self):
        """Changing colormap between runs updates actor; screenshots differ."""
        dag = DAG()
        reconciler = SceneReconciler(plotter=self.plotter)

        # Run 1 — viridis
        code1 = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="viridis", name="vol")'
        r1 = execute_pipeline(code1, dag)
        rec1 = reconciler.reconcile(r1.actors)
        assert rec1.added >= 1

        img1 = os.path.join(self.tmpdir, "rec_r1.png")
        self.plotter.screenshot(img1)

        # Run 2 — plasma (same mesh, different colormap)
        code2 = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="plasma", name="vol")'
        r2 = execute_pipeline(code2, dag)
        rec2 = reconciler.reconcile(r2.actors)
        assert rec2.updated >= 1
        assert rec2.added == 0
        assert rec2.removed == 0

        img2 = os.path.join(self.tmpdir, "rec_r2.png")
        self.plotter.screenshot(img2)

        # Different colormaps should produce different images
        with open(img1, "rb") as f1, open(img2, "rb") as f2:
            assert f1.read() != f2.read(), "viridis and plasma renders should differ"

    # ------------------------------------------------------------------
    # 4. Actor removal: 2 actors → 1 actor
    # ------------------------------------------------------------------

    def test_reconciler_actor_removal(self):
        """Reconciler removes actor when it disappears from the pipeline."""
        dag = DAG()
        reconciler = SceneReconciler(plotter=self.plotter)

        code1 = f"""
mesh = read("{self.data_path}")
hot = mesh.threshold(value=500, scalars="Temperature")
cold = mesh.threshold(value=500, scalars="Temperature", invert=True)
show(hot, colormap="inferno", name="hot")
show(cold, colormap="cool", name="cold")
"""
        r1 = execute_pipeline(code1, dag)
        rec1 = reconciler.reconcile(r1.actors)
        assert rec1.added == 2
        assert rec1.removed == 0

        # Second pipeline: only "hot" remains — "cold" should be removed
        code2 = f"""
mesh = read("{self.data_path}")
hot = mesh.threshold(value=500, scalars="Temperature")
show(hot, colormap="inferno", name="hot")
"""
        r2 = execute_pipeline(code2, dag)
        rec2 = reconciler.reconcile(r2.actors)
        assert rec2.removed >= 1
        assert rec2.added == 0

    # ------------------------------------------------------------------
    # 5. Unchanged actors are not re-added
    # ------------------------------------------------------------------

    def test_reconciler_unchanged_actors_not_re_added(self):
        """Same actor reconciled twice → second pass is all unchanged."""
        dag = DAG()
        reconciler = SceneReconciler(plotter=self.plotter)

        code = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="viridis", name="vol")'
        r1 = execute_pipeline(code, dag)
        rec1 = reconciler.reconcile(r1.actors)
        assert rec1.added == 1

        # Identical second run
        r2 = execute_pipeline(code, dag)
        rec2 = reconciler.reconcile(r2.actors)
        assert rec2.unchanged == 1
        assert rec2.added == 0
        assert rec2.updated == 0
        assert rec2.removed == 0

    # ------------------------------------------------------------------
    # 6. Session.screenshot() produces a real image
    # ------------------------------------------------------------------

    def test_session_screenshot(self):
        """Session.screenshot() saves a non-trivial image file."""
        dag = DAG()
        session = Session(dag=dag, plotter=self.plotter)

        code = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="viridis")'
        session.execute(code)

        img_path = os.path.join(self.tmpdir, "session_shot.png")
        session.screenshot(img_path)
        assert png_is_nonempty(img_path), (
            f"Expected a non-trivial screenshot at {img_path}, "
            f"got size={os.path.getsize(img_path) if os.path.exists(img_path) else 'missing'}"
        )

    # ------------------------------------------------------------------
    # 7. Session caching + re-render: second execute is a cache hit
    # ------------------------------------------------------------------

    def test_session_caching_with_render(self):
        """Second execute() with same code has cache hits and produces same image."""
        dag = DAG()
        session = Session(dag=dag, plotter=self.plotter)

        code = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="viridis")'

        # First run: cold cache
        session.execute(code)
        img1 = os.path.join(self.tmpdir, "cache_r1.png")
        session.screenshot(img1)

        # Second run: all hits
        result2 = session.execute(code)
        assert result2.stats["hits"] > 0
        assert result2.stats["misses"] == 0

        img2 = os.path.join(self.tmpdir, "cache_r2.png")
        session.screenshot(img2)

        # Same data + same colormap → same render
        with open(img1, "rb") as f1, open(img2, "rb") as f2:
            assert f1.read() == f2.read(), "Identical pipeline should produce identical screenshots"

    # ------------------------------------------------------------------
    # 8. Reconciler handles the tuple format from execute_pipeline directly
    # ------------------------------------------------------------------

    def test_reconciler_tuple_format_with_plotter(self):
        """Reconciler accepts (mesh, kwargs) tuples (the execute_pipeline output format)."""
        dag = DAG()
        reconciler = SceneReconciler(plotter=self.plotter)

        code = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="viridis")'
        result = execute_pipeline(code, dag)

        # result.actors is already list of (mesh_proxy, kwargs) tuples
        rec = reconciler.reconcile(result.actors)
        assert rec.added >= 1

        img = os.path.join(self.tmpdir, "tuple_fmt.png")
        self.plotter.screenshot(img)
        assert png_is_nonempty(img)

    # ------------------------------------------------------------------
    # 9. Opacity change is applied in-place (no remove/add, no flicker)
    # ------------------------------------------------------------------

    def test_opacity_change_in_place(self):
        """Same mesh, opacity changed: actor updated in-place via GetProperty().

        Verifies:
        - updated_property == 1 (not updated=1), so no remove/re-add happened
        - The live actor's GetProperty().GetOpacity() reflects the new value
        - Screenshots differ (opacity change is visually applied)

        Uses execute_pipeline so mesh hashes are stable TrackedProxy hashes
        (not raw-mesh stable_hash which varies per call).
        """
        dag = DAG()
        reconciler = SceneReconciler(plotter=self.plotter)

        # Run 1: opacity=1.0 (fully opaque)
        code1 = f'mesh = read("{self.data_path}")\nshow(mesh, opacity=1.0, name="vol")'
        r1_exec = execute_pipeline(code1, dag)
        r1 = reconciler.reconcile(r1_exec.actors)
        assert r1.added == 1

        # Retrieve the live actor stored by the reconciler
        actor_after_add = reconciler._previous["vol"].actor
        assert actor_after_add is not None, "Actor should be stored in reconciler state"
        assert abs(actor_after_add.GetProperty().GetOpacity() - 1.0) < 1e-6

        img1 = os.path.join(self.tmpdir, "opacity_1.0.png")
        self.plotter.screenshot(img1)

        # Run 2: same mesh file, only opacity changed → in-place update
        # execute_pipeline with same DAG returns the cached mesh proxy (same hash)
        code2 = f'mesh = read("{self.data_path}")\nshow(mesh, opacity=0.2, name="vol")'
        r2_exec = execute_pipeline(code2, dag)
        r2 = reconciler.reconcile(r2_exec.actors)

        assert r2.updated_property == 1, (
            f"Expected updated_property=1 for opacity-only change, got {r2}"
        )
        assert r2.updated == 0, "No full remove/re-add should happen for opacity-only change"
        assert r2.added == 0
        assert r2.removed == 0
        assert r2.unchanged == 0

        # The same actor object should still be in the reconciler state (in-place update)
        actor_after_update = reconciler._previous["vol"].actor
        assert actor_after_update is actor_after_add, (
            "In-place update must keep the same actor object — no remove/re-add"
        )

        # VTK property should now reflect the new opacity
        assert abs(actor_after_update.GetProperty().GetOpacity() - 0.2) < 1e-6, (
            f"Expected opacity=0.2, got {actor_after_update.GetProperty().GetOpacity()}"
        )

        # Trigger a re-render so the opacity change is flushed to the frame buffer
        self.plotter.render()

        img2 = os.path.join(self.tmpdir, "opacity_0.2.png")
        self.plotter.screenshot(img2)

        # Different opacity should produce visually different renders
        with open(img1, "rb") as f1, open(img2, "rb") as f2:
            assert f1.read() != f2.read(), (
                "opacity=1.0 and opacity=0.2 should produce different screenshots"
            )

    # ------------------------------------------------------------------
    # 10. Colormap change triggers full remove+re-add (not in-place)
    # ------------------------------------------------------------------

    def test_colormap_change_full_update(self):
        """Same mesh, colormap changed: actor is removed and re-added.

        Verifies:
        - updated == 1 (full mapper rebuild, not an in-place property update)
        - updated_property == 0
        - Screenshots differ (colormap change is visually applied)

        Uses execute_pipeline so mesh hashes are stable TrackedProxy hashes.
        """
        dag = DAG()
        reconciler = SceneReconciler(plotter=self.plotter)

        # Run 1: viridis colormap
        code1 = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="viridis", name="vol")'
        r1_exec = execute_pipeline(code1, dag)
        r1 = reconciler.reconcile(r1_exec.actors)
        assert r1.added == 1

        img1 = os.path.join(self.tmpdir, "cmap_viridis.png")
        self.plotter.screenshot(img1)

        # Run 2: same mesh, different colormap → full remove+re-add
        code2 = f'mesh = read("{self.data_path}")\nshow(mesh, colormap="plasma", name="vol")'
        r2_exec = execute_pipeline(code2, dag)
        r2 = reconciler.reconcile(r2_exec.actors)

        assert r2.updated == 1, (
            f"Expected updated=1 for colormap change, got {r2}"
        )
        assert r2.updated_property == 0, (
            "Colormap change must not be treated as a property-only update"
        )
        assert r2.added == 0
        assert r2.removed == 0
        assert r2.unchanged == 0

        img2 = os.path.join(self.tmpdir, "cmap_plasma.png")
        self.plotter.screenshot(img2)

        # Different colormaps should produce visually different renders
        with open(img1, "rb") as f1, open(img2, "rb") as f2:
            assert f1.read() != f2.read(), (
                "viridis and plasma colormaps should produce different screenshots"
            )
