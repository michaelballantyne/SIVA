"""Purity and statefulness tests for PyVista/VTK operations.

These tests probe whether PyVista filter operations are pure (same input =
same output) or whether hidden state, aliasing, and mutation can cause the
tracked-execution cache to return stale or wrong results.

Each test documents:
- What it's testing and why it matters for caching
- What the actual behavior is (not what we wish it were)

Tests marked xfail reveal genuine caching hazards. Tests that pass confirm
safe behavior. See PURITY-ANALYSIS.md for the full analysis.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.core import DAG
from tracked_execution.dispatch import stable_hash
from tracked_execution.proxy import TrackedProxy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_proxy(mesh, dag=None):
    """Wrap a mesh in a TrackedProxy backed by a fresh or given DAG."""
    if dag is None:
        dag = DAG()
    h = stable_hash(("root", id(mesh)))
    dag.cache[h] = mesh
    dag.current_run.add(h)
    return TrackedProxy(mesh, h, dag), dag, h


# ===========================================================================
# 1. Source mutation after filter — does filtered output change?
# ===========================================================================

class TestSourceMutationAfterFilter:
    """Verify that PyVista filter outputs are decoupled from their source mesh.

    VTK pipelines can be lazy: filters hold a reference to upstream data and
    only recompute on demand. If PyVista exposes lazy outputs, mutating the
    source after calling .threshold() would silently change the cached result.
    """

    def test_source_scalar_mutation_does_not_affect_filtered_n_points(self):
        """Modifying source T array after threshold does not change filtered n_points.

        This checks that threshold() runs eagerly, not lazily. If n_points changed
        after zeroing the source, the VTK pipeline would be leaking laziness.
        """
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        filtered = mesh.threshold(value=500)
        n_before = filtered.n_points

        # Zero out source array — if pipeline is lazy, filtered would recompute
        mesh["T"][:] = 0
        n_after = filtered.n_points

        # Result: PyVista executes threshold eagerly — output is decoupled.
        # n_before and n_after are identical.
        assert n_before == n_after, (
            "threshold() appears lazy: n_points changed after source mutation. "
            "This would make caching unreliable."
        )

    def test_complete_source_array_replacement_does_not_affect_filtered(self):
        """Replacing T on the source mesh with a new array does not affect filtered.

        Even more aggressive than in-place mutation: assigning a completely
        new array to mesh['T'] should not change the already-computed output.
        """
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        filtered = mesh.threshold(value=500)
        n_before = filtered.n_points

        # Replace the entire array (not just mutate in-place)
        mesh["T"] = np.zeros(mesh.n_points)
        n_after = filtered.n_points

        assert n_before == n_after


# ===========================================================================
# 2. VTK pipeline laziness leaking through
# ===========================================================================

class TestPipelineLaziness:
    """Check whether PyVista's filter calls are truly eager or defer to VTK laziness.

    VTK's native pipeline is lazy: vtkThreshold.Update() is required before
    output is computed. PyVista calls Update() inside each filter method, so
    results should be computed immediately. But does the returned object hold
    a live VTK reference that re-reads the source on access?
    """

    def test_threshold_is_eager_not_lazy(self):
        """n_points is stable before and after source mutation — filter ran eagerly."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        filtered = mesh.threshold(value=500)
        n_before = filtered.n_points

        # If lazy, mutating source to 999 would push ALL points above threshold,
        # making n_after == mesh.n_points instead of the original filtered count.
        mesh["T"][:] = 999
        n_after = filtered.n_points

        assert n_before == n_after, (
            f"VTK laziness is leaking: n_before={n_before}, n_after={n_after}. "
            "Cached threshold results can be invalidated by later source mutations."
        )

    def test_filter_result_type_is_concrete_not_pipeline(self):
        """threshold() returns a concrete mesh type, not a VTK pipeline object."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        filtered = mesh.threshold(value=500)
        # PyVista eagerly executes and returns UnstructuredGrid (not a lazy object)
        assert isinstance(filtered, pv.UnstructuredGrid), (
            f"Expected UnstructuredGrid, got {type(filtered).__name__}. "
            "Filter result type affects caching assumptions."
        )


# ===========================================================================
# 3. Filter output: copy vs view of source data
# ===========================================================================

class TestFilterOutputCopyVsView:
    """Determine whether filter output arrays share memory with the source.

    VTK optimizes the passthrough case: when ALL points satisfy the threshold
    condition, VTK reuses the source VTK array object directly rather than
    copying. This means filtered["T"] can be a view of mesh["T"] when the
    filter is a no-op (all points pass).

    When only a subset of points pass (the typical case), VTK allocates a new
    array for the subset, so filtered["T"] is an independent copy.

    This distinction is a caching hazard: in the passthrough case, mutations
    to the source array after filtering will corrupt the cached filter result.
    """

    @pytest.mark.xfail(
        reason=(
            "CACHING HAZARD: VTK passthrough optimization. When ALL points pass "
            "the threshold, VTK reuses the source VTK array object directly "
            "instead of copying. filtered['T'] shares the VTK buffer with mesh['T']. "
            "Mutating mesh['T'] after caching the threshold result corrupts the cache."
        ),
        strict=True,
    )
    def test_filtered_array_is_not_view_of_source_when_all_points_pass(self):
        """When all points pass threshold, VTK reuses the source array (no copy).

        This is the VTK passthrough optimization. The filtered result's T array
        points to the same VTK vtkDataArray as the source mesh's T array.
        Mutating the source will retroactively change the filtered result.

        This test is xfail to document the hazard: the assertion below will fail
        because the cached result IS corrupted by source mutation.
        """
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        # Set all values to 600 so ALL points pass threshold(500)
        mesh["T"] = np.full(mesh.n_points, 600.0)
        filtered = mesh.threshold(value=500, scalars="T")
        assert filtered.n_points == mesh.n_points, "Expected all points to pass"

        original_val = filtered["T"][0]  # should be 600.0

        # Zero out the source — if sharing VTK buffer, this corrupts filtered too
        mesh["T"][:] = 0

        new_val = filtered["T"][0]
        # This assertion FAILS: new_val is 0.0 because they share the VTK buffer
        assert new_val == original_val, (
            f"filtered['T'] shares VTK buffer with mesh['T'] in passthrough case: "
            f"original={original_val:.2f}, after mutation={new_val:.2f}. "
            "VTK reuses the source array when all points pass the filter."
        )

    def test_filtered_array_is_independent_copy_when_partial_points_pass(self):
        """When a subset of points pass threshold, VTK allocates a new array.

        This is the normal (non-passthrough) case. The filtered result gets its
        own VTK array copy, independent of the source. Source mutation does not
        affect the cached result.
        """
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        # Values 0..999; threshold 300 passes roughly 70%, not all
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        filtered = mesh.threshold(value=300, scalars="T")
        assert 0 < filtered.n_points < mesh.n_points, (
            f"Expected partial pass, got {filtered.n_points}/{mesh.n_points}"
        )

        original_val = filtered["T"][0]

        # Zero out the source array
        mesh["T"][:] = 0

        new_val = filtered["T"][0]
        assert new_val == original_val, (
            f"filtered['T'] shares memory with mesh['T'] even in partial-pass case: "
            f"original={original_val:.2f}, after mutation={new_val:.2f}."
        )

    def test_source_zeroed_does_not_change_filtered_values_partial_pass(self):
        """After zeroing source T, filtered T values are unchanged (partial pass case)."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        rng = np.random.RandomState(42)
        mesh["T"] = rng.rand(mesh.n_points) * 1000
        # Force partial pass: use a high threshold
        filtered = mesh.threshold(value=800, scalars="T")
        assert 0 < filtered.n_points < mesh.n_points

        pre_mutation_vals = filtered["T"].copy()
        mesh["T"][:] = 0
        post_mutation_vals = filtered["T"]

        assert np.allclose(pre_mutation_vals, post_mutation_vals)


# ===========================================================================
# 4. Array access: view into VTK storage
# ===========================================================================

class TestArrayAccessViewBehavior:
    """Determine whether mesh["T"] returns a view or copy of internal storage.

    This matters for two reasons:
    1. If arr = mesh["T"] is a view, then arr[0] = X also mutates mesh["T"][0].
       Code that gets an array from a proxy (then escapes the proxy) can
       silently corrupt the source object stored in the cache.
    2. Two successive mesh["T"] calls return different Python objects but they
       share the same underlying VTK memory buffer.
    """

    def test_array_from_mesh_is_a_view_into_vtk_storage(self):
        """mesh['T'] returns a view: mutating the array mutates the mesh.

        This is an expected VTK/PyVista behavior, but it has caching implications:
        any code that gets a raw array out of a cached mesh and writes to it
        will corrupt the cached object for all future cache hits.
        """
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        arr = mesh["T"]

        # arr should NOT own its data — it's a view into VTK's buffer
        assert not arr.flags.owndata, (
            "mesh['T'] returned an array that owns its data (copy), not a view. "
            "This is actually safer for caching, but unusual for PyVista."
        )

        arr[0] = -999.0
        assert mesh["T"][0] == -999.0, (
            "Mutation of arr did not propagate to mesh['T']. "
            "Expected view behavior."
        )

    def test_two_getitem_calls_share_underlying_buffer(self):
        """Two mesh['T'] calls return different Python objects sharing the same buffer."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        arr1 = mesh["T"]
        arr2 = mesh["T"]

        # They are not the same Python object
        assert arr1 is not arr2

        # But they share the same underlying buffer
        arr1[0] = -999.0
        assert arr2[0] == -999.0, (
            "arr1 and arr2 do not share storage. "
            "If mesh['T'] returned independent copies each time, "
            "this would actually be safer for caching."
        )

    def test_points_attribute_is_also_a_view(self):
        """mesh.points returns a view: mutating it mutates the mesh in-place."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        pts = mesh.points

        assert not pts.flags.owndata, "mesh.points should be a view, not a copy"

        pts[0] = [99.0, 99.0, 99.0]
        assert np.allclose(mesh.points[0], [99.0, 99.0, 99.0]), (
            "Mutation of pts[0] did not propagate to mesh.points[0]."
        )


# ===========================================================================
# 5. Multiple filter calls — independent outputs
# ===========================================================================

class TestMultipleFilterCallsIndependence:
    """Calling the same filter twice should return independent output objects.

    If threshold() returned the same internal VTK output object each time,
    then two cached results pointing to the same underlying object would
    interfere with each other when one is mutated.
    """

    def test_two_threshold_calls_return_different_objects(self):
        """threshold() with same args produces two distinct Python objects."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        t1 = mesh.threshold(value=300)
        t2 = mesh.threshold(value=300)

        assert t1 is not t2, "Two threshold calls returned the same object"

    def test_two_threshold_calls_produce_same_data(self):
        """threshold() with same args produces identical data (deterministic)."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        t1 = mesh.threshold(value=300)
        t2 = mesh.threshold(value=300)

        assert t1.n_points == t2.n_points

    def test_mutating_one_output_does_not_affect_other(self):
        """Mutating t1 does not corrupt t2 (they don't share VTK output storage).

        If two calls to threshold() reuse the same VTK vtkUnstructuredGrid output
        object, mutating one would corrupt the other. This would break the cache's
        assumption that stored results are independent.
        """
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        t1 = mesh.threshold(value=300)
        t2 = mesh.threshold(value=300)

        original_n = t2.n_points

        # Completely trash t1's points
        t1.points[:] = 0

        assert t2.n_points == original_n, (
            "t2.n_points changed after mutating t1. "
            "Both threshold calls share the same VTK output buffer."
        )
        # t2 points should be unchanged
        assert not np.all(t2.points == 0), (
            "t2.points were all zeroed — t1 and t2 share point storage."
        )


# ===========================================================================
# 6. Contour/isosurface determinism
# ===========================================================================

class TestContourDeterminism:
    """Verify that contour() is deterministic given the same input.

    Non-determinism in filter output would mean the cache could serve a result
    that doesn't match what a fresh execution would produce, even with no mutation.
    """

    def test_contour_same_args_same_n_points(self):
        """Two contour calls with same args produce the same number of points."""
        mesh = pv.ImageData(dimensions=(20, 20, 20))
        mesh["T"] = np.random.RandomState(42).rand(mesh.n_points) * 1000
        c1 = mesh.contour(isosurfaces=[500], scalars="T")
        c2 = mesh.contour(isosurfaces=[500], scalars="T")

        assert c1.n_points == c2.n_points, (
            f"contour() is non-deterministic: c1={c1.n_points}, c2={c2.n_points} points"
        )

    def test_contour_same_args_same_point_coordinates(self):
        """Two contour calls with same args produce identical point coordinates."""
        mesh = pv.ImageData(dimensions=(20, 20, 20))
        mesh["T"] = np.random.RandomState(42).rand(mesh.n_points) * 1000
        c1 = mesh.contour(isosurfaces=[500], scalars="T")
        c2 = mesh.contour(isosurfaces=[500], scalars="T")

        assert c1.n_points == c2.n_points
        assert np.allclose(c1.points, c2.points), (
            "contour() produces different point coordinates on successive calls. "
            "This would mean a cache hit returns geometrically different data."
        )


# ===========================================================================
# 7. Chained filters and intermediate mutation
# ===========================================================================

class TestChainedFiltersIntermediateMutation:
    """Verify that downstream filter outputs are isolated from intermediate mutation.

    In a pipeline: mesh -> threshold -> extract_surface
    If the cache stores 'threshed' and later something mutates threshed.points,
    does the cached 'surfaced' object reflect that mutation?
    """

    def test_mutating_intermediate_points_does_not_change_downstream_n_points(self):
        """Zeroing threshed.points does not change surfaced.n_points."""
        mesh = pv.ImageData(dimensions=(20, 20, 20))
        mesh["T"] = np.random.RandomState(42).rand(mesh.n_points) * 1000
        threshed = mesh.threshold(value=500, scalars="T")
        surfaced = threshed.extract_surface()
        n_before = surfaced.n_points

        # Corrupt the intermediate result's geometry
        threshed.points[:] = 0

        assert surfaced.n_points == n_before, (
            "surfaced.n_points changed after mutating threshed.points. "
            "extract_surface() output shares geometry with its input."
        )

    def test_mutating_intermediate_arrays_does_not_change_downstream_arrays(self):
        """Mutating threshed['T'] does not affect surfaced['T']."""
        mesh = pv.ImageData(dimensions=(20, 20, 20))
        mesh["T"] = np.random.RandomState(42).rand(mesh.n_points) * 1000
        threshed = mesh.threshold(value=500, scalars="T")
        surfaced = threshed.extract_surface()

        if "T" not in surfaced.array_names or surfaced.n_points == 0:
            pytest.skip("T not carried through to surface in this configuration")

        original_val = surfaced["T"][0]

        # Mutate the intermediate's T array
        threshed["T"][:] = 77777

        assert surfaced["T"][0] == original_val, (
            f"surfaced['T'][0] changed from {original_val} to {surfaced['T'][0]} "
            "after mutating threshed['T']. The arrays share storage."
        )


# ===========================================================================
# 8. Cache sabotage: direct mutation of cached objects
# ===========================================================================

class TestCacheSabotage:
    """Show that the cache stores live references, not frozen copies.

    If code obtains a raw reference to a cached object (by bypassing the proxy,
    e.g. through object.__getattribute__(proxy, '_real')) and mutates it, all
    subsequent cache hits for that entry will return the corrupted object.
    This is the most operationally serious hazard.
    """

    def test_mutating_raw_cached_object_corrupts_cache_hits(self):
        """Direct mutation of a cached filter result is served by subsequent hits.

        This test documents reality: the cache stores the live Python object.
        There is no copy-on-cache, so mutation goes undetected.
        """
        dag = DAG()
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)

        h = stable_hash(("root", "sabotage_test"))
        dag.cache[h] = mesh
        dag.current_run.add(h)
        proxy = TrackedProxy(mesh, h, dag)

        # Run 1: compute and cache the threshold result
        dag.begin_run()
        dag.current_run.add(h)
        filtered = proxy.threshold(value=500)
        filtered_hash = object.__getattribute__(filtered, "_hash")
        n_original = filtered.n_points
        dag.end_run()

        # Sabotage: access the raw cached object and corrupt its T array
        raw_cached = dag.cache[filtered_hash]
        raw_cached["T"][:] = 99999  # corrupt the cached data

        # Run 2: same op -> cache hit -> returns the sabotaged object
        dag.begin_run()
        dag.current_run.add(h)
        proxy2 = TrackedProxy(mesh, h, dag)
        filtered2 = proxy2.threshold(value=500)
        dag.end_run()

        real_result = object.__getattribute__(filtered2, "_real")
        corrupted_val = real_result["T"][0]

        # This assertion PASSES, documenting the hazard:
        # the cache hit returns the mutated data.
        assert corrupted_val == 99999, (
            "Expected cache to serve the sabotaged value 99999, "
            f"but got {corrupted_val}. "
            "This test documents that the cache stores live references."
        )
        assert dag.stats()["hits"] >= 1, "Expected a cache hit on run 2"

    def test_n_points_unchanged_after_array_corruption(self):
        """Array corruption on cached object does not change n_points.

        Even after sabotaging array values, n_points remains the same because
        point count is structural (topology), not data-dependent. This shows
        the hazard is to array values, not mesh topology.
        """
        dag = DAG()
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)

        h = stable_hash(("root", "n_points_test"))
        dag.cache[h] = mesh
        dag.current_run.add(h)
        proxy = TrackedProxy(mesh, h, dag)

        dag.begin_run()
        dag.current_run.add(h)
        filtered = proxy.threshold(value=500)
        filtered_hash = object.__getattribute__(filtered, "_hash")
        n_original = filtered.n_points
        dag.end_run()

        # Corrupt arrays but not topology
        dag.cache[filtered_hash]["T"][:] = 0

        dag.begin_run()
        dag.current_run.add(h)
        filtered2 = TrackedProxy(mesh, h, dag).threshold(value=500)
        n_from_hit = filtered2.n_points
        dag.end_run()

        # n_points is unaffected by array corruption
        assert n_from_hit == n_original


# ===========================================================================
# 9. set_active_scalars as hidden state in filter calls
# ===========================================================================

class TestActiveScalarsHiddenState:
    """set_active_scalars creates hidden state that affects filter results.

    When scalars= is omitted from threshold(), the filter uses the mesh's
    current active scalar. If someone calls mesh.set_active_scalars() between
    two pipeline runs, the SAME call threshold(value=500) produces DIFFERENT
    results — but our content hash (which hashes only explicit args) treats
    them as the same operation, serving a cache hit with the wrong data.

    This is the most insidious hazard because it's not an in-place mutation
    of the data itself, but a change to mesh metadata that silently redirects
    filter behavior.
    """

    def test_threshold_without_scalars_uses_active_scalar(self):
        """threshold(value) without scalars= selects based on active scalar name."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)        # values 0-999
        mesh["P"] = np.full(mesh.n_points, 999.0)                 # all 999

        mesh.set_active_scalars("T")
        t_active_T = mesh.threshold(value=500)

        mesh.set_active_scalars("P")
        t_active_P = mesh.threshold(value=500)

        # With T active: roughly half pass (values 500-999)
        # With P active: all pass (all values are 999 > 500)
        assert t_active_T.n_points != t_active_P.n_points, (
            "threshold() ignores active_scalars — the hazard would not exist. "
            f"Both gave {t_active_T.n_points} points."
        )

    def test_cache_returns_correct_result_after_active_scalars_change(self):
        """Cache correctly serves different results when active_scalars changes between runs.

        Run 1: active=T, threshold(500) -> ~500 points (half of 1000)
        Run 2: active=P (all 999), threshold(500) -> 1000 points
        The cache now includes active_scalars_name in the hash, so these are distinct
        cache entries and the correct result is returned for each run.
        """
        import warnings
        dag = DAG()
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        mesh["P"] = np.full(mesh.n_points, 999.0)

        h = stable_hash(("root", "active_scalars_test"))
        dag.cache[h] = mesh
        dag.current_run.add(h)

        # Run 1: active=T
        dag.begin_run()
        dag.current_run.add(h)
        mesh.set_active_scalars("T")
        proxy1 = TrackedProxy(mesh, h, dag)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t1 = proxy1.threshold(value=500)
        n1 = t1.n_points
        dag.end_run()

        # Run 2: active=P — should give ALL points; cache must NOT serve Run 1 result
        dag.begin_run()
        dag.current_run.add(h)
        mesh.set_active_scalars("P")
        proxy2 = TrackedProxy(mesh, h, dag)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t2 = proxy2.threshold(value=500)
        n2 = t2.n_points
        dag.end_run()

        # n2 should be 1000 (all points have P=999 > 500), not n1 (~500)
        assert n2 == mesh.n_points, (
            f"Cache returned {n2} points instead of {mesh.n_points}. "
            "The active_scalars hidden state was not captured in the hash, "
            "so the cache served the wrong result."
        )
        # Also check n1 is not the same as n2 (T is a ramp, P is constant 999)
        assert n1 != n2, (
            f"Expected n1 ({n1}) != n2 ({n2}): different active scalars should yield "
            "different threshold results."
        )

    def test_explicit_scalars_arg_makes_threshold_safe_to_cache(self):
        """With explicit scalars=, threshold() is deterministic and safe to cache.

        When the caller always passes scalars='T' or scalars='P' explicitly,
        the scalars name is included in the content hash and the cache correctly
        distinguishes the two operations.
        """
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        mesh["P"] = np.full(mesh.n_points, 999.0)

        t_T = mesh.threshold(value=500, scalars="T")
        t_P = mesh.threshold(value=500, scalars="P")

        # These should differ, and their cache hashes will also differ
        assert t_T.n_points != t_P.n_points

        # Confirm same scalars= produces same result regardless of active scalar
        mesh.set_active_scalars("P")
        t_T_again = mesh.threshold(value=500, scalars="T")
        assert t_T_again.n_points == t_T.n_points, (
            "threshold(scalars='T') gave different results when active_scalars='P'. "
            "Explicit scalars= arg should override active_scalars."
        )


# ===========================================================================
# 10. Proxy-layer mutation protection
# ===========================================================================

class TestProxyMutationProtection:
    """Verify that TrackedProxy blocks in-place mutations via the public API.

    The proxy prevents __setitem__ and attribute mutation, but raw array
    references can still escape and be mutated by callers.
    """

    def test_proxy_blocks_setitem_on_mesh(self):
        """proxy['NewField'] = arr raises AttributeError."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        proxy, dag, _ = make_proxy(mesh)
        dag.begin_run()
        with pytest.raises(AttributeError):
            proxy["NewField"] = np.ones(mesh.n_points)

    def test_proxy_blocks_setattr(self):
        """proxy.some_attr = value raises AttributeError."""
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        proxy, dag, _ = make_proxy(mesh)
        with pytest.raises(AttributeError):
            proxy.some_attr = 42

    def test_raw_array_from_proxy_is_mutable_view(self):
        """Internal _real array accessed via object.__getattribute__ is a mutable view.

        This documents that bypass of the proxy layer (e.g., via _real) gives
        access to the live array. Once code has a raw reference, there is no
        protection against mutation.

        Callers outside the proxy system (e.g., VTK callbacks, numpy ufuncs
        operating on unwrapped arrays) can silently corrupt cached objects.
        """
        dag = DAG()
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)

        h = stable_hash(("root", "raw_view_test"))
        dag.cache[h] = mesh
        dag.current_run.add(h)
        proxy = TrackedProxy(mesh, h, dag)

        dag.begin_run()
        # Get T through proxy — returns a TrackedProxy wrapping the array
        arr_proxy = proxy["T"]

        # Extract the underlying raw array (bypassing proxy protection)
        arr_real = object.__getattribute__(arr_proxy, "_real")

        # Mutate it — this goes directly into VTK's buffer
        arr_real[0] = -999.0

        # The mutation propagated to the mesh in the cache
        assert mesh["T"][0] == -999.0, (
            "Mutation of arr_real[0] did not affect mesh['T'][0]. "
            "Expected view semantics."
        )
        dag.end_run()


# ===========================================================================
# 11. Active scalars hash correctness (new tests for the fix)
# ===========================================================================

class TestActiveScalarsHashCorrectness:
    """Tests verifying that active_scalars_name is included in the cache hash
    for scalar-sensitive methods called without scalars=.

    These tests confirm the fix for the caching hazard documented in
    TestActiveScalarsHiddenState.
    """

    def test_active_scalars_different_hash(self):
        """Same threshold call on mesh with different active_scalars produces different cache keys.

        When scalars= is omitted, the cache key must encode the active scalar name
        so that two calls with different active scalars do not collide.
        """
        import warnings
        from tracked_execution.dispatch import dispatch

        dag = DAG()
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        mesh["P"] = np.full(mesh.n_points, 999.0)

        h = stable_hash(("root", "hash_diff_test"))
        dag.cache[h] = mesh
        dag.current_run.add(h)

        # Compute hash for threshold(500) with active=T
        mesh.set_active_scalars("T")
        proxy_T = TrackedProxy(mesh, h, dag)
        dag.begin_run()
        dag.current_run.add(h)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result_T = proxy_T.threshold(value=500)
        hash_T = object.__getattribute__(result_T, "_hash")
        dag.end_run()

        # Compute hash for threshold(500) with active=P
        mesh.set_active_scalars("P")
        proxy_P = TrackedProxy(mesh, h, dag)
        dag.begin_run()
        dag.current_run.add(h)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            result_P = proxy_P.threshold(value=500)
        hash_P = object.__getattribute__(result_P, "_hash")
        dag.end_run()

        assert hash_T != hash_P, (
            "threshold(500) with active=T and active=P produced the same cache hash. "
            "Different active scalars must produce different cache keys."
        )

    def test_active_scalars_explicit_scalars_ignores_active(self):
        """When scalars= is provided, active_scalars does not affect the hash.

        Calling threshold(value=500, scalars='T') always uses T regardless of
        active_scalars_name, so the hash must NOT include active_scalars_name in
        this case (to preserve cache hits across active-scalar changes).
        """
        dag = DAG()
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        mesh["P"] = np.full(mesh.n_points, 999.0)

        h = stable_hash(("root", "explicit_scalars_hash_test"))
        dag.cache[h] = mesh
        dag.current_run.add(h)

        # Compute hash for threshold(500, scalars='T') with active=T
        mesh.set_active_scalars("T")
        proxy1 = TrackedProxy(mesh, h, dag)
        dag.begin_run()
        dag.current_run.add(h)
        result1 = proxy1.threshold(value=500, scalars="T")
        hash1 = object.__getattribute__(result1, "_hash")
        dag.end_run()

        # Compute hash for threshold(500, scalars='T') with active=P
        # active_scalars_name changed, but scalars= is explicit, so hash must be the same
        mesh.set_active_scalars("P")
        proxy2 = TrackedProxy(mesh, h, dag)
        dag.begin_run()
        dag.current_run.add(h)
        result2 = proxy2.threshold(value=500, scalars="T")
        hash2 = object.__getattribute__(result2, "_hash")
        dag.end_run()

        assert hash1 == hash2, (
            f"threshold(scalars='T') produced different hashes when active_scalars changed. "
            f"hash1={hash1[:8]}..., hash2={hash2[:8]}... "
            "Explicit scalars= should be independent of active_scalars_name."
        )
        # Both calls should produce a cache hit on the second run
        assert dag.stats()["hits"] >= 1, (
            "Expected a cache hit when threshold(scalars='T') was called with same explicit scalars."
        )

    def test_set_active_scalars_blocked(self):
        """Calling set_active_scalars on a proxy raises AttributeError with helpful message.

        set_active_scalars() is now blacklisted because it mutates hidden state
        that affects scalar-sensitive filters. Users should always pass scalars=
        explicitly instead.
        """
        dag = DAG()
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)
        mesh["P"] = np.full(mesh.n_points, 999.0)
        proxy, dag, _ = make_proxy(mesh, dag)

        with pytest.raises(AttributeError) as exc_info:
            proxy.set_active_scalars("P")

        error_msg = str(exc_info.value)
        assert "set_active_scalars" in error_msg, (
            f"Error message does not mention set_active_scalars: {error_msg}"
        )
        assert "blocked" in error_msg.lower() or "hidden state" in error_msg.lower(), (
            f"Error message does not explain why it is blocked: {error_msg}"
        )

    def test_active_scalars_cache_correct(self):
        """The cache returns the correct result (not a stale hit) when active_scalars changes.

        This is the end-to-end correctness test: two pipeline runs with different
        active scalars must yield results matching the respective active scalar,
        not a stale cache hit from the first run.
        """
        import warnings
        dag = DAG()
        mesh = pv.ImageData(dimensions=(10, 10, 10))
        mesh["T"] = np.arange(mesh.n_points, dtype=float)   # ramp 0..999
        mesh["P"] = np.full(mesh.n_points, 999.0)            # all 999

        h = stable_hash(("root", "correct_cache_test"))
        dag.cache[h] = mesh
        dag.current_run.add(h)

        # Run 1: active=T — roughly half of points exceed 500
        dag.begin_run()
        dag.current_run.add(h)
        mesh.set_active_scalars("T")
        proxy1 = TrackedProxy(mesh, h, dag)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t1 = proxy1.threshold(value=500)
        n1 = t1.n_points
        dag.end_run()

        # Run 2: active=P — ALL points have P=999 > 500, so all should pass
        dag.begin_run()
        dag.current_run.add(h)
        mesh.set_active_scalars("P")
        proxy2 = TrackedProxy(mesh, h, dag)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t2 = proxy2.threshold(value=500)
        n2 = t2.n_points
        dag.end_run()

        assert n2 == mesh.n_points, (
            f"Run 2 (active=P) returned {n2} points instead of {mesh.n_points}. "
            "The cache served the wrong (stale) result from Run 1."
        )
        assert n1 < mesh.n_points, (
            f"Run 1 (active=T, ramp 0-999) should have fewer than {mesh.n_points} points "
            f"above threshold 500, but got {n1}."
        )
