"""Binding path for self-describing containers whose *semantics* are unknown.

For a custom HDF5 file the container is fully known — h5py reads any HDF5 and
its internal map tells us where every dataset's bytes live. What we don't know
is the *binding*: which datasets are variables, what the dimensions are, where
the metadata lives. That interpretation is the one good job for an LLM, and it
is verifiable without executing any generated code:

    1. h5py deterministically extracts the schema tree (paths, shapes, dtypes,
       attribute keys/values) — small text, no bulk data.
    2. Fingerprint the schema (structure, not sizes) -> a signature.
    3. Seen the signature -> reuse the frozen, verified binding (no LLM).
       Fresh -> the LLM proposes a binding SPEC (data, not code).
    4. Verify every claim in the spec against the file's own metadata. Pass ->
       freeze it keyed by the signature. Fail -> reject (and, for the LLM path,
       retry with the specific violation).

No exec, no run-and-pray: the spec is declarative and the file's metadata is the
oracle. The LLM never reads the data and never cuts bytes.
"""

import os
import json
import hashlib

from datasetInfo import DatasetInfo

MAX_BINDING_RETRIES = 4
BINDING_CACHE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "binding_cache")


# ---------------------------------------------------------------------------
# Schema extraction (deterministic, h5py)
# ---------------------------------------------------------------------------
def _attr_value(v):
    """Coerce an HDF5 attribute to a JSON-friendly value (small ones only).

    Order matters: a numpy byte scalar (np.bytes_) is BOTH np.generic and bytes,
    and `.item()` yields a Python `bytes` — so unwrap numpy first, then decode the
    result. Otherwise the raw bytes survive into json.dumps and crash it
    (e.g. a `format: b'nyx-lyaf'` attribute)."""
    try:
        import numpy as np
        if isinstance(v, np.ndarray):
            if v.size > 16:
                return f"<array {tuple(v.shape)} {v.dtype}>"
            v = v.tolist()
        elif isinstance(v, np.generic):
            v = v.item()
    except Exception:
        pass
    return _decode_bytes(v)


def _decode_bytes(v):
    """Recursively decode bytes (incl. inside lists from byte arrays) to str."""
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    if isinstance(v, (list, tuple)):
        return [_decode_bytes(x) for x in v]
    return v


def extract_schema(filepath):
    """Walk an HDF5 file and return its schema tree (no bulk data read)."""
    import h5py

    datasets = {}
    group_attrs = {}
    group_attr_values = {}

    with h5py.File(filepath, 'r') as f:
        def visit(name, obj):
            if isinstance(obj, h5py.Dataset):
                datasets[name] = {
                    "shape": list(obj.shape),
                    "ndim": obj.ndim,
                    "dtype": str(obj.dtype),
                }
            elif isinstance(obj, h5py.Group) and len(obj.attrs):
                group_attrs[name] = sorted(obj.attrs.keys())
                group_attr_values[name] = {k: _attr_value(obj.attrs[k]) for k in obj.attrs}

        f.visititems(visit)
        if len(f.attrs):
            group_attrs["/"] = sorted(f.attrs.keys())
            group_attr_values["/"] = {k: _attr_value(f.attrs[k]) for k in f.attrs}

    return {
        "datasets": datasets,
        "group_attrs": group_attrs,
        "group_attr_values": group_attr_values,
    }


# ---------------------------------------------------------------------------
# Signature (single hash over a canonical, size-normalized schema)
# ---------------------------------------------------------------------------
def canonical_schema(schema):
    """Structure-only view used for the signature: dataset paths + dtype + ndim
    + TRAILING shape dims (the leading count axis is dropped, so different N
    hash the same), plus sorted group attribute keys."""
    items = []
    for path in sorted(schema["datasets"]):
        d = schema["datasets"][path]
        trailing = list(d["shape"][1:])  # drop leading count axis (data, not layout)
        items.append([path, d["dtype"], d["ndim"], trailing])
    attrs = {g: sorted(keys) for g, keys in sorted(schema["group_attrs"].items())}
    return {"datasets": items, "group_attrs": attrs}


def schema_signature(schema):
    blob = json.dumps(canonical_schema(schema), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Verification (the oracle — deterministic, never LLM-generated)
# ---------------------------------------------------------------------------
def verify_binding(binding, schema):
    """Check every claim in a binding against the file's own metadata.
    Raises ValueError on the first violation; returns True if sound."""
    if not isinstance(binding, dict):
        raise ValueError("binding must be an object")

    datasets = schema["datasets"]
    group_attrs = schema.get("group_attrs", {})

    dims = binding.get("dimensions", {})
    if not isinstance(dims, dict):
        raise ValueError("binding.dimensions must be an object")

    # Resolve each dimension's length from its source dataset.
    dim_lengths = {}
    for dname, dspec in dims.items():
        if not isinstance(dspec, dict) or "source" not in dspec:
            raise ValueError(f"dimension {dname!r} must specify a 'source' dataset")
        src = dspec["source"]
        if src not in datasets:
            raise ValueError(f"dimension {dname!r} source {src!r} is not a dataset in the file")
        axis = dspec.get("axis", 0)
        shape = datasets[src]["shape"]
        if not (0 <= axis < len(shape)):
            raise ValueError(f"dimension {dname!r} axis {axis} out of range for {src} shape {shape}")
        dim_lengths[dname] = shape[axis]

    variables = binding.get("variables")
    if not isinstance(variables, list) or not variables:
        raise ValueError("binding.variables must be a non-empty list")

    seen = set()
    for v in variables:
        if not isinstance(v, dict):
            raise ValueError(f"variable entry must be an object: {v!r}")
        name = v.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"variable name must be a non-empty string: {v!r}")
        if name in seen:
            raise ValueError(f"duplicate variable name {name!r}")
        seen.add(name)

        src = v.get("source")
        if src not in datasets:
            raise ValueError(f"variable {name!r} source {src!r} is not a dataset in the file")
        shape = datasets[src]["shape"]

        comp = v.get("component")
        if comp is not None:
            # A component selects a column of a 2-D dataset (e.g. (N,3) -> x/y/z).
            # 1-D has no column; 3-D+ is a grid variable, not a component source.
            if len(shape) != 2:
                raise ValueError(
                    f"variable {name!r} has a component but source {src} is "
                    f"{len(shape)}-D {shape}; component is only for 2-D datasets")
            if not (0 <= comp < shape[-1]):
                raise ValueError(f"variable {name!r} component {comp} out of range for {src} shape {shape}")

        dim = v.get("dim")
        if dim is not None:
            if dim not in dim_lengths:
                raise ValueError(f"variable {name!r} references unknown dimension {dim!r}")
            if len(shape) == 0:
                raise ValueError(
                    f"variable {name!r} has dimension {dim!r} but source {src} is 0-D (scalar)")
            if shape[0] != dim_lengths[dim]:
                raise ValueError(
                    f"variable {name!r} leading length {shape[0]} != dimension "
                    f"{dim!r} length {dim_lengths[dim]}")

    for g in binding.get("attributes_from", []):
        if g not in group_attrs:
            raise ValueError(f"attributes_from group {g!r} has no attributes / not found")

    return True


# ---------------------------------------------------------------------------
# Build a DatasetInfo from a verified binding
# ---------------------------------------------------------------------------
def build_info(filepath, schema, binding):
    datasets = schema["datasets"]

    dimensions = {}
    for dname, dspec in binding.get("dimensions", {}).items():
        src = dspec["source"]
        axis = dspec.get("axis", 0)
        dimensions[dname] = datasets[src]["shape"][axis]

    # Grid variables are multi-D (nx, ny, nz). region/subsample need the full
    # shape TUPLE — the convention every other adapter uses — not a single-axis
    # length, so expose it here (overriding any single-axis 'grid' set above).
    grid_shapes = {tuple(datasets[v["source"]]["shape"])
                   for v in binding["variables"]
                   if v.get("component") is None
                   and len(datasets[v["source"]]["shape"]) >= 3}
    if len(grid_shapes) == 1:
        dimensions["grid"] = grid_shapes.pop()

    variables = [v["name"] for v in binding["variables"]]

    attributes = {}
    attr_values = schema.get("group_attr_values", {})
    for g in binding.get("attributes_from", []):
        for k, val in attr_values.get(g, {}).items():
            attributes[k] = val

    info = DatasetInfo(filepath, "HDF5", variables,
                       dimensions=dimensions, attributes=attributes)
    # Per-variable read token consumed by HDF5Adapter.read_array. This is the
    # single location mechanism the universal load() uses (generic HDF5 has no
    # entry and defaults to the variable name = dataset path).
    info.variable_locations = {
        v["name"]: {"source": v["source"],
                    "component": v.get("component"),
                    "dim": v.get("dim")}
        for v in binding["variables"]
    }
    info.binding = binding  # kept for provenance; load() reads variable_locations
    return info


# ---------------------------------------------------------------------------
# Binding cache (signature -> verified binding)
# ---------------------------------------------------------------------------
def _cache_path(sig):
    return os.path.join(BINDING_CACHE_DIR, f"{sig}.json")


def load_cached_binding(sig):
    path = _cache_path(sig)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            return json.load(f).get("binding")
    except Exception:
        return None


def save_cached_binding(sig, schema, binding):
    os.makedirs(BINDING_CACHE_DIR, exist_ok=True)
    with open(_cache_path(sig), "w") as f:
        json.dump({"signature": sig,
                   "canonical_schema": canonical_schema(schema),
                   "binding": binding}, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# LLM proposer (declarative spec out; verified before use)
# ---------------------------------------------------------------------------
_binder = None


def _configure_binder():
    global _binder
    if _binder is not None:
        return _binder

    import dspy  # ImportError if not installed
    from llm_config import get_lm

    class BindSchema(dspy.Signature):
        """Map an HDF5 file's schema to a semantic binding.

        You are given the schema of a self-describing HDF5 file: every dataset
        path with its shape and dtype, and the attribute keys/values on each
        group. Decide which datasets are the data VARIABLES, what the DIMENSIONS
        are, and which groups hold the global ATTRIBUTES. Output ONLY a JSON
        object (no prose, no code) of this form:

            {
              "dimensions": {
                 "particles": {"source": "<dataset path>", "axis": 0}
              },
              "variables": [
                 {"name": "x",  "source": "PartType1/Coordinates", "component": 0, "dim": "particles"},
                 {"name": "id", "source": "PartType1/ParticleIDs", "dim": "particles"}
              ],
              "attributes_from": ["/Header"]
            }

        Rules:
        - Only reference dataset paths and groups that appear in the schema.
        - "component" selects a column of a 2-D dataset (e.g. Coordinates (N,3)):
          0=x, 1=y, 2=z. Omit it for 1-D datasets.
        - Every variable on a dimension must have that dimension's length as its
          leading axis. A 3-D dataset (nx,ny,nz) is a grid variable: give it a
          "grid" dimension (or omit "dim"), no component.
        - Give variables clear physical names (x, y, z, vx, vy, vz, mass, id,
          density, temperature, ...). Group attributes like /Header carry
          BoxSize, Redshift, etc.
        """
        schema_json: str = dspy.InputField(desc="JSON schema: datasets (path/shape/dtype) and group attributes")
        previous_error: str = dspy.InputField(
            desc="Why the previous binding failed verification; empty on first try. Fix it.")
        binding_json: str = dspy.OutputField(desc="A single JSON binding object")

    dspy.configure(lm=get_lm(max_tokens=8000))
    _binder = dspy.ChainOfThought(BindSchema)
    return _binder


def _strip_fences(text):
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines)
    return text


def _schema_for_llm(schema):
    """Compact text of the schema shown to the LLM."""
    return json.dumps({
        "datasets": {p: {"shape": d["shape"], "dtype": d["dtype"]}
                     for p, d in schema["datasets"].items()},
        "group_attributes": schema.get("group_attr_values", schema.get("group_attrs", {})),
    }, indent=2, default=str)


def propose_and_verify(schema):
    """Ask the LLM for a binding and verify it against the schema. Returns a
    verified binding dict, or None if the LLM path is unavailable / never
    validated within the retry budget."""
    try:
        binder = _configure_binder()
    except Exception:
        return None

    schema_text = _schema_for_llm(schema)
    previous_error = ""

    for attempt in range(1, MAX_BINDING_RETRIES + 1):
        prediction = None  # may stay None if binder() itself raises
        try:
            prediction = binder(schema_json=schema_text, previous_error=previous_error)
            binding = json.loads(_strip_fences(prediction.binding_json))
            verify_binding(binding, schema)  # deterministic check vs the file
        except Exception as e:
            produced = getattr(prediction, 'binding_json', '') if prediction is not None else ''
            previous_error = (
                f"Attempt {attempt} produced:\n{produced}\n"
                f"Verification error: {e}"
            )
            continue
        return binding

    return None


# ---------------------------------------------------------------------------
# Entry point used by HDF5Adapter.inspect
# ---------------------------------------------------------------------------
def bind_hdf5(filepath):
    """Return a richly-bound DatasetInfo for an HDF5 file, or None to let the
    caller fall back to a generic flat listing."""
    schema = extract_schema(filepath)
    sig = schema_signature(schema)

    binding = load_cached_binding(sig)
    if binding is None:
        binding = propose_and_verify(schema)
        if binding is None:
            return None
        save_cached_binding(sig, schema, binding)

    # Re-verify even a cached binding against THIS file's schema (guards against
    # signature collisions or schema drift). Cheap and deterministic.
    verify_binding(binding, schema)
    return build_info(filepath, schema, binding)
