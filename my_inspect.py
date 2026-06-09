import h5py
import pygio
import os
from datasetInfo import DatasetInfo

#Inspect a data file and return metadata information.
def inspect_file(filepath):
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"File not found: {filepath}")
    
    ext = os.path.splitext(filepath)[1].lower()
    
    if ext in ['.h5', '.hdf5', '.hdf']:
        return _inspect_hdf5(filepath)
    else:
        # Try GenericIO for files without standard extensions
        try:
            return _inspect_genericio(filepath)
        except Exception as e:
            raise ValueError(f"Unsupported or unreadable file type: {filepath}\nError: {e}")

#Inspect GenericIO file.
def _inspect_genericio(filepath):
    import os
    os.environ['GENERICIO_NO_MPI'] = 'true'
    
    # Try reading the base file or first partition
    try:
        # First try the file as-is
        data = pygio.read_genericio(filepath)
    except:
        # Try with #0 partition notation (for partitioned files)
        data = pygio.read_genericio(f"{filepath}#0")
    
    # Get variable names from dictionary keys
    variables = list(data.keys())
    
    # Get dimensions (number of particles from first variable)
    dimensions = {}
    if variables:
        first_var = variables[0]
        dimensions['particles'] = len(data[first_var])
    
    # Get additional info about data ranges
    attributes = {}

    try:
        # If pygio provides these, capture them
        if hasattr(data, 'phys_scale'):
            attributes['phys_scale'] = data.phys_scale
        if hasattr(data, 'phys_origin'):
            attributes['phys_origin'] = data.phys_origin
    except:
        pass

    for var in variables:
        arr = data[var]
        attributes[f"{var}_min"] = float(arr.min())
        attributes[f"{var}_max"] = float(arr.max())
    
    return DatasetInfo(
        filepath=filepath,
        filetype="GenericIO",
        variables=variables,
        dimensions=dimensions,
        attributes=attributes
    )

# Inspect HDF5 file.
def _inspect_hdf5(filepath):
    variables = []
    attributes = {}
    dimensions = {}
    dataset_shapes = {}

    with h5py.File(filepath, 'r') as f:
        def collect_datasets(name, obj):
            if isinstance(obj, h5py.Dataset):
                variables.append(name)
                dataset_shapes[name] = obj.shape

        f.visititems(collect_datasets)
        for key in f.attrs:
            val = f.attrs[key]
            attributes[key] = val.item() if hasattr(val, 'item') else val

    # Store per-variable shape metadata
    for var, shape in dataset_shapes.items():
        attributes[f"{var}_shape"] = shape

    # Detect particle-like data: all 1D datasets with the same length
    if dataset_shapes:
        all_1d = all(len(s) == 1 for s in dataset_shapes.values())
        if all_1d:
            lengths = set(s[0] for s in dataset_shapes.values())
            if len(lengths) == 1:
                dimensions['particles'] = lengths.pop()

    return DatasetInfo(filepath, "HDF5", variables, dimensions=dimensions, attributes=attributes)
