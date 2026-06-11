"""Tier-1 fallback: generate an adapter for a file no registered reader claims.

Flow (reached only after yt / HDF5 / FITS / GenericIO all decline):
    1. gather evidence about the file (name, extension, size, hex header)
    2. ask the LLM to identify the format and pick the appropriate *installed*
       reader library (yt, astropy, netCDF4, pyarrow, scipy, ...)
    3. the LLM writes a small module: inspect() + load(), using that library
    4. run inspect() on the real file and validate the result (conformance)
    5. on success, freeze the module to generated_adapters/<ext>.py and register
       it so the next file of this format skips the LLM entirely (it's Tier 0)

The LLM never reads the bytes itself and never hand-parses raw bytes — it only
identifies the format and wires up a trusted reader. Trust comes from the
validation in step 4, not from the model's say-so.

Security note: the generated module runs via exec() in this process. Only use
on files/machines you trust.
"""

import os
import types
import traceback

from adapters import (
    FormatAdapter,
    DatasetInfo,
    register_generated_adapter,
)

LLM_MODEL = os.environ.get("VISLANG_LLM_MODEL", "anthropic/claude-opus-4-8")
MAX_RETRIES = 4
HEADER_BYTES = 1024
TAIL_BYTES = 256
GENERATED_ADAPTERS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "generated_adapters")


# ---------------------------------------------------------------------------
# Wrapping a generated module as a FormatAdapter
# ---------------------------------------------------------------------------
class GeneratedModuleAdapter(FormatAdapter):
    """Wraps an LLM-generated module (FILETYPE/EXTENSIONS/inspect/load) so it
    plugs into the same registry as the hand-written adapters."""

    def __init__(self, name, module, extensions):
        self.name = name
        self._module = module
        self._extensions = tuple(e.lower() for e in extensions if e)

    def can_handle(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        return bool(self._extensions) and ext in self._extensions

    def inspect(self, filepath):
        result = self._module.inspect(filepath)
        _validate_inspect(result)
        return DatasetInfo(
            filepath, result["filetype"], list(result["variables"]),
            dimensions=dict(result.get("dimensions", {}) or {}),
            attributes=dict(result.get("attributes", {}) or {}),
        )

    def load(self, dataset_info, variables=None, dimensions=None):
        data = self._module.load(dataset_info.filepath,
                                 variables=variables, dimensions=dimensions)
        if not isinstance(data, dict):
            raise ValueError("generated load() must return {varname: array}")
        for k, v in data.items():
            dataset_info.data[k] = v
        dataset_info.loaded = True
        dataset_info.selection_info = {
            'variables_loaded': list(data.keys()),
            'dimension_selection': dimensions,
        }
        return dataset_info


# ---------------------------------------------------------------------------
# Validation (the conformance check — hand-written, never generated)
# ---------------------------------------------------------------------------
def _validate_inspect(result):
    if not isinstance(result, dict):
        raise ValueError(f"inspect() must return a dict, got {type(result).__name__}")
    for key in ("filetype", "variables", "dimensions", "attributes"):
        if key not in result:
            raise ValueError(f"inspect() result is missing the '{key}' key")
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


# ---------------------------------------------------------------------------
# Evidence shown to the LLM
# ---------------------------------------------------------------------------
def _gather_evidence(filepath):
    size = os.path.getsize(filepath)
    with open(filepath, "rb") as f:
        head = f.read(HEADER_BYTES)
        if size > HEADER_BYTES + TAIL_BYTES:
            f.seek(-TAIL_BYTES, os.SEEK_END)
            tail = f.read(TAIL_BYTES)
        else:
            tail = b""

    def hexdump(data, base=0):
        lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i + 16]
            hexpart = " ".join(f"{b:02x}" for b in chunk)
            asciipart = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
            lines.append(f"{base + i:08x}  {hexpart:<47}  |{asciipart}|")
        return "\n".join(lines)

    parts = [
        f"Filename: {os.path.basename(filepath)}",
        f"Extension: {os.path.splitext(filepath)[1] or '(none)'}",
        f"File size: {size} bytes",
        "",
        f"Hex dump of first {len(head)} bytes:",
        hexdump(head),
    ]
    if tail:
        parts += ["", f"Hex dump of last {len(tail)} bytes:",
                  hexdump(tail, base=size - len(tail))]
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# DSPy generator
# ---------------------------------------------------------------------------
_generator = None


def _configure_generator():
    global _generator
    if _generator is not None:
        return _generator

    import dspy  # raises ImportError if not installed
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot generate an adapter.")

    class WriteAdapter(dspy.Signature):
        """Identify a scientific data file's format and write a reader module.

        You are given evidence about a file (name, extension, size, hex dumps).
        Infer the format and choose the appropriate ALREADY-INSTALLED Python
        reader library for it (e.g. yt, astropy.io.fits, netCDF4, pyarrow,
        h5py, scipy.io, PIL, rasterio). Do NOT hand-parse raw bytes — use the
        library. Write a complete, self-contained Python module that defines:

            FILETYPE = "<short format name>"
            EXTENSIONS = ["<.ext>", ...]   # extensions this format uses

            def inspect(filepath):
                return {
                    "filetype": FILETYPE,
                    "variables": [<field/column/dataset names>],   # non-empty
                    "dimensions": {...},   # e.g. {"particles": N} or {"grid": (nx,ny,nz)}
                    "attributes": {...},   # JSON-friendly metadata
                }

            def load(filepath, variables=None, dimensions=None):
                # return {variable_name: numpy.ndarray}; default = all variables
                ...

        Rules:
        - Use the standard reader library for the identified format; assume it
          is installed. Import it inside the functions.
        - 'variables' must be non-empty and contain the real data fields.
        - All metadata values must be JSON-serializable (use .item()/float()).
        - Read real data from filepath; never invent values.
        - Output only the Python module source.
        """
        file_evidence: str = dspy.InputField(
            desc="Filename, extension, size, hex dump of head/tail")
        previous_attempt: str = dspy.InputField(
            desc="Previous code and the error it produced; empty on first try. "
                 "Fix the error; do not repeat it.")
        module_code: str = dspy.OutputField(
            desc="Complete Python module source (FILETYPE, EXTENSIONS, inspect, load)")

    lm = dspy.LM(LLM_MODEL, temperature=None, max_tokens=16000)
    dspy.configure(lm=lm)
    _generator = dspy.ChainOfThought(WriteAdapter)
    return _generator


def _strip_fences(code):
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code


def _exec_module(code, modname):
    mod = types.ModuleType(modname)
    exec(compile(code, f"<{modname}>", "exec"), mod.__dict__)
    if not callable(getattr(mod, "inspect", None)):
        raise ValueError("generated module does not define inspect(filepath)")
    if not callable(getattr(mod, "load", None)):
        raise ValueError("generated module does not define load(filepath, ...)")
    return mod


def _cache_path_for(filepath):
    ext = os.path.splitext(filepath)[1].lower().lstrip(".") or "noext"
    return os.path.join(GENERATED_ADAPTERS_DIR, f"{ext}.py")


def _wrap_and_register(mod, fallback_ext=None):
    name = getattr(mod, "FILETYPE", None) or "LLMGenerated"
    exts = list(getattr(mod, "EXTENSIONS", []) or [])
    if not exts and fallback_ext:
        exts = [fallback_ext]
    adapter = GeneratedModuleAdapter(f"{name} (LLM)", mod, exts)
    register_generated_adapter(adapter)
    return adapter


# ---------------------------------------------------------------------------
# Public entry points (called by adapters.get_adapter)
# ---------------------------------------------------------------------------
def load_cached_adapters():
    """Register any previously-frozen generated adapters. No LLM/API needed."""
    if not os.path.isdir(GENERATED_ADAPTERS_DIR):
        return
    for fname in os.listdir(GENERATED_ADAPTERS_DIR):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(GENERATED_ADAPTERS_DIR, fname)
        try:
            with open(path) as f:
                mod = _exec_module(f.read(), f"vislang_gen_{fname[:-3]}")
            _wrap_and_register(mod, fallback_ext="." + fname[:-3])
        except Exception:
            continue  # skip a broken cached module


def try_generate_adapter(filepath):
    """Generate, validate, freeze, and register an adapter for filepath.

    Returns a FormatAdapter instance on success, or None if generation isn't
    possible (deps/key missing) or never validated. get_adapter() turns None
    into a clean UnsupportedFormatError.
    """
    try:
        generator = _configure_generator()
    except Exception:
        # dspy not installed, or no API key — Tier 1 unavailable
        return None

    evidence = _gather_evidence(filepath)
    fallback_ext = os.path.splitext(filepath)[1].lower() or None
    previous_attempt = ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            prediction = generator(file_evidence=evidence,
                                   previous_attempt=previous_attempt)
            code = _strip_fences(prediction.module_code)
            mod = _exec_module(code, f"vislang_gen_attempt_{attempt}")
            # Conformance: actually run inspect() on the real file and validate
            result = mod.inspect(filepath)
            _validate_inspect(result)
        except Exception:
            previous_attempt = (
                f"--- Attempt {attempt} code ---\n{locals().get('code', '')}\n"
                f"--- Error ---\n{traceback.format_exc(limit=5)}"
            )
            continue

        # Success — freeze and register
        os.makedirs(GENERATED_ADAPTERS_DIR, exist_ok=True)
        with open(_cache_path_for(filepath), "w") as f:
            f.write(code)
        return _wrap_and_register(mod, fallback_ext=fallback_ext)

    return None
