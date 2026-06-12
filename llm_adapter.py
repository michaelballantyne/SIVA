"""Tier-1 fallback: generate an adapter for a file no registered reader claims.

Flow (reached only after yt / HDF5 / FITS / GenericIO all decline):
    1. gather evidence about the file (name, extension, size, hex header)
    2. ask the LLM to identify the format and pick the appropriate *installed*
       reader library (numpy, netCDF4, pyarrow, scipy, ...)
    3. the LLM writes a small module with TWO functions only:
           inspect(filepath)              -> metadata dict
           read_array(filepath, location) -> one full numpy array
       It never writes load(): all selection/subsampling/orchestration is the
       framework's universal load below, written once and shared by every
       generated adapter. The DatasetInfo is the format boundary — once
       inspect() fills it, downstream logic is format-blind.
    4. conformance: run inspect() on the real file, validate the result, then
       read_array() on the first variable and check it returns real data
    5. on success, freeze the module to generated_adapters/<ext>.py and register
       it so the next file of this format skips the LLM entirely (it's Tier 0)

The LLM never hand-parses raw bytes — it only identifies the format and wires
up a trusted reader. Trust comes from the conformance run in step 4, not from
the model's say-so.

Security note: the generated module runs via exec() in this process. Only use
on files/machines you trust.
"""

import os
import types
import traceback

import numpy as np

from adapters import (
    FormatAdapter,
    DatasetInfo,
    register_generated_adapter,
    _resolve_variables,
    _get_particle_indices,
    _get_grid_step,
)

MAX_RETRIES = 4
HEADER_BYTES = 1024
TAIL_BYTES = 256
GENERATED_ADAPTERS_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "generated_adapters")


def _say(msg):
    print(f"[VisLang] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Wrapping a generated module as a FormatAdapter
# ---------------------------------------------------------------------------
class GeneratedModuleAdapter(FormatAdapter):
    """Wraps an LLM-generated module (FILETYPE / EXTENSIONS / inspect /
    read_array) so it plugs into the same registry as hand-written adapters.

    load() here is UNIVERSAL: the generated code only knows how to pull one
    named array out of the file; variable resolution, particle subsampling,
    grid striding, and selection_info bookkeeping are framework code shared by
    every generated adapter.
    """

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
        # filetype must be self.name (the registry key), not the module's raw
        # FILETYPE string — load() routes back via get_adapter_for_info().
        info = DatasetInfo(
            filepath, self.name, list(result["variables"]),
            dimensions=dict(result.get("dimensions", {}) or {}),
            attributes=dict(result.get("attributes", {}) or {}),
        )
        # Optional map var name -> how the library addresses it (defaults to
        # the name itself). Survives my_load's deepcopy like binding does.
        info.variable_locations = dict(result.get("variable_locations", {}) or {})
        return info

    def load(self, dataset_info, variables=None, dimensions=None):
        variables = _resolve_variables(dataset_info, variables)
        locations = getattr(dataset_info, 'variable_locations', None) or {}

        total_particles = dataset_info.dimensions.get('particles', 0)
        particle_indices = (_get_particle_indices(dimensions, total_particles)
                            if total_particles else None)

        for var in variables:
            location = locations.get(var, var)
            arr = np.asarray(self._module.read_array(dataset_info.filepath, location))
            if arr.ndim == 3:
                step = _get_grid_step(dimensions, arr.shape[0])
                if step > 1:
                    arr = arr[::step, ::step, ::step]
            elif (arr.ndim in (1, 2) and particle_indices is not None
                    and arr.shape[0] == total_particles):
                # Subsample along the particle axis. Covers 1-D columns and
                # 2-D (N, k) component arrays; the shape guard keeps non-particle
                # arrays (e.g. a lookup table) intact.
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
            for v in variables:
                if dataset_info.data[v].ndim == 1:
                    dataset_info.selection_info['particles_loaded'] = len(dataset_info.data[v])
                    break
        if first_arr.ndim == 3:
            dataset_info.selection_info['grid_shape_loaded'] = first_arr.shape
        return dataset_info


# ---------------------------------------------------------------------------
# Conformance checks (hand-written, never generated)
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
    locations = result.get("variable_locations")
    if locations is not None:
        if not isinstance(locations, dict):
            raise ValueError("'variable_locations' must be a dict if present")
        unknown = set(locations) - set(variables)
        if unknown:
            raise ValueError(f"'variable_locations' has keys that are not variables: {unknown}")


def _check_read_array(mod, filepath, result):
    """Behavioral check: the generated read_array must return real data for
    the first declared variable."""
    var = result["variables"][0]
    location = (result.get("variable_locations") or {}).get(var, var)
    arr = np.asarray(mod.read_array(filepath, location))
    if arr.size == 0:
        raise ValueError(f"read_array({var!r}) returned an empty array")
    if arr.ndim == 0:
        raise ValueError(
            f"read_array({var!r}) returned a 0-d scalar; scalars belong in "
            f"'attributes', not 'variables'")
    return var, arr


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
    from llm_config import get_lm

    class WriteAdapter(dspy.Signature):
        """Identify a scientific data file's format and write a reader module.

        You are given evidence about a file (name, extension, size, hex dumps).
        Infer the format and choose the appropriate ALREADY-INSTALLED Python
        reader library for it (e.g. numpy, netCDF4, pyarrow, h5py, scipy.io,
        astropy.io.fits, zarr, asdf, pygio). Do NOT hand-parse raw bytes — use
        the library. Write a complete, self-contained Python module defining
        EXACTLY this contract:

            FILETYPE = "<short format name>"
            EXTENSIONS = ["<.ext>", ...]   # extensions this format uses

            def inspect(filepath):
                return {
                    "filetype": FILETYPE,
                    "variables": [<field/column/dataset names>],   # non-empty
                    "dimensions": {...},   # e.g. {"particles": N} or {"grid": (nx,ny,nz)}
                    "attributes": {...},   # JSON-friendly metadata (scalars, units, ...)
                    # optional, only when a variable's name differs from how the
                    # library addresses it:
                    # "variable_locations": {"<variable>": <location>},
                }

            def read_array(filepath, location):
                # Return the ONE full numpy array for this variable/location.
                # No slicing, no subsetting — the framework does all selection.
                ...

        Do NOT write a load() function. Do NOT do any subsampling or selection.
        The framework owns all of that; your module only knows how to (a) list
        what is in the file and (b) fetch one named array.

        Rules:
        - Use the standard reader library for the identified format; assume it
          is installed. Import it inside the functions.
        - 'variables' must be non-empty and contain the real data fields. A
          scalar stored in the file (e.g. box_size) belongs in 'attributes',
          not 'variables'.
        - If the variables are particle-like 1-D columns of equal length N,
          report {"particles": N} in dimensions.
        - All metadata values must be JSON-serializable (use .item()/float()).
        - Read real metadata from filepath; never invent values. If required
          metadata is missing (e.g. no shape/dtype in a filename), raise a
          clear ValueError — never fall back to a hardcoded guess.
        - Output only the Python module source.
        """
        file_evidence: str = dspy.InputField(
            desc="Filename, extension, size, hex dump of head/tail")
        previous_attempt: str = dspy.InputField(
            desc="Previous code and the error it produced; empty on first try. "
                 "Fix the error; do not repeat it.")
        module_code: str = dspy.OutputField(
            desc="Complete Python module source (FILETYPE, EXTENSIONS, inspect, read_array)")

    dspy.configure(lm=get_lm(max_tokens=16000))
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
    if not callable(getattr(mod, "read_array", None)):
        raise ValueError("generated module does not define read_array(filepath, location)")
    return mod


def _cache_path_for(filepath):
    ext = os.path.splitext(filepath)[1].lower().lstrip(".") or "noext"
    return os.path.join(GENERATED_ADAPTERS_DIR, f"{ext}.py")


def _wrap_and_register(mod, fallback_ext=None):
    import adapters as _adapters

    filetype = getattr(mod, "FILETYPE", None) or "LLMGenerated"
    exts = list(getattr(mod, "EXTENSIONS", []) or [])
    # The extension that actually produced this adapter is canonical — always
    # claim it, even if the module's EXTENSIONS list disagrees/omits it.
    if fallback_ext and fallback_ext not in exts:
        exts.append(fallback_ext)

    # Unique registry name: two formats claiming the same FILETYPE must not
    # silently shadow each other (the name is also the load() routing key).
    name = f"{filetype} (LLM)"
    if name in _adapters._GENERATED_BY_NAME and fallback_ext:
        name = f"{filetype}{fallback_ext} (LLM)"

    adapter = GeneratedModuleAdapter(name, mod, exts)
    register_generated_adapter(adapter)
    return adapter


# ---------------------------------------------------------------------------
# Public entry points (called by adapters.get_adapter)
# ---------------------------------------------------------------------------
def load_cached_adapters():
    """Register any previously-frozen generated adapters. No LLM/API needed."""
    if not os.path.isdir(GENERATED_ADAPTERS_DIR):
        return
    for fname in sorted(os.listdir(GENERATED_ADAPTERS_DIR)):  # deterministic order
        if not fname.endswith(".py"):
            continue
        path = os.path.join(GENERATED_ADAPTERS_DIR, fname)
        try:
            with open(path) as f:
                mod = _exec_module(f.read(), f"vislang_gen_{fname[:-3]}")
            adapter = _wrap_and_register(mod, fallback_ext="." + fname[:-3])
            _say(f"Loaded frozen adapter {adapter.name!r} from {path}")
        except Exception as e:
            _say(f"Skipping cached adapter {path}: {type(e).__name__}: {e} "
                 f"(delete it to regenerate)")
            continue


def try_generate_adapter(filepath):
    """Generate, validate, freeze, and register an adapter for filepath.

    Returns a FormatAdapter instance on success, or None if generation isn't
    possible (deps/key missing) or never validated. get_adapter() turns None
    into a clean UnsupportedFormatError.
    """
    try:
        generator = _configure_generator()
    except Exception as e:
        _say(f"LLM unavailable ({type(e).__name__}: {e}) — cannot generate an adapter.")
        return None

    size = os.path.getsize(filepath)
    _say(f"Gathering evidence: {os.path.basename(filepath)} "
         f"({size / 1e6:.1f} MB, ext {os.path.splitext(filepath)[1] or '(none)'!r})")
    evidence = _gather_evidence(filepath)
    fallback_ext = os.path.splitext(filepath)[1].lower() or None
    previous_attempt = ""

    for attempt in range(1, MAX_RETRIES + 1):
        _say(f"LLM attempt {attempt}/{MAX_RETRIES}: asking for "
             f"inspect() + read_array() module...")
        code = ""
        try:
            prediction = generator(file_evidence=evidence,
                                   previous_attempt=previous_attempt)
            code = _strip_fences(prediction.module_code)
            mod = _exec_module(code, f"vislang_gen_attempt_{attempt}")
            _say(f"  generated: FILETYPE={getattr(mod, 'FILETYPE', '?')!r}, "
                 f"EXTENSIONS={getattr(mod, 'EXTENSIONS', [])!r} "
                 f"({len(code.splitlines())} lines)")

            # Conformance against the real file (the trust step):
            result = mod.inspect(filepath)
            _validate_inspect(result)
            _say(f"  conformance: inspect() OK — {len(result['variables'])} variables "
                 f"{result['variables'][:6]}, dimensions={result['dimensions']}")
            var, arr = _check_read_array(mod, filepath, result)
            _say(f"  conformance: read_array({var!r}) OK — shape {arr.shape}, dtype {arr.dtype}")
        except Exception:
            err = traceback.format_exc(limit=5)
            _say(f"  attempt {attempt} failed: {err.strip().splitlines()[-1]}")
            previous_attempt = (
                f"--- Attempt {attempt} code ---\n{code}\n"
                f"--- Error ---\n{err}"
            )
            continue

        # Success — freeze and register
        os.makedirs(GENERATED_ADAPTERS_DIR, exist_ok=True)
        cache_path = _cache_path_for(filepath)
        with open(cache_path, "w") as f:
            f.write(code)
        adapter = _wrap_and_register(mod, fallback_ext=fallback_ext)
        _say(f"✓ adapter {adapter.name!r} validated and frozen to {cache_path} "
             f"— future {fallback_ext or '(no-ext)'} files skip the LLM.")
        return adapter

    _say(f"✗ all {MAX_RETRIES} attempts failed; giving up on {filepath}.")
    return None
