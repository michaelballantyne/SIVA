"""Tests for the DSL annotate() form and the _coerce_color helper."""

import os
import sys
import unittest

import vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.dsl import PipelineBuilder, _coerce_color, interpret_build
from siva.renderer import Renderer, RenderMode


# ---------------------------------------------------------------------------
# _coerce_color unit tests
# ---------------------------------------------------------------------------

class TestCoerceColor(unittest.TestCase):
    """Unit tests for the _coerce_color helper."""

    def test_named_colors(self):
        """Spot-check several named colors from the lookup table."""
        cases = [
            ("white", (1, 1, 1)),
            ("black", (0, 0, 0)),
            ("red", (1, 0, 0)),
            ("blue", (0, 0, 1)),
            ("yellow", (1, 1, 0)),
            ("orange", (1, 0.5, 0)),
        ]
        for name, expected in cases:
            with self.subTest(name=name):
                self.assertEqual(_coerce_color(name), expected)

    def test_case_insensitive(self):
        self.assertEqual(_coerce_color("RED"), (1, 0, 0))
        self.assertEqual(_coerce_color("White"), (1, 1, 1))

    def test_hex_orange(self):
        r, g, b = _coerce_color("#ff8800")
        self.assertAlmostEqual(r, 1.0, places=2)
        self.assertAlmostEqual(g, 0.533, places=2)
        self.assertAlmostEqual(b, 0.0, places=2)

    def test_rgb_tuple_in_range(self):
        self.assertEqual(_coerce_color((0.5, 0.3, 0.1)), (0.5, 0.3, 0.1))

    def test_rgb_list_passthrough(self):
        result = _coerce_color([1.0, 0.0, 0.5])
        self.assertEqual(result, (1.0, 0.0, 0.5))

    # --- Adversarial inputs ---

    def test_unknown_string_falls_back_to_white(self):
        self.assertEqual(_coerce_color("notacolor"), (1, 1, 1))

    def test_none_falls_back_to_white(self):
        self.assertEqual(_coerce_color(None), (1, 1, 1))

    def test_empty_string_falls_back_to_white(self):
        self.assertEqual(_coerce_color(""), (1, 1, 1))

    def test_hex_wrong_length_falls_back_to_white(self):
        # 5-digit hex is not valid
        self.assertEqual(_coerce_color("#ff000"), (1, 1, 1))

    def test_hex_no_hash_falls_back_to_white(self):
        self.assertEqual(_coerce_color("ff0000"), (1, 1, 1))

    def test_out_of_range_tuple_clamped(self):
        # Values > 1.0 clamped to 1.0, values < 0 clamped to 0.0
        r, g, b = _coerce_color((2.0, -1.0, 0.5))
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 0.0)
        self.assertAlmostEqual(b, 0.5)

    def test_4tuple_alpha_dropped(self):
        # Alpha is silently dropped, RGB is used
        r, g, b = _coerce_color((0.5, 0.5, 0.5, 1.0))
        self.assertAlmostEqual(r, 0.5)
        self.assertAlmostEqual(g, 0.5)
        self.assertAlmostEqual(b, 0.5)

    def test_short_tuple_falls_back_to_white(self):
        self.assertEqual(_coerce_color((0.5, 0.5)), (1, 1, 1))


# ---------------------------------------------------------------------------
# PipelineBuilder.annotate() unit tests (no renderer needed)
# ---------------------------------------------------------------------------

class TestPipelineBuilderAnnotate(unittest.TestCase):
    """Test that PipelineBuilder.annotate() accumulates entries correctly."""

    def setUp(self):
        self.builder = PipelineBuilder()

    def test_single_annotate_stores_all_fields(self):
        self.builder.annotate(1.0, 2.0, 3.0, "test label")
        self.assertEqual(len(self.builder._annotations), 1)
        entry = self.builder._annotations[0]
        self.assertEqual(entry["x"], 1.0)
        self.assertEqual(entry["y"], 2.0)
        self.assertEqual(entry["z"], 3.0)
        self.assertEqual(entry["text"], "test label")
        self.assertEqual(entry["color"], "white")
        self.assertEqual(entry["font_size"], 14)

    def test_multiple_calls_accumulate(self):
        self.builder.annotate(0, 0, 0, "first")
        self.builder.annotate(1, 0, 0, "second")
        self.builder.annotate(2, 0, 0, "third")
        self.assertEqual(len(self.builder._annotations), 3)

    def test_initial_state_empty(self):
        self.assertEqual(self.builder._annotations, [])


# ---------------------------------------------------------------------------
# End-to-end tests with a real Renderer in OFFSCREEN mode
# ---------------------------------------------------------------------------

class TestAnnotateEndToEnd(unittest.TestCase):
    """Test that annotate() entries become vtkBillboardTextActor3D overlay actors."""

    def _make_renderer(self):
        return Renderer(mode=RenderMode.OFFSCREEN)

    def _build_sphere_pipeline(self, code_suffix=""):
        code = f"""
data = source('vtkSphereSource', Radius=1.0)
show(data)
{code_suffix}
"""
        builder, vtk_objects, _, _ = interpret_build(code)
        return builder, vtk_objects

    def _apply(self, builder, vtk_objects, renderer):
        renderer.clear()
        builder._build_show_directives(vtk_objects, renderer)
        builder._apply_scene_settings(renderer)

    def test_annotation_actors_are_billboard_type(self):
        builder, vtk_objects = self._build_sphere_pipeline(
            'annotate(0, 0, 0, "origin")'
        )
        r = self._make_renderer()
        self._apply(builder, vtk_objects, r)
        self.assertEqual(len(r._overlay_actors), 1)
        self.assertIsInstance(r._overlay_actors[0], vtk.vtkBillboardTextActor3D)

    def test_annotation_actor_text_and_position(self):
        builder, vtk_objects = self._build_sphere_pipeline(
            'annotate(5.0, 6.0, 7.0, "fire front")'
        )
        r = self._make_renderer()
        self._apply(builder, vtk_objects, r)
        actor = r._overlay_actors[0]
        self.assertEqual(actor.GetInput(), "fire front")
        pos = actor.GetPosition()
        self.assertAlmostEqual(pos[0], 5.0)
        self.assertAlmostEqual(pos[1], 6.0)
        self.assertAlmostEqual(pos[2], 7.0)

    def test_annotation_colors_named_hex_tuple(self):
        """Named, hex, and tuple colors all produce the expected RGB on the actor."""
        cases = [
            ('annotate(0,0,0,"l",color="red")', (1.0, 0.0, 0.0)),
            ('annotate(0,0,0,"l",color="#ff0000")', (1.0, 0.0, 0.0)),
            ('annotate(0,0,0,"l",color=(0.2,0.4,0.6))', (0.2, 0.4, 0.6)),
        ]
        for code_suffix, expected in cases:
            with self.subTest(code_suffix=code_suffix):
                builder, vtk_objects = self._build_sphere_pipeline(code_suffix)
                r = self._make_renderer()
                self._apply(builder, vtk_objects, r)
                clr = r._overlay_actors[0].GetTextProperty().GetColor()
                self.assertAlmostEqual(clr[0], expected[0], places=2)
                self.assertAlmostEqual(clr[1], expected[1], places=2)
                self.assertAlmostEqual(clr[2], expected[2], places=2)

    def test_annotation_font_size(self):
        builder, vtk_objects = self._build_sphere_pipeline(
            'annotate(0,0,0,"l",font_size=22)'
        )
        r = self._make_renderer()
        self._apply(builder, vtk_objects, r)
        self.assertEqual(r._overlay_actors[0].GetTextProperty().GetFontSize(), 22)

    def test_declarative_rebuild_clears_old_annotations(self):
        """Re-running the pipeline without annotate() removes the old actors."""
        code_with = """
data = source('vtkSphereSource')
show(data)
annotate(0, 0, 0, "one")
annotate(1, 0, 0, "two")
"""
        builder1, vtk_objects1, _, _ = interpret_build(code_with)
        r = self._make_renderer()
        self._apply(builder1, vtk_objects1, r)
        self.assertEqual(len(r._overlay_actors), 2)

        code_without = """
data = source('vtkSphereSource')
show(data)
"""
        builder2, vtk_objects2, _, _ = interpret_build(code_without)
        self._apply(builder2, vtk_objects2, r)
        self.assertEqual(len(r._overlay_actors), 0)

    # --- Regression: UseBoundsOff ---

    def test_annotation_actor_excluded_from_prop_bounds(self):
        """actor.GetUseBounds() must be False so cube-axes are not stretched."""
        builder, vtk_objects = self._build_sphere_pipeline(
            'annotate(1000, 1000, 1000, "far away")'
        )
        r = self._make_renderer()
        self._apply(builder, vtk_objects, r)
        actor = r._overlay_actors[0]
        self.assertEqual(actor.GetUseBounds(), False)

    def test_axes_bounds_unaffected_by_far_annotation(self):
        """cube-axes bounds must reflect data geometry, not annotation positions."""
        # Sphere bounds are roughly (-1,-1,-1) to (1,1,1)
        code_baseline = """
data = source('vtkSphereSource', Radius=1.0)
show(data)
"""
        builder_base, vtk_base, _, _ = interpret_build(code_baseline)
        r_base = self._make_renderer()
        r_base.clear()
        builder_base._build_show_directives(vtk_base, r_base)
        # Only add sphere actors — don't call _apply_scene_settings (adds title/axes)
        # so we get raw prop bounds from only the sphere.
        bounds_base = r_base._renderer.ComputeVisiblePropBounds()

        # Now add a far-away annotation and recompute visible prop bounds.
        code_ann = """
data = source('vtkSphereSource', Radius=1.0)
show(data)
annotate(1000, 1000, 1000, "far")
"""
        builder_ann, vtk_ann, _, _ = interpret_build(code_ann)
        r_ann = self._make_renderer()
        r_ann.clear()
        builder_ann._build_show_directives(vtk_ann, r_ann)
        builder_ann._apply_scene_settings(r_ann)
        bounds_ann = r_ann._renderer.ComputeVisiblePropBounds()

        # The annotation at (1000,1000,1000) must not expand the prop bounds.
        for i in range(6):
            self.assertAlmostEqual(
                bounds_base[i], bounds_ann[i], places=1,
                msg=f"bounds[{i}] changed: baseline={bounds_base[i]}, with annotation={bounds_ann[i]}"
            )


if __name__ == "__main__":
    unittest.main()
