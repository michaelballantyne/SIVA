"""Unit tests for siva/view_index.py — the trame-mode view index page.

These exercise the page rendering (stdlib-only, no optional deps needed)
and the aiohttp routes (``/views``, ``/thumb/<name>``) directly against a
fake, mutable registry (a list of ViewInfo we control) rather than real
trame servers or the full siva.server wiring. The route tests mount
``add_index_routes`` on a bare aiohttp application — exactly how
``siva.trame_backend`` mounts them on the shared trame server's app — and
skip cleanly when aiohttp (installed with the 'trame' extra) is missing.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.view_index import (
    ViewInfo,
    add_index_routes,
    render_index_html,
    resolve_url,
)

try:
    from aiohttp import web  # noqa: F401
    _HAVE_AIOHTTP = True
except ImportError:
    _HAVE_AIOHTTP = False


def _get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read()


def _get_status(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


class TestResolveUrl(unittest.TestCase):
    def test_no_proxy_env(self):
        url, proxy = resolve_url(1234, proxy_template=None)
        self.assertEqual(url, "http://localhost:1234/")
        self.assertIsNone(proxy)

    def test_proxy_template_substituted(self):
        url, proxy = resolve_url(
            1234, proxy_template="https://host.example/proxy/{{port}}/")
        self.assertEqual(url, "http://localhost:1234/")
        self.assertEqual(proxy, "https://host.example/proxy/1234/")

    def test_proxy_template_trailing_slash_enforced(self):
        _, proxy = resolve_url(
            1234, proxy_template="https://host.example/proxy/{{port}}")
        self.assertTrue(proxy.endswith("/"))

    def test_reads_env_when_template_omitted(self):
        prev = os.environ.get("VSCODE_PROXY_URI")
        os.environ["VSCODE_PROXY_URI"] = "https://env.example/proxy/{{port}}/"
        try:
            _, proxy = resolve_url(9999)
            self.assertEqual(proxy, "https://env.example/proxy/9999/")
        finally:
            if prev is None:
                os.environ.pop("VSCODE_PROXY_URI", None)
            else:
                os.environ["VSCODE_PROXY_URI"] = prev


class TestRenderIndexHtml(unittest.TestCase):
    def test_lists_view_names_and_links(self):
        views = [
            ViewInfo(name="main", url="http://localhost:1111/?ui=main",
                     proxy_url=None, current=True),
            ViewInfo(name="detail", url="http://localhost:1111/?ui=detail",
                     proxy_url=None, current=False),
        ]
        html = render_index_html("my-session", views)
        self.assertIn("main", html)
        self.assertIn("detail", html)
        self.assertIn("http://localhost:1111/?ui=main", html)
        self.assertIn("http://localhost:1111/?ui=detail", html)
        self.assertIn("focused", html)  # marker for the current view
        self.assertIn("my-session", html)

    def test_prefers_proxy_url_when_present(self):
        views = [
            ViewInfo(name="main", url="http://localhost:1111/",
                     proxy_url="https://host/proxy/1111/", current=True),
        ]
        html = render_index_html("s", views)
        self.assertIn("https://host/proxy/1111/", html)
        self.assertNotIn("http://localhost:1111/", html)

    def test_empty_registry(self):
        html = render_index_html("s", [])
        self.assertIn("No live views", html)

    def test_thumbnail_uses_relative_src(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n")
            path = f.name
        try:
            views = [ViewInfo(name="main", url="http://localhost:1111/",
                               proxy_url=None, current=True,
                               thumbnail_path=path)]
            html = render_index_html("s", views)
            # Relative src (no leading slash) so it works under a
            # path-stripping proxy that serves the page at a sub-path.
            self.assertIn('src="thumb/main"', html)
            self.assertNotIn('src="/thumb/main"', html)
        finally:
            os.remove(path)

    def test_no_thumbnail_when_file_missing(self):
        views = [ViewInfo(name="main", url="http://localhost:1111/",
                           proxy_url=None, current=True,
                           thumbnail_path="/nonexistent/path.png")]
        html = render_index_html("s", views)
        self.assertNotIn('src="thumb/main"', html)


class _IndexAppHarness:
    """Serve add_index_routes() on a bare aiohttp app, on a private loop.

    Mirrors the production mounting (routes added to an aiohttp application
    that serves them from an asyncio loop) closely enough that the tests
    can drive the routes over real HTTP with urllib.
    """

    def __init__(self, snapshot_fn, session_label):
        from aiohttp import web

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="index-test-loop", daemon=True)
        self._thread.start()

        async def _start():
            app = web.Application()
            add_index_routes(app, snapshot_fn, session_label)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            return runner, runner.addresses[0][1]

        fut = asyncio.run_coroutine_threadsafe(_start(), self._loop)
        self._runner, self.port = fut.result(timeout=10)
        self.url = f"http://127.0.0.1:{self.port}/"

    def shutdown(self):
        async def _stop():
            await self._runner.cleanup()

        try:
            asyncio.run_coroutine_threadsafe(_stop(), self._loop).result(timeout=10)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=5)


@unittest.skipUnless(_HAVE_AIOHTTP, "aiohttp (trame extra) not installed")
class TestIndexRoutes(unittest.TestCase):
    def setUp(self):
        self._views = []
        self.server = _IndexAppHarness(
            snapshot_fn=lambda: list(self._views),
            session_label="test-session",
        )
        self.index_url = f"{self.server.url}views"

    def tearDown(self):
        self.server.shutdown()

    def test_serves_200_with_view_names(self):
        self._views = [
            ViewInfo(name="alpha", url="http://localhost:1000/?ui=alpha",
                     proxy_url=None, current=True),
        ]
        status, body = _get(self.index_url)
        self.assertEqual(status, 200)
        text = body.decode()
        self.assertIn("alpha", text)
        self.assertIn("http://localhost:1000/?ui=alpha", text)

    def test_proxied_links_when_set(self):
        self._views = [
            ViewInfo(name="alpha", url="http://localhost:1000/",
                     proxy_url="https://host.example/proxy/1000/",
                     current=True),
        ]
        _, body = _get(self.index_url)
        text = body.decode()
        self.assertIn("https://host.example/proxy/1000/", text)
        self.assertNotIn("http://localhost:1000/", text)

    def test_thumbnail_serves_existing_png(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\n" + b"fakepixels")
            path = f.name
        try:
            self._views = [
                ViewInfo(name="alpha", url="http://localhost:1000/",
                          proxy_url=None, current=True,
                          thumbnail_path=path),
            ]
            status, body = _get(f"{self.server.url}thumb/alpha")
            self.assertEqual(status, 200)
            self.assertEqual(body, open(path, "rb").read())
        finally:
            os.remove(path)

    def test_thumbnail_404s_for_missing_view(self):
        self._views = [
            ViewInfo(name="alpha", url="http://localhost:1000/",
                     proxy_url=None, current=True),
        ]
        status = _get_status(f"{self.server.url}thumb/nope")
        self.assertEqual(status, 404)

    def test_thumbnail_404s_for_view_without_thumbnail_file(self):
        self._views = [
            ViewInfo(name="alpha", url="http://localhost:1000/",
                     proxy_url=None, current=True,
                     thumbnail_path="/nonexistent/thumb.png"),
        ]
        status = _get_status(f"{self.server.url}thumb/alpha")
        self.assertEqual(status, 404)

    def test_thumbnail_traversal_attempt_404s(self):
        self._views = [
            ViewInfo(name="alpha", url="http://localhost:1000/",
                     proxy_url=None, current=True),
        ]
        # Never resolved as a filesystem path — only matched against live
        # view names, so this can't escape into the filesystem.
        status = _get_status(f"{self.server.url}thumb/../secret")
        self.assertEqual(status, 404)

    def test_registry_changes_reflected_on_add(self):
        status, body = _get(self.index_url)
        self.assertNotIn("newview", body.decode())
        self._views.append(
            ViewInfo(name="newview", url="http://localhost:3000/",
                     proxy_url=None, current=False))
        _, body2 = _get(self.index_url)
        self.assertIn("newview", body2.decode())

    def test_registry_changes_reflected_on_remove(self):
        self._views = [
            ViewInfo(name="temp", url="http://localhost:4000/",
                     proxy_url=None, current=True),
        ]
        _, body = _get(self.index_url)
        self.assertIn("temp", body.decode())
        self._views = []
        _, body2 = _get(self.index_url)
        self.assertNotIn("temp", body2.decode())

    def test_unknown_path_404s(self):
        status = _get_status(f"{self.server.url}nope")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
