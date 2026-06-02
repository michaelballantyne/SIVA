import h5py
import pygio
import os
from datasetInfo import DatasetInfo

def inspect_file(filepath):
    """Inspect a data file and return metadata information."""
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


def _inspect_genericio(filepath):
    """Inspect GenericIO file."""
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
    
    with h5py.File(filepath, 'r') as f:
        def collect_datasets(name, obj):
            if isinstance(obj, h5py.Dataset):
                variables.append(name)
        
        f.visititems(collect_datasets)
        attributes = {key: f.attrs[key] for key in f.attrs}
    
    return DatasetInfo(filepath, "HDF5", variables, attributes=attributes)
