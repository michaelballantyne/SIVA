"""Tests for named-color resolution shared by background() and show(color=).

Covers the ``siva.colors.resolve_color`` helper directly (presets, named
colors, hex, error messages), its use inside ``background()``
(``siva.dsl.PipelineBuilder``), inside ``show(color=)``
(``siva.filters.create_show``), and an end-to-end build+render check under
Xvfb.
"""

import difflib
import os
import sys
import unittest

import vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.colors import BACKGROUND_PRESETS, resolve_color
from siva.compute import evaluate
from siva.dsl import PipelineBuilder
from siva.filters import create_show
from siva.renderer import Renderer, RenderMode
from siva import scene as scene_mod


def _vtk_rgb(name):
    c = vtk.vtkNamedColors().GetColor3d(name)
    return (c[0], c[1], c[2])


# ---------------------------------------------------------------------------
# resolve_color() unit tests
# ---------------------------------------------------------------------------

class TestResolveColorPresets(unittest.TestCase):
    def test_presets_unchanged(self):
        expected = {
            "dark": (0.02, 0.02, 0.06),
            "light": (0.85, 0.85, 0.9),
            "black": (0.0, 0.0, 0.0),
            "white": (1.0, 1.0, 1.0),
        }
        self.assertEqual(BACKGROUND_PRESETS, expected)
        for name, rgb in expected.items():
            self.assertEqual(resolve_color(name), rgb)


class TestResolveColorTriples(unittest.TestCase):
    def test_rgb_triple_passthrough(self):
        self.assertEqual(resolve_color((0.2, 0.4, 0.6)), (0.2, 0.4, 0.6))

    def test_list_triple(self):
        self.assertEqual(resolve_color([1.0, 0.0, 0.5]), (1.0, 0.0, 0.5))

    def test_wrong_length_rejected(self):
        with self.assertRaises(ValueError):
            resolve_color((0.5, 0.5))
        with self.assertRaises(ValueError):
            resolve_color((0.1, 0.2, 0.3, 0.4))

    def test_out_of_range_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_color((2.0, 0.5, 0.5))
        self.assertIn("0.0-1.0", str(ctx.exception))

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            resolve_color((-0.1, 0.5, 0.5))

    def test_255_scale_rejected_not_silently_rescaled(self):
        # 0-255 triples must raise, not get silently divided by 255.
        with self.assertRaises(ValueError):
            resolve_color((255, 128, 0))


class TestResolveColorNamedColors(unittest.TestCase):
    def test_lowercase_name(self):
        self.assertEqual(resolve_color("wheat"), _vtk_rgb("wheat"))

    def test_title_case_with_space(self):
        self.assertEqual(resolve_color("Slate Gray"), _vtk_rgb("slategray"))

    def test_snake_case(self):
        self.assertEqual(resolve_color("slate_gray"), _vtk_rgb("slategray"))

    def test_tomato(self):
        self.assertEqual(resolve_color("tomato"), _vtk_rgb("tomato"))


class TestResolveColorHex(unittest.TestCase):
    def test_hex_lowercase(self):
        r, g, b = resolve_color("#ff8800")
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 0x88 / 255.0)
        self.assertAlmostEqual(b, 0.0)

    def test_hex_uppercase(self):
        self.assertEqual(resolve_color("#FF0000"), resolve_color("#ff0000"))

    def test_hex_wrong_length_falls_through_to_error(self):
        with self.assertRaises(ValueError):
            resolve_color("#fff")


class TestResolveColorErrors(unittest.TestCase):
    def test_unknown_string_raises_with_presets_listed(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_color("not_a_real_color_xyz")
        msg = str(ctx.exception)
        for preset in BACKGROUND_PRESETS:
            self.assertIn(preset, msg)

    def test_unknown_string_suggests_close_matches(self):
        # 'wheatt' is a one-character typo of the real name 'wheat'.
        with self.assertRaises(ValueError) as ctx:
            resolve_color("wheatt")
        msg = str(ctx.exception)
        self.assertIn("wheat", msg)

    def test_suggestions_capped_at_three(self):
        # Sanity-check difflib actually returns close matches for a typo of
        # a real vtkNamedColors name, and that resolve_color surfaces them.
        matches = difflib.get_close_matches("slategray", ["slategray", "slateblue", "steelblue"], n=3)
        self.assertTrue(matches)

    def test_non_string_non_sequence_rejected(self):
        with self.assertRaises(ValueError):
            resolve_color(42)
        with self.assertRaises(ValueError):
            resolve_color(None)


# ---------------------------------------------------------------------------
# background() integration
# ---------------------------------------------------------------------------

class TestBackgroundNamedColors(unittest.TestCase):
    def setUp(self):
        self.builder = PipelineBuilder()

    def test_preset_still_works(self):
        self.builder.background("white")
        self.assertEqual(self.builder._background, (1.0, 1.0, 1.0))

    def test_named_color(self):
        self.builder.background("wheat")
        self.assertEqual(self.builder._background, _vtk_rgb("wheat"))

    def test_named_color_with_spaces_and_case(self):
        self.builder.background("Slate Gray")
        self.assertEqual(self.builder._background, _vtk_rgb("slategray"))

    def test_hex(self):
        self.builder.background("#112233")
        r, g, b = self.builder._background
        self.assertAlmostEqual(r, 0x11 / 255.0)
        self.assertAlmostEqual(g, 0x22 / 255.0)
        self.assertAlmostEqual(b, 0x33 / 255.0)

    def test_unknown_name_raises(self):
        with self.assertRaises(ValueError):
            self.builder.background("not_a_real_color_xyz")

    def test_rgb_triple_still_works(self):
        self.builder.background(0.1, 0.2, 0.3)
        self.assertEqual(self.builder._background, (0.1, 0.2, 0.3))

    def test_rgb_triple_out_of_range_now_rejected(self):
        with self.assertRaises(ValueError):
            self.builder.background(2.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# show(color=) integration
# ---------------------------------------------------------------------------

class TestShowColorNamed(unittest.TestCase):
    def test_named_color_reaches_actor_property(self):
        sphere = vtk.vtkSphereSource()
        sphere.Update()
        actor, _ = create_show(sphere, color="tomato")
        self.assertEqual(actor.GetProperty().GetColor(), _vtk_rgb("tomato"))

    def test_hex_color_reaches_actor_property(self):
        sphere = vtk.vtkSphereSource()
        sphere.Update()
        actor, _ = create_show(sphere, color="#112233")
        r, g, b = actor.GetProperty().GetColor()
        self.assertAlmostEqual(r, 0x11 / 255.0)
        self.assertAlmostEqual(g, 0x22 / 255.0)
        self.assertAlmostEqual(b, 0x33 / 255.0)

    def test_rgb_triple_still_works(self):
        sphere = vtk.vtkSphereSource()
        sphere.Update()
        actor, _ = create_show(sphere, color=(0.2, 0.4, 0.6))
        r, g, b = actor.GetProperty().GetColor()
        self.assertAlmostEqual(r, 0.2)
        self.assertAlmostEqual(g, 0.4)
        self.assertAlmostEqual(b, 0.6)

    def test_unknown_name_raises(self):
        sphere = vtk.vtkSphereSource()
        sphere.Update()
        with self.assertRaises(ValueError):
            create_show(sphere, color="not_a_real_color_xyz")


# ---------------------------------------------------------------------------
# End-to-end: real pipeline + Renderer in OFFSCREEN mode
# ---------------------------------------------------------------------------

class TestNamedColorsEndToEnd(unittest.TestCase):
    def test_background_wheat_and_show_color_tomato(self):
        code = """
from siva.spec_api import *

data = source('vtkSphereSource', Radius=1.0)
show(data, "sphere", color="tomato")
background("wheat")
"""
        result = evaluate(code)
        r = Renderer(mode=RenderMode.OFFSCREEN)
        r.clear()
        scene_mod.build_show_actors(result.shows, result.outputs, r)
        scene_mod.apply_scene_settings(result.scene, r)

        bg = r._renderer.GetBackground()
        self.assertEqual(bg, _vtk_rgb("wheat"))

        actor = r._actors["sphere"]
        self.assertEqual(actor.GetProperty().GetColor(), _vtk_rgb("tomato"))


if __name__ == "__main__":
    unittest.main()
