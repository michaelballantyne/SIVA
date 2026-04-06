"""Tests for the annotate() and clear_annotations() MCP tools."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Server import helpers (same pattern as test_server_tools.py)
# ---------------------------------------------------------------------------

import vislang.server as srv  # noqa: E402


# ---------------------------------------------------------------------------
# VTK import (real VTK, not mocked)
# ---------------------------------------------------------------------------
import vtk  # noqa: E402


# ---------------------------------------------------------------------------
# Helper: build a minimal fake renderer that satisfies annotate() internals
# ---------------------------------------------------------------------------

class _FakeVtkRenderer:
    """Minimal stand-in for vtkRenderer that records AddActor/RemoveActor calls."""

    def __init__(self):
        self._added = []
        self._removed = []

    def AddActor(self, actor):
        self._added.append(actor)

    def RemoveActor(self, actor):
        self._removed.append(actor)


class _FakeRenderer:
    """Fake vislang Renderer object used by annotate/clear_annotations."""

    def __init__(self):
        self._renderer = _FakeVtkRenderer()
        self._render_calls = 0

    def render(self):
        self._render_calls += 1

    def run_on_main_thread(self, fn):
        return fn()


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestAnnotateActorCreation(unittest.TestCase):
    """Test that annotate() creates a vtkBillboardTextActor3D with correct properties."""

    def setUp(self):
        # Initialise a clean test context and inject a fake renderer.
        self._fake_renderer = _FakeRenderer()
        self._ctx = srv._init_for_test(self._fake_renderer)

    def _call_annotate(self, **kwargs):
        defaults = dict(x=10.0, y=20.0, z=5.0, label="Test", color="white", font_size=14)
        defaults.update(kwargs)
        with patch.object(srv, "_with_screenshot", side_effect=lambda r: r):
            return srv.annotate(**defaults)

    def test_returns_success_message(self):
        result = self._call_annotate(label="Fire front", x=100, y=200, z=50)
        self.assertIn("Fire front", result)
        self.assertIn("100", result)

    def test_actor_added_to_renderer(self):
        self._call_annotate(label="Alpha")
        self.assertEqual(len(self._fake_renderer._renderer._added), 1)
        actor = self._fake_renderer._renderer._added[0]
        self.assertIsInstance(actor, vtk.vtkBillboardTextActor3D)

    def test_actor_position_set(self):
        self._call_annotate(x=1.5, y=2.5, z=3.5, label="Pos")
        actor = self._fake_renderer._renderer._added[0]
        pos = actor.GetPosition()
        self.assertAlmostEqual(pos[0], 1.5)
        self.assertAlmostEqual(pos[1], 2.5)
        self.assertAlmostEqual(pos[2], 3.5)

    def test_actor_text_set(self):
        self._call_annotate(label="Hello World")
        actor = self._fake_renderer._renderer._added[0]
        self.assertEqual(actor.GetInput(), "Hello World")

    def test_actor_font_size(self):
        self._call_annotate(label="Big", font_size=24)
        actor = self._fake_renderer._renderer._added[0]
        self.assertEqual(actor.GetTextProperty().GetFontSize(), 24)

    def test_actor_color_white(self):
        self._call_annotate(label="W", color="white")
        actor = self._fake_renderer._renderer._added[0]
        r, g, b = actor.GetTextProperty().GetColor()
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 1.0)
        self.assertAlmostEqual(b, 1.0)

    def test_actor_color_red(self):
        self._call_annotate(label="R", color="red")
        actor = self._fake_renderer._renderer._added[0]
        r, g, b = actor.GetTextProperty().GetColor()
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 0.0)
        self.assertAlmostEqual(b, 0.0)

    def test_actor_color_yellow(self):
        self._call_annotate(label="Y", color="yellow")
        actor = self._fake_renderer._renderer._added[0]
        r, g, b = actor.GetTextProperty().GetColor()
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 1.0)
        self.assertAlmostEqual(b, 0.0)

    def test_actor_color_hex(self):
        self._call_annotate(label="Hex", color="#ff8800")
        actor = self._fake_renderer._renderer._added[0]
        r, g, b = actor.GetTextProperty().GetColor()
        self.assertAlmostEqual(r, 1.0, places=2)
        self.assertAlmostEqual(g, 0.533, places=2)
        self.assertAlmostEqual(b, 0.0, places=2)

    def test_actor_unknown_color_defaults_to_white(self):
        self._call_annotate(label="Unk", color="notacolor")
        actor = self._fake_renderer._renderer._added[0]
        r, g, b = actor.GetTextProperty().GetColor()
        self.assertAlmostEqual(r, 1.0)
        self.assertAlmostEqual(g, 1.0)
        self.assertAlmostEqual(b, 1.0)

    def test_annotation_stored_in_context(self):
        self._call_annotate(label="Stored")
        self.assertIn("Stored", self._ctx.annotations)
        actor = self._ctx.annotations["Stored"]
        self.assertIsInstance(actor, vtk.vtkBillboardTextActor3D)

    def test_duplicate_label_replaces_old_actor(self):
        """Adding an annotation with the same label removes the previous one."""
        self._call_annotate(label="Dup", x=0, y=0, z=0)
        old_actor = self._ctx.annotations["Dup"]

        self._call_annotate(label="Dup", x=5, y=5, z=5)
        new_actor = self._ctx.annotations["Dup"]

        # Old actor should have been removed from renderer
        self.assertIn(old_actor, self._fake_renderer._renderer._removed)
        # New actor added
        self.assertIn(new_actor, self._fake_renderer._renderer._added)
        self.assertIsNot(old_actor, new_actor)

    def test_multiple_annotations_stored(self):
        self._call_annotate(label="A", x=1, y=0, z=0)
        self._call_annotate(label="B", x=2, y=0, z=0)
        self._call_annotate(label="C", x=3, y=0, z=0)
        self.assertEqual(len(self._ctx.annotations), 3)
        self.assertIn("A", self._ctx.annotations)
        self.assertIn("B", self._ctx.annotations)
        self.assertIn("C", self._ctx.annotations)

    def test_render_called_after_add(self):
        initial = self._fake_renderer._render_calls
        self._call_annotate(label="RenderTest")
        self.assertEqual(self._fake_renderer._render_calls, initial + 1)


class TestClearAnnotations(unittest.TestCase):
    """Test that clear_annotations() removes all labels cleanly."""

    def setUp(self):
        self._fake_renderer = _FakeRenderer()
        self._ctx = srv._init_for_test(self._fake_renderer)

    def _add_annotation(self, label, x=0, y=0, z=0):
        with patch.object(srv, "_with_screenshot", side_effect=lambda r: r):
            srv.annotate(x=x, y=y, z=z, label=label)

    def _clear(self):
        with patch.object(srv, "_with_screenshot", side_effect=lambda r: r):
            return srv.clear_annotations()

    def test_clear_empty_scene_returns_zero_count(self):
        result = self._clear()
        self.assertIn("0", result)

    def test_clear_removes_actors_from_renderer(self):
        self._add_annotation("X1")
        self._add_annotation("X2")
        actor1, actor2 = list(self._ctx.annotations.values())

        self._clear()

        self.assertIn(actor1, self._fake_renderer._renderer._removed)
        self.assertIn(actor2, self._fake_renderer._renderer._removed)

    def test_clear_empties_annotations_dict(self):
        self._add_annotation("P")
        self._add_annotation("Q")
        self.assertEqual(len(self._ctx.annotations), 2)

        self._clear()
        self.assertEqual(len(self._ctx.annotations), 0)

    def test_clear_returns_count_in_message(self):
        self._add_annotation("A")
        self._add_annotation("B")
        self._add_annotation("C")
        result = self._clear()
        self.assertIn("3", result)

    def test_clear_render_called(self):
        self._add_annotation("Z")
        before = self._fake_renderer._render_calls
        self._clear()
        self.assertEqual(self._fake_renderer._render_calls, before + 1)

    def test_add_after_clear_works(self):
        """Annotations can be added again after clearing."""
        self._add_annotation("First")
        self._clear()
        self._add_annotation("Second")
        self.assertIn("Second", self._ctx.annotations)
        self.assertNotIn("First", self._ctx.annotations)


class TestAnnotateUsesWithScreenshot(unittest.TestCase):
    """Verify annotate and clear_annotations call _with_screenshot."""

    def setUp(self):
        self._fake_renderer = _FakeRenderer()
        self._ctx = srv._init_for_test(self._fake_renderer)

    def test_annotate_calls_with_screenshot(self):
        with patch.object(srv, "_with_screenshot", return_value="mocked") as mock_ws:
            result = srv.annotate(x=0, y=0, z=0, label="SS")
            mock_ws.assert_called_once()
        self.assertEqual(result, "mocked")

    def test_clear_annotations_calls_with_screenshot(self):
        with patch.object(srv, "_with_screenshot", return_value="mocked") as mock_ws:
            result = srv.clear_annotations()
            mock_ws.assert_called_once()
        self.assertEqual(result, "mocked")


if __name__ == "__main__":
    unittest.main()
