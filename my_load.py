import numpy as np
import pygio
import os
import copy

# Load data from file and return new DatasetInfo with data.
# Args:
#     dataset_info: DatasetInfo object from inspect_file()
#     variables: List of variable names to load (None = all)
#     dimensions: Dict with dimension selection, e.g.:
#                {'particles': slice(0, 1000)}  # first 1000
#                {'particles': slice(None, None, 10)}  # every 10th
#                {'particles': 5000}  # random 5000 particles
#                {'particles': 0.1}  # random 10% of particles

# Returns:
#     New DatasetInfo object with data loaded
def load(dataset_info, variables=None, dimensions=None):
    # Create a copy
    loaded_info = copy.deepcopy(dataset_info)

    if loaded_info.filetype == "GenericIO":
        return _load_genericio(loaded_info, variables, dimensions)
    elif loaded_info.filetype == "HDF5":
        return _load_hdf5(loaded_info, variables, dimensions)
    else:
        raise ValueError(f"Unsupported file type: {loaded_info.filetype}")


def _load_genericio(dataset_info, variables=None, dimensions=None):
    """Load data from GenericIO file into DatasetInfo."""
    os.environ['GENERICIO_NO_MPI'] = 'true'
    
    # Read the file
    try:
        raw_data = pygio.read_genericio(dataset_info.filepath)
    except:
        raw_data = pygio.read_genericio(f"{dataset_info.filepath}#0")
    
    # Find which variables to load
    if variables is None:
        variables = dataset_info.variables
    else:
        # Validate requested variables exist
        invalid = set(variables) - set(dataset_info.variables)
        if invalid:
            raise ValueError(f"Variables not found in file: {invalid}")
    
    # Get total particle count
    total_particles = dataset_info.dimensions.get('particles', 0)
    
    # Determine particle indices to load
    particle_indices = _get_particle_indices(dimensions, total_particles)
    
    # Load and select data for each variable
    for var in variables:
        data = raw_data[var]
        
        if particle_indices is not None:
            data = data[particle_indices]
        
        dataset_info.data[var] = data
    
    # Update metadata
    dataset_info.loaded = True
    dataset_info.selection_info = {
        'variables_loaded': variables,
        'total_particles': total_particles,
        'particles_loaded': len(dataset_info.data[variables[0]]),
        'dimension_selection': dimensions
    }
    
    return dataset_info


# Convert dimension selection to particle indices.

# Returns:
#     None (load all), slice object, or numpy array of indices
def _get_particle_indices(dimensions, total_particles):
    if dimensions is None or 'particles' not in dimensions:
        return None
    
    selection = dimensions['particles']
    
    # Case 1: Already a slice object (deterministic slicing)
    if isinstance(selection, slice):
        return selection
    
    # Case 2: Float between 0-1 (random fraction)
    elif isinstance(selection, float):
        if not 0 < selection <= 1:
            raise ValueError(f"Float selection must be between 0 and 1, got {selection}")
        n_select = int(total_particles * selection)
        return np.random.choice(total_particles, size=n_select, replace=False)
    
    # Case 3: Integer (random N particles)
    elif isinstance(selection, int):
        if selection > total_particles:
            raise ValueError(f"Cannot select {selection} particles from {total_particles}")
        return np.random.choice(total_particles, size=selection, replace=False)
    
    else:
        raise ValueError(f"Invalid dimension selection type: {type(selection)}")


# Convert grid selection to a per-axis step (stride).
#     {'grid': 64}                       -> stride so each axis ends up ~64 cells
#     {'grid': slice(None, None, 8)}     -> every 8th cell along each axis
#     {'grid': 0.25}                     -> keep 25% of cells per axis (stride 4)
# Returns: int step (1 = load full resolution)
def _get_grid_step(dimensions, axis_size):
    if dimensions is None or 'grid' not in dimensions:
        return 1

    selection = dimensions['grid']

    # Float fraction: 0.25 -> keep every 4th cell
    if isinstance(selection, float):
        if not 0 < selection <= 1:
            raise ValueError(f"Float grid selection must be between 0 and 1, got {selection}")
        return max(1, int(round(1 / selection)))

    # Int target resolution: 64 -> stride so axis ends up ~64 cells
    elif isinstance(selection, int):
        if selection <= 0:
            raise ValueError(f"Grid resolution must be positive, got {selection}")
        return max(1, axis_size // selection)

    # Slice: use its step directly
    elif isinstance(selection, slice):
        return selection.step or 1

    else:
        raise ValueError(f"Invalid grid selection type: {type(selection)}")


def _load_hdf5(dataset_info, variables=None, dimensions=None):
    import h5py

    if variables is None:
        variables = dataset_info.variables
    else:
        invalid = set(variables) - set(dataset_info.variables)
        if invalid:
            raise ValueError(f"Variables not found in file: {invalid}")

    total_particles = dataset_info.dimensions.get('particles', 0)
    # Only compute particle indices if this is 1D particle-style data
    particle_indices = _get_particle_indices(dimensions, total_particles) if total_particles else None

    with h5py.File(dataset_info.filepath, 'r') as f:
        for var in variables:
            dset = f[var]
            if dset.ndim == 3:
                # Stride-read directly from file — full array never enters memory
                step = _get_grid_step(dimensions, dset.shape[0])
                arr = dset[::step, ::step, ::step]
            else:
                arr = dset[:]
                # Apply particle selection only for 1D arrays
                if arr.ndim == 1 and particle_indices is not None:
                    arr = arr[particle_indices]
            dataset_info.data[var] = arr

    dataset_info.loaded = True

    first_arr = dataset_info.data[variables[0]]
    dataset_info.selection_info = {
        'variables_loaded': variables,
        'dimension_selection': dimensions,
    }
    if total_particles:
        dataset_info.selection_info['total_particles'] = total_particles
        if first_arr.ndim == 1:
            dataset_info.selection_info['particles_loaded'] = len(first_arr)
    if first_arr.ndim == 3:
        dataset_info.selection_info['grid_shape_loaded'] = first_arr.shape

    return dataset_info