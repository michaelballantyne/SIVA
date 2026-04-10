"""reconciler.py — Scene reconciliation for incremental PyVista Plotter updates.

Provides:
    SceneReconciler  — diffs old vs new actor sets, applies minimal updates
    ActorRecord      — record of a tracked actor (name, mesh_hash, params_hash)
    ReconcileResult  — counts from a reconcile pass (unchanged, updated, added, removed,
                       updated_property)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .dispatch import stable_hash, _unwrap
from .proxy import TrackedProxy


# Params that only affect VTK Property (cheap in-place update, no mapper rebuild).
# These can be applied via actor.GetProperty() without removing/re-adding the actor.
_PROPERTY_ONLY_PARAMS: frozenset[str] = frozenset([
    "opacity",
])


@dataclass
class ActorRecord:
    """Record of an actor previously reconciled into the scene.

    Attributes:
        name:        Name used to identify the actor in the plotter.
        mesh_hash:   Content hash of the mesh data.
        params_hash: Content hash of the display parameters (colormap, opacity, etc.).
        params:      The raw display parameter dict (used for in-place property diffing).
        actor:       The live VTK actor object returned by plotter.add_mesh(), or None
                     when running in diff-only mode (plotter=None).
    """
    name: str
    mesh_hash: str
    params_hash: str
    params: dict = field(default_factory=dict)
    actor: Optional[Any] = field(default=None, compare=False)


@dataclass
class ReconcileResult:
    """Counts from a single reconcile() pass.

    Attributes:
        unchanged:        Number of actors that were identical to their previous state.
        updated:          Number of actors that were removed and re-added (mesh or params
                          changed, requiring a mapper/colormap rebuild).
        updated_property: Number of actors whose display properties were updated in-place
                          (e.g. opacity only).  No remove/re-add; no flicker.
        added:            Number of actors that are new (no previous state).
        removed:          Number of actors that were in the previous state but are gone now.
    """
    unchanged: int = 0
    updated: int = 0
    updated_property: int = 0
    added: int = 0
    removed: int = 0


def _mesh_hash(mesh_or_proxy) -> str:
    if isinstance(mesh_or_proxy, TrackedProxy):
        return object.__getattribute__(mesh_or_proxy, "_hash")
    return stable_hash(mesh_or_proxy)


def _params_hash(params: dict) -> str:
    return stable_hash(params)


def _is_property_only_change(prev_params: dict, new_params: dict) -> bool:
    """Return True if *new_params* differs from *prev_params* only in
    :data:`_PROPERTY_ONLY_PARAMS` keys (opacity, …).

    "Property-only" changes can be applied cheaply via
    ``actor.GetProperty()`` without removing and re-adding the actor.
    """
    prev_keys = set(prev_params.keys())
    new_keys = set(new_params.keys())

    if prev_keys != new_keys:
        # A key was added or removed — might need a full rebuild
        # Unless the differing keys are all property-only
        differing_keys = prev_keys.symmetric_difference(new_keys)
        if not differing_keys.issubset(_PROPERTY_ONLY_PARAMS):
            return False
        # Also check value changes in common keys
        changed_values = {k for k in prev_keys & new_keys if prev_params[k] != new_params[k]}
        return changed_values.issubset(_PROPERTY_ONLY_PARAMS)

    # Same key set — check which values changed
    changed_keys = {k for k in prev_keys if prev_params[k] != new_params[k]}
    return bool(changed_keys) and changed_keys.issubset(_PROPERTY_ONLY_PARAMS)


def _apply_property_update(actor: Any, prev_params: dict, new_params: dict) -> None:
    """Apply in-place property updates from *prev_params* → *new_params*.

    Only properties listed in :data:`_PROPERTY_ONLY_PARAMS` are handled here.
    Unknown properties are silently ignored (the caller already verified that
    only property-only keys changed).

    Args:
        actor:       The live VTK/PyVista actor returned by ``plotter.add_mesh()``.
        prev_params: The previously-applied display parameter dict.
        new_params:  The new display parameter dict.
    """
    if actor is None:
        return  # diff-only mode

    vtk_prop = actor.GetProperty()

    # Opacity — cheap scalar update
    prev_opacity = prev_params.get("opacity")
    new_opacity = new_params.get("opacity")
    if new_opacity is not None and new_opacity != prev_opacity:
        vtk_prop.SetOpacity(float(new_opacity))
    elif new_opacity is None and prev_opacity is not None:
        # Opacity key removed — reset to VTK default (1.0)
        vtk_prop.SetOpacity(1.0)


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

        Strategy:
        - Unchanged (same name, same mesh_hash, same params_hash) → skip.
        - Same mesh, params differ only in property-only keys (e.g. opacity) →
          update the actor in-place via ``actor.GetProperty()``.  No remove/add,
          no flicker.  Counted as ``updated_property``.
        - Same mesh, params differ in non-property-only keys (e.g. colormap) →
          remove and re-add.  Counted as ``updated``.
        - Different mesh (same name) → remove and re-add.  Counted as ``updated``.
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
            new_records[name] = ActorRecord(
                name=name, mesh_hash=mh, params_hash=ph, params=dict(params)
            )

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
                # Carry the live actor forward so future in-place updates work
                new_rec.actor = prev_rec.actor
            elif (prev_rec.mesh_hash == new_rec.mesh_hash
                    and _is_property_only_change(prev_rec.params, new_rec.params)):
                # Same mesh, only cheap property-only params changed — update in place
                if self._plotter is not None:
                    _apply_property_update(prev_rec.actor, prev_rec.params, new_rec.params)
                new_rec.actor = prev_rec.actor
                result.updated_property += 1
            else:
                # Mesh changed, or a non-trivial param (colormap, scalars) changed
                # — remove and re-add
                if self._plotter is not None:
                    self._plotter.remove_actor(name)
                    mesh, params = new_named[name]
                    new_rec.actor = self._plotter.add_mesh(_unwrap(mesh), name=name, **params)
                result.updated += 1

        # Add new actors
        for name in added_names:
            if self._plotter is not None:
                mesh, params = new_named[name]
                new_records[name].actor = self._plotter.add_mesh(
                    _unwrap(mesh), name=name, **params
                )
            result.added += 1

        # Update state
        self._previous = new_records

        return result
