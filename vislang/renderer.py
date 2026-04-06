"""VTK renderer with interactive (default) or off-screen mode."""

import enum
import logging
import queue
import threading
import vtk

logger = logging.getLogger("vislang.renderer")

vtk.vtkObject.GlobalWarningDisplayOff()


class RenderMode(enum.Enum):
    """How the renderer manages threading and display.

    OFFSCREEN: No window, no event loop. Everything runs inline on the
        calling thread. Fast and simple — used by headless agents.
    INTERACTIVE: Opens a window, runs an event loop on the main thread,
        dispatches work via a shared queue. Production mode for humans.
    HEADLESS_INTERACTIVE: Offscreen rendering but with interactive-mode
        threading (event loop + work queue). For testing the threading
        path without a display.
    """
    OFFSCREEN = "offscreen"
    INTERACTIVE = "interactive"
    HEADLESS_INTERACTIVE = "headless_interactive"


# Shared work queue for interactive mode: all Renderer instances post here,
# and the single event-loop thread drains it.
_shared_work_queue = queue.Queue()
_main_thread_id = threading.get_ident()  # updated by run_event_loop()

# Provider function that returns any initialized interactor for event pumping.
# Set by the server layer via set_interactor_provider().
_interactor_provider = None


def set_interactor_provider(fn):
    """Register a callable that returns any available vtkRenderWindowInteractor.

    The event loop calls this each iteration to find an interactor for
    pumping OS events.  On macOS/Cocoa, any single interactor drains the
    global event queue, so it handles events for all windows.
    """
    global _interactor_provider
    _interactor_provider = fn


def _get_any_interactor():
    if _interactor_provider:
        return _interactor_provider()
    return None


class Renderer:
    def __init__(self, width=640, height=800, mode=RenderMode.INTERACTIVE):
        self._mode = mode
        self._width = width
        self._height = height
        self._interactor = None
        self._render_window = None
        self._renderer = None
        self._light_kit = None
        self._initialized = False

        self._actors = {}  # name -> vtkActor or vtkVolume (3D geometry only)
        self._overlay_actors = []  # list of vtkProp2D (title, etc.) added via AddViewProp
        self._overlays = {}  # name -> vtkProp2D (scalar bars, named 2D overlays)
        # No window to show — initialize immediately
        if mode != RenderMode.INTERACTIVE:
            self._ensure_initialized()

    def _ensure_initialized(self):
        """Lazily create the VTK window and renderer on first use."""
        if self._initialized:
            return
        self._initialized = True

        self._render_window = vtk.vtkRenderWindow()
        self._render_window.SetSize(self._width, self._height)

        if self._mode == RenderMode.INTERACTIVE:
            self._render_window.SetWindowName("VisLang")
        else:
            self._render_window.SetOffScreenRendering(True)

        self._renderer = vtk.vtkRenderer()
        self._renderer.SetBackground(0.15, 0.15, 0.2)
        self._render_window.AddRenderer(self._renderer)

        self._light_kit = vtk.vtkLightKit()
        self._light_kit.AddLightsToRenderer(self._renderer)

        if self._mode != RenderMode.OFFSCREEN:
            self._interactor = vtk.vtkRenderWindowInteractor()
            self._interactor.SetRenderWindow(self._render_window)
            self._interactor.SetInteractorStyle(
                vtk.vtkInteractorStyleTrackballCamera()
            )
            self._interactor.Initialize()

    def run_on_main_thread(self, fn):
        """Run fn on the main thread. If already on main thread, run directly.
        Otherwise queue it via the shared work queue and block until complete."""
        global _main_thread_id
        if self._mode == RenderMode.OFFSCREEN or threading.get_ident() == _main_thread_id:
            return fn()
        logger.debug("Queuing work to main thread")
        result_queue = queue.Queue()
        _shared_work_queue.put((fn, result_queue))
        ok, result = result_queue.get()
        if ok:
            logger.debug("Main-thread work completed")
            return result
        else:
            logger.error("Main-thread work raised: %s", result)
            raise result

    def run_event_loop(self):
        """Process VTK events and queued work in a loop (blocks). Call from main thread.

        Uses get_any_interactor (set via set_interactor_provider) to find any
        initialized interactor for pumping OS events.  On macOS/Cocoa,
        ProcessEvents() drains the global NSApplication event queue, so any
        single interactor handles events for all windows.
        """
        import time

        global _main_thread_id
        _main_thread_id = threading.get_ident()
        while True:
            # Drain shared work queue (serves all Renderer instances)
            while not _shared_work_queue.empty():
                try:
                    fn, result_queue = _shared_work_queue.get_nowait()
                    try:
                        result = fn()
                        result_queue.put((True, result))
                    except Exception as e:
                        result_queue.put((False, e))
                except queue.Empty:
                    break
            # Pump OS events via any available interactor
            interactor = _get_any_interactor()
            if interactor:
                interactor.ProcessEvents()
            time.sleep(0.016)  # ~60 fps

    def clear(self):
        self._ensure_initialized()
        for name, item in self._actors.items():
            if isinstance(item, vtk.vtkVolume):
                self._renderer.RemoveVolume(item)
            else:
                self._renderer.RemoveActor(item)
        self._actors.clear()
        for actor2d in self._overlay_actors:
            self._renderer.RemoveViewProp(actor2d)
        self._overlay_actors.clear()
        for actor2d in self._overlays.values():
            self._renderer.RemoveViewProp(actor2d)
        self._overlays.clear()

    def add_overlay_actor(self, actor2d):
        """Add a 2D overlay actor (e.g. vtkTextActor) that will be removed on clear().

        Unlike add_actor(), overlay actors are not keyed by name — they are
        accumulated in a list and cleared as a group during pipeline rebuild.
        This is appropriate for pipeline-generated overlays such as title text.
        Annotations have their own lifecycle managed by clear_annotations().
        """
        self._ensure_initialized()
        self._overlay_actors.append(actor2d)
        self._renderer.AddViewProp(actor2d)

    def add_overlay(self, name, actor2d):
        """Add a named 2D overlay actor (e.g. vtkScalarBarActor) to the scene.

        Named overlays are stored in _overlays (separate from 3D _actors) so
        that code iterating _actors for bounds or geometry never encounters 2D
        actors.  Replaces any existing overlay with the same name.  Removed on
        clear().
        """
        self._ensure_initialized()
        if name in self._overlays:
            self._renderer.RemoveViewProp(self._overlays[name])
        self._overlays[name] = actor2d
        self._renderer.AddViewProp(actor2d)

    def destroy(self):
        """Tear down the render window and all VTK resources.

        After this call the Renderer is unusable. Used by close_view() to
        actually close the OS window rather than leaving it open and dead.
        """
        if not self._initialized:
            return
        self.clear()
        if self._render_window:
            self._render_window.Finalize()
            self._render_window.RemoveRenderer(self._renderer)
            self._render_window = None
        # Pump events after Finalize so the OS processes the window close.
        # On macOS/Cocoa, ProcessEvents drains the global event queue.
        if self._interactor:
            self._interactor.ProcessEvents()
            self._interactor.SetRenderWindow(None)
            self._interactor = None
        self._renderer = None
        self._light_kit = None
        self._initialized = False

    def add_actor(self, name, actor):
        self._ensure_initialized()
        if name in self._actors:
            old = self._actors[name]
            if isinstance(old, vtk.vtkVolume):
                self._renderer.RemoveVolume(old)
            else:
                self._renderer.RemoveActor(old)
        self._actors[name] = actor
        self._renderer.AddActor(actor)

    def add_volume(self, name, volume):
        """Add a vtkVolume to the renderer, replacing any existing item with this name."""
        self._ensure_initialized()
        if name in self._actors:
            old = self._actors[name]
            if isinstance(old, vtk.vtkVolume):
                self._renderer.RemoveVolume(old)
            else:
                self._renderer.RemoveActor(old)
        self._actors[name] = volume
        self._renderer.AddVolume(volume)

    def remove_actor(self, name):
        self._ensure_initialized()
        if name in self._actors:
            item = self._actors[name]
            if isinstance(item, vtk.vtkVolume):
                self._renderer.RemoveVolume(item)
            else:
                self._renderer.RemoveActor(item)
            del self._actors[name]

    def set_camera(self, position=None, focal_point=None, up=None, zoom=None):
        self._ensure_initialized()
        cam = self._renderer.GetActiveCamera()
        if position is not None:
            cam.SetPosition(*position)
        if focal_point is not None:
            cam.SetFocalPoint(*focal_point)
        if up is not None:
            cam.SetViewUp(*up)
        if zoom is not None:
            cam.Zoom(zoom)
        self._renderer.ResetCameraClippingRange()

    def get_camera_state(self):
        self._ensure_initialized()
        cam = self._renderer.GetActiveCamera()
        return {
            "position": list(cam.GetPosition()),
            "focal_point": list(cam.GetFocalPoint()),
            "up": list(cam.GetViewUp()),
        }

    def set_background(self, r, g, b):
        self._ensure_initialized()
        self._renderer.SetBackground(r, g, b)

    def reset_camera(self):
        self._ensure_initialized()
        self._renderer.ResetCamera()

    def suggest_camera(self, style="overview"):
        """Compute a good camera position based on visible actors.

        Styles:
          overview - elevated oblique view of the whole scene
          closeup - close to the center of all actors
          top_down - bird's eye view looking down
          side - side view from the south
        """
        # Get combined bounds of all actors
        if not self._actors:
            return None

        xmin = ymin = zmin = float("inf")
        xmax = ymax = zmax = float("-inf")
        for actor in self._actors.values():
            b = actor.GetBounds()
            # Skip actors with invalid bounds (safety guard)
            if b is None or any(abs(v) > 1e10 for v in b):
                continue
            xmin = min(xmin, b[0])
            xmax = max(xmax, b[1])
            ymin = min(ymin, b[2])
            ymax = max(ymax, b[3])
            zmin = min(zmin, b[4])
            zmax = max(zmax, b[5])

        cx = (xmin + xmax) / 2
        cy = (ymin + ymax) / 2
        cz = (zmin + zmax) / 2
        dx = xmax - xmin
        dy = ymax - ymin
        dz = zmax - zmin
        extent = max(dx, dy, dz)

        if style == "overview":
            return {
                "position": (cx, cy - extent * 0.8, cz + extent * 0.6),
                "focal_point": (cx, cy, cz),
                "up": (0, 0, 1),
            }
        elif style == "closeup":
            return {
                "position": (cx + extent * 0.3, cy - extent * 0.3, cz + extent * 0.25),
                "focal_point": (cx, cy, cz),
                "up": (0, 0, 1),
            }
        elif style == "top_down":
            return {
                "position": (cx, cy, cz + extent),
                "focal_point": (cx, cy, cz),
                "up": (0, 1, 0),
            }
        elif style == "side":
            return {
                "position": (cx, cy - extent, cz),
                "focal_point": (cx, cy, cz),
                "up": (0, 0, 1),
            }
        return None

    def is_window_closed(self) -> bool:
        """Return True if the OS window was closed by the user.

        Only meaningful in INTERACTIVE mode — in OFFSCREEN and
        HEADLESS_INTERACTIVE modes there is no real OS window, so this
        always returns False.

        Detection relies on vtkRenderWindow.GetMapped(): after the OS
        window is created via Initialize()/Render() it returns 1; if the
        user closes the window (or the OS destroys it) VTK calls
        Finalize() internally and GetMapped() drops back to 0.

        Returns False when:
          - mode is not INTERACTIVE (offscreen / headless)
          - the renderer has never been initialized (window not yet shown)
          - the renderer was explicitly destroyed via destroy()
        """
        if self._mode != RenderMode.INTERACTIVE:
            return False
        if not self._initialized or self._render_window is None:
            return False
        return self._render_window.GetMapped() == 0

    def render(self):
        self._ensure_initialized()
        self._render_window.Render()

    def screenshot(self, path="screenshot.png"):
        self._ensure_initialized()
        self.render()
        w2i = vtk.vtkWindowToImageFilter()
        w2i.SetInput(self._render_window)
        w2i.SetInputBufferTypeToRGB()
        w2i.ReadFrontBufferOff()
        w2i.Update()

        writer = vtk.vtkPNGWriter()
        writer.SetFileName(path)
        writer.SetInputConnection(w2i.GetOutputPort())
        writer.Write()
        return path
