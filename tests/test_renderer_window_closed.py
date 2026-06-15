"""Unit tests for Renderer.is_window_closed().

Tests cover OFFSCREEN, HEADLESS_INTERACTIVE, and INTERACTIVE mode stubs
(using mock render-window objects so no real display is required).
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.renderer import Renderer, RenderMode


class TestIsWindowClosedOffscreen(unittest.TestCase):
    """OFFSCREEN mode — no real OS window, always returns False."""

    def test_offscreen_returns_false_before_init(self):
        r = Renderer(mode=RenderMode.OFFSCREEN)
        # Renderer auto-initializes in OFFSCREEN mode, but window isn't real
        self.assertFalse(r.is_window_closed())

    def test_offscreen_returns_false_regardless_of_mapped(self):
        r = Renderer(mode=RenderMode.OFFSCREEN)
        # Even if we somehow set GetMapped to 0, should still return False
        if r._render_window:
            r._render_window.GetMapped = lambda: 0
        self.assertFalse(r.is_window_closed())


class TestIsWindowClosedHeadlessInteractive(unittest.TestCase):
    """HEADLESS_INTERACTIVE mode — no real OS window, always returns False."""

    def test_headless_interactive_returns_false(self):
        r = Renderer(mode=RenderMode.HEADLESS_INTERACTIVE)
        self.assertFalse(r.is_window_closed())

    def test_headless_interactive_returns_false_even_after_init(self):
        r = Renderer(mode=RenderMode.HEADLESS_INTERACTIVE)
        # Force init
        r._ensure_initialized()
        self.assertFalse(r.is_window_closed())


class TestIsWindowClosedInteractiveMode(unittest.TestCase):
    """INTERACTIVE mode — uses GetMapped() on the render window."""

    def _make_interactive_renderer_with_mock_window(self, mapped_value):
        """Create a Renderer in INTERACTIVE mode with a mocked render window."""
        r = Renderer.__new__(Renderer)
        r._mode = RenderMode.INTERACTIVE
        r._initialized = True
        r._render_window = MagicMock()
        r._render_window.GetMapped.return_value = mapped_value
        return r

    def test_interactive_not_initialized_returns_false(self):
        """An INTERACTIVE renderer that has never been initialized reports not closed."""
        r = Renderer.__new__(Renderer)
        r._mode = RenderMode.INTERACTIVE
        r._initialized = False
        r._render_window = None
        self.assertFalse(r.is_window_closed())

    def test_interactive_none_render_window_returns_false(self):
        """If _render_window is None (e.g., after destroy()), return False."""
        r = Renderer.__new__(Renderer)
        r._mode = RenderMode.INTERACTIVE
        r._initialized = True
        r._render_window = None
        self.assertFalse(r.is_window_closed())

    def test_interactive_window_open_returns_false(self):
        """GetMapped() == 1 means the window is still open."""
        r = self._make_interactive_renderer_with_mock_window(mapped_value=1)
        self.assertFalse(r.is_window_closed())

    def test_interactive_window_closed_returns_true(self):
        """GetMapped() == 0 means the OS window was closed."""
        r = self._make_interactive_renderer_with_mock_window(mapped_value=0)
        self.assertTrue(r.is_window_closed())

    def test_interactive_queries_get_mapped(self):
        """is_window_closed() must call GetMapped() on the render window."""
        r = self._make_interactive_renderer_with_mock_window(mapped_value=1)
        r.is_window_closed()
        r._render_window.GetMapped.assert_called()


if __name__ == "__main__":
    unittest.main()
