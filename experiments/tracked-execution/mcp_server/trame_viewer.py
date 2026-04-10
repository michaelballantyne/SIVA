"""Trame-based browser viewer for tracked execution pipelines.

This module provides a minimal Trame application that serves a 3D VTK view
in the browser, enabling remote use and container deployments where no
display is available.

ARCHITECTURE OVERVIEW
---------------------
Trame runs its own asyncio event loop (via wslink/aiohttp). The MCP server
(FastMCP) also uses asyncio. These cannot share the same event loop directly.

Integration options:
1. Run Trame in a *background thread* (exec_mode="main", thread=True).
   Trame's server.start(thread=True) disables signal handlers so it's safe
   in a non-main thread. This is the approach used here.
2. Run Trame as an asyncio task (exec_mode="task") inside the MCP server's
   event loop. Possible but more invasive — avoided for now.

MULTI-VIEW STRATEGY
-------------------
Trame's VtkRemoteView is bound to a single vtkRenderWindow at construction
time. To support multiple pipeline views (one per pipeline file) we:
  - Create one pv.Plotter per view (each has its own vtkRenderWindow).
  - Show only one view at a time via a tab bar; switching tabs calls
    replace_view() on the single VtkRemoteView widget, which swaps the
    underlying render window without rebuilding the UI.

PUSH MECHANISM
--------------
After the reconciler changes actors (i.e., after pipeline re-execution):
  - Call update_view(name) which calls vtk_view.update() — this pushes a
    fresh screenshot image from the server to all connected browser clients.

SCREENSHOT
----------
Screenshots are captured directly from the PyVista plotter (off-screen
render + screenshot), bypassing the browser entirely. This matches the
existing MCP server screenshot() tool behaviour.

KNOWN LIMITATIONS
-----------------
- VtkRemoteView sends rendered *images* to the browser (JPEG/PNG over
  WebSocket). It does not do client-side GPU rendering. Quality and
  interactivity depend on network latency.
- replace_view() causes a brief visual flicker when switching tabs.
- Trame's event loop and the MCP server's event loop are *separate* asyncio
  loops running in separate threads. State mutations from the MCP thread must
  use server.state.dirty() + server.state.flush() (or trame's @state.change)
  to propagate to clients. Direct mutations from the worker thread are safe
  because trame-server serialises state access.
- Off-screen rendering requires Xvfb (xvfb-run -a) in headless environments.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pyvista as pv

if TYPE_CHECKING:
    from trame_server.core import Server as TrameServer


class TrameViewer:
    """Manages a Trame server with tabbed 3D views.

    Each registered view corresponds to one PyVista plotter (and therefore
    one VTK render window). Views are shown in browser tabs; only one view
    is rendered at a time (the active one).

    Parameters
    ----------
    port : int
        Port for the Trame HTTP/WebSocket server. Default 8080.
    host : str
        Host to bind to. Default "localhost".
    offscreen : bool
        If True, plotters are created off-screen. Should match the MCP
        server's rendering mode. Default True.
    """

    def __init__(self, port: int = 8080, host: str = "localhost", offscreen: bool = True):
        self.port = port
        self.host = host
        self.offscreen = offscreen

        self.plotters: dict[str, pv.Plotter] = {}  # view_name -> plotter
        self._server: TrameServer | None = None
        self._vtk_view = None          # the single VtkRemoteView widget
        self._active_view: str | None = None
        self._server_thread: threading.Thread | None = None
        self._ui_built = False

        # Import trame lazily so importing this module doesn't fail when
        # trame is not installed.
        self._setup_server()

    # ------------------------------------------------------------------
    # Server / UI construction
    # ------------------------------------------------------------------

    def _setup_server(self) -> None:
        """Create the Trame server (does not start the HTTP listener yet)."""
        from trame.app import get_server
        self._server = get_server("tracked-execution-viewer")
        self._server.client_type = "vue3"

        # Enable required client modules.
        from trame.widgets import vtk as vtk_widgets
        from trame.widgets import vuetify3 as vuetify3_widgets
        vtk_widgets.initialize(self._server)
        vuetify3_widgets.initialize(self._server)

        # Initial state.
        self._server.state.views = []
        self._server.state.active_view = ""
        self._server.state.change("active_view")(self._on_active_view_change)

    def _on_active_view_change(self, active_view: str | None = None, **kwargs) -> None:
        """Called by Trame when state.active_view changes (tab switch)."""
        if active_view is None or active_view not in self.plotters:
            return
        self._active_view = active_view
        # Swap the render window in the single VtkRemoteView widget.
        if self._vtk_view is not None:
            plotter = self.plotters[active_view]
            self._vtk_view.replace_view(plotter.render_window)

    def _build_ui(self) -> None:
        """Construct the Trame UI layout (called once, before server starts)."""
        if self._ui_built:
            return
        self._ui_built = True

        from trame.ui.vuetify3 import SinglePageLayout
        from trame.widgets import vtk as vtk_widgets
        from trame.widgets import vuetify3 as vuetify3_widgets

        server = self._server

        with SinglePageLayout(server) as layout:
            layout.title.set_text("Tracked Execution Viewer")
            layout.icon.click = None

            with layout.toolbar:
                # Tab bar for selecting the active view.
                vuetify3_widgets.VSpacer()
                vuetify3_widgets.VTabs(
                    v_model=("active_view", ""),
                    density="compact",
                    children=[
                        # Rendered client-side via v-for on the `views` state list.
                        # We use a raw HTML string via trame's html.RawHtml for simplicity.
                    ],
                )
                # We can't use v-for from Python easily, so we'll handle tab
                # rendering via javascript template. Instead, we provide a
                # simpler dropdown selector.
                vuetify3_widgets.VSpacer()
                vuetify3_widgets.VSelect(
                    v_model=("active_view", ""),
                    items=("views", []),
                    label="Active view",
                    density="compact",
                    hide_details=True,
                    style="max-width: 200px",
                )

            with layout.content:
                # A full-height container for the VTK view.
                with vuetify3_widgets.VContainer(
                    fluid=True,
                    classes="pa-0 fill-height",
                ):
                    # Placeholder: VtkRemoteView is added dynamically when the
                    # first plotter is registered via add_view(). We can't create
                    # it here because we need a render window.
                    #
                    # To work around this, we create a sentinel plotter that is
                    # replaced once a real view is added.
                    if self._active_view is not None and self._active_view in self.plotters:
                        initial_ren_win = self.plotters[self._active_view].render_window
                    else:
                        # Create a minimal off-screen plotter as placeholder.
                        self._placeholder_plotter = pv.Plotter(off_screen=True)
                        initial_ren_win = self._placeholder_plotter.render_window

                    self._vtk_view = vtk_widgets.VtkRemoteView(
                        initial_ren_win,
                        ref="vtk_view",
                        interactive_quality=60,
                        interactive_ratio=1,
                        still_quality=100,
                        still_ratio=1,
                        style="width: 100%; height: 100%;",
                    )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_view(self, name: str, plotter: pv.Plotter) -> None:
        """Register a plotter for a named view.

        Parameters
        ----------
        name : str
            Unique name for this view (e.g. the pipeline filename stem).
        plotter : pv.Plotter
            The PyVista plotter whose render window to display.
        """
        self.plotters[name] = plotter
        self._server.state.views = list(self.plotters.keys())

        if self._active_view is None:
            self._active_view = name
            self._server.state.active_view = name
            # If the UI was already built with a placeholder, swap now.
            if self._vtk_view is not None:
                self._vtk_view.replace_view(plotter.render_window)
        elif self._active_view == name:
            # Same view updated — push fresh image.
            self._push_image()

    def remove_view(self, name: str) -> None:
        """Remove a view and free its Trame registration.

        Does NOT close the underlying plotter — the MCP server owns that.

        Parameters
        ----------
        name : str
            View name to remove.
        """
        if name not in self.plotters:
            return
        del self.plotters[name]
        self._server.state.views = list(self.plotters.keys())

        if self._active_view == name:
            # Switch to first remaining view, or clear.
            remaining = list(self.plotters.keys())
            if remaining:
                new_active = remaining[0]
                self._active_view = new_active
                self._server.state.active_view = new_active
                if self._vtk_view is not None:
                    self._vtk_view.replace_view(self.plotters[new_active].render_window)
            else:
                self._active_view = None
                self._server.state.active_view = ""

    def update_view(self, name: str) -> None:
        """Push a fresh image to browser clients after actors change.

        Call this after the reconciler has updated the scene for *name*.

        Parameters
        ----------
        name : str
            View name whose content has changed.
        """
        if name == self._active_view:
            self._push_image()

    def _push_image(self) -> None:
        """Push the current render of the active view to all clients."""
        if self._vtk_view is not None and self._server is not None:
            self._vtk_view.update()

    def start(self, block: bool = True) -> None:
        """Start the Trame web server.

        Parameters
        ----------
        block : bool
            If True (default), blocks the calling thread until the server
            exits. If False, starts the server in a background thread and
            returns immediately.

        Notes
        -----
        - Trame's event loop is separate from the MCP server's asyncio loop.
        - When block=False, updates from the MCP thread reach Trame clients
          because trame-server uses thread-safe state management.
        - Always call add_view() with at least one plotter before calling
          start(), otherwise the placeholder render window is used.
        """
        self._build_ui()

        url = f"http://{self.host}:{self.port}"
        print(f"Trame viewer starting at {url}")

        if block:
            self._server.start(
                port=self.port,
                host=self.host,
                open_browser=False,
                show_connection_info=True,
                disable_logging=False,
                exec_mode="main",
                timeout=0,
            )
        else:
            self._server_thread = threading.Thread(
                target=self._run_server_blocking,
                daemon=True,
                name="trame-viewer",
            )
            self._server_thread.start()

    def _run_server_blocking(self) -> None:
        """Thread target: run the Trame server (blocks until exit)."""
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            self._server.start(
                port=self.port,
                host=self.host,
                open_browser=False,
                show_connection_info=True,
                disable_logging=False,
                exec_mode="main",
                thread=True,   # disables signal handlers (required in non-main thread)
                timeout=0,
            )
        finally:
            loop.close()

    def stop(self) -> None:
        """Stop the Trame web server (if running in background thread).

        Server.stop() is an async coroutine in trame-server. We schedule it
        in the server's event loop via asyncio.run_coroutine_threadsafe, which
        is safe to call from any thread.
        """
        if self._server is None:
            return
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            # If the server thread owns a loop, try run_coroutine_threadsafe.
            # In practice, the simplest safe approach is to just let the daemon
            # thread exit when the process exits.
            coro = self._server.stop()
            if asyncio.iscoroutine(coro):
                try:
                    asyncio.run_coroutine_threadsafe(coro, loop).result(timeout=2)
                except Exception:
                    # If that fails, schedule and forget — daemon thread exits anyway.
                    pass
        except Exception:
            pass

    @property
    def url(self) -> str:
        """Return the base URL for the Trame viewer."""
        return f"http://{self.host}:{self.port}"

    def is_running(self) -> bool:
        """Return True if the background server thread is alive."""
        if self._server_thread is not None:
            return self._server_thread.is_alive()
        return False
