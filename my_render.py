# render.py
import os
os.environ['PYVISTA_OFF_SCREEN'] = 'false'
os.environ['PYVISTA_USE_PANEL'] = '0'

import pyvista as pv
import numpy as np

pv.set_jupyter_backend('trame')
pv.global_theme.jupyter_backend = 'trame'


def detect_positions(data_dict):
    """
    Use heuristics to detect x, y, z coordinates
    
    Args:
        data_dict: dict with variable names as keys, numpy arrays as values
    
    Returns:
        tuple: (x_var, y_var, z_var) or None if not found
    """
    keys = list(data_dict.keys())
    
    # Heuristic 1: Exact matches
    if 'x' in keys and 'y' in keys and 'z' in keys:
        return ('x', 'y', 'z')
    
    # Heuristic 2: Case-insensitive
    keys_lower = {k.lower(): k for k in keys}
    if 'x' in keys_lower and 'y' in keys_lower and 'z' in keys_lower:
        return (keys_lower['x'], keys_lower['y'], keys_lower['z'])
    
    # Heuristic 3: Common patterns
    patterns = [
        ('X', 'Y', 'Z'),
        ('pos_x', 'pos_y', 'pos_z'),
        ('position_x', 'position_y', 'position_z'),
        ('px', 'py', 'pz'),
    ]
    
    for px, py, pz in patterns:
        if px in keys and py in keys and pz in keys:
            return (px, py, pz)
    
    # Heuristic 4: Variables with "x", "y", "z" in name (if unique)
    x_candidates = [k for k in keys if 'x' in k.lower()]
    y_candidates = [k for k in keys if 'y' in k.lower()]
    z_candidates = [k for k in keys if 'z' in k.lower()]
    
    if len(x_candidates) == 1 and len(y_candidates) == 1 and len(z_candidates) == 1:
        return (x_candidates[0], y_candidates[0], z_candidates[0])
    
    # Not found
    return None


def prompt_user_for_positions(available_vars):
    """
    Interactive prompt in Jupyter for user to select position variables
    
    Args:
        available_vars: list of variable names
        
    Returns:
        tuple: (x_var, y_var, z_var)
    """
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
        print(f"    ❌ '{x_var}' not found. Try again.")
    
    while True:
        y_var = input("  Y coordinate: ").strip()
        if y_var in available_vars:
            break
        print(f"    ❌ '{y_var}' not found. Try again.")
    
    while True:
        z_var = input("  Z coordinate: ").strip()
        if z_var in available_vars:
            break
        print(f"    ❌ '{z_var}' not found. Try again.")
    
    print(f"\n✓ Using: x={x_var}, y={y_var}, z={z_var}")
    print("="*60 + "\n")
    
    return (x_var, y_var, z_var)


def render(loaded_dataset, subsample_factor=30, grid_size=128):
    """
    Render loaded dataset using PyVista + Trame (server-compatible)
    
    Args:
        loaded_dataset: DatasetInfo object (returned from load() function)
        subsample_factor: downsample particles by this factor for point cloud
        grid_size: resolution of density grids (grid_size^3 voxels)
    """
    
    print("="*60)
    print("RENDERING DATASET")
    print("="*60)
    
    # Check if data is loaded
    if not loaded_dataset.loaded:
        print("❌ ERROR: Data not loaded!")
        print("   Please call load(dataset) first.")
        return
    
    if not loaded_dataset.data:
        print("❌ ERROR: No data arrays found in dataset!")
        return
    
    data = loaded_dataset.data  # The actual dict of numpy arrays
    
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
        print("⚠️  Could not auto-detect position variables")
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
    
    # 3. Identify scalar fields (everything except position vars)
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
    
    # Scalar field grids
    grids = {}
    for var in scalar_vars:
        hist, _ = np.histogramdd(
            positions,
            bins=grid_size,
            range=bounds,
            weights=data[var]
        )
        # Average per voxel (with proper out parameter)
        grids[var] = np.zeros_like(hist)  # ← FIX: Initialize output
        np.divide(hist, hist_density, out=grids[var], where=hist_density>0)  # ← FIX
        print(f"  ✓ {var}")
    
    # 5. Subsample for point cloud
    print(f"\n[5/5] Subsampling particles (factor: {subsample_factor})...")
    indices = np.random.choice(num_particles, num_particles//subsample_factor, replace=False)
    points_subsample = positions[indices]
    print(f"  ✓ {len(points_subsample):,} points for visualization")
    
    # ==== SPATIAL ALIGNMENT CHECK ====
    print("\n" + "="*50)
    print("SPATIAL ALIGNMENT CHECK")
    print("="*50)
    print(f"\n📍 PARTICLE DATA:")
    print(f"  Total particles: {num_particles:,}")
    print(f"  X: [{x_min:.2f}, {x_max:.2f}]")
    print(f"  Y: [{y_min:.2f}, {y_max:.2f}]")
    print(f"  Z: [{z_min:.2f}, {z_max:.2f}]")
    
    in_bounds = np.all(
        (positions >= [x_min, y_min, z_min]) &
        (positions <= [x_max, y_max, z_max]),
        axis=1
    )
    print(f"\n✓ Particles inside grid: {in_bounds.sum():,} / {num_particles:,} ({100*in_bounds.mean():.1f}%)")
    print("="*50 + "\n")
    
    # ==== CREATE PYVISTA VISUALIZATION ====
    print("Creating PyVista visualization...")
    pl = pv.Plotter(notebook=True)
    
    # Add particle density volume
    grid_density = pv.ImageData(dimensions=hist_density.shape)
    grid_density.point_data['density'] = np.log10(hist_density.flatten(order='F') + 1)
    grid_density.origin = (x_min, y_min, z_min)
    grid_density.spacing = (
        (x_max - x_min) / grid_size,
        (y_max - y_min) / grid_size,
        (z_max - z_min) / grid_size
    )
    
    pl.add_volume(
        grid_density,
        scalars='density',
        cmap='viridis',
        opacity='sigmoid',
        name='Particle Count Density (log)'
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
        print(f"  ✓ Point cloud colored by: {color_by}")
    else:
        pl.add_mesh(
            point_cloud,
            color='white',
            point_size=2,
            render_points_as_spheres=True,
            opacity=0.5,
            name='Particles'
        )
        print(f"  ✓ Point cloud (default color)")
    
    # Camera setup
    pl.camera_position = 'iso'
    pl.add_axes()
    pl.add_bounding_box()
    
    # Summary
    print("\n" + "="*60)
    print("✨ VISUALIZATION READY")
    print("="*60)
    print(f"File: {loaded_dataset.filepath}")
    print(f"Position variables: {x_var}, {y_var}, {z_var}")
    print(f"Coloring particles by: {color_by if color_by else 'default'}")
    print(f"Available fields: {scalar_vars}")
    print(f"Particles rendered: {len(points_subsample):,}")
    if loaded_dataset.selection_info:
        print(f"\nSelection applied:")
        for key, val in loaded_dataset.selection_info.items():
            print(f"  {key}: {val}")
    print("="*60 + "\n")
    
    # Show with trame backend
    pl.show(jupyter_backend='trame')