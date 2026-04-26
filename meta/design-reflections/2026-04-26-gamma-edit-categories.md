# Gamma edit categories vs. our test matrix

Planning note for revising `tests/test_build_cache.py` and
`tests/test_hot_reload.py`. Source: Petricek 2020, *Foundations of a live
data exploration environment*
([blog](https://tomasp.net/blog/2020/data-exploration-calculus/),
[arXiv 2002.06190](https://arxiv.org/abs/2002.06190)).

## 1. The Gamma model

Petricek binds a program to a dependency graph whose nodes are keyed by
structure plus free-variable bindings; previews cache per node and are
reused while bindings are unchanged. Figure 9 / Theorem 8 enumerates
**preview-preserving edits**: parameter/member edits, edits to unrelated
`let`s, and let-introduction/elimination via cut-and-paste. A typical
analyst edit (tweak an arg, extract a `let`, append a stage) should not
invalidate upstream work.

## 2. Edit taxonomy mapped to our cache

| category | example | expected behavior |
|---|---|---|
| `edit-mem` (param tweak) | `ThresholdRange=[100,1000]` -> `[200,1000]` | node + descendants miss; ancestors hit |
| `edit-let` (unrelated binding) | rewrite a sibling `show()` | target subtree fully hits |
| `let-intro-var` (extract constant) | `100.0` -> `lo = 100.0; ... lo` | same hash; full hit |
| `let-intro/elim-ins/del` (cut+paste, inline) | move `surf = filter(...)` | reused node; full hit |
| append-tail | add `smooth = filter(..., input=surf)` | upstream hits; new node misses |
| prepend-source (swap `FileName`) | new dataset path | source + descendants miss |
| file-mtime change (same code) | `touch output.vti` | descendants miss via fingerprint |
| no-op rewrite (whitespace) | reformat | identical hash; full hit |

## 3. Gaps in current matrix

- `test_build_cache.py`: no `let-intro-var` extraction test (does
  `lo = 100.0; ThresholdRange=[lo, 1000]` hash-equal the inline form?); no
  `let-elim` inlining; no reorder / cut-paste; no whitespace-only rewrite
  asserting `hits == node_count`. Append-tail is only tested in reverse
  (`test_cache_eviction_on_smaller_pipeline`); the forward small->big
  "all-prefix-hit" case is missing.
- `test_hot_reload.py`: `TestCacheHitsThroughHotReload` covers same-content
  idempotence and `TestNoStaleResult` covers full v1->v2 replacement, but
  no partial-edit test asserts `cache.hits > 0` in `status.json` after a
  single-param edit. No file-mtime-only change to a referenced dataset.

## 4. Pitfalls from The Gamma

- **Determinacy (Thm 7):** node-keyed cache is valid only if structural
  identity survives edits. Our `stable_hash((kind, params, parent_hashes))`
  is the analogue, but `params` holds raw Python values; dict-key order,
  numpy dtype drift, or float-vs-int could spuriously break identity. Add
  a property test.
- **Free-variable scope (lemma 13):** reuse holds only when FVs of the
  sub-expression are unchanged. Our hash ignores Python aliasing; a
  pipeline closing over a module-level constant won't invalidate when that
  constant mutates.
- **External effects:** Petricek assumes purity. File mtimes cover VTK
  readers; HTTP, env vars, in-memory arrays-by-reference are invisible.
