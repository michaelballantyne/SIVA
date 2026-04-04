"""Headless VTK renderer for off-screen rendering."""

import vtk


class Renderer:
    def __init__(self, width=1920, height=1080):
        self._render_window = vtk.vtkRenderWindow()
        self._render_window.SetOffScreenRendering(True)
        self._render_window.SetSize(width, height)

        self._renderer = vtk.vtkRenderer()
        self._renderer.SetBackground(0.15, 0.15, 0.2)
        self._render_window.AddRenderer(self._renderer)

        self._actors = {}  # name -> vtkActor

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
