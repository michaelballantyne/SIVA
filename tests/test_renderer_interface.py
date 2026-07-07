"""Conformance test for the Renderer public interface.

This test formalizes the de-facto interface that server.py, dsl.py,
hot_reload.py, and run.py rely on when they hold a "renderer". By making the
interface an explicit, checked list we ensure a future alternate backend (e.g.
a Trame-based renderer) can be validated against the same surface, and we catch
accidental drift (a caller reaching for a private attribute, or a fake that no
longer mirrors the real thing).

The interface is deliberately the *behavioral* surface — the methods and
properties external modules call — not the internal VTK plumbing (which stays
private in renderer.py).
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.renderer import Renderer, RenderMode


# The canonical public interface. Every renderer backend must provide these.
PUBLIC_INTERFACE = [
    # rendering / output
    "render",
    "screenshot",
    # threading / dispatch
    "dispatch",
    "run_event_loop",
    # scene content
    "clear",
    "add_actor",
    "add_volume",
    "add_overlay_actor",
    "add_scalar_bar",
    # camera
    "set_camera",
    "get_camera_state",
    "reset_camera",
    "suggest_camera",
    "get_active_camera",
    # window / view properties
    "set_background",
    "set_size",
    "get_size",
    "get_visible_bounds",
    "camera_positioned",
    "mode",
    "view_name",
    "is_window_closed",
    "destroy",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestRendererPublicInterface(unittest.TestCase):
    def test_renderer_provides_full_interface(self):
        """Renderer must expose every name in the canonical public interface.

        Checked against an OFFSCREEN instance so instance attributes set in
        __init__ (e.g. view_name) count, not just class-level members.
        """
        r = Renderer(mode=RenderMode.OFFSCREEN)
        try:
            missing = [name for name in PUBLIC_INTERFACE if not hasattr(r, name)]
        finally:
            r.destroy()
        self.assertEqual(
            missing, [],
            f"Renderer is missing public interface members: {missing}",
        )

    def test_no_duplicate_interface_names(self):
        self.assertEqual(
            len(PUBLIC_INTERFACE), len(set(PUBLIC_INTERFACE)),
            "PUBLIC_INTERFACE contains duplicates",
        )


class TestTrameRendererConformance(unittest.TestCase):
    """The trame backend must provide the same public interface.

    Import-guarded: skips cleanly when the optional 'trame' extra isn't
    installed. Checks the interface by attribute presence on the class so it
    doesn't need to actually start a trame server here (that is exercised in
    tests/test_trame_backend.py under xvfb).
    """

    def test_trame_renderer_class_provides_full_interface(self):
        try:
            import trame  # noqa: F401
            from siva.trame_backend import TrameRenderer
        except ImportError:
            self.skipTest("trame extra not installed")
        missing = [name for name in PUBLIC_INTERFACE
                   if not hasattr(TrameRenderer, name)]
        # view_name is an instance attribute set in __init__, not a class
        # member — every other interface name is a method/property on the
        # class (inherited from Renderer or overridden).
        missing = [m for m in missing if m != "view_name"]
        self.assertEqual(
            missing, [],
            f"TrameRenderer is missing public interface members: {missing}",
        )


class TestNoExternalPrivateReaches(unittest.TestCase):
    """No module outside renderer.py should reach into Renderer's privates.

    This is the seam that lets an alternate backend drop in: callers must go
    through the public interface, not touch _render_window, _interactor,
    _camera_positioned, _mode, or _renderer on a renderer they were handed.
    """

    # Patterns that indicate an external module reaching a renderer private.
    _FORBIDDEN = [
        re.compile(r"renderer\._(?:render_window|interactor|camera_positioned|mode|renderer|initialized)\b"),
        re.compile(r"cur_renderer\._"),
        re.compile(r"\.run_on_main_thread\b"),  # renamed to dispatch
    ]

    def test_siva_package_has_no_private_reaches(self):
        offenders = []
        siva_dir = _REPO_ROOT / "siva"
        for py in siva_dir.glob("*.py"):
            if py.name == "renderer.py":
                continue  # renderer owns its own privates
            text = py.read_text()
            for lineno, line in enumerate(text.splitlines(), 1):
                for pat in self._FORBIDDEN:
                    if pat.search(line):
                        offenders.append(f"{py.name}:{lineno}: {line.strip()}")
        self.assertEqual(
            offenders, [],
            "Found external reaches into Renderer privates:\n" + "\n".join(offenders),
        )

    def test_no_run_on_main_thread_anywhere(self):
        """The rename to dispatch() should be complete across the repo."""
        offenders = []
        for base in ("siva", "tests", "scripts"):
            root = _REPO_ROOT / base
            if not root.exists():
                continue
            for py in root.rglob("*.py"):
                if py.name == "test_renderer_interface.py":
                    continue  # this file names the old symbol in a regex
                text = py.read_text()
                if "run_on_main_thread" in text:
                    offenders.append(str(py.relative_to(_REPO_ROOT)))
        self.assertEqual(
            offenders, [],
            "run_on_main_thread still referenced (should be dispatch): "
            + ", ".join(offenders),
        )


class TestFakesConform(unittest.TestCase):
    """The renderer fakes used in tests must expose the interface subset they
    stand in for, using the public names (dispatch, mode, camera_positioned)
    rather than mirroring privates. Verified by scanning the fake source so the
    check is robust to how the test module is imported."""

    # Files that define a renderer fake and the public names they should carry.
    _FAKE_FILES = [
        "tests/test_stateful_integration.py",
        "tests/test_hot_reload.py",
        "tests/test_terse_report.py",
        "tests/test_mcp_protocol.py",
        "scripts/bench_hot_reload.py",
    ]

    def test_fakes_use_public_dispatch(self):
        offenders = []
        for rel in self._FAKE_FILES:
            path = _REPO_ROOT / rel
            text = path.read_text()
            # Must define dispatch, must not define the old name.
            if "def dispatch(" not in text:
                offenders.append(f"{rel}: no dispatch() defined")
            if "run_on_main_thread" in text:
                offenders.append(f"{rel}: still references run_on_main_thread")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_fakes_use_public_mode_and_camera_positioned(self):
        offenders = []
        for rel in self._FAKE_FILES:
            path = _REPO_ROOT / rel
            text = path.read_text()
            if re.search(r"\b_camera_positioned\b", text):
                offenders.append(f"{rel}: still uses private _camera_positioned")
            if re.search(r"\b_mode\b", text):
                offenders.append(f"{rel}: still uses private _mode")
        self.assertEqual(offenders, [], "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
