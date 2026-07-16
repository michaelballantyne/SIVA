"""Extent catalog verification (REMOTE_COMPUTE_PLAN.md Phase 1).

Plain-python asserts (no pytest on the cluster). Run from the repo root:
    python tests/test_catalog.py
Covers: exact store/lookup, misses, delta partitioning (the "+1 field" case),
grid containment reuse (a cached superset slab sliced to serve a narrower
strided request), phase-misalignment rejection, invalidation, schema
round-trip, persistence across instances, and corrupt-manifest recovery.
"""

import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from my_catalog import ExtentCatalog, make_source_id

PASS = []


def check(name, cond, detail=""):
    assert cond, f"{name}: {detail}"
    PASS.append(name)
    print(f"  ok  {name}")


def main():
    tmp = tempfile.mkdtemp(prefix="vislang_catalog_test_")
    root = os.path.join(tmp, "cache")
    rng = np.random.default_rng(0)

    # -- source ids -----------------------------------------------------------
    sid = make_source_id("ssh://host/data/run42.h5", 1024, 1700000000.0)
    check("source_id_stable",
          sid == make_source_id("ssh://host/data/run42.h5", 1024, 1700000000.0))
    check("source_id_hex16", len(sid) == 16 and int(sid, 16) >= 0)
    check("source_id_mtime_sensitive",
          sid != make_source_id("ssh://host/data/run42.h5", 1024, 1700000001.0))

    cat = ExtentCatalog(root=root)

    # -- schema round-trip ----------------------------------------------------
    schema = {"variables": ["a", "b", "c"], "dimensions": {"grid": [200, 60, 30]},
              "positions": None}
    cat.store_schema(sid, schema)
    check("schema_roundtrip", cat.schema(sid) == schema)
    check("schema_unknown_none", cat.schema("deadbeefdeadbeef") is None)

    # -- exact store/lookup ---------------------------------------------------
    key = {"grid_ranges": [[0, 100, 2], [0, 60, 1], [0, 30, 1]]}
    a = rng.random((50, 60, 30)).astype(np.float32)
    cat.store(sid, "a", key, a)
    hit = cat.lookup(sid, "a", key)
    check("exact_hit", hit is not None and np.array_equal(hit, a))
    check("exact_hit_dtype", hit.dtype == a.dtype)
    check("miss_other_var", cat.lookup(sid, "zzz", key) is None)
    # a different key only misses when containment can't serve it either —
    # stride 5 is incompatible with the cached stride 2 (5 % 2 != 0)
    check("miss_other_key",
          cat.lookup(sid, "a", {"grid_ranges": [[0, 100, 5], [0, 60, 1], [0, 30, 1]]}) is None)
    # a genuinely CONTAINED sub-request of the same extent is served by slicing
    sub = cat.lookup(sid, "a", {"grid_ranges": [[0, 100, 2], [0, 60, 1], [0, 29, 1]]})
    check("containment_of_own_key", sub is not None and sub.shape == (50, 60, 29))
    check("miss_other_source", cat.lookup("deadbeefdeadbeef", "a", key) is None)

    # key order must not matter (canonicalization)
    pkey = {"post_ops": [{"op": "RowSample", "factor": 10}], "particles": 10}
    pts = rng.random((17, 3))
    cat.store(sid, "pos", pkey, pts)
    reordered = {"particles": 10, "post_ops": [{"op": "RowSample", "factor": 10}]}
    check("canonical_key_order",
          np.array_equal(cat.lookup(sid, "pos", reordered), pts))

    # overwriting the same key replaces, not duplicates
    a2 = a + 1.0
    cat.store(sid, "a", key, a2)
    check("overwrite_same_key", np.array_equal(cat.lookup(sid, "a", key), a2))

    # -- delta partition: the "+1 field" case ---------------------------------
    cat.store(sid, "b", key, rng.random((50, 60, 30)))
    have, missing = cat.delta(sid, ["a", "b", "c"], key)
    check("delta_have", sorted(have) == ["a", "b"])
    check("delta_missing", missing == ["c"])
    check("delta_have_values", np.array_equal(have["a"], a2))

    # -- grid containment reuse -----------------------------------------------
    orig = rng.random((200, 60, 30)).astype(np.float32)
    slab_key = {"grid_ranges": [[0, 200, 1], [0, 60, 1], [None, None, 1]]}
    cat.store(sid, "d", slab_key, orig)

    req = {"grid_ranges": [[0, 100, 2], [10, 50, 2], [None, None, 3]]}
    got = cat.lookup(sid, "d", req)
    check("containment_hit", got is not None)
    check("containment_equals_slice",
          np.array_equal(got, orig[0:100:2, 10:50:2, ::3]))

    # offset start on an unstrided cache, phase-aligned ((30-0) % 3 == 0)
    req2 = {"grid_ranges": [[30, 150, 3], [0, 60, 1], [None, None, 1]]}
    check("containment_offset_start",
          np.array_equal(cat.lookup(sid, "d", req2), orig[30:150:3]))

    # same-stride cached superset (K == k, A == a)
    strided = orig[0:200:2]
    cat.store(sid, "e", {"grid_ranges": [[0, 200, 2], [0, 60, 1], [0, 30, 1]]}, strided)
    req3 = {"grid_ranges": [[0, 100, 2], [0, 60, 1], [0, 30, 1]]}
    check("containment_same_stride",
          np.array_equal(cat.lookup(sid, "e", req3), orig[0:100:2]))

    # -- conservative rejections ----------------------------------------------
    # phase-misaligned against an unstrided cache: (1-0) % 2 != 0
    bad = {"grid_ranges": [[1, 100, 2], [0, 60, 1], [None, None, 1]]}
    check("reject_phase_unstrided", cat.lookup(sid, "d", bad) is None)
    # same stride, different phase: K==k==2 but A=0 != a=2
    bad2 = {"grid_ranges": [[2, 100, 2], [0, 60, 1], [0, 30, 1]]}
    check("reject_phase_same_stride", cat.lookup(sid, "e", bad2) is None)
    # request exceeds cached stop
    check("reject_out_of_bounds",
          cat.lookup(sid, "d", {"grid_ranges": [[0, 100, 2], [0, 61, 1], [None, None, 1]]}) is None)
    # cached stop bounded, request stop unknown (null): not comparable
    check("reject_null_vs_bounded",
          cat.lookup(sid, "d", {"grid_ranges": [[0, 100, 2], [None, None, 1], [None, None, 1]]}) is None)
    # incompatible strides (cached K=2, requested k=3)
    check("reject_stride_mismatch",
          cat.lookup(sid, "e", {"grid_ranges": [[0, 100, 3], [0, 60, 1], [0, 30, 1]]}) is None)
    # post_ops present: containment must not apply
    check("reject_post_ops",
          cat.lookup(sid, "d", {"grid_ranges": [[0, 100, 2], [0, 60, 1], [None, None, 1]],
                                "post_ops": [{"op": "VoxelMask"}]}) is None)
    # ndim mismatch
    check("reject_ndim_mismatch",
          cat.lookup(sid, "d", {"grid_ranges": [[0, 100, 2], [0, 60, 1]]}) is None)

    # containment also feeds delta
    have2, missing2 = cat.delta(sid, ["d", "x"], req)
    check("delta_via_containment", list(have2) == ["d"] and missing2 == ["x"])

    # -- persistence across instances ------------------------------------------
    cat2 = ExtentCatalog(root=root)
    check("reopen_schema", cat2.schema(sid) == schema)
    check("reopen_lookup", np.array_equal(cat2.lookup(sid, "a", key), a2))
    check("reopen_containment", np.array_equal(cat2.lookup(sid, "d", req),
                                               orig[0:100:2, 10:50:2, ::3]))

    # -- invalidate -------------------------------------------------------------
    sid2 = make_source_id("ssh://host/data/other.h5", 7, 1.0)
    cat2.store(sid2, "keepme", {"grid_ranges": [[0, 4, 1]]}, np.arange(4))
    n_files_before = len(os.listdir(cat2.extent_dir))
    cat2.invalidate(sid)
    check("invalidate_schema", cat2.schema(sid) is None)
    check("invalidate_lookup", cat2.lookup(sid, "a", key) is None)
    check("invalidate_files_removed", len(os.listdir(cat2.extent_dir)) < n_files_before)
    check("invalidate_other_source_intact",
          np.array_equal(cat2.lookup(sid2, "keepme", {"grid_ranges": [[0, 4, 1]]}),
                         np.arange(4)))
    check("invalidate_unknown_noop", cat2.invalidate("no_such_source") is None)

    # -- corrupt manifest recovery ----------------------------------------------
    with open(cat2.manifest_path, "w") as f:
        f.write("{{{ this is not json")
    cat3 = ExtentCatalog(root=root)
    check("corrupt_manifest_empty", cat3.schema(sid2) is None
          and cat3.lookup(sid2, "keepme", {"grid_ranges": [[0, 4, 1]]}) is None)
    cat3.store(sid2, "fresh", {"grid_ranges": [[0, 3, 1]]}, np.ones(3))
    check("corrupt_manifest_functional",
          np.array_equal(ExtentCatalog(root=root).lookup(
              sid2, "fresh", {"grid_ranges": [[0, 3, 1]]}), np.ones(3)))

    # missing manifest (fresh root) also starts empty
    cat4 = ExtentCatalog(root=os.path.join(tmp, "fresh_root"))
    check("missing_manifest_empty", cat4.schema(sid) is None)

    shutil.rmtree(tmp)
    print(f"ALL {len(PASS)} CHECKS PASSED")


if __name__ == "__main__":
    main()
