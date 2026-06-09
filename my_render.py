import os

# For headless server
os.environ['PYVISTA_OFF_SCREEN'] = 'true'
os.environ['PYVISTA_USE_PANEL'] = '0'

import pyvista as pv
import numpy as np

pv.set_jupyter_backend('trame')
pv.global_theme.jupyter_backend = 'trame'

try:
    pv.start_xvfb()  # Virtual display for servers
except:
    # No Xvfb — tell VTK to use EGL (GPU headless, common on compute nodes)
    os.environ.setdefault('VTK_DEFAULT_OPENGL_WINDOW', 'vtkEGLRenderWindow')


# Use heuristics to detect x, y, z coordinates
# Args:
#     data_dict: dict with variable names as keys, numpy arrays as values
# Returns:
#     tuple: (x_var, y_var, z_var) or None if not found
def detect_positions(data_dict):
    keys = list(data_dict.keys())
    
    # Exact matches
    if 'x' in keys and 'y' in keys and 'z' in keys:
        return ('x', 'y', 'z')
    
    # Case-insensitive
    keys_lower = {k.lower(): k for k in keys}
    if 'x' in keys_lower and 'y' in keys_lower and 'z' in keys_lower:
        return (keys_lower['x'], keys_lower['y'], keys_lower['z'])
    
    # Common patterns probably
    patterns = [
        ('X', 'Y', 'Z'),
        ('pos_x', 'pos_y', 'pos_z'),
        ('position_x', 'position_y', 'position_z'),
        ('px', 'py', 'pz'),
    ]
    
    for px, py, pz in patterns:
        if px in keys and py in keys and pz in keys:
            return (px, py, pz)
    
    # Variables with "x", "y", "z" in name (if unique)
    x_candidates = [k for k in keys if 'x' in k.lower()]
    y_candidates = [k for k in keys if 'y' in k.lower()]
    z_candidates = [k for k in keys if 'z' in k.lower()]
    
    if len(x_candidates) == 1 and len(y_candidates) == 1 and len(z_candidates) == 1:
        return (x_candidates[0], y_candidates[0], z_candidates[0])
    
    return None

# Interactive prompt in Jupyter for user to select position variables
# Args:
#     available_vars: list of variable names
# Returns:
#     tuple: (x_var, y_var, z_var)
def prompt_user_for_positions(available_vars):
    print("\n" + "="*60)
    print("POSITION VARIABLE SELECTION")
    print("="*60)
    print("\nAvailable variables:")
    for i, var in enumerate(available_vars, 1):
        print(f"  {i:2d}. {var}")
    
    print("\nEnter the variable names for spatial coordinates:")
    
    while True:
        x_var = input("  X coordinate: ").strip()
        if x_var in available_vars:
            break
        print(f"'{x_var}' not found. Try again.")
    
    while True:
        y_var = input("  Y coordinate: ").strip()
        if y_var in available_vars:
            break
        print(f"'{y_var}' not found. Try again.")
    
    while True:
        z_var = input("  Z coordinate: ").strip()
        if z_var in available_vars:
            break
        print(f"'{z_var}' not found. Try again.")
    
    print(f"\n✓ Using: x={x_var}, y={y_var}, z={z_var}")
    print("="*60 + "\n")
    
    return (x_var, y_var, z_var)

_VOL_RENDER_RES = 64  # downsample target per axis; k3d sends binary so this is fine

_CMAP_NAMES = ['viridis', 'plasma', 'inferno', 'magma', 'hot',
               'Blues', 'Reds', 'Greens', 'Purples', 'YlOrBr', 'cividis', 'BuGn', 'RdPu']


def _k3d_colormap(name):
    """Convert a matplotlib colormap to the flat [t,r,g,b,...] format k3d expects."""
    import matplotlib.cm as cm
    cmap = cm.get_cmap(name)
    t = np.linspace(0, 1, 256, dtype=np.float32)
    rgba = cmap(t).astype(np.float32)
    result = np.empty(256 * 4, dtype=np.float32)
    result[0::4] = t
    result[1::4] = rgba[:, 0]
    result[2::4] = rgba[:, 1]
    result[3::4] = rgba[:, 2]
    return result

# Render all 3D fields via k3d WebGL — binary transfer, interactive in browser,
# no server-side OpenGL needed.
def _render_volumetric(loaded_dataset):
    import k3d

    data = loaded_dataset.data
    vol_vars = [k for k, v in data.items() if v.ndim == 3]
    if not vol_vars:
        print("ERROR: No 3D arrays found for volumetric rendering!")
        return

    n = len(vol_vars)
    print(f"\nRendering {n} volumetric field(s) via k3d at {_VOL_RENDER_RES}³/field...")

    # Sigmoid-shaped opacity: low values transparent, high values opaque
    opacity_fn = np.array([0.0, 0.0, 0.2, 0.0, 0.5, 0.1, 0.8, 0.5, 1.0, 0.9],
                          dtype=np.float32)

    plot = k3d.plot(height=600)

    for i, var in enumerate(vol_vars):
        field = data[var].astype(np.float32)
        step = max(1, field.shape[0] // _VOL_RENDER_RES)
        ds = np.ascontiguousarray(field[::step, ::step, ::step])
        print(f"  • {var}  {field.shape} → {ds.shape}")

        # log-scale so large dynamic-range fields (density, tau) aren't washed out
        ds_log = np.log10(np.abs(ds) + 1)
        ds_log = np.nan_to_num(ds_log, nan=0.0, posinf=0.0, neginf=0.0)

        vol = k3d.volume(
            ds_log,
            color_map=_k3d_colormap(_CMAP_NAMES[i % len(_CMAP_NAMES)]),
            color_range=[float(ds_log.min()), float(ds_log.max())],
            opacity_function=opacity_fn,
            name=var.split('/')[-1],
        )
        plot += vol
        print(f"    ✓ added")

    plot.display()
    print(f"\n✓ All {n} field(s) rendered — rotate/zoom in the widget above")


# Render loaded dataset using PyVista + Trame (server-compatible)
# Args:
#     loaded_dataset: DatasetInfo object (returned from load() function)
#     subsample_factor: downsample particles by this factor for point cloud
#     grid_size: resolution of density grids (grid_size^3 voxels)
def render(loaded_dataset, subsample_factor=30, grid_size=128):
    print("="*60)
    print("RENDERING DATASET (Server Mode)")
    print("="*60)

    # Check if data is loaded
    if not loaded_dataset.loaded:
        print("ERROR: Data not loaded!")
        print("   Please call load(dataset) first.")
        return

    if not loaded_dataset.data:
        print("ERROR: No data arrays found in dataset!")
        return

    data = loaded_dataset.data

    # Route volumetric (3D) HDF5 data to a dedicated renderer
    if any(v.ndim == 3 for v in data.values()):
        print("\nDetected volumetric (3D) data — using volume renderer")
        _render_volumetric(loaded_dataset)
        return
    
    # 1. Detect position variables
    print("\n[1/5] Detecting spatial coordinates...")
    position_vars = detect_positions(data)
    
    if position_vars:
        print(f"✓ Found candidates: {position_vars}")
        response = input("Use these as positions? [Y/n]: ").strip().lower()
        
        if response in ['', 'y', 'yes']:
            x_var, y_var, z_var = position_vars
        else:
            available = list(data.keys())
            x_var, y_var, z_var = prompt_user_for_positions(available)
    else:
        print("Could not auto-detect position variables")
        available = list(data.keys())
        x_var, y_var, z_var = prompt_user_for_positions(available)
    
    # 2. Build positions array
    print(f"\n[2/5] Building position array from: {x_var}, {y_var}, {z_var}")
    positions = np.stack([
        data[x_var],
        data[y_var],
        data[z_var]
    ], axis=1)
    
    num_particles = len(positions)
    print(f"✓ Total particles: {num_particles:,}")
    
    # Compute bounds
    x_min, x_max = positions[:, 0].min(), positions[:, 0].max()
    y_min, y_max = positions[:, 1].min(), positions[:, 1].max()
    z_min, z_max = positions[:, 2].min(), positions[:, 2].max()
    
    print(f"  X range: [{x_min:.2f}, {x_max:.2f}]")
    print(f"  Y range: [{y_min:.2f}, {y_max:.2f}]")
    print(f"  Z range: [{z_min:.2f}, {z_max:.2f}]")
    
    bounds = [[x_min, x_max], [y_min, y_max], [z_min, z_max]]
    
    # 3. Identify scalar fields
    scalar_vars = [k for k in data.keys() 
                   if k not in [x_var, y_var, z_var]]
    
    print(f"\n[3/5] Found {len(scalar_vars)} scalar fields:")
    for var in scalar_vars:
        print(f"  • {var}")
        
    # 4. Create density grids
    print(f"\n[4/5] Creating {grid_size}³ density grids...")
    
    # Particle count grid
    hist_density, edges = np.histogramdd(
        positions, 
        bins=grid_size, 
        range=bounds
    )
    print(f"  ✓ Particle density")
    
    # Scalar field grids (FIXED: proper numpy divide)
    grids = {}
    for var in scalar_vars:
        hist, _ = np.histogramdd(
            positions,
            bins=grid_size,
            range=bounds,
            weights=data[var]
        )
        # Average per voxel (initialize output first)
        grids[var] = np.zeros_like(hist)
        np.divide(hist, hist_density, out=grids[var], where=hist_density>0)
        print(f"  ✓ {var}")
    
    # 5. Subsample for point cloud
    print(f"\n[5/5] Subsampling particles (factor: {subsample_factor})...")
    indices = np.random.choice(num_particles, num_particles//subsample_factor, replace=False)
    points_subsample = positions[indices]
    print(f"  ✓ {len(points_subsample):,} points for visualization")
    
    # ==== CREATE PYVISTA VISUALIZATION ====
    print("\n" + "="*60)
    print("Creating PyVista scene (off-screen rendering)...")
    print("="*60)
    
    # Create plotter with explicit off-screen setting
    pl = pv.Plotter(
        notebook=True,
        off_screen=True  # ← EXPLICIT for server
    )
    
    # Add particle density volume
    grid_density = pv.ImageData(dimensions=hist_density.shape)
    grid_density.point_data['density'] = np.log10(hist_density.flatten(order='F') + 1)
    grid_density.origin = (x_min, y_min, z_min)
    grid_density.spacing = (
        (x_max - x_min) / grid_size,
        (y_max - y_min) / grid_size,
        (z_max - z_min) / grid_size
    )
    
    print("  ✓ Adding volume rendering (density grid)")
    pl.add_volume(
        grid_density,
        scalars='density',
        cmap='viridis',
        opacity='sigmoid',
        name='Particle Density (log)'
    )
    
    # Add point cloud
    point_cloud = pv.PolyData(points_subsample)
    
    # Add all scalar fields to point cloud
    for var in scalar_vars:
        point_cloud[var] = data[var][indices]
    
    # Color by first available scalar (prefer 'mass' if available)
    if 'mass' in scalar_vars:
        color_by = 'mass'
    elif scalar_vars:
        color_by = scalar_vars[0]
    else:
        color_by = None
    
    print(f"  ✓ Adding point cloud")
    if color_by:
        pl.add_mesh(
            point_cloud,
            scalars=color_by,
            cmap='plasma',
            point_size=2,
            render_points_as_spheres=True,
            opacity=0.5,
            name=f'Particles (colored by {color_by})'
        )
        print(f"    → Colored by: {color_by}")
    else:
        pl.add_mesh(
            point_cloud,
            color='white',
            point_size=2,
            render_points_as_spheres=True,
            opacity=0.5,
            name='Particles'
        )
        print(f"    → Default coloring")
    
    # Camera setup
    pl.camera_position = 'iso'
    pl.add_axes()
    pl.add_bounding_box()
    
    # Summary
    print("\n" + "="*60)
    print("✨ VISUALIZATION READY")
    print("="*60)
    print(f"File: {loaded_dataset.filepath}")
    print(f"Position: ({x_var}, {y_var}, {z_var})")
    print(f"Scalars: {', '.join(scalar_vars)}")
    print(f"Points: {len(points_subsample):,}")
    if color_by:
        print(f"Coloring: {color_by}")
    print("="*60)
    
    # Show with Trame (web-based, no X11 needed)
    print("\nLaunching Trame viewer...")
    try:
        pl.show(jupyter_backend='trame')
        print("✓ Trame viewer started successfully")
    except Exception as e:
        print(f"Error launching viewer: {e}")
        print("\nTrying alternative rendering...")
