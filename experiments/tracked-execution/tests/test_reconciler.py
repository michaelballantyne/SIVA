"""Tests for SceneReconciler.

All tests run in diff-only mode (plotter=None) so no display or VTK rendering
context is required.  The reconciler still computes all counts; it just skips
the actual plotter.add_mesh / plotter.remove_actor calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import pyvista as pv

# Ensure package is importable from its source directory
_LIB_DIR = Path(__file__).resolve().parent.parent
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from tracked_execution.dispatch import DAG
from tracked_execution.dispatch import stable_hash
from tracked_execution.proxy import TrackedProxy
from tracked_execution.reconciler import (
    ActorRecord,
    ReconcileResult,
    SceneReconciler,
    _is_property_only_change,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_mesh(n: int = 5, seed: int = 42) -> pv.ImageData:
    """Create a small synthetic PyVista mesh."""
    rng = np.random.default_rng(seed)
    mesh = pv.ImageData(dimensions=(n, n, n))
    mesh["Temperature"] = rng.random(mesh.n_points) * 1000
    return mesh


def make_proxy(mesh=None, tag: str = "test_mesh") -> TrackedProxy:
    """Wrap a mesh in a TrackedProxy."""
    dag = DAG()
    if mesh is None:
        mesh = make_mesh()
    h = stable_hash(("root", tag))
    dag.cache[h] = mesh
    dag.current_run.add(h)
    return TrackedProxy(mesh, h, dag)


# ---------------------------------------------------------------------------
# 1. Initial reconcile — all actors are new
# ---------------------------------------------------------------------------

class TestReconcileInitial:
    def test_initial_reconcile_adds_all(self):
        """First reconcile with two actors → both are added, none unchanged/removed."""
        reconciler = SceneReconciler(plotter=None)

        actors = [
            {"mesh": make_proxy(), "params": {"name": "fire", "colormap": "viridis"}},
            {"mesh": make_proxy(tag="mesh2"), "params": {"name": "smoke", "colormap": "gray"}},
        ]

        result = reconciler.reconcile(actors)

        assert result.added == 2
        assert result.unchanged == 0
        assert result.updated == 0
        assert result.removed == 0

    def test_initial_reconcile_single_actor(self):
        """Single actor on first reconcile → added=1."""
        reconciler = SceneReconciler(plotter=None)
        actors = [{"mesh": make_proxy(), "params": {"name": "vol"}}]

        result = reconciler.reconcile(actors)
        assert result.added == 1

    def test_initial_empty(self):
        """Reconciling empty actor list → all zeros."""
        reconciler = SceneReconciler(plotter=None)
        result = reconciler.reconcile([])
        assert result.added == 0
        assert result.unchanged == 0
        assert result.updated == 0
        assert result.removed == 0


# ---------------------------------------------------------------------------
# 2. No change — same actors re-reconciled
# ---------------------------------------------------------------------------

class TestReconcileNoChange:
    def test_no_change_single_actor(self):
        """Same actor reconciled twice → second pass is all unchanged."""
        reconciler = SceneReconciler(plotter=None)
        proxy = make_proxy()
        actors = [{"mesh": proxy, "params": {"name": "fire", "colormap": "viridis"}}]

        reconciler.reconcile(actors)
        result = reconciler.reconcile(actors)

        assert result.unchanged == 1
        assert result.added == 0
        assert result.updated == 0
        assert result.removed == 0

    def test_no_change_multiple_actors(self):
        """Multiple actors unchanged on second pass."""
        reconciler = SceneReconciler(plotter=None)
        actors = [
            {"mesh": make_proxy(), "params": {"name": "a"}},
            {"mesh": make_proxy(tag="b"), "params": {"name": "b"}},
            {"mesh": make_proxy(tag="c"), "params": {"name": "c"}},
        ]

        reconciler.reconcile(actors)
        result = reconciler.reconcile(actors)

        assert result.unchanged == 3
        assert result.added == 0
        assert result.updated == 0
        assert result.removed == 0


# ---------------------------------------------------------------------------
# 3. Params changed — same mesh, different display params
# ---------------------------------------------------------------------------

class TestReconcileParamChange:
    def test_colormap_change_updates_actor(self):
        """Same mesh, different colormap → actor is updated (updated=1)."""
        reconciler = SceneReconciler(plotter=None)
        proxy = make_proxy()

        actors_v1 = [{"mesh": proxy, "params": {"name": "fire", "colormap": "viridis"}}]
        actors_v2 = [{"mesh": proxy, "params": {"name": "fire", "colormap": "plasma"}}]

        reconciler.reconcile(actors_v1)
        result = reconciler.reconcile(actors_v2)

        assert result.updated == 1
        assert result.unchanged == 0
        assert result.added == 0
        assert result.removed == 0

    def test_opacity_change_updates_property_in_place(self):
        """Same mesh, different opacity → in-place property update (updated_property=1, not updated)."""
        reconciler = SceneReconciler(plotter=None)
        proxy = make_proxy()

        r1 = reconciler.reconcile([{"mesh": proxy, "params": {"name": "vol", "opacity": 0.8}}])
        r2 = reconciler.reconcile([{"mesh": proxy, "params": {"name": "vol", "opacity": 0.3}}])

        assert r1.added == 1
        assert r2.updated_property == 1
        assert r2.updated == 0
        assert r2.unchanged == 0
        assert r2.added == 0
        assert r2.removed == 0

    def test_color_change_updates_actor(self):
        """Same mesh, different color string → updated=1."""
        reconciler = SceneReconciler(plotter=None)
        proxy = make_proxy()

        reconciler.reconcile([{"mesh": proxy, "params": {"name": "iso", "color": "red"}}])
        result = reconciler.reconcile([{"mesh": proxy, "params": {"name": "iso", "color": "blue"}}])

        assert result.updated == 1


# ---------------------------------------------------------------------------
# 4. Mesh changed — different mesh, same name
# ---------------------------------------------------------------------------

class TestReconcileMeshChange:
    def test_different_mesh_replaces_actor(self):
        """Different mesh content under the same name → updated=1."""
        reconciler = SceneReconciler(plotter=None)

        proxy_a = make_proxy(make_mesh(seed=1), tag="mesh_a")
        proxy_b = make_proxy(make_mesh(seed=2), tag="mesh_b")

        # Both have the same actor name but different meshes
        actors_v1 = [{"mesh": proxy_a, "params": {"name": "fire"}}]
        actors_v2 = [{"mesh": proxy_b, "params": {"name": "fire"}}]

        reconciler.reconcile(actors_v1)
        result = reconciler.reconcile(actors_v2)

        assert result.updated == 1
        assert result.unchanged == 0
        assert result.added == 0
        assert result.removed == 0

    def test_different_raw_mesh_replaces_actor(self):
        """Non-proxy raw mesh also uses stable_hash for comparison."""
        reconciler = SceneReconciler(plotter=None)

        mesh_a = make_mesh(seed=10)
        mesh_b = make_mesh(seed=99)

        # Raw meshes (not proxies) use stable_hash fallback
        r1 = reconciler.reconcile([{"mesh": mesh_a, "params": {"name": "vol"}}])
        r2 = reconciler.reconcile([{"mesh": mesh_b, "params": {"name": "vol"}}])

        assert r1.added == 1
        assert r2.updated == 1


# ---------------------------------------------------------------------------
# 5. Actor added — new actor appears in new set
# ---------------------------------------------------------------------------

class TestReconcileActorAdded:
    def test_new_actor_appears(self):
        """A second actor appearing in the new set → added=1, unchanged=1."""
        reconciler = SceneReconciler(plotter=None)

        proxy_a = make_proxy(tag="a")
        proxy_b = make_proxy(tag="b")

        actors_v1 = [{"mesh": proxy_a, "params": {"name": "a"}}]
        actors_v2 = [
            {"mesh": proxy_a, "params": {"name": "a"}},
            {"mesh": proxy_b, "params": {"name": "b"}},
        ]

        reconciler.reconcile(actors_v1)
        result = reconciler.reconcile(actors_v2)

        assert result.unchanged == 1
        assert result.added == 1
        assert result.updated == 0
        assert result.removed == 0

    def test_add_three_at_once(self):
        """Expanding from 1 actor to 4 → added=3."""
        reconciler = SceneReconciler(plotter=None)

        actors_v1 = [{"mesh": make_proxy(), "params": {"name": "a"}}]
        actors_v2 = [
            {"mesh": make_proxy(), "params": {"name": "a"}},
            {"mesh": make_proxy(tag="b"), "params": {"name": "b"}},
            {"mesh": make_proxy(tag="c"), "params": {"name": "c"}},
            {"mesh": make_proxy(tag="d"), "params": {"name": "d"}},
        ]

        reconciler.reconcile(actors_v1)
        result = reconciler.reconcile(actors_v2)

        assert result.unchanged == 1
        assert result.added == 3
        assert result.removed == 0


# ---------------------------------------------------------------------------
# 6. Actor removed — actor disappears from new set
# ---------------------------------------------------------------------------

class TestReconcileActorRemoved:
    def test_actor_disappears(self):
        """An actor that was present is now absent → removed=1, unchanged=1."""
        reconciler = SceneReconciler(plotter=None)

        proxy_a = make_proxy(tag="a")
        proxy_b = make_proxy(tag="b")

        actors_v1 = [
            {"mesh": proxy_a, "params": {"name": "a"}},
            {"mesh": proxy_b, "params": {"name": "b"}},
        ]
        actors_v2 = [{"mesh": proxy_a, "params": {"name": "a"}}]

        reconciler.reconcile(actors_v1)
        result = reconciler.reconcile(actors_v2)

        assert result.unchanged == 1
        assert result.removed == 1
        assert result.added == 0
        assert result.updated == 0

    def test_all_actors_removed(self):
        """Removing all actors → removed=N, nothing else."""
        reconciler = SceneReconciler(plotter=None)

        actors = [
            {"mesh": make_proxy(), "params": {"name": "a"}},
            {"mesh": make_proxy(tag="b"), "params": {"name": "b"}},
        ]

        reconciler.reconcile(actors)
        result = reconciler.reconcile([])

        assert result.removed == 2
        assert result.unchanged == 0
        assert result.added == 0
        assert result.updated == 0


# ---------------------------------------------------------------------------
# 7. Tuple format (executor output format)
# ---------------------------------------------------------------------------

class TestReconcileTupleFormat:
    def test_tuple_format_accepted(self):
        """Actors as (mesh, params) tuples (executor format) are reconciled."""
        reconciler = SceneReconciler(plotter=None)
        proxy = make_proxy()

        actors = [(proxy, {"name": "fire", "colormap": "viridis"})]
        result = reconciler.reconcile(actors)
        assert result.added == 1

    def test_tuple_format_no_change(self):
        """Same tuple-format actors on second pass → unchanged."""
        reconciler = SceneReconciler(plotter=None)
        proxy = make_proxy()
        actors = [(proxy, {"name": "fire"})]

        reconciler.reconcile(actors)
        result = reconciler.reconcile(actors)
        assert result.unchanged == 1

    def test_invalid_format_raises(self):
        """Passing an unsupported actor format raises ValueError."""
        reconciler = SceneReconciler(plotter=None)
        with pytest.raises(ValueError, match="2-tuple"):
            reconciler.reconcile(["not_a_valid_entry"])


# ---------------------------------------------------------------------------
# 8. Auto-naming (no explicit name in params)
# ---------------------------------------------------------------------------

class TestAutoNaming:
    def test_auto_name_assigned(self):
        """Actors without explicit name get auto-names (actor_0, actor_1, ...)."""
        reconciler = SceneReconciler(plotter=None)

        actors = [
            {"mesh": make_proxy(), "params": {"colormap": "viridis"}},
            {"mesh": make_proxy(tag="b"), "params": {"colormap": "gray"}},
        ]

        result = reconciler.reconcile(actors)
        assert result.added == 2
        # Names in state should be actor_0 and actor_1
        assert "actor_0" in reconciler._previous
        assert "actor_1" in reconciler._previous

    def test_auto_name_no_change(self):
        """Auto-named actors are stable across reconcile calls."""
        reconciler = SceneReconciler(plotter=None)
        proxy = make_proxy()
        actors = [{"mesh": proxy, "params": {"colormap": "viridis"}}]

        reconciler.reconcile(actors)
        result = reconciler.reconcile(actors)
        assert result.unchanged == 1
