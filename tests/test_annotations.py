"""Tests for the DSL annotate() form and the _coerce_color helper."""

import os
import sys
import unittest

import vtk
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vislang.dsl import PipelineBuilder, _coerce_color, interpret_build
from vislang.renderer import Renderer, RenderMode


# ---------------------------------------------------------------------------
# _coerce_color unit tests
# ---------------------------------------------------------------------------

class TestCoerceColor(unittest.TestCase):
    """Unit tests for the _coerce_color helper."""

    def test_white_string(self):
        self.assertEqual(_coerce_color("white"), (1, 1, 1))

    def test_black_string(self):
        self.assertEqual(_coerce_color("black"), (0, 0, 0))

    def test_red_string(self):
        self.assertEqual(_coerce_color("red"), (1, 0, 0))

    def test_green_string(self):
        self.assertEqual(_coerce_color("green"), (0, 1, 0))

    def test_blue_string(self):
        self.assertEqual(_coerce_color("blue"), (0, 0, 1))

    def test_yellow_string(self):
        self.assertEqual(_coerce_color("yellow"), (1, 1, 0))

    def test_hex_string_orange(self):
        r, g, b = _coerce_color("#ff8800")
        self.assertAlmostEqual(r, 1.0, places=2)
        self.assertAlmostEqual(g, 0.533, places=2)
        self.assertAlmostEqual(b, 0.0, places=2)

    def test_hex_string_pure_red(self):
        r, g, b = _coerce_color("#ff0000")
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 0.0)
        self.assertAlmostEqual(b, 0.0)

    def test_rgb_tuple_passthrough(self):
        self.assertEqual(_coerce_color((0.5, 0.3, 0.1)), (0.5, 0.3, 0.1))

    def test_rgb_list_passthrough(self):
        result = _coerce_color([1.0, 0.0, 0.5])
        self.assertEqual(result, (1.0, 0.0, 0.5))

    def test_unknown_string_falls_back_to_white(self):
        self.assertEqual(_coerce_color("notacolor"), (1, 1, 1))

    def test_case_insensitive(self):
        self.assertEqual(_coerce_color("RED"), (1, 0, 0))
        self.assertEqual(_coerce_color("White"), (1, 1, 1))


# ---------------------------------------------------------------------------
# PipelineBuilder.annotate() unit tests (no renderer needed)
# ---------------------------------------------------------------------------

class TestPipelineBuilderAnnotate(unittest.TestCase):
    """Test that PipelineBuilder.annotate() accumulates entries correctly."""

    def setUp(self):
        self.builder = PipelineBuilder()

    def test_single_annotate_appends_entry(self):
        self.builder.annotate(1.0, 2.0, 3.0, "test label")
        self.assertEqual(len(self.builder._annotations), 1)
        entry = self.builder._annotations[0]
        self.assertEqual(entry["x"], 1.0)
        self.assertEqual(entry["y"], 2.0)
        self.assertEqual(entry["z"], 3.0)
        self.assertEqual(entry["text"], "test label")

    def test_default_color_and_font_size(self):
        self.builder.annotate(0, 0, 0, "label")
        entry = self.builder._annotations[0]
        self.assertEqual(entry["color"], "white")
        self.assertEqual(entry["font_size"], 14)

    def test_color_override(self):
        self.builder.annotate(0, 0, 0, "label", color="red")
        self.assertEqual(self.builder._annotations[0]["color"], "red")

    def test_font_size_override(self):
        self.builder.annotate(0, 0, 0, "label", font_size=20)
        self.assertEqual(self.builder._annotations[0]["font_size"], 20)

    def test_multiple_calls_accumulate(self):
        self.builder.annotate(0, 0, 0, "first")
        self.builder.annotate(1, 0, 0, "second")
        self.builder.annotate(2, 0, 0, "third")
        self.assertEqual(len(self.builder._annotations), 3)

    def test_duplicate_text_allowed(self):
        """Multiple annotations with the same text are NOT deduplicated."""
        self.builder.annotate(0, 0, 0, "same")
        self.builder.annotate(1, 0, 0, "same")
        self.assertEqual(len(self.builder._annotations), 2)
        self.assertEqual(self.builder._annotations[0]["text"], "same")
        self.assertEqual(self.builder._annotations[1]["text"], "same")

    def test_initial_annotations_is_empty_list(self):
        self.assertEqual(self.builder._annotations, [])

    def test_tuple_color_stored_as_is(self):
        self.builder.annotate(0, 0, 0, "label", color=(0.5, 0.2, 0.8))
        self.assertEqual(self.builder._annotations[0]["color"], (0.5, 0.2, 0.8))


# ---------------------------------------------------------------------------
# End-to-end tests with a real Renderer in OFFSCREEN mode
# ---------------------------------------------------------------------------

class TestAnnotateEndToEnd(unittest.TestCase):
    """Test that annotate() entries become vtkBillboardTextActor3D overlay actors."""

    def _make_renderer(self):
        return Renderer(mode=RenderMode.OFFSCREEN)

    def _build_sphere_pipeline(self, code_suffix=""):
        """Build a minimal sphere pipeline and return (builder, vtk_objects)."""
        code = f"""
data = source('vtkSphereSource', Radius=1.0)
show(data)
{code_suffix}
"""
        builder, vtk_objects, _, _ = interpret_build(code)
        return builder, vtk_objects

    def test_two_annotations_produce_two_overlay_actors(self):
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(0, 0, 0, "origin")
annotate(1, 0, 0, "x-axis")
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        self.assertEqual(len(r._overlay_actors), 2)

    def test_annotation_actors_are_billboard_type(self):
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(0, 0, 0, "origin")
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        actor = r._overlay_actors[0]
        self.assertIsInstance(actor, vtk.vtkBillboardTextActor3D)

    def test_annotation_actor_text(self):
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(0, 0, 0, "fire front")
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        actor = r._overlay_actors[0]
        self.assertEqual(actor.GetInput(), "fire front")

    def test_annotation_actor_position(self):
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(5.0, 6.0, 7.0, "test")
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        actor = r._overlay_actors[0]
        pos = actor.GetPosition()
        self.assertAlmostEqual(pos[0], 5.0)
        self.assertAlmostEqual(pos[1], 6.0)
        self.assertAlmostEqual(pos[2], 7.0)

    def test_annotation_actor_color_named(self):
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(0, 0, 0, "label", color="red")
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        actor = r._overlay_actors[0]
        clr = actor.GetTextProperty().GetColor()
        self.assertAlmostEqual(clr[0], 1.0)
        self.assertAlmostEqual(clr[1], 0.0)
        self.assertAlmostEqual(clr[2], 0.0)

    def test_annotation_actor_color_hex(self):
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(0, 0, 0, "label", color="#ff0000")
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        actor = r._overlay_actors[0]
        clr = actor.GetTextProperty().GetColor()
        self.assertAlmostEqual(clr[0], 1.0, places=2)
        self.assertAlmostEqual(clr[1], 0.0, places=2)
        self.assertAlmostEqual(clr[2], 0.0, places=2)

    def test_annotation_actor_color_tuple(self):
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(0, 0, 0, "label", color=(0.2, 0.4, 0.6))
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        actor = r._overlay_actors[0]
        clr = actor.GetTextProperty().GetColor()
        self.assertAlmostEqual(clr[0], 0.2, places=2)
        self.assertAlmostEqual(clr[1], 0.4, places=2)
        self.assertAlmostEqual(clr[2], 0.6, places=2)

    def test_annotation_actor_font_size(self):
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(0, 0, 0, "label", font_size=22)
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        actor = r._overlay_actors[0]
        self.assertEqual(actor.GetTextProperty().GetFontSize(), 22)

    def test_declarative_rebuild_clears_old_annotations(self):
        """Re-running the pipeline without annotate() removes the old actors."""
        # First build: two annotations
        code_with = """
data = source('vtkSphereSource')
show(data)
annotate(0, 0, 0, "one")
annotate(1, 0, 0, "two")
"""
        builder1, vtk_objects1, _, _ = interpret_build(code_with)
        r = self._make_renderer()
        builder1.apply_to_renderer(vtk_objects1, r)
        self.assertEqual(len(r._overlay_actors), 2)

        # Second build: no annotations (declarative rebuild)
        code_without = """
data = source('vtkSphereSource')
show(data)
"""
        builder2, vtk_objects2, _, _ = interpret_build(code_without)
        # apply_to_renderer calls renderer.clear() which removes old actors
        builder2.apply_to_renderer(vtk_objects2, r)
        self.assertEqual(len(r._overlay_actors), 0)

    def test_annotations_with_same_text_both_rendered(self):
        """Same text in multiple calls creates distinct actors (no dedup)."""
        builder, vtk_objects = self._build_sphere_pipeline("""
annotate(0, 0, 0, "dup")
annotate(1, 0, 0, "dup")
""")
        r = self._make_renderer()
        r.clear()
        builder._build_show_directives(vtk_objects, r)
        builder._apply_scene_settings(r)

        self.assertEqual(len(r._overlay_actors), 2)
        self.assertEqual(r._overlay_actors[0].GetInput(), "dup")
        self.assertEqual(r._overlay_actors[1].GetInput(), "dup")


if __name__ == "__main__":
    unittest.main()
