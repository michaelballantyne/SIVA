import h5py
import pygio
import os
import traceback
from datasetInfo import DatasetInfo

# ---------------------------------------------------------------------------
# AI parser configuration
# ---------------------------------------------------------------------------
# Model string is LiteLLM format (DSPy uses LiteLLM under the hood).
# Requires ANTHROPIC_API_KEY in the environment.
LLM_MODEL = os.environ.get("VISLANG_LLM_MODEL", "anthropic/claude-opus-4-8")
MAX_RETRIES = 5
HEADER_BYTES = 2048   # how much of the file the LLM gets to see (head)
TAIL_BYTES = 256      # some formats keep metadata in a footer
GENERATED_PARSERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                     "generated_parsers")

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
        except Exception as genericio_error:
            # Unknown format: fall back to an LLM-generated parser
            try:
                return _inspect_with_ai(filepath)
            except Exception as ai_error:
                raise ValueError(
                    f"Unsupported or unreadable file type: {filepath}\n"
                    f"GenericIO error: {genericio_error}\n"
                    f"AI parser error: {ai_error}"
                )

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


# ===========================================================================
# AI-generated parser fallback (DSPy + Anthropic Claude)
# ===========================================================================
#
# Flow: gather evidence about the file (name, size, hex dump of header/tail,
# text preview) -> ask the LLM to write a parse(filepath) function -> exec it
# -> validate the result -> on failure, feed the error back and retry (max
# MAX_RETRIES). Working parsers are cached in generated_parsers/<ext>.py so
# the next file of the same format skips the LLM entirely.
#
# Security note: generated code runs with exec() in this process — no
# sandboxing. Only use on files/machines you trust.

_dspy_generator = None  # lazily-initialized dspy.ChainOfThought


def _configure_dspy():
    """Set up DSPy with the Anthropic backend. Lazy so that HDF5/GenericIO
    paths work without dspy installed."""
    global _dspy_generator
    if _dspy_generator is not None:
        return _dspy_generator

    try:
        import dspy
    except ImportError:
        raise RuntimeError(
            "dspy is required for the AI parser fallback: pip install dspy"
        )
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set; cannot generate a parser for an "
            "unknown file format."
        )

    class GenerateParser(dspy.Signature):
        """Write a Python parser for an unknown scientific data file format.

        You are given evidence about a data file (name, size, hex dump of its
        header and tail, and a text preview if it is text-like). Infer the
        format and write a complete, self-contained Python module that defines:

            def parse(filepath):
                ...
                return {
                    "filetype": <short format name, str>,
                    "variables": <list of variable/column/dataset names>,
                    "dimensions": <dict, e.g. {"particles": N} or {"rows": N}>,
                    "attributes": <dict of metadata: per-variable min/max,
                                   shapes, units, header fields, etc.>,
                }

        Rules:
        - Only use the Python standard library plus numpy (and pandas if the
          file is tabular). Do not use libraries that may not be installed.
        - "variables" must be non-empty: the actual data fields in the file.
        - All dict values must be JSON-serializable Python scalars, strings,
          lists, or tuples (convert numpy scalars with .item() or float()).
        - The function must actually read the file at filepath — never invent
          metadata that is not derived from the file contents.
        - Be defensive: handle byte order, delimiters, comment lines, missing
          values as suggested by the evidence.
        - Output only the Python source code for the module.
        """
        file_evidence: str = dspy.InputField(
            desc="Filename, size, hex dump of head/tail, text preview")
        previous_attempt: str = dspy.InputField(
            desc="The previously generated code and the error it produced; "
                 "empty string on the first attempt. Fix the error, do not "
                 "repeat the same mistake.")
        parser_code: str = dspy.OutputField(
            desc="Complete Python source defining parse(filepath)")

    # temperature=None so LiteLLM omits it (rejected by claude-opus-4-8)
    lm = dspy.LM(LLM_MODEL, temperature=None, max_tokens=16000)
    dspy.configure(lm=lm)
    _dspy_generator = dspy.ChainOfThought(GenerateParser)
    return _dspy_generator


def _gather_file_evidence(filepath):
    """Build the textual description of the unknown file shown to the LLM."""
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(HEADER_BYTES)
        if size > HEADER_BYTES + TAIL_BYTES:
            f.seek(-TAIL_BYTES, os.SEEK_END)
            tail = f.read(TAIL_BYTES)
        else:
            tail = b""

    def hexdump(data, base_offset=0):
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hexpart = " ".join(f"{b:02x}" for b in chunk)
            asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{base_offset + i:08x}  {hexpart:<47}  |{asciipart}|")
        return "\n".join(lines)

    evidence = [
        f"Filename: {os.path.basename(filepath)}",
        f"Extension: {os.path.splitext(filepath)[1] or '(none)'}",
        f"File size: {size} bytes",
        "",
        f"Hex dump of first {len(head)} bytes:",
        hexdump(head),
    ]
    if tail:
        evidence += ["", f"Hex dump of last {len(tail)} bytes:",
                     hexdump(tail, base_offset=size - len(tail))]

    # Text preview if the head decodes and is mostly printable
    try:
        text = head.decode("utf-8")
        printable = sum(c.isprintable() or c in "\r\n\t" for c in text)
        if printable / max(len(text), 1) > 0.95:
            evidence += ["", "The file appears to be text. Preview:",
                         text[:1500]]
    except UnicodeDecodeError:
        pass

    return "\n".join(evidence)


def _run_parser_code(code, filepath):
    """exec() generated parser code and call its parse() on filepath."""
    namespace = {"__name__": "vislang_generated_parser",
                 "__file__": "<generated>"}
    exec(compile(code, "<generated_parser>", "exec"), namespace)
    parse = namespace.get("parse")
    if not callable(parse):
        raise ValueError("Generated code does not define a parse(filepath) function")
    return parse(filepath)


def _validate_result(result):
    """Metadata sanity check on what the generated parse() returned.
    Raises ValueError with a message suitable for feeding back to the LLM."""
    if not isinstance(result, dict):
        raise ValueError(f"parse() must return a dict, got {type(result).__name__}")
    for key in ("filetype", "variables", "dimensions", "attributes"):
        if key not in result:
            raise ValueError(f"parse() result is missing the '{key}' key")
    if not isinstance(result["filetype"], str) or not result["filetype"].strip():
        raise ValueError("'filetype' must be a non-empty string")
    variables = result["variables"]
    if not isinstance(variables, (list, tuple)) or len(variables) == 0:
        raise ValueError("'variables' must be a non-empty list of names")
    if not all(isinstance(v, str) for v in variables):
        raise ValueError("'variables' must contain only strings")
    if not isinstance(result["dimensions"], dict):
        raise ValueError("'dimensions' must be a dict")
    if not isinstance(result["attributes"], dict):
        raise ValueError("'attributes' must be a dict")


def _strip_code_fences(code):
    """LLMs sometimes wrap code in ```python ... ``` despite instructions."""
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        lines = lines[1:]  # drop opening fence (``` or ```python)
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code


def _cache_path_for(filepath):
    ext = os.path.splitext(filepath)[1].lower().lstrip(".") or "noext"
    return os.path.join(GENERATED_PARSERS_DIR, f"{ext}.py")


def _result_to_datasetinfo(filepath, result):
    return DatasetInfo(
        filepath=filepath,
        filetype=f"{result['filetype']} (AI-parsed)",
        variables=list(result["variables"]),
        dimensions=dict(result["dimensions"]),
        attributes=dict(result["attributes"]),
    )


def _try_cached_parser(filepath):
    """Run a previously generated parser for this extension, if one exists.
    Returns a DatasetInfo or None."""
    cache_path = _cache_path_for(filepath)
    if not os.path.exists(cache_path):
        return None
    try:
        with open(cache_path) as f:
            code = f.read()
        result = _run_parser_code(code, filepath)
        _validate_result(result)
        return _result_to_datasetinfo(filepath, result)
    except Exception:
        # Cached parser doesn't fit this file — fall through to the LLM
        return None


# Inspect an unknown-format file via an LLM-generated parser.
def _inspect_with_ai(filepath):
    cached = _try_cached_parser(filepath)
    if cached is not None:
        return cached

    generator = _configure_dspy()
    evidence = _gather_file_evidence(filepath)

    previous_attempt = ""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        prediction = generator(file_evidence=evidence,
                               previous_attempt=previous_attempt)
        code = _strip_code_fences(prediction.parser_code)

        try:
            result = _run_parser_code(code, filepath)
            _validate_result(result)
        except Exception:
            last_error = traceback.format_exc(limit=5)
            previous_attempt = (
                f"--- Attempt {attempt} code ---\n{code}\n"
                f"--- Error it produced ---\n{last_error}"
            )
            continue

        # Success: cache the parser for future files of this format
        os.makedirs(GENERATED_PARSERS_DIR, exist_ok=True)
        with open(_cache_path_for(filepath), "w") as f:
            f.write(code)
        return _result_to_datasetinfo(filepath, result)

    raise ValueError(
        f"AI parser failed after {MAX_RETRIES} attempts for {filepath}.\n"
        f"Last error:\n{last_error}"
    )
