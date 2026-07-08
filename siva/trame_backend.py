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
Every ``TrameRenderer`` (every view) shares **one** asyncio event loop
(``run_shared_loop()``, run by ``server.main()`` on the real process main
thread before any view is constructed). Each view's trame server is
scheduled onto that shared loop (``exec_mode="task"``) rather than getting
its own OS thread. All VTK access from other threads (the MCP thread, the
hot-reload build worker) is marshalled onto that loop's thread via
``dispatch``, which uses ``asyncio.run_coroutine_threadsafe`` (with a
run-inline-if-already-on-owner check to avoid self-deadlock, mirroring the
desktop work-queue pattern in ``renderer.py``).

This one design is required on macOS and is simply used everywhere, rather
than branching on platform: VTK's Cocoa backend creates a native
``NSWindow`` during *any* ``vtkRenderWindow.Render()`` call -- even with
``SetOffScreenRendering(True)`` and no interactor at all -- and Cocoa
requires that happen on the process main thread. Critically, this includes
renders that **wslink triggers internally** in response to a browser client
connecting or interacting (initial frame push, resize, drag) -- those never
go through SIVA's own ``render()``/``dispatch()`` at all, so no amount of
wrapping SIVA's own call sites would be sufficient; the loop those renders
run on has to *be* the main thread. Since ``server.main()`` is always
called directly from the process entry point (no thread wrapper), whichever
thread calls ``run_shared_loop()`` there already *is* the real main thread
on every platform, so satisfying macOS costs nothing elsewhere -- there is
no Linux-specific per-view-thread path to maintain.

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
the localhost URL, via ``siva.view_index.resolve_url`` (shared with the
view-index page so the substitution logic lives in one place).

Each view auto-picks its own port; ``siva.view_index.ViewIndexServer``
provides the one stable, listable entry point across all live views (see
``server.py``'s ``main()`` and the ``list_views`` / ``view_url`` tools).
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import threading

from .renderer import Renderer, RenderMode
from .view_index import resolve_url

logger = logging.getLogger("siva.renderer")

# Monotonic counter so each TrameRenderer gets a process-unique trame server
# name even if view names repeat (trame caches servers globally by name).
_server_seq = itertools.count()

# The shared loop every TrameRenderer schedules its trame server onto (see
# the module docstring). Created by run_shared_loop(), which server.main()
# calls before constructing any TrameRenderer.
_shared_loop = None
_shared_loop_thread_id = None
_shared_loop_ready = threading.Event()


def run_shared_loop():
    """Run the one shared trame event loop forever (blocks the caller).

    Called by server.main() on the real process main thread. Every
    TrameRenderer constructed afterward schedules its trame server onto
    this loop rather than spinning up its own thread, so every render it
    ever triggers -- including ones wslink issues internally in response
    to browser interaction -- runs on this thread, satisfying Cocoa's
    main-thread requirement on macOS (see module docstring).
    """
    global _shared_loop, _shared_loop_thread_id
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _shared_loop = loop
    _shared_loop_thread_id = threading.get_ident()
    _shared_loop_ready.set()
    loop.run_forever()


def stop_shared_loop():
    """Stop the shared loop (threadsafe), letting ``run_shared_loop()`` return.

    Called by server.main()'s MCP thread after the MCP client disconnects and
    views are torn down; without this the main thread would block in
    ``run_shared_loop()`` forever and the process would never exit.
    """
    loop = _shared_loop
    if loop is not None and not loop.is_closed():
        loop.call_soon_threadsafe(loop.stop)


class TrameRenderer(Renderer):
    """A Renderer backed by a trame ``VtkRemoteView`` served over websocket.

    Constructing a ``TrameRenderer`` schedules its VTK objects and trame
    server onto the shared event loop (see the module docstring and
    ``run_shared_loop()``). The constructor blocks until the server is
    ready (or raises if startup fails).
    """

    def __init__(self, width=1920, height=1080, view_name=None, port=0,
                 host="127.0.0.1", startup_timeout=30):
        # Set up all base-class attributes (mode=TRAME defers VTK creation —
        # see Renderer.__init__ — so nothing touches VTK on the constructing
        # thread; _async_build() builds it on the shared loop below).
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
        self._ready_async = None

        if not _shared_loop_ready.wait(timeout=startup_timeout):
            raise RuntimeError(
                "Trame shared event loop is not running -- server.main() "
                "must call trame_backend.run_shared_loop() before "
                "constructing a TrameRenderer"
            )
        self._owner_thread_id = _shared_loop_thread_id
        self._loop = _shared_loop
        fut = asyncio.run_coroutine_threadsafe(self._async_build(), _shared_loop)
        try:
            fut.result(timeout=startup_timeout)
        except Exception as e:
            raise RuntimeError(
                f"Trame server for view '{view_name}' failed to start: {e}"
            ) from e

    # -- shared loop ---------------------------------------------------------

    async def _async_build(self):
        """Build VTK + wire the trame server on the shared loop.

        Runs as a coroutine on ``_shared_loop`` -- i.e. on the real process
        main thread -- so this setup *and* every render the server triggers
        afterward (including ones wslink issues internally in response to
        browser interaction, which never go through SIVA's own
        render()/dispatch()) happen on the one thread Cocoa allows.
        """
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
                # trame's stillRatio defaults to 1 (CSS-pixel resolution),
                # not scaled by devicePixelRatio, so the browser upscales the
                # image on Retina displays and it looks soft. 2 covers most
                # HiDPI displays. SIVA is a single-user local tool (no real
                # bandwidth/latency constraint), so interactiveRatio/Quality
                # match the still values too -- no resolution/JPEG-quality
                # drop while dragging, at the cost of slower frames during
                # interaction on very large scenes.
                still_ratio=2,
                interactive_ratio=2,
                interactive_quality=100,
            )
        self._server.controller.view_update = self._view.update

        self._ready_async = asyncio.Event()
        # "task" schedules the server onto the already-running shared loop
        # and returns immediately; readiness comes via on_server_ready.
        self._server.start(
            exec_mode="task",
            thread=True,
            port=self._port_req,
            host=self._host,
            open_browser=False,
            show_connection_info=False,
            timeout=0,
        )
        await self._ready_async.wait()

    def _on_ready(self, **_kwargs):
        """Called on the loop thread once the server is listening."""
        self._loop = asyncio.get_running_loop()
        self._port = self._server.port
        self._url, self._proxy_url = resolve_url(self._port)
        logger.info("Trame view '%s' serving at %s", self.view_name, self._url)
        if self._proxy_url:
            logger.info("Trame view '%s' proxied at %s",
                        self.view_name, self._proxy_url)
        self._ready_async.set()

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
        """No-op: present for interface compatibility.

        The event loop is ``run_shared_loop()``, called once by
        ``server.main()`` for the whole process, not per-renderer.
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
        """Tear down VTK and stop the trame server on the shared loop."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return

        async def _teardown_and_stop():
            try:
                Renderer.destroy(self)
            except Exception:  # pragma: no cover - defensive
                logger.debug("VTK teardown failed", exc_info=True)
            try:
                await self._server.stop()
            except Exception:  # pragma: no cover - defensive
                logger.debug("Trame server stop failed", exc_info=True)

        if threading.get_ident() == self._owner_thread_id:
            # Already on the shared loop's own thread -- can't block it
            # waiting on itself; just schedule and let it run.
            asyncio.ensure_future(_teardown_and_stop())
        else:
            try:
                fut = asyncio.run_coroutine_threadsafe(_teardown_and_stop(), loop)
                fut.result(timeout=10)
            except Exception:  # pragma: no cover - defensive
                logger.debug("Trame view teardown failed", exc_info=True)
