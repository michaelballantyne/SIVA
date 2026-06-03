import pygio
import os
import numpy as np


# Downloads the data to a provided path, in a specified format.
# Args:
#     dataset_info: DatasetInfo object with data loaded
#     output_path: Path to save file
#     format: Output format (None = same as source, or 'genericio', 'hdf5', etc.)
#     phys_scale: Physical scale [Lx, Ly, Lz] for GenericIO (default: [1.0, 1.0, 1.0])
#     phys_origin: Physical origin [x0, y0, z0] for GenericIO (default: [0.0, 0.0, 0.0])

def download(dataset_info, output_path, format=None, phys_scale=None, phys_origin=None):
    if not dataset_info.loaded:
        raise ValueError("No data loaded. Call load() first.")
    
    # Determine output format
    if format is None:
        format = dataset_info.filetype.lower()
    
    if format.lower() == "genericio":
        _download_genericio(dataset_info, output_path, phys_scale, phys_origin)
    elif format.lower() == "hdf5":
        raise NotImplementedError("HDF5 output not yet implemented")
    else:
        raise ValueError(f"Unsupported output format: {format}")


# Write DatasetInfo to GenericIO file.
def _download_genericio(dataset_info, output_path, phys_scale=None, phys_origin=None):
    os.environ['GENERICIO_NO_MPI'] = 'true'
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else '.', exist_ok=True)
    
    # Set defaults for physical parameters
    if phys_scale is None:
        # Try to get from attributes, otherwise use default
        if 'phys_scale' in dataset_info.attributes:
            phys_scale = dataset_info.attributes['phys_scale']
        else:
            phys_scale = [1.0, 1.0, 1.0]
    
    if phys_origin is None:
        if 'phys_origin' in dataset_info.attributes:
            phys_origin = dataset_info.attributes['phys_origin']
        else:
            phys_origin = [0.0, 0.0, 0.0]
    
    # Write the file - pygio expects a dictionary
    pygio.write_genericio(
        output_path,
        dataset_info.data,  # Already a dict!
        phys_scale,
        phys_origin
    )
    
    print(f"Saved {dataset_info.selection_info['particles_loaded']} particles to {output_path}")
    print(f"Variables: {', '.join(dataset_info.data.keys())}")
    print(f"Physical scale: {phys_scale}")
    print(f"Physical origin: {phys_origin}")