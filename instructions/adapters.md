# Adapters & the Trust Ladder

Format dispatch lives in `adapters.py`. Each adapter implements one contract:
`can_handle` (cheap recognition), `inspect` (metadata → `DatasetInfo`), and
`read_array` (one selected numpy array). All orchestration — variable
resolution, subsampling, selection bookkeeping — is universal framework code in
`my_load.py`; adapters only know how to recognize a file and fetch one array.
`DatasetInfo` is the format boundary: once `inspect` fills it, everything
downstream is format-blind.

## The trust ladder
- **Tier 0 — installed, hand-written readers (full trust).** `yt` first (it
  auto-detects most simulation formats with proper fields/units), then magic-byte
  fallbacks: HDF5 (h5py), FITS (astropy), GenericIO/HACC (pygio). Registry order
  is `[yt, HDF5, FITS, GenericIO]`; first `can_handle` wins.
- **Tier 1 — no built-in reader, but a trusted library exists.** The LLM
  (`llm_adapter.py`) identifies the format and writes a small module with only
  `inspect` + `read_array` using that library. It is run through a hand-written
  conformance gate against the real file, then **frozen** to
  `generated_adapters/<ext>.py` and registered — becoming Tier 0 for future
  files (no further LLM calls).
- **Tier 2 — headerless / handmade raw bytes with no library.** Generally NOT
  supported; raise `UnsupportedFormatError` rather than guess a byte layout. The
  one sanctioned exception is a **declared, verifiable convention** — e.g.
  `generated_adapters/raw.py` parses `<name>_<nx>x<ny>x<nz>_<dtype>.raw` and
  **checks the file's byte count equals nx·ny·nz·itemsize** before trusting it.
  The filename + size check is the deterministic oracle.

## The binding path (custom HDF5) — `schema_binding.py`
For HDF5 the *container* is known (h5py reads any HDF5) but the *semantics* are
not. So: fingerprint the schema → cache hit reuses a frozen declarative binding
(no LLM) → miss has the LLM propose a JSON binding (data, not code) that MUST
pass `verify_binding` (a deterministic oracle against the file's own metadata)
before it is used and frozen. No exec, no run-and-pray.

See `vislang://instructions/soundness` for why it works this way.
