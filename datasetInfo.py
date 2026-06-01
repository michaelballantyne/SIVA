
class DatasetInfo:
    def __init__(self, filepath, filetype, variables, dimensions=None, attributes=None):
        self.filepath = filepath
        self.filetype = filetype
        self.variables = variables
        self.dimensions = dimensions or {}
        self.attributes = attributes or {}
    
    def __str__(self):
        output = []
        output.append(f"File: {self.filepath}")
        output.append(f"Type: {self.filetype}")
        output.append(f"\nVariables ({len(self.variables)}):")
        for var in self.variables:
            output.append(f"  - {var}")
        
        if self.dimensions:
            output.append(f"\nDimensions:")
            for dim, size in self.dimensions.items():
                output.append(f"  - {dim}: {size}")
        
        if self.attributes:
            output.append(f"\nAttributes:")
            for attr, value in self.attributes.items():
                output.append(f"  - {attr}: {value}")
        
        return "\n".join(output)