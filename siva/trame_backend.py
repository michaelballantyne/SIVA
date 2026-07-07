"""Trame rendering backend for SIVA.

`TrameRenderer` implements the full public Renderer interface (see
``tests/test_renderer_interface.py``) by serving each view as an interactive
browser view through trame's ``VtkRemoteView`` (server-side VTK rendering
streamed to the browser over a websocket). It reuses the desktop
``Renderer``'s scene-management logic (actors, volumes, scalar bars, camera,
LightKit) and diverges only where trame requires it: window/interactor
creation flags, the event loop, ``dispatch``, ``render``, ``screenshot``, and
teardown.

Architecture
------------
A dedicated thread owns both the trame/asyncio event loop *and* the VTK
objects. All VTK access from other threads (the MCP thread, the hot-reload
build worker) is marshalled onto that thread via ``dispatch``, which uses
``asyncio.run_coroutine_threadsafe`` (with a run-inline-if-already-on-owner
check to avoid self-deadlock, mirroring the desktop work-queue pattern in
``renderer.py``).

The trame server is embedded with the accommodations documented in the
design investigation:

- ``show_connection_info=False`` and ``open_browser=False`` keep **stdout
  clean** — SIVA speaks MCP JSON-RPC on stdio and any banner would corrupt
  it. All diagnostics go to ``.siva/server.log``.
- ``timeout=0`` disables wslink's default 300s idle auto-shutdown.
- ``host="127.0.0.1"`` binds loopback only.
- ``thread=True`` tells wslink it is not on the main thread (no signal
  handlers) and to create its own event loop there.
- ``port=0`` (the default) auto-picks a free port; the real port is read
  back from ``server.port`` once the server is ready.

When ``VSCODE_PROXY_URI`` is set (code-server / Coder terminals), the
proxied URL is computed by substituting ``{{port}}`` and reported alongside
the localhost URL.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import os
import threading

from .renderer import Renderer, RenderMode

logger = logging.getLogger("siva.renderer")

# Monotonic counter so each TrameRenderer gets a process-unique trame server
# name even if view names repeat (trame caches servers globally by name).
_server_seq = itertools.count()


class TrameRenderer(Renderer):
    """A Renderer backed by a trame ``VtkRemoteView`` served over websocket.

    Constructing a ``TrameRenderer`` spins up a dedicated thread that builds
    the VTK objects, wires them into a trame server, and runs the trame
    event loop. The constructor blocks until the server is ready (or raises
    if startup fails).
    """

    def __init__(self, width=1920, height=1080, view_name=None, port=0,
                 host="127.0.0.1", startup_timeout=30):
        # Set up all base-class attributes (mode=TRAME defers VTK creation —
        # see Renderer.__init__ — so nothing touches VTK on the constructing
        # thread; the owner thread builds it below).
        super().__init__(width=width, height=height, mode=RenderMode.TRAME,
                         view_name=view_name)

        self._siva_size = (width, height)
        self._port_req = port
        self._host = host

        self._server = None
        self._view = None
        self._loop = None
        self._owner_thread_id = None
        self._port = None
        self._url = None
        self._proxy_url = None
        self._start_error = None
        self._ready = threading.Event()

        self._thread = threading.Thread(
            target=self._thread_main,
            name=f"trame-{view_name or 'view'}",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=startup_timeout):
            raise RuntimeError(
                f"Trame server for view '{view_name}' did not become ready "
                f"within {startup_timeout}s"
            )
        if self._start_error is not None:
            raise self._start_error

    # -- owner thread ------------------------------------------------------

    def _thread_main(self):
        """Owner thread: build VTK, wire trame, run the event loop (blocks)."""
        try:
            self._owner_thread_id = threading.get_ident()
            # Build the offscreen render window, renderer, and (uninitialized-
            # for-interaction but Initialize()d) interactor on THIS thread.
            self._ensure_initialized(*self._siva_size)

            from trame.app import get_server
            from trame.ui.html import DivLayout
            from trame.widgets.vtk import VtkRemoteView

            server_name = f"siva-{self.view_name or 'view'}-{next(_server_seq)}"
            self._server = get_server(server_name, client_type="vue3")
            self._server.controller.on_server_ready.add(self._on_ready)

            with DivLayout(self._server):
                self._view = VtkRemoteView(
                    self._render_window,
                    ref="view",
                    style="position:absolute;top:0;left:0;width:100%;height:100%;",
                )
            self._server.controller.view_update = self._view.update

            # Blocks running the aiohttp app + asyncio loop until stop().
            self._server.start(
                exec_mode="main",
                thread=True,
                port=self._port_req,
                host=self._host,
                open_browser=False,
                show_connection_info=False,
                timeout=0,
            )
        except Exception as e:  # pragma: no cover - startup failure path
            logger.exception("Trame server thread crashed for view %s",
                             self.view_name)
            self._start_error = e
        finally:
            self._ready.set()

    def _on_ready(self, **_kwargs):
        """Called on the loop thread once the server is listening."""
        self._loop = asyncio.get_running_loop()
        self._port = self._server.port
        self._url = f"http://localhost:{self._port}/"
        proxy = os.environ.get("VSCODE_PROXY_URI")
        if proxy:
            self._proxy_url = proxy.replace("{{port}}", str(self._port))
        logger.info("Trame view '%s' serving at %s", self.view_name, self._url)
        if self._proxy_url:
            logger.info("Trame view '%s' proxied at %s",
                        self.view_name, self._proxy_url)
        self._ready.set()

    # -- URLs --------------------------------------------------------------

    @property
    def url(self):
        """The localhost URL where this view is served (None until ready)."""
        return self._url

    @property
    def proxy_url(self):
        """Proxied URL (VSCODE_PROXY_URI substituted), or None."""
        return self._proxy_url

    @property
    def port(self):
        """The actual TCP port the trame server bound to."""
        return self._port

    def view_url(self):
        """Return {"url": ..., "proxy_url": ...} for this view."""
        return {"url": self._url, "proxy_url": self._proxy_url}

    # -- dispatch / event loop --------------------------------------------

    def dispatch(self, fn):
        """Run *fn* on the thread that owns the trame loop and VTK objects.

        Runs inline if already on the owner thread (avoids self-deadlock,
        mirroring the desktop work-queue pattern). Otherwise schedules the
        call on the trame event loop and blocks for the result.
        """
        if threading.get_ident() == self._owner_thread_id:
            return fn()
        loop = self._loop
        if loop is None or loop.is_closed():
            raise RuntimeError(
                "Trame renderer loop is not available (server not ready or "
                "already destroyed)"
            )

        async def _runner():
            return fn()

        future = asyncio.run_coroutine_threadsafe(_runner(), loop)
        return future.result()

    def run_event_loop(self):
        """No-op: the trame server thread already owns and runs the loop.

        Present for interface compatibility. In trame mode ``main()`` runs
        ``mcp.run()`` on the main thread (like offscreen); the render event
        loop lives on this backend's dedicated thread, started in
        ``__init__``.
        """
        return None

    # -- rendering ---------------------------------------------------------

    def render(self):
        """Render the VTK scene and push a fresh frame to connected clients."""
        self._ensure_initialized()
        self._render_window.Render()
        self._push_frame()

    def _push_frame(self):
        """Push the current frame to any connected browser clients.

        Safe when no client is connected (view.update() is then a no-op) and
        callable from any thread — marshalled onto the loop when off-thread.
        """
        if self._server is None:
            return
        view_update = getattr(self._server.controller, "view_update", None)
        if view_update is None:
            return
        try:
            if threading.get_ident() == self._owner_thread_id:
                view_update()
            elif self._loop is not None and not self._loop.is_closed():
                self._loop.call_soon_threadsafe(view_update)
        except Exception:  # pragma: no cover - defensive
            logger.debug("view_update failed", exc_info=True)

    def set_size(self, width, height):
        """Set the SIVA-intended render window size.

        Tracks the intended size so ``screenshot`` can render at a stable
        resolution even after a browser client has resized the shared window.
        """
        self._siva_size = (width, height)
        super().set_size(width, height)

    def screenshot(self, path="screenshot.png"):
        """Capture a screenshot at the SIVA-intended size, then restore.

        The remote view resizes the shared render window to match the browser
        canvas whenever a client interacts. We save the current size, render
        and capture at the intended SIVA size (via the parent PNG+JPEG
        dual-write path), then restore the previous size so the browser view
        is unaffected.
        """
        self._ensure_initialized()
        saved = tuple(self._render_window.GetSize())
        target = self._siva_size or saved
        if tuple(target) != saved:
            self._render_window.SetSize(*target)
        try:
            return super().screenshot(path)
        finally:
            if tuple(target) != saved:
                self._render_window.SetSize(*saved)

    def is_window_closed(self) -> bool:
        """Always False — a trame view has no closable OS window."""
        return False

    # -- teardown ----------------------------------------------------------

    def destroy(self):
        """Tear down VTK, stop the trame server, and join the owner thread."""
        loop = self._loop
        if (loop is not None and not loop.is_closed()
                and self._thread is not None and self._thread.is_alive()):
            # Free VTK on the owner thread first (it owns those objects).
            try:
                self.dispatch(lambda: Renderer.destroy(self))
            except Exception:  # pragma: no cover - defensive
                logger.debug("VTK teardown failed", exc_info=True)
            # Then stop the server, which unblocks server.start() and lets
            # the owner thread exit.
            try:
                fut = asyncio.run_coroutine_threadsafe(
                    self._server.stop(), loop)
                fut.result(timeout=10)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Trame server stop failed", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=10)
