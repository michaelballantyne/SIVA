"""Incremental scene reconciliation: diffs old vs new actor sets and applies minimal updates."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .dispatch import stable_hash, _unwrap
from .proxy import TrackedProxy


# Params that only affect VTK Property — can be applied in-place without a mapper rebuild.
_PROPERTY_ONLY_PARAMS: frozenset[str] = frozenset(["opacity"])


@dataclass
class ActorRecord:
    """Snapshot of an actor in the current scene (name, mesh hash, params hash, live actor)."""

    name: str
    mesh_hash: str
    params_hash: str
    params: dict = field(default_factory=dict)
    actor: Optional[Any] = field(default=None, compare=False)


@dataclass
class ReconcileResult:
    """Counts from a single reconcile() pass."""

    unchanged: int = 0
    updated: int = 0         # removed and re-added (mesh or non-trivial param changed)
    updated_property: int = 0  # in-place property update only (e.g. opacity); no flicker
    added: int = 0
    removed: int = 0


def _mesh_hash(mesh_or_proxy) -> str:
    if isinstance(mesh_or_proxy, TrackedProxy):
        return object.__getattribute__(mesh_or_proxy, "_hash")
    return stable_hash(mesh_or_proxy)


def _params_hash(params: dict) -> str:
    return stable_hash(params)


def _is_property_only_change(prev_params: dict, new_params: dict) -> bool:
    """Return True if new_params differs from prev_params only in _PROPERTY_ONLY_PARAMS keys."""
    prev_keys = set(prev_params.keys())
    new_keys = set(new_params.keys())

    if prev_keys != new_keys:
        differing_keys = prev_keys.symmetric_difference(new_keys)
        if not differing_keys.issubset(_PROPERTY_ONLY_PARAMS):
            return False
        changed_values = {k for k in prev_keys & new_keys if prev_params[k] != new_params[k]}
        return changed_values.issubset(_PROPERTY_ONLY_PARAMS)

    changed_keys = {k for k in prev_keys if prev_params[k] != new_params[k]}
    return bool(changed_keys) and changed_keys.issubset(_PROPERTY_ONLY_PARAMS)


def _apply_property_update(actor: Any, prev_params: dict, new_params: dict) -> None:
    """Apply in-place VTK property updates (opacity, …) without a mapper rebuild."""
    if actor is None:
        return  # diff-only mode

    vtk_prop = actor.GetProperty()
    prev_opacity = prev_params.get("opacity")
    new_opacity = new_params.get("opacity")
    if new_opacity is not None and new_opacity != prev_opacity:
        vtk_prop.SetOpacity(float(new_opacity))
    elif new_opacity is None and prev_opacity is not None:
        vtk_prop.SetOpacity(1.0)  # reset to VTK default


class SceneReconciler:
    """Compute the minimal add/remove operations to update a PyVista Plotter scene.

    When plotter is None (diff-only mode), counts are computed but no plotter
    methods are called — useful for testing without a display.
    """

    def __init__(self, plotter=None):
        self._plotter = plotter
        self._previous: dict[str, ActorRecord] = {}  # name → previous ActorRecord

    def reconcile(self, new_actors: list[dict]) -> ReconcileResult:
        """Diff new_actors against current scene state and apply minimal updates.

        Each entry may be a dict with ``mesh`` and ``params`` keys, or a
        2-tuple ``(mesh, params)`` as produced by execute_pipeline.
        """
        result = ReconcileResult()

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

        new_named: dict[str, tuple[Any, dict]] = {}
        for i, (mesh, params) in enumerate(normalized):
            name = params.pop("name", None) or f"actor_{i}"
            new_named[name] = (mesh, params)

        new_records: dict[str, ActorRecord] = {}
        for name, (mesh, params) in new_named.items():
            mh = _mesh_hash(mesh)
            ph = _params_hash(params)
            new_records[name] = ActorRecord(
                name=name, mesh_hash=mh, params_hash=ph, params=dict(params)
            )

        prev_names = set(self._previous.keys())
        new_names = set(new_records.keys())
        removed_names = prev_names - new_names
        added_names = new_names - prev_names
        common_names = prev_names & new_names

        for name in removed_names:
            if self._plotter is not None:
                self._plotter.remove_actor(name)
            result.removed += 1

        for name in common_names:
            prev_rec = self._previous[name]
            new_rec = new_records[name]
            if (prev_rec.mesh_hash == new_rec.mesh_hash
                    and prev_rec.params_hash == new_rec.params_hash):
                result.unchanged += 1
                new_rec.actor = prev_rec.actor  # carry forward for future in-place updates
            elif (prev_rec.mesh_hash == new_rec.mesh_hash
                    and _is_property_only_change(prev_rec.params, new_rec.params)):
                if self._plotter is not None:
                    _apply_property_update(prev_rec.actor, prev_rec.params, new_rec.params)
                new_rec.actor = prev_rec.actor
                result.updated_property += 1
            else:
                # Mesh or non-trivial param changed — remove and re-add
                if self._plotter is not None:
                    self._plotter.remove_actor(name)
                    mesh, params = new_named[name]
                    new_rec.actor = self._plotter.add_mesh(_unwrap(mesh), name=name, **params)
                result.updated += 1

        for name in added_names:
            if self._plotter is not None:
                mesh, params = new_named[name]
                new_records[name].actor = self._plotter.add_mesh(
                    _unwrap(mesh), name=name, **params
                )
            result.added += 1

        self._previous = new_records

        return result
