# Hashing vs. structural keys in the build cache

A reflection prompted by a question that turned out to have a sharper
answer than expected: do we actually benefit from content-hashing nodes
in the build cache, vs. keying the cache on a structural tuple directly?

## Two different uses of hashing in VisLang

The codebase uses sha256 in two places that are easy to conflate but
serve different purposes:

1. **`source_hash`** (`vislang/hot_reload.py`): sha256 of the
   `view-*.py` file contents. Used by `BuildCoordinator` to dedupe
   concurrent build requests for the same file content, and surfaced as
   a short prefix in `pipeline_status()` output and log lines so a human
   can tell which in-flight build is which. Also written to
   `view-<name>.status.json`.
2. **Per-node content hash** (`vislang/dsl.py:_compute_content_hash`,
   `vislang/build_cache.py`): sha256 of `(vtk_class, params,
   parent_hashes)` for each DSL node. Used as the dict key for the
   `BuildCache`. Never escapes the build process — does not appear in
   status files, logs, or any external interface.

The arguments for and against hashing differ between these two uses.

## `source_hash` earns its keep

For file-level identity, sha256 is doing real work:

- **Dedup of identical saves.** If a file is saved twice with the same
  content (or two clients submit the same file), the coordinator
  recognizes them as one build. A monotonic counter or timestamp
  couldn't do this; path+mtime would falsely distinguish content-equal
  saves.
- **Stable build IDs in human-facing output.** `"Build in flight: hash
  4f3a2c..."` lets a user (or another tool reading `status.json`)
  identify a build across tool calls and log lines.

Both uses fundamentally want content-equality on a file. Hashing is the
natural answer.

## Per-node hashing isn't paying for itself

The per-node hash is a different story. It is *only* used as a dict key
in `BuildCache`. Walking through the supposed benefits of hashing for
this case:

1. **O(1) deep compare (Merkle property).** Real if you're comparing
   subtrees outside a hash table, or transmitting a subtree across a
   process boundary and want a cheap fingerprint. We do neither. Cache
   lookup is `dict[hash, vtk_object]`, which is O(1) for any hashable
   key. A structural tuple `(vtk_class, frozen_params, parent_node_id)`
   — where `parent_node_id` is interned identity, not a recursive
   structural key — gives the same O(1) lookup. The only structural
   variant where deep compare costs would bite is the naive
   "tuple-of-tuples all the way down," and there's no reason to write
   that.
2. **Stable, printable IDs.** Real if the IDs are surfaced. Per-node
   hashes never appear in `status.json`, log messages, or any user-
   visible output. The printable-ID benefit is unrealized.
3. **Cross-process / persistent caches.** Speculative. There is no
   on-disk cache today, and we may never want one — a long-lived MCP
   process holds the in-memory cache for the duration of a session.
4. **Edit-stability under refactoring** (the Petricek/Gamma question).
   Initially I framed this as a hashing-vs-structural distinction. It
   isn't. Whether `lo = 100.0; ... lo` cache-hits against the inline
   `100.0` is determined by *what goes into the key* (post-evaluation
   value vs. syntactic form), not by whether the key is then digested.
   Modulo collisions, hash equality and structural equality decide
   identically on the same input.

## What hashing costs at the per-node layer

Not much, but not zero:

- **Debuggability of cache misses.** When a build doesn't hit and you
  expected it to, a 32-byte digest tells you nothing about which input
  changed. A structural tuple lets you diff the components directly.
  Recoverable by logging the tuple alongside the digest, but currently
  we don't.
- **The repr-fallback footgun in `stable_hash`** (`build_cache.py:54`).
  Unknown types fall back to `repr(obj)`, which can collide silently
  for two semantically distinct objects sharing a repr — producing a
  *false hit*, the worst possible failure mode. With structural
  keying using Python's default `==` on unknown types, the failure
  mode would be a *false miss* (rebuild unnecessarily) — which fails
  safe. This is the realistic correctness risk in this design, and
  the design reflection at `2026-04-26-gamma-edit-categories.md` §4
  already flags adjacent concerns (dict-key order, dtype drift,
  float-vs-int).

The fallback issue isn't intrinsic to hashing — `stable_hash` could
raise on unknown types, or fall back to `id(obj)` (which fails safe
the same way structural would). But it's a bug the current hashing
implementation has, and a structural-key implementation wouldn't have
the same trap by default.

## Implications

Two reasonable directions, neither urgent:

- **Keep hashing, fix the fallback.** Smallest change. Replace the
  `repr` fallback in `stable_hash` with `raise` (preferred) or
  `id(obj) + warning.log` (safer if we can't enumerate param types).
  Optionally log the structural tuple alongside the hash on cache
  miss for debuggability. Removes the only active correctness risk
  while keeping the operational story unchanged.
- **Switch the per-node cache to structural keys.** Use
  `(vtk_class, frozen_params, parent_node_id)` as the dict key.
  Eliminates the canonicalization-correctness category entirely
  (failures become rebuilds, not miscaches) and makes cache misses
  introspectable. Costs nothing in lookup performance. The only thing
  given up is a uniform digest for per-node identity, which we don't
  use anyway.

`source_hash` should stay as it is in either case — its use of hashing
is well-justified.

## What I learned

The original framing — "is hashing earning its keep?" — collapsed two
unrelated design choices: file-level content-equality (where hashing
is the right tool) and per-node cache key encoding (where it's
incidental). Once separated, the per-node case clearly doesn't gain
anything from being a hash today; the choice is between
"hash-with-fixed-fallback" and "structural key," and the structural
key is moderately better on debuggability and fail-safety with no
realized downside.

Edit-stability under refactoring (the Gamma question) is also not a
hashing concern at all — it's about what the key's domain is, which is
orthogonal. That conflation is worth being careful about in future
reasoning.
