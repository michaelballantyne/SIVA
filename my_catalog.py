"""Local extent catalog — the "track what we have" half of remote compute.

Phase 1 of REMOTE_COMPUTE_PLAN.md. Every array piece we materialize from a
remote source (a narrowed field, a reduced slab) is written to a local cache
and indexed here, so the next request can be answered as `need - have = fetch`
and we never move the same bytes over the wire twice. The catalog is a plain
cache index: it records what is on local disk and computes what's missing; it
does not care how the missing pieces arrive (remote reducer, whole-file
fallback) — that logic lives in the planner.

Layout under a cache root (default ``vislang_cache``):
    catalog.json          manifest: schemas + extent index (atomic rewrite)
    extents/<hex>.npy     one numpy array per cached extent

Keys are (source_id, variable, canonical narrow_key). Beyond exact-key hits,
`lookup` reuses a cached *superset* slab when both keys are pure grid crops
(`grid_ranges` only) and the cached ranges contain and phase-align with the
request — slicing a bigger local array beats refetching. Reuse is deliberately
conservative: a false miss only costs a slower fetch, a false hit is wrong data.
"""

import hashlib
import json
import os

import numpy as np

_MANIFEST = "catalog.json"
_EXTENT_DIR = "extents"


def make_source_id(uri, size, mtime, header_hash=""):
    """Stable short identity for a remote source without hashing its bytes."""
    blob = f"{uri}|{size}|{mtime}|{header_hash}".encode()
    return hashlib.sha256(blob).hexdigest()[:16]


def _canon(narrow_key):
    """Canonical JSON for a narrow_key dict (sorted keys -> stable extent key)."""
    return json.dumps(narrow_key, sort_keys=True, separators=(",", ":"))


def _grid_only(narrow_key):
    """The narrow_key describes a pure grid crop/stride: reuse-eligible."""
    return (isinstance(narrow_key, dict) and set(narrow_key) == {"grid_ranges"}
            and isinstance(narrow_key["grid_ranges"], (list, tuple)))


def _axis_slice(req, had):
    """Slice (into the cached axis) serving req from had, or None.

    req/had are [start, stop, step] with None meaning full extent (start=0,
    stop=end-of-axis). Conservative: any case we can't prove is a miss.
    """
    if len(req) != 3 or len(had) != 3:
        return None
    a, b, k = req
    A, B, K = had
    a0 = 0 if a is None else a
    A0 = 0 if A is None else A
    k = 1 if k is None else k
    K = 1 if K is None else K
    ok_int = all(isinstance(v, int) and v >= 0 for v in (a0, A0))
    if not (ok_int and isinstance(k, int) and isinstance(K, int) and k >= 1 and K >= 1):
        return None
    if A0 > a0:
        return None                       # cached starts after the request
    if B is not None and (b is None or not isinstance(b, int) or b > B):
        return None                       # cached stop bounded; request isn't proven inside
    if K == 1:
        # cached is unstrided: any stride can be carved out, but only when the
        # request's phase lands on the cached origin (spec'd conservatism).
        if (a0 - A0) % k != 0:
            return None
        stop = None if b is None else max(b - A0, 0)
        return slice(a0 - A0, stop, k)
    if K == k:
        if A0 != a0:
            return None                   # same stride must share its phase exactly
        stop = None if b is None else -(-(b - a0) // k)   # ceil: element count
        return slice(0, stop, 1)
    return None


class ExtentCatalog:
    """JSON-manifest index over locally cached array extents."""

    def __init__(self, root="vislang_cache"):
        self.root = root
        self.extent_dir = os.path.join(root, _EXTENT_DIR)
        self.manifest_path = os.path.join(root, _MANIFEST)
        os.makedirs(self.extent_dir, exist_ok=True)
        self._manifest = self._load_manifest()

    # -- manifest persistence -------------------------------------------------

    def _load_manifest(self):
        """Read the manifest; a missing or corrupt one just starts empty."""
        try:
            with open(self.manifest_path) as f:
                m = json.load(f)
            if isinstance(m, dict) and isinstance(m.get("schemas"), dict) \
                    and isinstance(m.get("extents"), dict):
                return m
        except (OSError, ValueError):
            pass
        return {"version": 1, "schemas": {}, "extents": {}}

    def _save_manifest(self):
        """Atomic rewrite: never leave a half-written catalog.json behind."""
        tmp = self.manifest_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self._manifest, f, indent=1)
        os.replace(tmp, self.manifest_path)

    # -- schema ---------------------------------------------------------------

    def store_schema(self, source_id, schema):
        """schema = {"variables": [...], "dimensions": {...}, "positions": [...]|None}."""
        self._manifest["schemas"][source_id] = schema
        self._save_manifest()

    def schema(self, source_id):
        return self._manifest["schemas"].get(source_id)

    # -- extents --------------------------------------------------------------

    def store(self, source_id, var, narrow_key, arr):
        """Cache arr as the extent produced by narrow_key; overwrite same key."""
        key = _canon(narrow_key)
        hexid = hashlib.sha256(f"{source_id}\0{var}\0{key}".encode()).hexdigest()[:16]
        np.save(os.path.join(self.extent_dir, hexid + ".npy"), np.asarray(arr))
        entries = self._manifest["extents"].setdefault(source_id, {}).setdefault(var, [])
        entries[:] = [e for e in entries if e["key"] != key]
        entries.append({"key": key, "narrow": narrow_key, "file": hexid + ".npy",
                        "shape": list(np.asarray(arr).shape), "dtype": str(arr.dtype)})
        self._save_manifest()

    def _read(self, entry):
        try:
            return np.load(os.path.join(self.extent_dir, entry["file"]),
                           allow_pickle=False)
        except (OSError, ValueError):
            return None                   # manifest points at a lost file: miss

    def lookup(self, source_id, var, narrow_key):
        """Cached array for this exact request, or a slice of a containing
        pure-grid extent. None on any doubt — a miss is never wrong data."""
        entries = self._manifest["extents"].get(source_id, {}).get(var, [])
        key = _canon(narrow_key)
        for e in entries:
            if e["key"] == key:
                return self._read(e)
        if not _grid_only(narrow_key):
            return None
        req = narrow_key["grid_ranges"]
        for e in entries:
            if not _grid_only(e["narrow"]):
                continue
            had = e["narrow"]["grid_ranges"]
            if len(had) != len(req):
                continue
            slices = [_axis_slice(r, h) for r, h in zip(req, had)]
            if any(s is None for s in slices):
                continue
            arr = self._read(e)
            if arr is not None and arr.ndim == len(slices):
                return arr[tuple(slices)]
        return None

    def delta(self, source_id, variables, narrow_key):
        """Partition a request: ({var: cached array}, [vars we must fetch])."""
        have, missing = {}, []
        for var in variables:
            arr = self.lookup(source_id, var, narrow_key)
            if arr is None:
                missing.append(var)
            else:
                have[var] = arr
        return have, missing

    def invalidate(self, source_id):
        """Drop schema and all extents for a stale source (changed size/mtime)."""
        self._manifest["schemas"].pop(source_id, None)
        for entries in self._manifest["extents"].pop(source_id, {}).values():
            for e in entries:
                try:
                    os.remove(os.path.join(self.extent_dir, e["file"]))
                except OSError:
                    pass
        self._save_manifest()
