"""Tests for the camera_orbit MCP tool.

Tests verify:
- Correct number of frames returned
- Parameter validation / clamping (n_frames, elevation)
- Camera state is restored after the orbit
- Return value is a flat list of alternating text + Image items
"""

import math
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Stub mcp and vislang.renderer so the server can be imported without VTK
# display or a real MCP installation.
# ---------------------------------------------------------------------------

import vislang.server as srv  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_renderer(position=(100.0, -200.0, 300.0),
                        focal_point=(50.0, 50.0, 10.0),
                        up=(0.0, 0.0, 1.0)):
    """Build a mock Renderer whose camera state methods work predictably."""
    renderer = MagicMock()
    cam_state = {
        "position": list(position),
        "focal_point": list(focal_point),
        "up": list(up),
    }
    renderer.get_camera_state.return_value = cam_state
    # run_on_main_thread just calls the function directly
    renderer.run_on_main_thread.side_effect = lambda fn: fn()
    # screenshot returns the path it was given
    renderer.screenshot.side_effect = lambda path: path
    return renderer


def _set_renderer(mock_renderer):
    """Wire a mock renderer into the server's current view context."""
    srv._init_for_test(mock_renderer)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCameraOrbitFrameCount(unittest.TestCase):
    """camera_orbit should return 2*n_frames items (text + Image per frame)."""

    def _run_orbit(self, n_frames, elevation=30.0):
        mock_renderer = _make_mock_renderer()
        _set_renderer(mock_renderer)
        result = srv.camera_orbit(n_frames=n_frames, elevation=elevation)
        return result, mock_renderer

    def test_default_eight_frames(self):
        result, _ = self._run_orbit(8)
        self.assertEqual(len(result), 16)  # 8 * (text + Image)

    def test_single_frame(self):
        result, _ = self._run_orbit(1)
        self.assertEqual(len(result), 2)

    def test_four_frames(self):
        result, _ = self._run_orbit(4)
        self.assertEqual(len(result), 8)

    def test_sixteen_frames(self):
        result, _ = self._run_orbit(16)
        self.assertEqual(len(result), 32)


class TestCameraOrbitClamping(unittest.TestCase):
    """n_frames and elevation should be clamped to safe ranges."""

    def _run_orbit(self, **kwargs):
        mock_renderer = _make_mock_renderer()
        _set_renderer(mock_renderer)
        return srv.camera_orbit(**kwargs), mock_renderer

    def test_n_frames_clamped_to_minimum_one(self):
        result, _ = self._run_orbit(n_frames=0)
        # 0 -> clamped to 1 -> 2 items
        self.assertEqual(len(result), 2)

    def test_n_frames_clamped_to_maximum_sixteen(self):
        result, _ = self._run_orbit(n_frames=100)
        # 100 -> clamped to 16 -> 32 items
        self.assertEqual(len(result), 32)

    def test_elevation_clamped_to_89(self):
        # Should not raise; extreme elevation is clamped
        result, _ = self._run_orbit(n_frames=4, elevation=180.0)
        self.assertEqual(len(result), 8)

    def test_elevation_clamped_to_minus_89(self):
        result, _ = self._run_orbit(n_frames=4, elevation=-180.0)
        self.assertEqual(len(result), 8)


class TestCameraOrbitRestoresCamera(unittest.TestCase):
    """After orbit, the camera should be restored to its original state."""

    def test_camera_restored(self):
        original_pos = (100.0, -200.0, 300.0)
        original_focal = (50.0, 50.0, 10.0)
        original_up = (0.0, 0.0, 1.0)

        mock_renderer = _make_mock_renderer(
            position=original_pos,
            focal_point=original_focal,
            up=original_up,
        )
        _set_renderer(mock_renderer)

        srv.camera_orbit(n_frames=4, elevation=30.0)

        # The last set_camera call should restore the original state
        last_call = mock_renderer.set_camera.call_args_list[-1]
        kwargs = last_call[1]  # keyword arguments
        self.assertEqual(kwargs["position"], list(original_pos))
        self.assertEqual(kwargs["focal_point"], list(original_focal))
        self.assertEqual(kwargs["up"], list(original_up))


class TestCameraOrbitReturnStructure(unittest.TestCase):
    """Return value should be flat list alternating text strings and Image objects."""

    def test_alternating_text_and_image(self):
        mock_renderer = _make_mock_renderer()
        _set_renderer(mock_renderer)

        result = srv.camera_orbit(n_frames=4, elevation=30.0)

        self.assertIsInstance(result, list)
        for i, item in enumerate(result):
            if i % 2 == 0:
                # Even index: text description string
                self.assertIsInstance(item, str, f"Item {i} should be str")
                self.assertIn("Frame", item)
            else:
                # Odd index: Image object (constructed from mcp.server.fastmcp.Image)
                # The exact type varies based on how Image is mocked, so we just
                # verify it is NOT a plain str (i.e. it is some kind of object).
                self.assertNotIsInstance(item, str, f"Item {i} should not be a plain str")

    def test_descriptions_contain_azimuth_and_elevation(self):
        mock_renderer = _make_mock_renderer()
        _set_renderer(mock_renderer)

        result = srv.camera_orbit(n_frames=4, elevation=45.0)

        descriptions = [result[i] for i in range(0, len(result), 2)]
        for desc in descriptions:
            self.assertIn("45.0", desc)  # elevation in description

    def test_screenshots_use_distinct_paths(self):
        mock_renderer = _make_mock_renderer()
        _set_renderer(mock_renderer)

        srv.camera_orbit(n_frames=4)

        screenshot_paths = [c[0][0] for c in mock_renderer.screenshot.call_args_list]
        self.assertEqual(len(screenshot_paths), 4)
        self.assertEqual(len(set(screenshot_paths)), 4, "Each frame should use a unique path")


class TestCameraOrbitPositions(unittest.TestCase):
    """Camera positions computed during orbit should be on a circle around the focal point."""

    def test_positions_equidistant_from_focal_point(self):
        focal = (50.0, 50.0, 10.0)
        cam_pos = (150.0, 50.0, 10.0)  # distance = 100, elevation = 0 in z
        mock_renderer = _make_mock_renderer(position=cam_pos, focal_point=focal)
        _set_renderer(mock_renderer)

        srv.camera_orbit(n_frames=8, elevation=0.0)

        # All set_camera calls except the final restore should place the camera
        # at the same distance from the focal point.
        calls = mock_renderer.set_camera.call_args_list
        # Last call is the restore; all before are orbit frames
        orbit_calls = calls[:-1]
        self.assertEqual(len(orbit_calls), 8)

        expected_distance = math.sqrt(
            (cam_pos[0] - focal[0]) ** 2 +
            (cam_pos[1] - focal[1]) ** 2 +
            (cam_pos[2] - focal[2]) ** 2
        )

        for c in orbit_calls:
            pos = c[1]["position"]
            dist = math.sqrt(
                (pos[0] - focal[0]) ** 2 +
                (pos[1] - focal[1]) ** 2 +
                (pos[2] - focal[2]) ** 2
            )
            self.assertAlmostEqual(dist, expected_distance, places=6,
                                   msg=f"Frame position {pos} distance {dist} != {expected_distance}")

    def test_azimuths_evenly_spaced(self):
        """With elevation=0, frames should be evenly distributed around the focal z-axis."""
        focal = (0.0, 0.0, 0.0)
        cam_pos = (100.0, 0.0, 0.0)
        mock_renderer = _make_mock_renderer(position=cam_pos, focal_point=focal)
        _set_renderer(mock_renderer)

        n = 4
        srv.camera_orbit(n_frames=n, elevation=0.0)

        calls = mock_renderer.set_camera.call_args_list
        orbit_calls = calls[:-1]
        self.assertEqual(len(orbit_calls), n)

        angles = []
        for c in orbit_calls:
            pos = c[1]["position"]
            az = math.atan2(pos[1] - focal[1], pos[0] - focal[0])
            angles.append(az)

        # Expected azimuths: 0, pi/2, pi, 3pi/2
        # Normalise both to [0, 2*pi) before comparing
        expected = [2 * math.pi * i / n for i in range(n)]
        for got, exp in zip(angles, expected):
            got_norm = got % (2 * math.pi)
            exp_norm = exp % (2 * math.pi)
            self.assertAlmostEqual(got_norm, exp_norm, places=6)


if __name__ == "__main__":
    unittest.main()
