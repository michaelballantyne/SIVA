"""Tests for the trame rendering backend (siva/trame_backend.py).

These construct a real TrameRenderer headless (under xvfb on Linux) and
verify: the screenshot size guard, that the trame server serves HTTP on a
loopback auto-port, clean shutdown, and proxy-URL construction.

The whole module skips cleanly if the optional 'trame' extra isn't
installed. Networking is confined to 127.0.0.1 — no external access.
"""

from __future__ import annotations

import os
import socket
import unittest
import urllib.request

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import trame  # noqa: F401
    from trame.widgets.vtk import VtkRemoteView  # noqa: F401
    _HAVE_TRAME = True
except ImportError:
    _HAVE_TRAME = False

import vtk

# Every TrameRenderer schedules its setup onto trame_backend._shared_loop,
# which only exists once something has called trame_backend.run_shared_loop()
# -- normally done once by server.main() before constructing any view. These
# tests construct TrameRenderer directly, so setUpModule() below bootstraps
# that loop on a background thread for the whole test process.
#
# On macOS specifically this doesn't work: VTK's Cocoa backend requires
# render-window creation and every Render() call -- including ones wslink
# triggers internally -- to happen on the *real* process main thread, and
# pytest's own test-running thread needs to stay free to run the tests, so
# nothing here can call run_shared_loop() on that thread. Skipped there
# rather than reworked around that constraint; exercised instead via a real
# `siva.server --trame` subprocess (manually verified, not yet automated --
# see the 2026-07-06 macos-cocoa-render-thread-constraint doc in SIVA-meta).
_SKIP_REASON_MACOS = (
    "TrameRenderer requires the shared trame loop to run on the real main "
    "thread on macOS, which a bare test process can't provide without "
    "blocking test execution itself"
)


def setUpModule():
    if not _HAVE_TRAME or sys.platform == "darwin":
        return
    import threading
    from siva import trame_backend
    threading.Thread(target=trame_backend.run_shared_loop, daemon=True).start()
    trame_backend._shared_loop_ready.wait(timeout=10)


@unittest.skipUnless(_HAVE_TRAME, "trame extra not installed")
@unittest.skipIf(sys.platform == "darwin", _SKIP_REASON_MACOS)
class TestTrameRenderer(unittest.TestCase):
    def setUp(self):
        from siva.trame_backend import TrameRenderer
        # Small window keeps rendering fast; port=0 auto-picks.
        self.r = TrameRenderer(width=320, height=240, view_name="test", port=0)

    def tearDown(self):
        try:
            self.r.destroy()
        except Exception:
            pass

    def _add_cone(self):
        def _build():
            cs = vtk.vtkConeSource()
            m = vtk.vtkPolyDataMapper()
            m.SetInputConnection(cs.GetOutputPort())
            a = vtk.vtkActor()
            a.SetMapper(m)
            self.r.add_actor("cone", a)
            self.r.reset_camera()
        self.r.dispatch(_build)

    def test_ready_reports_port_and_url(self):
        self.assertIsInstance(self.r.port, int)
        self.assertGreater(self.r.port, 0)
        self.assertEqual(self.r.url, f"http://localhost:{self.r.port}/")

    def test_mode_is_trame(self):
        from siva.renderer import RenderMode
        self.assertEqual(self.r.mode, RenderMode.TRAME)

    def test_is_window_closed_false(self):
        self.assertFalse(self.r.is_window_closed())

    def test_render_and_screenshot(self):
        self._add_cone()
        self.r.dispatch(self.r.render)
        out = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "_trame_shot.png",
        )
        try:
            jpg = self.r.dispatch(lambda: self.r.screenshot(out))
            # Dual-write contract: returns the JPEG, PNG written alongside.
            self.assertTrue(jpg.endswith(".jpg"))
            self.assertTrue(os.path.exists(jpg))
            self.assertTrue(os.path.exists(out))
            self.assertGreater(os.path.getsize(jpg), 0)
            # PNG rendered at the SIVA-intended size (size guard).
            reader = vtk.vtkPNGReader()
            reader.SetFileName(out)
            reader.Update()
            dims = reader.GetOutput().GetDimensions()
            self.assertEqual((dims[0], dims[1]), (320, 240))
        finally:
            for p in (out, out[:-4] + ".jpg"):
                if os.path.exists(p):
                    os.remove(p)

    def test_screenshot_size_guard_restores_browser_size(self):
        # Simulate a browser client having resized the shared window, then
        # confirm screenshot renders at the SIVA size and restores the size.
        self.r.dispatch(lambda: self.r._render_window.SetSize(500, 500))
        out = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "_trame_guard.png",
        )
        try:
            self.r.dispatch(lambda: self.r.screenshot(out))
            reader = vtk.vtkPNGReader()
            reader.SetFileName(out)
            reader.Update()
            dims = reader.GetOutput().GetDimensions()
            self.assertEqual((dims[0], dims[1]), (320, 240))
            # Window size restored to the browser-imposed size.
            restored = self.r.dispatch(lambda: tuple(self.r._render_window.GetSize()))
            self.assertEqual(restored, (500, 500))
        finally:
            for p in (out, out[:-4] + ".jpg"):
                if os.path.exists(p):
                    os.remove(p)

    def test_http_serves_html(self):
        with urllib.request.urlopen(self.r.url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            body = resp.read().decode()
        self.assertGreater(len(body), 0)
        # The trame client bundle is referenced from the page.
        self.assertIn("script", body.lower())

    def test_destroy_closes_port(self):
        port = self.r.port
        self.r.destroy()
        s = socket.socket()
        s.settimeout(1)
        try:
            with self.assertRaises((ConnectionRefusedError, OSError)):
                s.connect(("127.0.0.1", port))
        finally:
            s.close()


@unittest.skipUnless(_HAVE_TRAME, "trame extra not installed")
@unittest.skipIf(sys.platform == "darwin", _SKIP_REASON_MACOS)
class TestProxyUrl(unittest.TestCase):
    def test_vscode_proxy_uri_substituted(self):
        from siva.trame_backend import TrameRenderer
        prev = os.environ.get("VSCODE_PROXY_URI")
        os.environ["VSCODE_PROXY_URI"] = "https://host.example/proxy/{{port}}/"
        try:
            r = TrameRenderer(width=160, height=120, view_name="proxy", port=0)
            try:
                self.assertEqual(
                    r.proxy_url,
                    f"https://host.example/proxy/{r.port}/",
                )
                self.assertEqual(
                    r.view_url(),
                    {"url": r.url, "proxy_url": r.proxy_url},
                )
            finally:
                r.destroy()
        finally:
            if prev is None:
                os.environ.pop("VSCODE_PROXY_URI", None)
            else:
                os.environ["VSCODE_PROXY_URI"] = prev


if __name__ == "__main__":
    unittest.main()
