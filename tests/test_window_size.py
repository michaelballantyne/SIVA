"""Tests for durable window size: the base Renderer's _siva_size bookkeeping
and the DSL window_size() scene form.

Covers three behaviours from the "window size is not durable or declarable"
backlog item:
  1. A size set via Renderer.set_size() (or the set_window_size() MCP tool)
     survives pipeline rebuilds instead of reverting to the lazy
     _ensure_initialized() default.
  2. The DSL window_size() form declares a size in the pipeline file itself,
     which wins over a prior tool-set size while present.
  3. When window_size() is absent from the file, a previously tool-set size
     is left alone (not reset back to the default).
"""

import os
import sys
import unittest

from PIL import Image as PILImage

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.compute import evaluate
from siva.dsl import PipelineBuilder
from siva.renderer import Renderer, RenderMode
from siva import scene as scene_mod
import siva.server as srv


# ---------------------------------------------------------------------------
# Renderer-level durability (real VTK, offscreen, single-threaded)
# ---------------------------------------------------------------------------

class TestRendererSizeDurability(unittest.TestCase):
    """Exercises Renderer.set_size()/_siva_size directly, mirroring the
    xvfb-offscreen verification recipe from the backlog item."""

    def test_set_size_persists_across_render_and_rebuild(self):
        """set_size() sticks through a render+screenshot, then through a
        rebuild (clear + re-render) with no further set_size() call."""
        r = Renderer(mode=RenderMode.OFFSCREEN)
        r.set_size(600, 600)
        r.render()
        path = r.screenshot("shot1.png")
        self.assertEqual(r.get_size(), (600, 600))
        png_path = path[:-4] + ".png"
        with PILImage.open(png_path) as im:
            self.assertEqual(im.size, (600, 600))

        # Simulate a pipeline rebuild: clear actors, render again. Nothing
        # here calls set_size() again -- the size must still stick.
        r.clear()
        r.render()
        path2 = r.screenshot("shot2.png")
        self.assertEqual(r.get_size(), (600, 600))
        png_path2 = path2[:-4] + ".png"
        with PILImage.open(png_path2) as im:
            self.assertEqual(im.size, (600, 600))

    def test_constructor_size_respected_when_init_is_deferred(self):
        """HEADLESS_INTERACTIVE/INTERACTIVE defer VTK creation past __init__;
        the first lazy _ensure_initialized() call must use the constructor's
        width/height, not its own unrelated 640x800 default."""
        r = Renderer(1920, 1080, mode=RenderMode.HEADLESS_INTERACTIVE)
        # Nothing has touched set_size() -- get_size() triggers the first
        # _ensure_initialized() call.
        self.assertEqual(r.get_size(), (1920, 1080))

    def test_default_size_is_640x800(self):
        """Sanity check on the actual default, which set_window_size()'s
        docstring must describe accurately."""
        r = Renderer(mode=RenderMode.OFFSCREEN)
        self.assertEqual(r.get_size(), (640, 800))

    def test_get_size_does_not_reset_a_requested_size(self):
        """get_size() (a no-args _ensure_initialized() caller) must not
        clobber a size requested via set_size()."""
        r = Renderer(mode=RenderMode.OFFSCREEN)
        r.set_size(300, 300)
        self.assertEqual(r.get_size(), (300, 300))
        # Calling again is idempotent.
        self.assertEqual(r.get_size(), (300, 300))


# ---------------------------------------------------------------------------
# PipelineBuilder unit tests
# ---------------------------------------------------------------------------

class TestPipelineBuilderWindowSize(unittest.TestCase):

    def test_initial_state_none(self):
        b = PipelineBuilder()
        self.assertIsNone(b._window_size)

    def test_window_size_stores_ints(self):
        b = PipelineBuilder()
        b.window_size(800, 600)
        self.assertEqual(b._window_size, (800, 600))

    def test_window_size_coerces_to_int(self):
        b = PipelineBuilder()
        b.window_size(800.0, 600.0)
        self.assertEqual(b._window_size, (800, 600))
        self.assertIsInstance(b._window_size[0], int)


# ---------------------------------------------------------------------------
# Scene-level: window_size() DSL form -> SceneSpec -> apply_scene_settings()
# ---------------------------------------------------------------------------

class TestWindowSizeDeclarativeForm(unittest.TestCase):
    """End-to-end through evaluate() + apply_scene_settings(), real Renderer
    (offscreen, single-threaded) -- mirrors tests/test_annotations.py's
    TestAnnotateEndToEnd pattern."""

    def _make_renderer(self):
        return Renderer(mode=RenderMode.OFFSCREEN)

    def _build(self, code_suffix=""):
        code = f"""
from siva.spec_api import *

data = source('vtkSphereSource', Radius=1.0)
show(data)
{code_suffix}
"""
        return evaluate(code)

    def _apply(self, result, renderer):
        renderer.clear()
        scene_mod.build_show_actors(result.shows, result.outputs, renderer)
        scene_mod.apply_scene_settings(result.scene, renderer)

    def test_window_size_absent_scene_field_is_none(self):
        result = self._build()
        self.assertIsNone(result.scene.window_size)

    def test_window_size_present_scene_field(self):
        result = self._build("window_size(1024, 768)")
        self.assertEqual(result.scene.window_size, (1024, 768))

    def test_window_size_sets_renderer_size(self):
        result = self._build("window_size(1024, 768)")
        r = self._make_renderer()
        self._apply(result, r)
        self.assertEqual(r.get_size(), (1024, 768))

    def test_window_size_present_wins_over_prior_tool_set_size(self):
        """A file's window_size() overrides a size previously set via the
        set_window_size() MCP tool (renderer.set_size())."""
        r = self._make_renderer()
        r.set_size(500, 500)  # stands in for a prior set_window_size() call
        result = self._build("window_size(1024, 768)")
        self._apply(result, r)
        self.assertEqual(r.get_size(), (1024, 768))

    def test_window_size_absent_leaves_prior_tool_set_size(self):
        """When window_size() is not in the file, a size set via the tool
        is left alone -- never reset back to the 640x800 default."""
        r = self._make_renderer()
        r.set_size(500, 500)  # stands in for a prior set_window_size() call
        result = self._build()  # no window_size() call
        self._apply(result, r)
        self.assertEqual(r.get_size(), (500, 500))

    def test_window_size_persists_across_rebuild_without_the_form(self):
        """Once dropped from the file, a size set earlier (whether via
        window_size() or the tool) keeps applying across further rebuilds."""
        r = self._make_renderer()
        result1 = self._build("window_size(1024, 768)")
        self._apply(result1, r)
        self.assertEqual(r.get_size(), (1024, 768))

        # Rebuild without window_size() in the file.
        result2 = self._build()
        self._apply(result2, r)
        self.assertEqual(r.get_size(), (1024, 768))

    def test_window_size_screenshot_matches_declared_size(self):
        result = self._build("window_size(640, 480)")
        r = self._make_renderer()
        self._apply(result, r)
        path = r.screenshot("scene_shot.png")
        png_path = path[:-4] + ".png"
        with PILImage.open(png_path) as im:
            self.assertEqual(im.size, (640, 480))


# ---------------------------------------------------------------------------
# Server tool: set_window_size()
# ---------------------------------------------------------------------------

class TestSetWindowSizeTool(unittest.TestCase):
    """Exercises the set_window_size() MCP tool against a real (offscreen,
    single-threaded) Renderer via _init_for_test(). Never touches the
    BuildCoordinator's background worker thread (no request_build() calls),
    so nothing here crosses threads with the real VTK render window."""

    def setUp(self):
        os.makedirs(".siva", exist_ok=True)
        self.renderer = Renderer(mode=RenderMode.OFFSCREEN)
        self.ctx = srv._init_for_test(self.renderer)
        self.addCleanup(self.ctx.shutdown)

    def test_set_window_size_updates_renderer(self):
        srv.set_window_size(500, 400)
        self.assertEqual(self.renderer.get_size(), (500, 400))

    def test_set_window_size_result_includes_screenshot(self):
        result = srv.set_window_size(500, 400)
        self.assertEqual(len(result), 2)
        self.assertIn("500x400", result[0])

    def test_set_window_size_persists_across_a_rebuild(self):
        """A size set via the tool must not revert on the next pipeline
        build (the regression this backlog item is about)."""
        srv.set_window_size(600, 600)

        code = """
from siva.spec_api import *

data = source('vtkSphereSource', Radius=1.0)
show(data)
"""
        result = evaluate(code)
        self.renderer.clear()
        scene_mod.build_show_actors(result.shows, result.outputs, self.renderer)
        scene_mod.apply_scene_settings(result.scene, self.renderer)
        self.renderer.render()

        self.assertEqual(self.renderer.get_size(), (600, 600))

    def test_docstring_states_actual_default(self):
        """Regression test for the false '1920x1080' default claim."""
        doc = srv.set_window_size.__doc__ or ""
        self.assertIn("640x800", doc)
        self.assertNotIn("1920x1080", doc)

    def test_docstring_mentions_window_size_form(self):
        doc = srv.set_window_size.__doc__ or ""
        self.assertIn("window_size", doc)


if __name__ == "__main__":
    unittest.main()
