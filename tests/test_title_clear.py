"""Tests that title() text actors are cleared on pipeline rebuild.

Regression test for: title() actors not cleared on wait_for_pipeline rebuild.
Previous text actors were persisting and overlapping because they were
added directly to the vtkRenderer (bypassing the overlay_actors tracking
list) and thus not removed during renderer.clear().
"""

import os
import sys
import unittest

import vtk

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.renderer import Renderer, RenderMode
from siva.run import interpret


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_actor2d(renderer_obj):
    """Return the number of vtkActor2D instances in the vtkRenderer."""
    vtk_renderer = renderer_obj._renderer
    collection = vtk_renderer.GetActors2D()
    collection.InitTraversal()
    count = 0
    while collection.GetNextActor2D() is not None:
        count += 1
    return count


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestTitleOverlayClear(unittest.TestCase):
    """Verify title() actors are tracked and cleared on pipeline rebuild."""

    def _make_renderer(self):
        return Renderer(320, 240, mode=RenderMode.OFFSCREEN)

    def test_title_actor_tracked_in_overlay_actors(self):
        """title() must register its actor via add_overlay_actor, not directly."""
        r = self._make_renderer()
        code = 'title("Test Title")'
        interpret(code, r)
        self.assertEqual(
            len(r._overlay_actors), 1,
            f"Expected 1 overlay actor after title(), got {len(r._overlay_actors)}"
        )
        actor = r._overlay_actors[0]
        self.assertIsInstance(actor, vtk.vtkTextActor)

    def test_title_actor_text(self):
        """Title text is set correctly on the text actor."""
        r = self._make_renderer()
        interpret('title("My Scene Title")', r)
        actor = r._overlay_actors[0]
        self.assertEqual(actor.GetInput(), "My Scene Title")

    def test_clear_removes_title_actor(self):
        """renderer.clear() removes the title text actor."""
        r = self._make_renderer()
        interpret('title("Persisted Title")', r)
        self.assertEqual(len(r._overlay_actors), 1)

        r.clear()

        self.assertEqual(
            len(r._overlay_actors), 0,
            "overlay_actors should be empty after clear()"
        )
        self.assertEqual(
            _count_actor2d(r), 0,
            "No Actor2D should remain in vtkRenderer after clear()"
        )

    def test_rebuild_does_not_accumulate_title_actors(self):
        """Running the pipeline twice must not leave duplicate title actors.

        This is the core regression: previously each rebuild appended a new
        vtkTextActor without removing the old one, causing overlap.
        """
        r = self._make_renderer()
        interpret('title("First Run")', r)
        self.assertEqual(len(r._overlay_actors), 1)

        # Second pipeline run — renderer.clear() is called inside interpret/build
        interpret('title("Second Run")', r)

        self.assertEqual(
            len(r._overlay_actors), 1,
            f"Expected exactly 1 overlay actor after second run, got {len(r._overlay_actors)}"
        )
        actor = r._overlay_actors[0]
        self.assertEqual(
            actor.GetInput(), "Second Run",
            "Active title text should be from the most recent pipeline run"
        )
        # Also verify at the VTK level
        self.assertEqual(
            _count_actor2d(r), 1,
            "Exactly one Actor2D should be in the vtkRenderer after second run"
        )

    def test_no_title_leaves_no_overlay_actors(self):
        """Pipeline without title() leaves overlay_actors empty."""
        r = self._make_renderer()
        interpret("", r)
        self.assertEqual(len(r._overlay_actors), 0)

    def test_rebuild_without_title_clears_previous_title(self):
        """If the second pipeline has no title(), the first title must be removed."""
        r = self._make_renderer()
        interpret('title("Will be gone")', r)
        self.assertEqual(len(r._overlay_actors), 1)

        # Second run has no title
        interpret("", r)
        self.assertEqual(
            len(r._overlay_actors), 0,
            "overlay_actors must be empty after pipeline rebuild with no title()"
        )
        self.assertEqual(
            _count_actor2d(r), 0,
            "No Actor2D should remain after rebuild without title()"
        )


class TestAxes(unittest.TestCase):
    def _make_renderer(self):
        return Renderer(400, 300, mode=RenderMode.OFFSCREEN)

    def test_axes_registers_actor(self):
        """axes() should add a __axes__ actor to the renderer."""
        r = self._make_renderer()
        interpret(
            'data = source("vtkSphereSource")\n'
            'show(data, "sphere")\n'
            'axes()\n',
            r,
        )
        self.assertIn("__axes__", r._actors)

    def test_axes_cleared_on_rebuild(self):
        """__axes__ actor must be removed when pipeline is rebuilt without axes()."""
        r = self._make_renderer()
        interpret(
            'data = source("vtkSphereSource")\n'
            'show(data, "sphere")\n'
            'axes()\n',
            r,
        )
        self.assertIn("__axes__", r._actors)
        interpret(
            'data = source("vtkSphereSource")\n'
            'show(data, "sphere")\n',
            r,
        )
        self.assertNotIn("__axes__", r._actors)

    def test_axes_custom_labels(self):
        """axes() with custom labels should still register the actor."""
        r = self._make_renderer()
        interpret(
            'data = source("vtkSphereSource")\n'
            'show(data, "sphere")\n'
            'axes(color=(1,1,0), labels=("X (m)", "Y (m)", "Z (m)"))\n',
            r,
        )
        self.assertIn("__axes__", r._actors)


if __name__ == "__main__":
    unittest.main()
