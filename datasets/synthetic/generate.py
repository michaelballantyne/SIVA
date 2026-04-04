#!/usr/bin/env python3
"""Generate a synthetic 64x64x64 VTK ImageData dataset.

Creates three fields on a regular grid:
  - "temperature" (scalar): Gaussian blob centered in the domain
  - "density" (scalar): linear gradient along the Z axis
  - "velocity" (vector): rigid-body rotation about the Z axis

Output: datasets/synthetic/data/output.vti
"""

import os
import math

import numpy as np
import vtk
from vtk.util.numpy_support import numpy_to_vtk

N = 64  # grid points per axis
SPACING = 1.0 / (N - 1)  # unit cube [0, 1]^3


def make_coords():
    """Return (N, N, N, 3) array of point positions."""
    lin = np.linspace(0.0, 1.0, N)
    x, y, z = np.meshgrid(lin, lin, lin, indexing="ij")
    return x, y, z


def make_temperature(x, y, z):
    """Gaussian blob centred at (0.5, 0.5, 0.5), peak = 1000."""
    sigma = 0.15
    r2 = (x - 0.5) ** 2 + (y - 0.5) ** 2 + (z - 0.5) ** 2
    return (1000.0 * np.exp(-r2 / (2.0 * sigma ** 2))).ravel()


def make_density(x, y, z):
    """Linear gradient: 0 at z=0, 1.225 at z=1 (rough air-density scale)."""
    return (1.225 * z).ravel()


def make_velocity(x, y, z):
    """Rigid-body rotation about the Z axis, centred at (0.5, 0.5).

    v = omega x r  with omega = (0, 0, 2*pi), giving one full rotation
    per unit time.  The result is a divergence-free 2-D swirl with vz = 0.
    """
    omega = 2.0 * math.pi
    vx = -omega * (y - 0.5)
    vy = omega * (x - 0.5)
    vz = np.zeros_like(x)
    vel = np.column_stack([vx.ravel(), vy.ravel(), vz.ravel()])
    return vel


def build_image_data():
    img = vtk.vtkImageData()
    img.SetDimensions(N, N, N)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(SPACING, SPACING, SPACING)

    x, y, z = make_coords()

    # --- temperature (scalar) ---
    temp = numpy_to_vtk(make_temperature(x, y, z), deep=True)
    temp.SetName("temperature")
    img.GetPointData().AddArray(temp)

    # --- density (scalar) ---
    dens = numpy_to_vtk(make_density(x, y, z), deep=True)
    dens.SetName("density")
    img.GetPointData().AddArray(dens)

    # --- velocity (vector) ---
    vel = numpy_to_vtk(make_velocity(x, y, z), deep=True)
    vel.SetName("velocity")
    vel.SetNumberOfComponents(3)
    img.GetPointData().AddArray(vel)

    # Set a default active scalar so VTK picks it up automatically.
    img.GetPointData().SetActiveScalars("temperature")
    img.GetPointData().SetActiveVectors("velocity")

    return img


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(here, "data")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "output.vti")

    img = build_image_data()

    writer = vtk.vtkXMLImageDataWriter()
    writer.SetFileName(out_path)
    writer.SetInputData(img)
    writer.SetCompressorTypeToZLib()
    writer.Write()

    size_mb = os.path.getsize(out_path) / (1024 * 1024)
    print(f"Wrote {out_path}  ({size_mb:.1f} MB)")
    print(f"  Dimensions : {N} x {N} x {N}")
    print(f"  Arrays     : temperature (scalar), density (scalar), velocity (vector)")


if __name__ == "__main__":
    main()
