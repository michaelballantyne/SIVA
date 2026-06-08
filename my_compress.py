# compress.py

import numpy as np
import copy

def compress(
    dataset_info,      # DatasetInfo object (must be loaded)
    variables,         # List of variable names, or dataset_info.variables
    error_bound,       # Numeric loss tolerance (e.g., 0.01)
    mode='auto'        # 'auto', 'absolute', 'relative', 'precision'
):
    """
    Compress specified variables using SPERR lossy compression.
    
    Args:
        dataset_info: DatasetInfo object with loaded data
        variables: List of variable names to compress
        error_bound: Maximum acceptable error
            - For 'absolute': |original - compressed| ≤ error_bound
            - For 'relative': |original - compressed|/|original| ≤ error_bound
            - For 'precision': error_bound in {16, 32} for float16/float32
        mode: Error mode
            - 'auto': Automatically choose absolute/relative per variable
            - 'absolute': Use absolute error bound
            - 'relative': Use relative error bound
            - 'precision': Simple dtype reduction (float64→float32 or float16)
    
    Returns:
        New DatasetInfo with compressed data and compression metadata
    
    Example:
        >>> data = load(inspect_file("data.gio"), dimensions={'particles': 0.1})
        >>> # Compress with auto mode (heuristic chooses per variable)
        >>> compressed = compress(data, ['temperature', 'energy'], error_bound=0.01)
        >>> 
        >>> # Force relative error for all variables
        >>> compressed = compress(data, data.variables, error_bound=0.05, mode='relative')
        >>> 
        >>> # Simple precision reduction
        >>> compressed = compress(data, data.variables, error_bound=32, mode='precision')
    """
    
    # Validation
    if not dataset_info.loaded:
        raise ValueError("Data must be loaded before compression. Call load() first.")
    
    if not isinstance(variables, list):
        variables = list(variables)
    
    # Validate variables exist
    invalid = set(variables) - set(dataset_info.data.keys())
    if invalid:
        raise ValueError(f"Variables not found in dataset: {invalid}")
    
    # Create a copy to avoid modifying original
    compressed_info = copy.deepcopy(dataset_info)
    
    # Initialize compression metadata
    compressed_info.compression_info = {
        'compressed': True,
        'variables': {},
        'total_original_size_mb': 0,
        'total_compressed_size_mb': 0
    }
    
    # Compress each variable
    for var in variables:
        original_data = dataset_info.data[var]
        
        # Skip non-numeric data
        if not np.issubdtype(original_data.dtype, np.number):
            print(f"⚠️  Skipping non-numeric variable: {var}")
            continue
        
        # Decide compression mode for this variable
        if mode == 'auto':
            var_mode = _decide_error_mode(original_data)
            print(f"📊 {var}: Auto-selected '{var_mode}' mode")
        else:
            var_mode = mode
        
        # Compress the data
        compressed_data, metadata = _compress_variable(
            original_data, 
            error_bound, 
            var_mode, 
            var
        )
        
        # Store compressed data
        compressed_info.data[var] = compressed_data
        
        # Store metadata
        compressed_info.compression_info['variables'][var] = metadata
        compressed_info.compression_info['total_original_size_mb'] += metadata['original_size_mb']
        compressed_info.compression_info['total_compressed_size_mb'] += metadata['compressed_size_mb']
    
    # Summary
    if compressed_info.compression_info['variables']:
        total_ratio = (
            compressed_info.compression_info['total_original_size_mb'] /
            max(compressed_info.compression_info['total_compressed_size_mb'], 1e-10)
        )
        compressed_info.compression_info['total_compression_ratio'] = total_ratio
        
        print(f"\n✅ Compression complete!")
        print(f"   Variables compressed: {len(compressed_info.compression_info['variables'])}")
        print(f"   Total compression ratio: {total_ratio:.2f}x")
        print(f"   Size: {compressed_info.compression_info['total_original_size_mb']:.1f} MB → "
              f"{compressed_info.compression_info['total_compressed_size_mb']:.1f} MB")
    
    return compressed_info


def _decide_error_mode(data):
    """
    Heuristic to automatically decide between absolute and relative error.
    
    Rules:
        1. Data crosses zero → absolute
        2. Contains values near zero → absolute  
        3. Narrow dynamic range (<10x) → absolute
        4. Wide dynamic range (>100x) → relative
        5. Default → relative
    
    Returns:
        'absolute' or 'relative'
    """
    data_min = np.min(data)
    data_max = np.max(data)
    data_abs = np.abs(data)
    
    # Rule 1: Crosses zero
    if data_min < 0 and data_max > 0:
        return 'absolute'
    
    # Rule 2: Contains values very close to zero
    if np.any(data_abs < 1e-10):
        return 'absolute'
    
    # Rule 3: Check dynamic range
    data_range = data_max - data_min
    data_magnitude = np.mean(data_abs)
    
    if data_magnitude > 0:
        # Narrow range → absolute
        if data_range / data_magnitude < 10:
            return 'absolute'
        
        # Wide range → relative
        if data_range / data_magnitude > 100:
            return 'relative'
    
    # Default: relative (works well for most scientific data)
    return 'relative'


def _compress_variable(data, error_bound, mode, var_name):
    """
    Compress a single variable using the specified method.
    
    Returns:
        (compressed_data, metadata_dict)
    """
    original_size = data.nbytes / (1024**2)  # MB
    
    metadata = {
        'original_dtype': str(data.dtype),
        'mode': mode,
        'error_bound': error_bound,
        'original_size_mb': original_size,
        'method': None
    }
    
    # Mode: precision reduction (simple, no SPERR needed)
    if mode == 'precision':
        if error_bound == 32:
            compressed = data.astype(np.float32)
            metadata['compressed_dtype'] = 'float32'
            metadata['method'] = 'dtype_reduction'
        elif error_bound == 16:
            compressed = data.astype(np.float16)
            metadata['compressed_dtype'] = 'float16'
            metadata['method'] = 'dtype_reduction'
        else:
            raise ValueError(f"For mode='precision', error_bound must be 16 or 32, got {error_bound}")
        
        metadata['compressed_size_mb'] = compressed.nbytes / (1024**2)
        metadata['compression_ratio'] = original_size / metadata['compressed_size_mb']
        
        return compressed, metadata
    
    # Mode: SPERR compression
    try:
        import PySPERR
        
        # SPERR requires 1D, 2D, or 3D data
        # For 1D particle data, we can compress directly
        
        if mode == 'absolute':
            # SPERR absolute error mode
            compressed_bytes = PySPERR.compress_1d(
                data,
                mode='abs',
                pwe=error_bound  # PWE = PointWise Error
            )
        elif mode == 'relative':
            # SPERR relative error mode  
            compressed_bytes = PySPERR.compress_1d(
                data,
                mode='psnr',  # SPERR uses PSNR for relative quality
                psnr=_error_bound_to_psnr(error_bound)
            )
        else:
            raise ValueError(f"Invalid mode for SPERR: {mode}")
        
        # Decompress to get the actual compressed values
        compressed = PySPERR.decompress_1d(compressed_bytes, data.shape)
        
        metadata['method'] = 'SPERR'
        metadata['compressed_dtype'] = str(compressed.dtype)
        metadata['compressed_size_mb'] = len(compressed_bytes) / (1024**2)
        metadata['compression_ratio'] = original_size / metadata['compressed_size_mb']
        
        # Compute actual error achieved
        abs_error = np.abs(data - compressed)
        metadata['max_absolute_error'] = float(np.max(abs_error))
        
        if mode == 'relative':
            rel_error = abs_error / (np.abs(data) + 1e-10)
            metadata['max_relative_error'] = float(np.max(rel_error))
        
        print(f"   {var_name}: {metadata['compression_ratio']:.1f}x compression "
              f"(max error: {metadata['max_absolute_error']:.2e})")
        
        return compressed, metadata
        
    except ImportError:
        # SPERR not available - fall back to simple methods
        print(f"⚠️  SPERR not installed. Falling back to simple compression for {var_name}")
        return _compress_fallback(data, error_bound, mode, metadata)


def _compress_fallback(data, error_bound, mode, metadata):
    """
    Fallback compression when SPERR is not available.
    Uses simple quantization or dtype reduction.
    """
    if mode == 'absolute':
        # Round to nearest multiple of error_bound
        scale = error_bound
        compressed = np.round(data / scale) * scale
        compressed = compressed.astype(np.float32)  # Also reduce precision
        metadata['method'] = 'quantization_absolute'
        
    elif mode == 'relative':
        # Determine appropriate precision based on relative error
        # For 1% error, we need ~log2(100) ≈ 7 bits of precision
        # For 0.1% error, ~10 bits, etc.
        if error_bound >= 0.01:  # 1% or worse → float16
            compressed = data.astype(np.float16)
            metadata['method'] = 'float16_fallback'
        else:  # < 1% → float32
            compressed = data.astype(np.float32)
            metadata['method'] = 'float32_fallback'
    else:
        raise ValueError(f"Invalid mode: {mode}")
    
    metadata['compressed_dtype'] = str(compressed.dtype)
    metadata['compressed_size_mb'] = compressed.nbytes / (1024**2)
    metadata['compression_ratio'] = metadata['original_size_mb'] / metadata['compressed_size_mb']
    
    return compressed, metadata


def _error_bound_to_psnr(relative_error_bound):
    """
    Convert relative error bound to PSNR (Peak Signal-to-Noise Ratio).
    
    PSNR = 20 * log10(1 / relative_error)
    
    Examples:
        1% error (0.01) → ~40 dB PSNR
        0.1% error (0.001) → ~60 dB PSNR
    """
    return 20 * np.log10(1.0 / relative_error_bound)


def print_compression_summary(dataset_info):
    """
    Pretty-print compression statistics.
    
    Usage:
        >>> compressed = compress(data, data.variables, 0.01)
        >>> print_compression_summary(compressed)
    """
    if not hasattr(dataset_info, 'compression_info') or not dataset_info.compression_info.get('compressed'):
        print("❌ Dataset is not compressed")
        return
    
    info = dataset_info.compression_info
    
    print("\n" + "="*70)
    print("📦 COMPRESSION SUMMARY")
    print("="*70)
    
    print(f"\nOverall:")
    print(f"  Total compression ratio: {info.get('total_compression_ratio', 0):.2f}x")
    print(f"  Original size:  {info['total_original_size_mb']:.2f} MB")
    print(f"  Compressed size: {info['total_compressed_size_mb']:.2f} MB")
    print(f"  Space saved: {info['total_original_size_mb'] - info['total_compressed_size_mb']:.2f} MB "
          f"({100 * (1 - info['total_compressed_size_mb']/info['total_original_size_mb']):.1f}%)")
    
    print(f"\nPer-variable details:")
    print(f"  {'Variable':<15} {'Mode':<10} {'Method':<15} {'Ratio':<8} {'Max Error':<12}")
    print(f"  {'-'*15} {'-'*10} {'-'*15} {'-'*8} {'-'*12}")
    
    for var, meta in info['variables'].items():
        error_str = f"{meta.get('max_absolute_error', 0):.2e}" if 'max_absolute_error' in meta else "N/A"
        print(f"  {var:<15} {meta['mode']:<10} {meta['method']:<15} "
              f"{meta['compression_ratio']:<8.1f} {error_str:<12}")
    
    print("="*70 + "\n")