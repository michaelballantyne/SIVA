"""Tests for the trame rendering backend (siva/trame_backend.py).

These construct real TrameRenderers headless (under xvfb on Linux) and
verify the single-port architecture: every view (and the index page) is
served by one shared trame server on one port, each view addressed by its
``?ui=<name>`` query parameter; plus the screenshot size guard, per-view
teardown (which retires the view's template but keeps the shared server
up), and proxy-URL construction.

The whole module skips cleanly if the optional 'trame' extra isn't
installed. Networking is confined to 127.0.0.1 — no external access.
"""

from __future__ import annotations

import os
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

# The index snapshot the shared app serves at /views; tests mutate this
# list in place. Configured in setUpModule(), before the first renderer
# exists (the shared app reads its configuration exactly once, at start).
_INDEX_VIEWS = []


def setUpModule():
    if not _HAVE_TRAME or sys.platform == "darwin":
        return
    import threading
    from siva import trame_backend
    trame_backend.configure_shared_app(
        index_snapshot_fn=lambda: list(_INDEX_VIEWS),
        session_label="trame-backend-tests",
    )
    threading.Thread(target=trame_backend.run_shared_loop, daemon=True).start()
    trame_backend._shared_loop_ready.wait(timeout=10)


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode()


@unittest.skipUnless(_HAVE_TRAME, "trame extra not installed")
@unittest.skipIf(sys.platform == "darwin", _SKIP_REASON_MACOS)
class TestTrameRenderer(unittest.TestCase):
    def setUp(self):
        from siva.trame_backend import TrameRenderer
        # Small window keeps rendering fast; the shared server's port is
        # picked once for the whole process.
        self.r = TrameRenderer(width=320, height=240, view_name="test")

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
        self.assertEqual(self.r.url, f"http://localhost:{self.r.port}/?ui=test")

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
        status, body = _get(self.r.url)
        self.assertEqual(status, 200)
        self.assertGreater(len(body), 0)
        # The trame client bundle is referenced from the page.
        self.assertIn("script", body.lower())


@unittest.skipUnless(_HAVE_TRAME, "trame extra not installed")
@unittest.skipIf(sys.platform == "darwin", _SKIP_REASON_MACOS)
class TestSinglePort(unittest.TestCase):
    """Everything is served by one shared server on one port."""

    def test_views_share_one_port_and_differ_by_query(self):
        from siva.trame_backend import TrameRenderer
        a = TrameRenderer(width=160, height=120, view_name="alpha")
        b = TrameRenderer(width=160, height=120, view_name="beta")
        try:
            self.assertEqual(a.port, b.port)
            self.assertEqual(a.url, f"http://localhost:{a.port}/?ui=alpha")
            self.assertEqual(b.url, f"http://localhost:{b.port}/?ui=beta")
        finally:
            a.destroy()
            b.destroy()

    def test_destroy_keeps_shared_server_up_and_retires_template(self):
        from siva.trame_backend import TrameRenderer, shared_app
        keeper = TrameRenderer(width=160, height=120, view_name="keeper")
        victim = TrameRenderer(width=160, height=120, view_name="victim")
        try:
            victim.destroy()
            # The shared port is still serving the surviving view.
            status, _ = _get(keeper.url)
            self.assertEqual(status, 200)
            # The destroyed view's template is now a "closed" notice,
            # pushed live through shared state to any open browser tab.
            template = shared_app().server.state["trame__template_victim"]
            self.assertIn("closed", template)
        finally:
            keeper.destroy()

    def test_view_name_reusable_after_destroy(self):
        from siva.trame_backend import TrameRenderer
        first = TrameRenderer(width=160, height=120, view_name="recycled")
        url = first.url
        first.destroy()
        second = TrameRenderer(width=160, height=120, view_name="recycled")
        try:
            # The name (and thus the URL) is freed by destroy and reclaimed.
            self.assertEqual(second.url, url)
        finally:
            second.destroy()

    def test_template_name_sanitized_and_deduped(self):
        from siva.trame_backend import TrameRenderer
        odd = TrameRenderer(width=160, height=120, view_name="a b")
        clash = TrameRenderer(width=160, height=120, view_name="a_b")
        try:
            self.assertEqual(odd.url.split("ui=")[1], "a_b")
            # Distinct live view names never share a template name.
            self.assertEqual(clash.url.split("ui=")[1], "a_b-2")
        finally:
            odd.destroy()
            clash.destroy()

    def test_index_page_served_on_same_port(self):
        from siva.trame_backend import TrameRenderer, shared_app
        from siva.view_index import ViewInfo
        r = TrameRenderer(width=160, height=120, view_name="indexed")
        _INDEX_VIEWS.append(ViewInfo(
            name="indexed", url=r.url, proxy_url=None, current=True))
        try:
            app = shared_app()
            self.assertEqual(app.index_url,
                             f"http://localhost:{app.port}/views")
            status, body = _get(app.index_url)
            self.assertEqual(status, 200)
            self.assertIn("indexed", body)
            self.assertIn("trame-backend-tests", body)
        finally:
            _INDEX_VIEWS.clear()
            r.destroy()


@unittest.skipUnless(_HAVE_TRAME, "trame extra not installed")
@unittest.skipIf(sys.platform == "darwin", _SKIP_REASON_MACOS)
class TestProxyUrl(unittest.TestCase):
    def test_vscode_proxy_uri_substituted(self):
        from siva.trame_backend import TrameRenderer
        prev = os.environ.get("VSCODE_PROXY_URI")
        os.environ["VSCODE_PROXY_URI"] = "https://host.example/proxy/{{port}}/"
        try:
            r = TrameRenderer(width=160, height=120, view_name="proxy")
            try:
                self.assertEqual(
                    r.proxy_url,
                    f"https://host.example/proxy/{r.port}/?ui=proxy",
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
