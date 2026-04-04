"""
Visualization of wind vortices driving lateral fire spread (VLS)
in wildfire simulation data from HIGRAD/FIRETEC.

Reads a .vts structured grid with u, v, w velocity components and theta
(potential temperature), computes vorticity, and renders:
  1. Terrain surface colored by fuel density (rhof_1)
  2. Fire region via theta isosurface (hot gas)
  3. Vortex structures via vorticity magnitude isosurfaces

Usage: python visualize_vortices.py [output.30000.vts]
"""

import sys
import vtk


def build_pipeline(filename):
    # --- Read data ---
    reader = vtk.vtkXMLStructuredGridReader()
    reader.SetFileName(filename)
    reader.Update()
    grid = reader.GetOutput()

    dims = [0, 0, 0]
    grid.GetDimensions(dims)
    print(f"Grid dimensions: {dims}")

    # --- Merge u, v, w into a velocity vector field ---
    calc = vtk.vtkArrayCalculator()
    calc.SetInputData(grid)
    calc.AddScalarArrayName("u")
    calc.AddScalarArrayName("v")
    calc.AddScalarArrayName("w")
    calc.SetFunction("u*iHat + v*jHat + w*kHat")
    calc.SetResultArrayName("velocity")
    calc.SetResultArrayType(vtk.VTK_FLOAT)
    calc.Update()

    # Set velocity as the active vectors
    calc.GetOutput().GetPointData().SetActiveVectors("velocity")

    # --- Compute vorticity (curl of velocity) ---
    vorticity = vtk.vtkCellDerivatives()
    vorticity.SetInputConnection(calc.GetOutputPort())
    vorticity.SetVectorModeToComputeVorticity()
    vorticity.SetTensorModeToPassTensors()
    vorticity.Update()

    # Convert cell data to point data for smoother visualization
    c2p = vtk.vtkCellDataToPointData()
    c2p.SetInputConnection(vorticity.GetOutputPort())
    c2p.Update()

    # Compute vorticity magnitude
    vort_mag = vtk.vtkArrayCalculator()
    vort_mag.SetInputConnection(c2p.GetOutputPort())
    vort_mag.AddVectorArrayName("Vorticity")
    vort_mag.SetFunction("mag(Vorticity)")
    vort_mag.SetResultArrayName("VorticityMagnitude")
    vort_mag.Update()

    return reader, calc, vort_mag


def create_terrain_actor(reader):
    """Extract bottom surface (k=0) and color by fuel density."""
    grid = reader.GetOutput()

    # Extract the bottom slice of the structured grid
    extract = vtk.vtkExtractGrid()
    extract.SetInputConnection(reader.GetOutputPort())
    dims = [0, 0, 0]
    grid.GetDimensions(dims)
    extract.SetVOI(0, dims[0] - 1, 0, dims[1] - 1, 0, 0)
    extract.Update()

    mapper = vtk.vtkDataSetMapper()
    mapper.SetInputConnection(extract.GetOutputPort())
    mapper.SetScalarModeToUsePointFieldData()
    mapper.SelectColorArray("rhof_1")
    mapper.SetScalarRange(0.0, 0.6)

    # Green-brown LUT for vegetation
    lut = vtk.vtkLookupTable()
    lut.SetNumberOfTableValues(256)
    lut.SetHueRange(0.1, 0.33)  # brown to green
    lut.SetSaturationRange(0.4, 0.8)
    lut.SetValueRange(0.1, 0.4)  # keep it dark
    lut.Build()
    mapper.SetLookupTable(lut)

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    return actor


def create_fire_actor(vort_mag_filter):
    """Isosurface of theta to show fire/flame regions."""
    # Extract theta > 400K as fire region
    thresh = vtk.vtkContourFilter()
    thresh.SetInputConnection(vort_mag_filter.GetOutputPort())
    thresh.SetInputArrayToProcess(0, 0, 0,
                                  vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS,
                                  "theta")
    thresh.SetValue(0, 450.0)  # flame threshold
    thresh.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(thresh.GetOutputPort())
    mapper.ScalarVisibilityOff()

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(1.0, 0.3, 0.05)  # orange-red
    actor.GetProperty().SetOpacity(0.4)
    return actor


def create_vortex_actors(vort_mag_filter):
    """Isosurfaces of vorticity magnitude to show vortex tubes."""
    actors = []

    # We'll show two iso-levels: strong and moderate vortices
    levels = [
        (3.0, (0.2, 0.6, 1.0), 0.5),   # moderate vorticity: blue, semi-transparent
        (5.0, (0.9, 0.2, 0.9), 0.7),    # strong vorticity: magenta, more opaque
    ]

    for value, color, opacity in levels:
        contour = vtk.vtkContourFilter()
        contour.SetInputConnection(vort_mag_filter.GetOutputPort())
        contour.SetInputArrayToProcess(0, 0, 0,
                                        vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS,
                                        "VorticityMagnitude")
        contour.SetValue(0, value)
        contour.Update()

        # Smooth the isosurface
        smoother = vtk.vtkWindowedSincPolyDataFilter()
        smoother.SetInputConnection(contour.GetOutputPort())
        smoother.SetNumberOfIterations(15)
        smoother.BoundarySmoothingOff()
        smoother.FeatureEdgeSmoothingOff()
        smoother.SetFeatureAngle(120.0)
        smoother.SetPassBand(0.001)
        smoother.NonManifoldSmoothingOn()
        smoother.NormalizeCoordinatesOn()
        smoother.Update()

        mapper = vtk.vtkPolyDataMapper()
        mapper.SetInputConnection(smoother.GetOutputPort())
        mapper.ScalarVisibilityOff()

        actor = vtk.vtkActor()
        actor.SetMapper(mapper)
        actor.GetProperty().SetColor(*color)
        actor.GetProperty().SetOpacity(opacity)
        actor.GetProperty().SetSpecular(0.3)
        actor.GetProperty().SetSpecularPower(20)
        actors.append(actor)

    return actors


def create_wind_arrows(calc_filter, reader):
    """Glyph arrows showing wind direction on a sampled subset."""
    grid = reader.GetOutput()
    dims = [0, 0, 0]
    grid.GetDimensions(dims)

    # Subsample to avoid overwhelming the scene
    extract = vtk.vtkExtractGrid()
    extract.SetInputConnection(calc_filter.GetOutputPort())
    # Sample every 30th point in x,y and take a few z levels near the ground
    extract.SetSampleRate(30, 30, 10)
    extract.SetVOI(0, dims[0] - 1, 0, dims[1] - 1, 0, 20)  # lower atmosphere
    extract.Update()

    arrow = vtk.vtkArrowSource()
    arrow.SetTipResolution(8)
    arrow.SetShaftResolution(8)

    glyph = vtk.vtkGlyph3D()
    glyph.SetInputConnection(extract.GetOutputPort())
    glyph.SetSourceConnection(arrow.GetOutputPort())
    glyph.SetVectorModeToUseVector()
    glyph.SetInputArrayToProcess(1, 0, 0,
                                  vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS,
                                  "velocity")
    glyph.SetScaleModeToScaleByVector()
    glyph.SetScaleFactor(3.0)
    glyph.OrientOn()
    glyph.Update()

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputConnection(glyph.GetOutputPort())
    mapper.ScalarVisibilityOff()

    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetColor(0.9, 0.9, 0.9)
    actor.GetProperty().SetOpacity(0.5)
    return actor


def main():
    filename = sys.argv[1] if len(sys.argv) > 1 else "output.30000.vts"
    print(f"Loading {filename}...")

    reader, calc, vort_mag = build_pipeline(filename)

    # Check vorticity range to pick good iso-levels
    vm_arr = vort_mag.GetOutput().GetPointData().GetArray("VorticityMagnitude")
    r = [0, 0]
    vm_arr.GetRange(r)
    print(f"Vorticity magnitude range: {r[0]:.3f} to {r[1]:.3f}")

    # --- Create renderer ---
    renderer = vtk.vtkRenderer()
    renderer.SetBackground(0.15, 0.15, 0.2)  # dark blue-gray

    # Add terrain
    print("Creating terrain...")
    terrain = create_terrain_actor(reader)
    renderer.AddActor(terrain)

    # Add fire isosurface
    print("Creating fire isosurface...")
    fire = create_fire_actor(vort_mag)
    renderer.AddActor(fire)

    # Add vortex isosurfaces
    print("Creating vortex isosurfaces...")
    vortex_actors = create_vortex_actors(vort_mag)
    for a in vortex_actors:
        renderer.AddActor(a)

    # Add wind arrows
    print("Creating wind arrows...")
    arrows = create_wind_arrows(calc, reader)
    renderer.AddActor(arrows)

    # --- Camera setup ---
    # Position to see the leeward slope and lateral spread
    camera = renderer.GetActiveCamera()
    camera.SetPosition(800, -600, 600)
    camera.SetFocalPoint(100, 0, 100)
    camera.SetViewUp(0, 0, 1)
    renderer.ResetCamera()
    camera.Zoom(1.2)

    # --- Light ---
    light = vtk.vtkLight()
    light.SetPosition(500, -500, 800)
    light.SetFocalPoint(0, 0, 0)
    light.SetColor(1.0, 0.95, 0.9)
    light.SetIntensity(0.8)
    renderer.AddLight(light)

    # --- Render window ---
    render_window = vtk.vtkRenderWindow()
    render_window.AddRenderer(renderer)
    render_window.SetSize(1600, 1000)
    render_window.SetWindowName("Vorticity-Driven Lateral Spread - Wildfire Simulation")

    # --- Interactor ---
    interactor = vtk.vtkRenderWindowInteractor()
    interactor.SetRenderWindow(render_window)

    style = vtk.vtkInteractorStyleTrackballCamera()
    interactor.SetInteractorStyle(style)

    # Add text annotation
    text = vtk.vtkTextActor()
    text.SetInput("Vorticity-Driven Lateral Spread\nt = 300s | Blue: moderate vorticity | Magenta: strong vorticity | Orange: fire")
    text.GetTextProperty().SetFontSize(16)
    text.GetTextProperty().SetColor(1, 1, 1)
    text.SetPosition(10, 10)
    renderer.AddActor2D(text)

    # Add scalar bar for terrain
    scalar_bar = vtk.vtkScalarBarActor()
    scalar_bar.SetLookupTable(terrain.GetMapper().GetLookupTable())
    scalar_bar.SetTitle("Fuel Density (kg/m³)")
    scalar_bar.SetNumberOfLabels(4)
    scalar_bar.SetWidth(0.08)
    scalar_bar.SetHeight(0.3)
    scalar_bar.SetPosition(0.9, 0.05)
    scalar_bar.GetTitleTextProperty().SetFontSize(12)
    scalar_bar.GetTitleTextProperty().SetColor(1, 1, 1)
    scalar_bar.GetLabelTextProperty().SetColor(1, 1, 1)
    renderer.AddActor2D(scalar_bar)

    print("Rendering... (close window or press 'q' to quit)")
    render_window.Render()
    interactor.Start()


if __name__ == "__main__":
    main()
