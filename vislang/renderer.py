"""VTK renderer with interactive (default) or off-screen mode."""

import logging
import queue
import threading
import vtk

logger = logging.getLogger("vislang.renderer")

vtk.vtkObject.GlobalWarningDisplayOff()


class Renderer:
    def __init__(self, width=1920, height=1080, offscreen=False):
        self._offscreen = offscreen
        self._interactor = None
        self._render_window = vtk.vtkRenderWindow()
        self._render_window.SetSize(width, height)

        if offscreen:
            self._render_window.SetOffScreenRendering(True)
        else:
            self._render_window.SetWindowName("VisLang")

        self._renderer = vtk.vtkRenderer()
        self._renderer.SetBackground(0.15, 0.15, 0.2)
        self._render_window.AddRenderer(self._renderer)

        # Add a light kit for better default illumination
        self._light_kit = vtk.vtkLightKit()
        self._light_kit.AddLightsToRenderer(self._renderer)

        # Thread-dispatch queue for interactive mode
        self._work_queue = queue.Queue()
        self._main_thread_id = threading.get_ident()

        if not offscreen:
            self._interactor = vtk.vtkRenderWindowInteractor()
            self._interactor.SetRenderWindow(self._render_window)
            self._interactor.SetInteractorStyle(
                vtk.vtkInteractorStyleTrackballCamera()
            )
            self._interactor.Initialize()

        self._actors = {}  # name -> vtkActor

    def run_on_main_thread(self, fn):
        """Run fn on the main thread. If already on main thread, run directly.
        Otherwise queue it and block until complete."""
        if self._offscreen or threading.get_ident() == self._main_thread_id:
            return fn()
        logger.debug("Queuing work to main thread")
        result_queue = queue.Queue()
        self._work_queue.put((fn, result_queue))
        ok, result = result_queue.get()
        if ok:
            logger.debug("Main-thread work completed")
            return result
        else:
            logger.error("Main-thread work raised: %s", result)
            raise result

    def run_event_loop(self):
        """Process VTK events and queued work in a loop (blocks). Call from main thread."""
        import time

        if not self._interactor:
            return
        self._main_thread_id = threading.get_ident()
        self._render_window.Render()
        while True:
            # Drain work queue
            while not self._work_queue.empty():
                try:
                    fn, result_queue = self._work_queue.get_nowait()
                    try:
                        result = fn()
                        result_queue.put((True, result))
                    except Exception as e:
                        result_queue.put((False, e))
                except queue.Empty:
                    break
            self._interactor.ProcessEvents()
            time.sleep(0.016)  # ~60 fps

    def clear(self):
        for actor in self._actors.values():
            self._renderer.RemoveActor(actor)
        self._actors.clear()

    def add_actor(self, name, actor):
        if name in self._actors:
            self._renderer.RemoveActor(self._actors[name])
        self._actors[name] = actor
        self._renderer.AddActor(actor)

    def remove_actor(self, name):
        if name in self._actors:
            self._renderer.RemoveActor(self._actors[name])
            del self._actors[name]

    def set_camera(self, position=None, focal_point=None, up=None, zoom=None):
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
        cam = self._renderer.GetActiveCamera()
        return {
            "position": list(cam.GetPosition()),
            "focal_point": list(cam.GetFocalPoint()),
            "up": list(cam.GetViewUp()),
        }

    def set_background(self, r, g, b):
        self._renderer.SetBackground(r, g, b)

    def reset_camera(self):
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

    def render(self):
        self._render_window.Render()

    def screenshot(self, path="screenshot.png"):
        self.render()
        w2i = vtk.vtkWindowToImageFilter()
        w2i.SetInput(self._render_window)
        w2i.SetInputBufferTypeToRGBA()
        w2i.ReadFrontBufferOff()
        w2i.Update()

        writer = vtk.vtkPNGWriter()
        writer.SetFileName(path)
        writer.SetInputConnection(w2i.GetOutputPort())
        writer.Write()
        return path
