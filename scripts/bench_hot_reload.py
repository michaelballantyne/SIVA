"""Benchmark: hot-reload latency from file write to build complete.

Measures:
  - Cold first build
  - Warm rebuild (same content) — near-zero, mostly cache hits
  - Visual-only param change (color/opacity, same data)
  - Mid-pipeline change (threshold value: partial cache)

Run with: xvfb-run -a .venv/bin/python scripts/bench_hot_reload.py
"""

from __future__ import annotations

import os
import sys
import shutil
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_SYNTHETIC_VTI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets", "synthetic", "data", "output.vti"
)

if not os.path.exists(_SYNTHETIC_VTI):
    print(f"Synthetic dataset not found at {_SYNTHETIC_VTI}")
    print("Run: .venv/bin/python datasets/synthetic/generate.py")
    sys.exit(1)


def _make_pipeline(threshold_low=100.0, color="red", opacity=1.0):
    return f"""\
data = source("vtkXMLImageDataReader", FileName="{_SYNTHETIC_VTI}")
thresh = threshold(input=data, ThresholdBy="temperature",
                   ThresholdRange=[{threshold_low}, 1000.0])
show(thresh, "heat", color_by="temperature",
     scalar_range=({threshold_low}, 1000.0), lut="fire",
     opacity={opacity})
"""


class _FakeRenderer:
    """Minimal renderer that avoids display but supports the coordinator interface."""
    mode = None
    camera_positioned = False

    def __init__(self):
        from siva.renderer import RenderMode
        self.mode = RenderMode.OFFSCREEN

    def render(self): pass

    def dispatch(self, fn):
        return fn()

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake-png")
        return path

    def clear(self): pass

    def get_camera_state(self):
        return {"position": [0, 0, 1], "focal_point": [0, 0, 0], "up": [0, 1, 0]}

    def set_camera(self, **kwargs): pass

    def suggest_camera(self, style="overview"):
        return {"position": [0, -1, 1], "focal_point": [0, 0, 0], "up": [0, 0, 1]}

    def set_background(self, *a, **kw): pass
    def add_actor(self, *a, **kw): pass
    def add_volume(self, *a, **kw): pass
    def add_scalar_bar(self, *a, **kw): pass
    def add_overlay_actor(self, *a, **kw): pass
    def destroy(self): pass


class _FakeCtx:
    def __init__(self, name, tmp_dir):
        self.name = name
        self._tmp = tmp_dir
        self.vtk_objects = {}
        self.current_code = ""
        self.version = 0
        from siva.build_cache import BuildCache
        self.cache = BuildCache()

    @property
    def pipeline_file(self):
        return os.path.join(self._tmp, f"view-{self.name}.py")

    @property
    def history_dir(self):
        return Path(self._tmp) / ".siva" / "history" / self.name


def _bench(label, coordinator, pipeline_path, code):
    """Write file, time from write to build complete."""
    # Reset latest so wait_for_current always triggers a new build
    with coordinator._lock:
        coordinator._latest = None

    t_write = time.monotonic()
    Path(pipeline_path).write_text(code)
    record = coordinator.wait_for_current(timeout=30.0)
    t_done = time.monotonic()

    if record is None or record.status != "ok":
        error = record.error if record else "None"
        print(f"  {label:<35}: FAILED — {error}")
        return None

    elapsed = t_done - t_write
    cache = record.log[1] if len(record.log) > 1 else ""
    print(f"  {label:<35}: {elapsed*1000:.0f}ms  {cache}")
    return elapsed


def main():
    tmp = tempfile.mkdtemp()
    Path(tmp, ".siva").mkdir(parents=True, exist_ok=True)

    try:
        renderer = _FakeRenderer()
        ctx = _FakeCtx("main", tmp)
        from siva.hot_reload import BuildCoordinator, PipelineWatcher
        coordinator = BuildCoordinator(ctx, renderer)
        pipeline_path = ctx.pipeline_file

        print()
        print("=== Hot-Reload Benchmark ===")
        print()

        # Cold build
        code_v1 = _make_pipeline(threshold_low=100.0, color="red", opacity=1.0)
        t1 = _bench("Cold first build", coordinator, pipeline_path, code_v1)

        # Warm rebuild (same content — wait_for_current returns _latest immediately)
        # We need to force a new build by resetting _latest manually
        # But actually: same hash → wait_for_current returns _latest directly
        # so it's near-zero (just a dict lookup)
        with coordinator._lock:
            old_latest = coordinator._latest
        t0_warm = time.monotonic()
        r_warm = coordinator.wait_for_current(timeout=5.0)
        t_warm = time.monotonic() - t0_warm
        warm_ms = t_warm * 1000
        print(f"  {'Warm (same content, cache hit)':<35}: {warm_ms:.1f}ms  (returned from _latest, no build)")

        # Visual-only param change (different color/opacity, same data nodes)
        code_v2 = _make_pipeline(threshold_low=100.0, color="blue", opacity=0.8)
        t2 = _bench("Visual param change (opacity/lut)", coordinator, pipeline_path, code_v2)

        # Mid-pipeline change (different threshold — data cached, filters rerun)
        code_v3 = _make_pipeline(threshold_low=200.0, color="blue", opacity=0.8)
        t3 = _bench("Mid-pipeline change (new threshold)", coordinator, pipeline_path, code_v3)

        # Warm rebuild of v3
        with coordinator._lock:
            coordinator._latest = None  # force rebuild
        code_v3b = _make_pipeline(threshold_low=200.0, color="blue", opacity=0.8)
        t4 = _bench("Warm rebuild of v3 (full cache hits)", coordinator, pipeline_path, code_v3b)

        coordinator.shutdown()
        print()
        print(f"  Versions saved: {ctx.version}")
        print()

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
