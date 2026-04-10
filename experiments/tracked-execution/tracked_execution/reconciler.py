"""reconciler.py — Scene reconciliation for incremental PyVista Plotter updates.

Provides:
    SceneReconciler  — diffs old vs new actor sets, applies minimal updates
    ActorRecord      — record of a tracked actor (name, mesh_hash, params_hash)
    ReconcileResult  — counts from a reconcile pass (unchanged, updated, added, removed)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .dispatch import stable_hash, _unwrap
from .proxy import TrackedProxy


@dataclass
class ActorRecord:
    """Record of an actor previously reconciled into the scene.

    Attributes:
        name:        Name used to identify the actor in the plotter.
        mesh_hash:   Content hash of the mesh data.
        params_hash: Content hash of the display parameters (colormap, opacity, etc.).
    """
    name: str
    mesh_hash: str
    params_hash: str


@dataclass
class ReconcileResult:
    """Counts from a single reconcile() pass.

    Attributes:
        unchanged: Number of actors that were identical to their previous state.
        updated:   Number of actors that were removed and re-added (mesh or params changed).
        added:     Number of actors that are new (no previous state).
        removed:   Number of actors that were in the previous state but are gone now.
    """
    unchanged: int = 0
    updated: int = 0
    added: int = 0
    removed: int = 0


def _mesh_hash(mesh_or_proxy) -> str:
    """Compute a content hash for a mesh or TrackedProxy."""
    if isinstance(mesh_or_proxy, TrackedProxy):
        return object.__getattribute__(mesh_or_proxy, "_hash")
    return stable_hash(mesh_or_proxy)


def _params_hash(params: dict) -> str:
    """Compute a content hash for display parameters."""
    return stable_hash(params)


class SceneReconciler:
    """Incrementally reconcile a set of desired actors against a PyVista Plotter.

    The reconciler tracks what was previously rendered and computes the minimal
    set of add/remove operations needed to bring the plotter to the new state.

    When ``plotter`` is ``None`` (diff-only mode), the reconciler computes all
    counts but does not actually call any plotter methods.  This is useful for
    testing the diff logic without requiring a display or VTK rendering context.

    Args:
        plotter: A ``pyvista.Plotter`` instance, or ``None`` for diff-only mode.

    Example::

        reconciler = SceneReconciler(plotter=None)
        actors = [{"mesh": mesh_proxy, "params": {"colormap": "viridis"}}]
        result = reconciler.reconcile(actors)
        assert result.added == 1

        # Same actors again — nothing changes
        result = reconciler.reconcile(actors)
        assert result.unchanged == 1
    """

    def __init__(self, plotter=None):
        self._plotter = plotter
        # name → ActorRecord for the previous reconciled state
        self._previous: dict[str, ActorRecord] = {}

    def reconcile(self, new_actors: list[dict]) -> ReconcileResult:
        """Reconcile *new_actors* against the current scene state.

        Each entry in *new_actors* should be a dict with:
            ``mesh``   — the mesh object (TrackedProxy or real PyVista mesh)
            ``params`` — display parameter dict (colormap, opacity, name, …)

        Alternatively, entries may be 2-tuples ``(mesh, params)`` as produced
        by ``execute_pipeline`` (which stores actors as ``(mesh_proxy, kwargs)``).

        Strategy (v1, simple-correct):
        - Unchanged (same name, same mesh_hash, same params_hash) → skip.
        - Changed (same name, different mesh or params) → remove and re-add.
        - New name → add.
        - Missing name (was in previous, not in new) → remove.

        Args:
            new_actors: List of actor descriptors.

        Returns:
            ReconcileResult with counts of each operation performed.
        """
        result = ReconcileResult()

        # Normalize entries: accept both dict and (mesh, params) tuple formats
        normalized: list[tuple[Any, dict]] = []
        for entry in new_actors:
            if isinstance(entry, dict):
                mesh = entry.get("mesh")
                params = dict(entry.get("params", {}))
            elif isinstance(entry, (tuple, list)) and len(entry) == 2:
                mesh, params = entry[0], dict(entry[1])
            else:
                raise ValueError(
                    f"Actor entry must be a dict or 2-tuple, got {type(entry)}"
                )
            normalized.append((mesh, params))

        # Assign names to each new actor
        new_named: dict[str, tuple[Any, dict]] = {}
        for i, (mesh, params) in enumerate(normalized):
            name = params.pop("name", None) or f"actor_{i}"
            new_named[name] = (mesh, params)

        # Compute hashes for new actors
        new_records: dict[str, ActorRecord] = {}
        for name, (mesh, params) in new_named.items():
            mh = _mesh_hash(mesh)
            ph = _params_hash(params)
            new_records[name] = ActorRecord(name=name, mesh_hash=mh, params_hash=ph)

        # Determine what changed
        prev_names = set(self._previous.keys())
        new_names = set(new_records.keys())

        removed_names = prev_names - new_names
        added_names = new_names - prev_names
        common_names = prev_names & new_names

        # Remove actors that are gone
        for name in removed_names:
            if self._plotter is not None:
                self._plotter.remove_actor(name)
            result.removed += 1

        # Handle common actors
        for name in common_names:
            prev_rec = self._previous[name]
            new_rec = new_records[name]
            if (prev_rec.mesh_hash == new_rec.mesh_hash
                    and prev_rec.params_hash == new_rec.params_hash):
                # Completely unchanged — skip
                result.unchanged += 1
            else:
                # Something changed — remove and re-add
                if self._plotter is not None:
                    self._plotter.remove_actor(name)
                    mesh, params = new_named[name]
                    self._plotter.add_mesh(_unwrap(mesh), name=name, **params)
                result.updated += 1

        # Add new actors
        for name in added_names:
            if self._plotter is not None:
                mesh, params = new_named[name]
                self._plotter.add_mesh(_unwrap(mesh), name=name, **params)
            result.added += 1

        # Update state
        self._previous = new_records

        return result
