"""Tests for terse vs verbose build reports from wait_for_pipeline / _build_report.

Verifies:
1. First build emits verbose-style report (has per-node section).
2. Second build with no changes emits a terse "no changes" report.
3. Second build with one param change lists the changed node tersely.
4. Errors always produce full structured error text even in terse mode.
5. verbose=True overrides terse default and returns full report.
6. Terse report is short (< 800 chars) for a happy-path no-change rebuild.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from siva.hot_reload import _build_report, _diff_node_statuses, _node_label, BuildCoordinator
from siva.renderer import RenderMode


# ---------------------------------------------------------------------------
# Fake renderer (mirrors the one in test_hot_reload.py)
# ---------------------------------------------------------------------------

class _FakeRenderer:
    mode = RenderMode.OFFSCREEN
    camera_positioned = False

    def render(self): pass

    def dispatch(self, fn):
        return fn()

    def screenshot(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(b"fake-png")
        return path

    def clear(self): pass

    def get_camera_state(self):
        return {"position": [0.0, 0.0, 1.0], "focal_point": [0.0, 0.0, 0.0], "up": [0.0, 1.0, 0.0]}

    def get_size(self):
        return (800, 600)

    def set_camera(self, **kwargs): pass
    def suggest_camera(self, style="overview"): return {"position": [0, -1, 1], "focal_point": [0, 0, 0], "up": [0, 0, 1]}
    def set_background(self, *args, **kwargs): pass
    def add_actor(self, *args, **kwargs): pass
    def add_volume(self, *args, **kwargs): pass
    def add_scalar_bar(self, *args, **kwargs): pass
    def add_overlay_actor(self, *args, **kwargs): pass
    def destroy(self): pass


# ---------------------------------------------------------------------------
# Minimal ViewContext stub (mirrors the one in test_hot_reload.py)
# ---------------------------------------------------------------------------

class _FakeCtx:
    def __init__(self, name: str, tmp_dir: str):
        self.name = name
        self._tmp = tmp_dir
        self.vtk_objects = {}
        self.current_code = ""
        self.version = 0
        self.applied_hash = None
        from siva.build_cache import BuildCache
        self.cache = BuildCache()

    @property
    def pipeline_file(self):
        return os.path.join(self._tmp, f"view-{self.name}.py")

    @property
    def history_dir(self):
        return Path(self._tmp) / ".siva" / "history" / self.name

    def save_version(self, code: str, screenshot_path) -> int:
        self.version += 1
        ver_dir = self.history_dir / f"v{self.version:04d}"
        ver_dir.mkdir(parents=True, exist_ok=True)
        (ver_dir / "pipeline.py").write_text(code)
        return self.version


# ---------------------------------------------------------------------------
# Sample node_statuses and show_statuses for unit tests
# ---------------------------------------------------------------------------

_CACHE_STATS_COLD = {"hits": 0, "misses": 3, "evictions": 0}
_CACHE_STATS_WARM = {"hits": 2, "misses": 1, "evictions": 0}
_CACHE_STATS_ALL_HITS = {"hits": 3, "misses": 0, "evictions": 0}

_NODE_STATUSES_V1 = {
    "0": {"status": "ok", "class": "vtkXMLImageDataReader", "name": "data"},
    "1": {"status": "ok", "class": "vtkThreshold", "name": "thresh",
          "num_points": 500, "num_cells": 400},
    "2": {"status": "ok", "class": "vtkDataSetSurfaceFilter", "name": "surf",
          "num_points": 200, "num_cells": 150},
}

_NODE_STATUSES_V2_CHANGED_THRESH = {
    "0": {"status": "ok", "class": "vtkXMLImageDataReader", "name": "data"},
    "1": {"status": "ok", "class": "vtkThreshold", "name": "thresh",
          "num_points": 600, "num_cells": 500},
    "2": {"status": "ok", "class": "vtkDataSetSurfaceFilter", "name": "surf",
          "num_points": 300, "num_cells": 250},
}

_NODE_STATUSES_WITH_ERROR = {
    "0": {"status": "ok", "class": "vtkXMLImageDataReader", "name": "data"},
    "1": {"status": "error", "class": "vtkThreshold", "name": "thresh",
          "kind": "build_error", "message": "field 'bad_field' not found"},
    "2": {"status": "skipped", "class": "vtkDataSetSurfaceFilter", "name": "surf",
          "upstream": "1"},
}

_NODE_STATUSES_WITH_WARNING = {
    "0": {"status": "ok", "class": "vtkXMLImageDataReader", "name": "data"},
    "1": {"status": "warning", "class": "vtkThreshold", "name": "thresh",
          "kind": "empty_output", "message": "Filter produced empty output"},
}

_SHOW_STATUSES_OK = {"surface": {"status": "ok", "class": "show"}}
_SHOW_STATUSES_EMPTY = {}


# ---------------------------------------------------------------------------
# Unit tests for _build_report
# ---------------------------------------------------------------------------

class TestBuildReportTerse(unittest.TestCase):
    """Unit tests for _build_report with verbose=False."""

    def _make_report(self, node_statuses, show_statuses=None, cache_stats=None,
                     prev_node_statuses=None, verbose=False):
        renderer = _FakeRenderer()
        return _build_report(
            node_statuses=node_statuses,
            show_statuses=show_statuses or _SHOW_STATUSES_OK,
            version=2,
            t_interpret=0.05,
            t_total=0.12,
            cache_stats=cache_stats or _CACHE_STATS_WARM,
            renderer=renderer,
            verbose=verbose,
            prev_node_statuses=prev_node_statuses,
        )

    def test_terse_no_changes_is_short(self):
        """Terse report with no changes is short (< 800 chars)."""
        hit_statuses = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
            "2": {"cached": True, "class": "vtkDataSetSurfaceFilter", "name": "surf"},
        }
        report = self._make_report(
            hit_statuses,
            prev_node_statuses=_NODE_STATUSES_V1,
            cache_stats=_CACHE_STATS_ALL_HITS,
        )
        self.assertLess(len(report), 800,
                        f"Terse no-change report should be < 800 chars, got {len(report)}")

    def test_terse_no_changes_says_no_data_node_changes(self):
        """Terse report with all-cache-hit build says 'No data-node changes'.

        The old bare "No changes" phrase was ambiguous — it only measured
        data-node diffs, but display-prop/camera/background/title/axes edits
        are re-applied on every build regardless. The phrase must be scoped
        to what it actually measures.
        """
        # Simulate a cache-hit build where all nodes have cached=True
        hit_statuses = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
            "2": {"cached": True, "class": "vtkDataSetSurfaceFilter", "name": "surf"},
        }
        report = self._make_report(
            hit_statuses,
            prev_node_statuses=_NODE_STATUSES_V1,
        )
        self.assertIn("No data-node changes", report)
        # The old bare phrase (unqualified) must not appear verbatim.
        self.assertNotIn("No changes", report)

    def test_terse_param_change_lists_changed_node(self):
        """Terse report with a cache-miss (param-changed) node names the rebuilt node."""
        # v2: data is cache hit; thresh and surf were rebuilt (no cached flag).
        prev_statuses = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
            "2": {"cached": True, "class": "vtkDataSetSurfaceFilter", "name": "surf"},
        }
        curr_statuses = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            # thresh rebuilt with new param (cache miss — no cached flag)
            "1": {"status": "ok", "class": "vtkThreshold", "name": "thresh",
                  "num_points": 600, "num_cells": 500},
            "2": {"status": "ok", "class": "vtkDataSetSurfaceFilter", "name": "surf",
                  "num_points": 300, "num_cells": 250},
        }
        report = self._make_report(
            curr_statuses,
            prev_node_statuses=prev_statuses,
        )
        self.assertIn("thresh", report,
                      "Rebuilt node 'thresh' should appear in terse report")
        self.assertNotIn("Nodes:", report,
                         "Terse report should not have full 'Nodes:' section")

    def test_terse_contains_version_and_cache(self):
        """Terse report includes version number and cache stats."""
        hit_statuses = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
            "2": {"cached": True, "class": "vtkDataSetSurfaceFilter", "name": "surf"},
        }
        report = self._make_report(
            hit_statuses,
            prev_node_statuses=_NODE_STATUSES_V1,
            cache_stats=_CACHE_STATS_WARM,
        )
        self.assertIn("v2", report)
        # Cache info: hits and misses
        self.assertIn("hits", report)
        self.assertIn("misses", report)

    def test_errors_always_produce_full_report(self):
        """Error builds always return verbose-style report regardless of verbose flag."""
        report = self._make_report(
            _NODE_STATUSES_WITH_ERROR,
            prev_node_statuses=_NODE_STATUSES_V1,
            verbose=False,
        )
        # Must contain the error message
        self.assertIn("ERROR", report)
        self.assertIn("bad_field", report)
        # Verbose path: has the 'Nodes:' section
        self.assertIn("Nodes:", report)

    def test_warnings_always_produce_full_report(self):
        """Warning builds always return verbose-style report regardless of verbose flag."""
        report = self._make_report(
            _NODE_STATUSES_WITH_WARNING,
            prev_node_statuses=_NODE_STATUSES_V1,
            verbose=False,
        )
        self.assertIn("WARNING", report)
        self.assertIn("Nodes:", report)

    def test_verbose_true_overrides_terse(self):
        """verbose=True returns full report even when all-hits (terse condition met)."""
        hit_statuses = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
            "2": {"cached": True, "class": "vtkDataSetSurfaceFilter", "name": "surf"},
        }
        terse = self._make_report(
            hit_statuses,
            prev_node_statuses=_NODE_STATUSES_V1,
            cache_stats=_CACHE_STATS_ALL_HITS,
            verbose=False,
        )
        full = self._make_report(
            hit_statuses,
            prev_node_statuses=_NODE_STATUSES_V1,
            cache_stats=_CACHE_STATS_ALL_HITS,
            verbose=True,
        )
        self.assertIn("Nodes:", full, "verbose=True should include 'Nodes:' section")
        self.assertNotIn("Nodes:", terse, "verbose=False should not include 'Nodes:' section")
        self.assertGreater(len(full), len(terse),
                           "verbose report should be longer than terse report")

    def test_verbose_camera_line_includes_up_and_sig_digit_formatting(self):
        """The verbose report's Camera line includes position, focal_point,
        and up (matching get_camera()'s significant-digit formatting via
        queries._fmt_tuple), rather than round(x, 1)."""
        full = self._make_report(
            _NODE_STATUSES_V1,
            prev_node_statuses=_NODE_STATUSES_V1,
            verbose=True,
        )
        camera_lines = [line for line in full.splitlines() if line.startswith("Camera:")]
        self.assertEqual(len(camera_lines), 1, f"Expected one Camera: line, got: {full!r}")
        camera_line = camera_lines[0]
        self.assertIn("position=", camera_line)
        self.assertIn("focal_point=", camera_line)
        self.assertIn("up=", camera_line)
        # _FakeRenderer.get_camera_state returns position=[0,0,1], up=[0,1,0]
        self.assertIn("position=(0, 0, 1)", camera_line)
        self.assertIn("focal_point=(0, 0, 0)", camera_line)
        self.assertIn("up=(0, 1, 0)", camera_line)

    def test_first_build_no_prev_statuses(self):
        """With no prev_node_statuses (first build), verbose=False still returns terse summary."""
        report = self._make_report(
            _NODE_STATUSES_V1,
            prev_node_statuses=None,
            verbose=False,
        )
        # Should be terse (no 'Nodes:' section) since no errors/warnings
        self.assertNotIn("Nodes:", report)
        self.assertIn("v2", report)

    def test_first_build_terse_says_initial_build_not_no_changes(self):
        """A first build (no prev_node_statuses to diff against) must not say
        'No data-node changes' — every node was just created, not unchanged.
        The terse header instead says 'Initial build' (node count already
        appears earlier in the header)."""
        report = self._make_report(
            _NODE_STATUSES_V1,
            prev_node_statuses=None,
            verbose=False,
        )
        self.assertIn("Initial build", report)
        self.assertNotIn("No data-node changes", report)

    def test_terse_shorter_than_verbose(self):
        """Terse report is always shorter than verbose for the same data."""
        hit_statuses = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
            "2": {"cached": True, "class": "vtkDataSetSurfaceFilter", "name": "surf"},
        }
        terse = self._make_report(
            hit_statuses,
            prev_node_statuses=_NODE_STATUSES_V1,
        )
        verbose = self._make_report(
            hit_statuses,
            prev_node_statuses=_NODE_STATUSES_V1,
            verbose=True,
        )
        self.assertLess(len(terse), len(verbose))


# ---------------------------------------------------------------------------
# Unit tests for _diff_node_statuses
# ---------------------------------------------------------------------------

class TestDiffNodeStatuses(unittest.TestCase):
    """Tests for _diff_node_statuses helper.

    The diff function uses 'cached: True' as the indicator of a cache hit.
    Cache hits are only reported if their diagnostic status changed.
    Cache misses (rebuilt nodes) are always reported as 'rebuilt'.
    """

    def test_no_prev_returns_empty(self):
        changes = _diff_node_statuses(_NODE_STATUSES_V1, None)
        self.assertEqual(changes, [])

    def test_all_cache_hits_no_changes(self):
        """All-cache-hit build produces no changes."""
        # Simulate a cache-hit build: all nodes have cached=True
        hit_statuses = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
            "2": {"cached": True, "class": "vtkDataSetSurfaceFilter", "name": "surf"},
        }
        changes = _diff_node_statuses(hit_statuses, _NODE_STATUSES_V1)
        self.assertEqual(changes, [],
                         "All cache hits with same status should produce no changes")

    def test_cache_miss_reported_as_rebuilt(self):
        """A node with no 'cached' field (cache miss) is reported as rebuilt."""
        # v1 was a full build (no cached=True). v2 has thresh rebuilt.
        prev = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
            "2": {"cached": True, "class": "vtkDataSetSurfaceFilter", "name": "surf"},
        }
        curr = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"status": "ok", "class": "vtkThreshold", "name": "thresh",
                  "num_points": 600},  # rebuilt — no cached flag
            "2": {"status": "ok", "class": "vtkDataSetSurfaceFilter", "name": "surf",
                  "num_points": 300},  # rebuilt — no cached flag
        }
        changes = _diff_node_statuses(curr, prev)
        # thresh and surf were rebuilt (cache misses) — entries include class
        # and output size (point/cell counts), so a rebuild is visibly not a
        # silent no-op.
        self.assertIn("rebuilt 'thresh' (vtkThreshold) → 600 points", changes)
        self.assertIn("rebuilt 'surf' (vtkDataSetSurfaceFilter) → 300 points", changes)
        # data was a cache hit with no status change
        self.assertNotIn("data", "\n".join(changes))

    def test_new_node_detected(self):
        prev = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
        }
        curr = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"status": "ok", "class": "vtkContourFilter", "name": "contour"},
        }
        changes = _diff_node_statuses(curr, prev)
        self.assertIn("added 'contour' (vtkContourFilter)", changes)

    def test_removed_node_detected(self):
        prev = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
            "1": {"cached": True, "class": "vtkThreshold", "name": "thresh"},
        }
        curr = {
            "0": {"cached": True, "class": "vtkXMLImageDataReader", "name": "data"},
        }
        changes = _diff_node_statuses(curr, prev)
        self.assertIn("removed 'thresh'", changes)

    def test_cache_hit_status_change_detected(self):
        """A cache hit that changes diagnostic status (ok -> error) is reported."""
        prev = {"0": {"cached": True, "class": "vtkThreshold", "name": "thresh",
                      "status": "ok"}}
        curr = {"0": {"cached": True, "class": "vtkThreshold", "name": "thresh",
                      "status": "error", "kind": "other", "message": "oops",
                      "upstream": ""}}
        changes = _diff_node_statuses(curr, prev)
        self.assertIn("updated 'thresh'", changes)


# ---------------------------------------------------------------------------
# Unit tests for _node_label
# ---------------------------------------------------------------------------

class TestNodeLabel(unittest.TestCase):
    """Tests for _node_label: how a node is named in build reports.

    Priority: explicit variable-binding name > the show() name it feeds (when
    unbound and shown under exactly one name) > the bare auto-generated id.
    """

    def test_bound_name_wins(self):
        self.assertEqual(_node_label(7, {"name": "thresh"}), "thresh")

    def test_shown_as_fallback_when_unbound(self):
        self.assertEqual(
            _node_label(7, {"shown_as": "skin"}), "node_7 [shown as 'skin']"
        )

    def test_bound_name_wins_over_shown_as(self):
        # compute.compute() never sets both, but _node_label should still
        # prefer the binding name if it did.
        self.assertEqual(
            _node_label(7, {"name": "thresh", "shown_as": "skin"}), "thresh"
        )

    def test_bare_id_when_neither(self):
        self.assertEqual(_node_label(7, {}), "node_7")


# ---------------------------------------------------------------------------
# Integration tests: coordinator produces terse and verbose reports
# ---------------------------------------------------------------------------

_SYNTHETIC_VTI = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "datasets", "synthetic", "data", "output.vti"
)


def _ensure_synthetic():
    if not os.path.exists(_SYNTHETIC_VTI):
        raise unittest.SkipTest("Synthetic dataset not present")


def _synthetic_vti_in_cwd():
    """Symlink the synthetic dataset into cwd and return its relative name.

    create_vtk_filter confines FileName to the working directory (see
    siva.filters.confine_to_workdir). BuildCoordinator doesn't chdir (in
    production there's a single global --workdir), so the dataset must be
    symlinked into the process's actual cwd for these builds to succeed.
    """
    link_name = "output.vti"
    if not os.path.exists(link_name):
        os.symlink(_SYNTHETIC_VTI, link_name)
    return link_name


class TestCoordinatorTerseVerbose(unittest.TestCase):
    """Integration tests verifying coordinator stores terse + verbose reports."""

    def setUp(self):
        _ensure_synthetic()
        self._tmp = tempfile.mkdtemp()
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
        self._ctx = _FakeCtx("main", self._tmp)
        self._renderer = _FakeRenderer()
        self._coordinator = BuildCoordinator(self._ctx, self._renderer)

    def tearDown(self):
        self._coordinator.shutdown()
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_pipeline(self, code):
        Path(self._coordinator._ctx.pipeline_file).write_text(code)

    def _pipeline_v1(self):
        return (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{_synthetic_vti_in_cwd()}")\n'
            'thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[100.0, 1000.0])\n'
            'surf = filter("vtkDataSetSurfaceFilter", input=thresh)\n'
            'show(surf, "surface")\n'
        )

    def _pipeline_v2(self):
        return (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{_synthetic_vti_in_cwd()}")\n'
            'thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[200.0, 1000.0])\n'
            'surf = filter("vtkDataSetSurfaceFilter", input=thresh)\n'
            'show(surf, "surface")\n'
        )

    def test_first_build_has_verbose_report(self):
        """First build: verbose_report is set (has full node listing)."""
        self._write_pipeline(self._pipeline_v1())
        r = self._coordinator.wait_for_current(timeout=15.0)
        self.assertIsNotNone(r)
        self.assertEqual(r.status, "ok", f"Build failed: {r.error}")
        self.assertIsNotNone(r.verbose_report)
        self.assertIn("Nodes:", r.verbose_report)

    def test_second_build_no_change_terse_is_short(self):
        """Second build with no content change: record.report (terse) is short."""
        # First build
        self._write_pipeline(self._pipeline_v1())
        r1 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r1.status, "ok", f"v1 failed: {r1.error}")

        # Slightly different pipeline (same source, different comment to force a new hash)
        v2_code = self._pipeline_v1() + "# same pipeline\n"
        self._write_pipeline(v2_code)
        r2 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertIsNotNone(r2)
        self.assertEqual(r2.status, "ok", f"v2 failed: {r2.error}")

        # Terse report should be short
        self.assertIsNotNone(r2.report)
        self.assertLess(len(r2.report), 800,
                        f"Terse report too long ({len(r2.report)} chars): {r2.report!r}")

    def test_second_build_identical_spec_says_spec_unchanged(self):
        """Second build with a new file hash but a structurally identical spec
        (same data nodes, same show() props, same scene state) says 'Spec
        unchanged' — the strongest form of "nothing happened", reserved for
        when literally nothing in the spec differs from the previous build.
        """
        self._write_pipeline(self._pipeline_v1())
        r1 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r1.status, "ok")

        # Force a new file hash with a trivial change that doesn't alter DSL params
        v2_code = self._pipeline_v1() + "# noop\n"
        self._write_pipeline(v2_code)
        r2 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r2.status, "ok")

        self.assertIn("Spec unchanged", r2.report,
                      f"Expected 'Spec unchanged' in terse report: {r2.report!r}")

    def test_second_build_param_change_lists_node(self):
        """Second build with one param change: terse report names the changed node."""
        self._write_pipeline(self._pipeline_v1())
        r1 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r1.status, "ok")

        self._write_pipeline(self._pipeline_v2())
        r2 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertIsNotNone(r2)
        self.assertEqual(r2.status, "ok", f"v2 failed: {r2.error}")

        # thresh changed parameters — should appear in terse report
        self.assertIn("thresh", r2.report,
                      f"Expected 'thresh' in terse report: {r2.report!r}")

    def test_rebuilt_node_reports_output_size(self):
        """A rebuilt (cache-miss) node's terse 'Changes:' entry includes its
        output point/cell counts, so a param edit's effect (or lack of one)
        on the geometry is visible without a separate query.
        """
        self._write_pipeline(self._pipeline_v1())
        r1 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r1.status, "ok")

        self._write_pipeline(self._pipeline_v2())
        r2 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r2.status, "ok", f"v2 failed: {r2.error}")

        self.assertRegex(
            r2.report, r"rebuilt 'thresh' \(vtkThreshold\) → [\d,]+ points, [\d,]+ cells",
            f"Expected sized rebuilt-node entry in terse report: {r2.report!r}",
        )

    def test_unbound_node_labeled_by_show_name(self):
        """A node with no variable binding, shown via an inline show() call,
        is labeled 'node_N [shown as ...]' in the verbose report instead of
        the opaque bare 'node_N'.
        """
        code = (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{_synthetic_vti_in_cwd()}")\n'
            'show(threshold(input=data, ThresholdBy="temperature", '
            'ThresholdRange=[100.0, 1000.0]), name="skin")\n'
        )
        self._write_pipeline(code)
        r = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r.status, "ok", f"build failed: {r.error}")
        self.assertIn("[shown as 'skin']", r.verbose_report,
                      f"Expected shown-as label in verbose report: {r.verbose_report!r}")

    def _pipeline_display_only(self, scalar_lo=100.0, scalar_hi=900.0):
        """Same data pipeline as _pipeline_v1, but with a color_by/scalar_range
        display prop on the show() directive — varying scalar_lo/hi between
        builds is a display-only edit (no data-node params change)."""
        return (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{_synthetic_vti_in_cwd()}")\n'
            'thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[100.0, 1000.0])\n'
            'surf = filter("vtkDataSetSurfaceFilter", input=thresh)\n'
            f'show(surf, "surface", color_by="temperature", scalar_range=({scalar_lo}, {scalar_hi}))\n'
        )

    def test_display_prop_only_change_names_reapplied_actor(self):
        """A display-prop-only edit (scalar_range on show()) is not swallowed
        by 'No data-node changes' — it must not read as a dropped edit, and
        the report must name the actor whose show() props were re-applied.
        """
        self._write_pipeline(self._pipeline_display_only(100.0, 900.0))
        r1 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r1.status, "ok", f"v1 failed: {r1.error}")

        # Display-only edit: scalar_range changes, no upstream filter param changes.
        self._write_pipeline(self._pipeline_display_only(200.0, 950.0))
        r2 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r2.status, "ok", f"v2 failed: {r2.error}")

        # The old ambiguous bare phrase must never appear next to a real edit.
        self.assertNotIn("No changes", r2.report,
                         f"Bare 'No changes' should never appear: {r2.report!r}")
        # Data nodes are unaffected (scalar_range is a show()-only prop).
        self.assertIn("No data-node changes", r2.report)
        # The actor whose display props were re-applied must be named.
        self.assertIn("surface", r2.report,
                      f"Expected re-applied actor 'surface' named in report: {r2.report!r}")

        # Verbose mode should also show the changed scalar_range key (old -> new).
        self.assertIsNotNone(r2.verbose_report)
        self.assertIn("scalar_range", r2.verbose_report)
        self.assertIn("100.0", r2.verbose_report)
        self.assertIn("200.0", r2.verbose_report)

    def _pipeline_with_camera(self, with_camera: bool):
        code = (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{_synthetic_vti_in_cwd()}")\n'
            'thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[100.0, 1000.0])\n'
            'surf = filter("vtkDataSetSurfaceFilter", input=thresh)\n'
            'show(surf, "surface")\n'
        )
        if with_camera:
            code += 'camera(position=(5.0, 5.0, 5.0), focal_point=(0.0, 0.0, 0.0))\n'
        return code

    def test_camera_only_change_reports_scene_set_from_file(self):
        """A camera()-only edit (no data-node change) is reported as scene
        state applied from the file, not swallowed by 'No data-node changes'.
        """
        self._write_pipeline(self._pipeline_with_camera(with_camera=False))
        r1 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r1.status, "ok", f"v1 failed: {r1.error}")

        self._write_pipeline(self._pipeline_with_camera(with_camera=True))
        r2 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r2.status, "ok", f"v2 failed: {r2.error}")

        self.assertNotIn("No changes", r2.report,
                         f"Bare 'No changes' should never appear: {r2.report!r}")
        self.assertIn("No data-node changes", r2.report)
        self.assertIn("camera", r2.report,
                      f"Expected camera to be reported as set from file: {r2.report!r}")

    def test_verbose_report_longer_than_terse(self):
        """verbose_report is longer than the terse report for the same build."""
        self._write_pipeline(self._pipeline_v1())
        r1 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r1.status, "ok")

        # Second build
        v2_code = self._pipeline_v1() + "# bump\n"
        self._write_pipeline(v2_code)
        r2 = self._coordinator.wait_for_current(timeout=15.0)
        self.assertEqual(r2.status, "ok")

        self.assertIsNotNone(r2.report)
        self.assertIsNotNone(r2.verbose_report)
        self.assertGreater(len(r2.verbose_report), len(r2.report),
                           "verbose_report should be longer than terse report")


# ---------------------------------------------------------------------------
# Integration tests via wait_for_pipeline MCP tool
# ---------------------------------------------------------------------------

class TestRunPipelineVerboseParam(unittest.TestCase):
    """Tests for wait_for_pipeline(verbose=...) MCP tool parameter."""

    def setUp(self):
        _ensure_synthetic()
        self._tmp = tempfile.mkdtemp()
        self._orig_cwd = os.getcwd()
        os.chdir(self._tmp)
        Path(self._tmp, ".siva").mkdir(parents=True, exist_ok=True)
        import siva.server as srv
        self._srv = srv
        renderer = _FakeRenderer()
        srv._init_for_test(renderer)

    def tearDown(self):
        import siva.server as srv
        for ctx in srv._views.values():
            try:
                ctx.shutdown()
            except Exception:
                pass
        os.chdir(self._orig_cwd)
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_pipeline(self, code):
        ctx = self._srv._current_ctx()
        Path(ctx.pipeline_file).write_text(code)

    def _pipeline(self, thresh_min=100.0, comment=""):
        code = (
            'from siva.spec_api import *\n\n'
            f'data = source("vtkXMLImageDataReader", FileName="{_synthetic_vti_in_cwd()}")\n'
            f'thresh = threshold(input=data, ThresholdBy="temperature", ThresholdRange=[{thresh_min}, 1000.0])\n'
            'surf = filter("vtkDataSetSurfaceFilter", input=thresh)\n'
            'show(surf, "surface")\n'
        )
        if comment:
            code += f"# {comment}\n"
        return code

    def test_wait_for_pipeline_verbose_false_terse_output(self):
        """wait_for_pipeline(verbose=False) returns short terse text on second build."""
        self._write_pipeline(self._pipeline())
        r1 = self._srv.wait_for_pipeline(verbose=False)
        self.assertIsInstance(r1, list)
        # Second call (same content, immediate return from cache)
        r2 = self._srv.wait_for_pipeline(verbose=False)
        text2 = r2[0]
        self.assertLess(len(text2), 800,
                        f"Terse output too long ({len(text2)} chars): {text2!r}")

    def test_wait_for_pipeline_verbose_true_full_output(self):
        """wait_for_pipeline(verbose=True) returns full report with 'Nodes:' section."""
        self._write_pipeline(self._pipeline())
        r = self._srv.wait_for_pipeline(verbose=True)
        text = r[0]
        self.assertIn("Nodes:", text,
                      f"verbose=True should include Nodes section: {text!r}")

    def test_wait_for_pipeline_default_is_terse(self):
        """wait_for_pipeline() with no args defaults to terse (verbose=False)."""
        self._write_pipeline(self._pipeline())
        # Build
        self._srv.wait_for_pipeline()
        # Second run — same content, returns from cache — should be terse
        r = self._srv.wait_for_pipeline()
        text = r[0]
        self.assertLess(len(text), 800,
                        f"Default wait_for_pipeline should be terse on second call, got {len(text)} chars")

    def test_verbose_false_terse_is_shorter_than_verbose_true(self):
        """Terse output is always shorter than verbose for the same pipeline."""
        self._write_pipeline(self._pipeline())
        r_terse = self._srv.wait_for_pipeline(verbose=False)
        r_verbose = self._srv.wait_for_pipeline(verbose=True)
        self.assertLess(len(r_terse[0]), len(r_verbose[0]),
                        "Terse report should be shorter than verbose report")


if __name__ == "__main__":
    unittest.main()
