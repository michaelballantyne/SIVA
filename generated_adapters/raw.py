
import numpy as np
import re
import os

FILETYPE = "raw_volume"
EXTENSIONS = [".raw"]

def inspect(filepath):
    """Inspect a raw binary volume file and return metadata."""
    # Try to extract dimensions and dtype from filename
    basename = os.path.basename(filepath)
    
    # Look for pattern like "302x302x302_uint8"
    pattern = r'(\d+)x(\d+)x(\d+)_(\w+)'
    match = re.search(pattern, basename)
    
    if match:
        nx, ny, nz = int(match.group(1)), int(match.group(2)), int(match.group(3))
        dtype_str = match.group(4)
    else:
        # Fallback: a cubic uint8 volume is recoverable from the size alone
        file_size = os.path.getsize(filepath)
        n = round(file_size ** (1/3))
        if n ** 3 == file_size:
            nx = ny = nz = n
            dtype_str = "uint8"
        else:
            # No metadata in the filename and the size fits no cubic uint8
            # volume — refuse rather than guess a layout.
            raise ValueError(
                f"Cannot determine raw volume layout for {basename!r}: "
                f"expected 'NxMxK_dtype' in the filename "
                f"(e.g. 'foo_302x302x302_uint8.raw').")
    
    return {
        "filetype": FILETYPE,
        "variables": ["volume"],
        "dimensions": {
            "grid": (nx, ny, nz)
        },
        "attributes": {
            "dtype": dtype_str,
            "shape": [nx, ny, nz],
            "description": "Raw binary volume data"
        }
    }

def read_array(filepath, location):
    """Read the volume array from a raw binary file."""
    # Get metadata to determine shape and dtype
    metadata = inspect(filepath)
    shape = tuple(metadata["dimensions"]["grid"])
    dtype_str = metadata["attributes"]["dtype"]
    
    # Map dtype string to numpy dtype
    dtype = np.dtype(dtype_str)
    
    # Read the raw binary data
    data = np.fromfile(filepath, dtype=dtype)
    
    # Reshape to 3D grid
    data = data.reshape(shape)
    
    return data