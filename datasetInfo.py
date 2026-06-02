import numpy as np

class DatasetInfo:
    """Container for dataset metadata and optionally loaded data."""
    
    def __init__(self, filepath, filetype, variables, dimensions=None, attributes=None):
        # Metadata (always populated by inspect)
        self.filepath = filepath
        self.filetype = filetype
        self.variables = variables
        self.dimensions = dimensions or {}
        self.attributes = attributes or {}
        
        # Data (populated by load)
        self.data = {}  # {variable_name: numpy_array}
        self.loaded = False
        self.selection_info = {}
    
    def __str__(self):
        output = []
        output.append(f"File: {self.filepath}")
        output.append(f"Type: {self.filetype}")
        
        # Show inspection info
        output.append(f"\nAvailable Variables ({len(self.variables)}):")
        for var in self.variables:
            output.append(f"  - {var}")
        
        if self.dimensions:
            output.append(f"\nDimensions:")
            for dim, size in self.dimensions.items():
                output.append(f"  - {dim}: {size}")
        
        # Show loaded data if present
        if self.loaded:
            output.append(f"\n{'='*50}")
            output.append(f"LOADED DATA:")
            output.append(f"\nLoaded Variables ({len(self.data)}):")
            for var, arr in self.data.items():
                output.append(f"  - {var}: shape={arr.shape}, dtype={arr.dtype}")
            
            if self.selection_info:
                output.append(f"\nSelection Applied:")
                for key, val in self.selection_info.items():
                    output.append(f"  - {key}: {val}")
        else:
            output.append(f"\n[Data not loaded - call load() to populate]")
        
        if self.attributes:
            output.append(f"\nAttributes:")
            for attr, value in self.attributes.items():
                output.append(f"  - {attr}: {value}")
        
        return "\n".join(output)