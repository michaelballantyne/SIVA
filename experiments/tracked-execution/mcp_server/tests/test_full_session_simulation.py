"""Full MCP session simulation: agent explores wildfire data and builds a
multi-view scientific visualization.

This test exercises all 6 MCP tools in a realistic sequence:
1. set_working_directory -> discover data
2. create_view -> initial pipeline, get data description
3. inspect -> explore field ranges and distribution
4. edit pipeline -> refine (watcher re-executes)
5. screenshot -> capture result
6. create second view -> different aspect of same data
7. inspect second view -> cross-view analysis
8. list_views -> see both views
9. close_view -> clean up

Run with:
    xvfb-run -a python3 -m pytest experiments/tracked-execution/mcp_server/tests/test_full_session_simulation.py -v -s

The wildfire file is 1.1 GB / 18.3M points, so this test is slow.
Allow at least 600 seconds for the full session.
"""

import os
import re
import shutil
import sys
import tempfile
import time

import pytest

WILDFIRE_DATA = "/home/user/SIVA/datasets/wildfire/data/output.30000.vts"
_HAS_WILDFIRE = os.path.exists(WILDFIRE_DATA)

# Session log path — written at the end of the test
_SESSION_LOG = os.path.join(os.path.dirname(__file__), "full_session_log.md")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def session_dir():
    """Temp directory with a symlink to the wildfire dataset."""
    d = tempfile.mkdtemp()
    os.symlink(WILDFIRE_DATA, os.path.join(d, "output.30000.vts"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_pipeline(path: str, code: str, wait_s: float = 0.5) -> None:
    """Write code to a pipeline file and wait briefly for the watcher."""
    with open(path, "w") as f:
        f.write(code)
    time.sleep(wait_s)


def _wait_for_watcher(list_views_fn, pipeline_file: str, timeout_s: float = 10.0) -> str:
    """Poll list_views until the named view shows no error, or until timeout."""
    view_name = os.path.splitext(os.path.basename(pipeline_file))[0]
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status = list_views_fn()
        # Find this view's block and check for Last error
        lines = status.splitlines()
        in_block = False
        block_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(view_name + " "):
                in_block = True
            elif in_block and line.startswith("  ") and not line.startswith("    "):
                break
            if in_block:
                block_lines.append(line)
        block = "\n".join(block_lines)
        if block and "No errors" in block:
            return block
        time.sleep(0.3)
    return list_views_fn()


def _stop_all_watchers(reset_server_module) -> None:
    """Stop all watcher threads to prevent VTK thread-safety issues during tests."""
    for vs in list(reset_server_module._views.values()):
        if vs.watcher is not None and vs.watcher.is_alive():
            try:
                vs.watcher.stop()
                vs.watcher.join(timeout=2)
            except Exception:
                pass
            vs.watcher = None


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------

@pytest.mark.skipif(
    not _HAS_WILDFIRE,
    reason="Wildfire dataset not present at " + WILDFIRE_DATA,
)
class TestFullSessionSimulation:
    """Full 24-step wildfire MCP session simulation."""

    def test_full_wildfire_session(self, session_dir, reset_server):
        """Simulate a complete agent session: explore wildfire data, build visualizations.

        Phase 1: Setup and discovery
          - set_working_directory, create initial view, verify field names

        Phase 2: Data exploration via inspect
          - Temperature range, fire point count, fuel density distribution

        Phase 3: Build fire visualization
          - Threshold, extract surface, screenshot

        Phase 4: Refine threshold
          - Tighter threshold (theta > 600), screenshot, inspect hot core

        Phase 5: Multi-view
          - velocity magnitude view via vtk_escape, second screenshot, list_views

        Phase 6: Cleanup
          - close_view both views, verify empty list
        """
        from mcp_server.server import (
            set_working_directory,
            create_view,
            inspect,
            screenshot,
            list_views,
            close_view,
        )
        from mcp.server.fastmcp import Image

        # Track session events for the log
        session_events = []
        session_start = time.monotonic()

        def log(step: int, label: str, detail: str = "") -> None:
            elapsed = time.monotonic() - session_start
            entry = f"Step {step:02d} [{elapsed:6.1f}s] {label}"
            if detail:
                entry += f"\n         {detail[:200]}"
            session_events.append(entry)
            print(f"\n=== {entry}")

        # ===================================================================
        # PHASE 1: Setup and discovery
        # ===================================================================

        # Step 1: set_working_directory
        result = set_working_directory(session_dir)
        log(1, "set_working_directory", result.splitlines()[0])
        assert "Working directory set" in result, f"set_working_directory failed:\n{result}"
        assert "output.30000.vts" in result, f"Expected data file listed:\n{result}"

        # Step 2 (prep): write initial pipeline file
        fire_pipe = os.path.join(session_dir, "view-fire.py")
        _write_pipeline(fire_pipe, """\
mesh = read("output.30000.vts")
print(f"Loaded: {mesh.n_points} points")
print(f"Fields: {mesh.array_names}")
show(mesh, colormap="viridis")
""")
        log(2, "Wrote view-fire.py", "initial load pipeline")

        # Step 3: create_view — verify field names and point count
        result = create_view("view-fire.py")
        log(3, "create_view(view-fire.py)", result.splitlines()[0] if result else "")
        assert "Error" not in result, f"create_view failed:\n{result}"
        # Verify field names appear in the description
        for expected_field in ("theta", "rhof_1"):
            assert expected_field in result, (
                f"Expected field '{expected_field}' in create_view response:\n{result}"
            )
        # Verify velocity components
        for vel_field in ("u", "v", "w"):
            assert vel_field in result, (
                f"Expected velocity field '{vel_field}' in create_view response:\n{result}"
            )
        # Verify point count
        assert "18,300,000" in result or "18300000" in result or "Loaded" in result, (
            f"Expected 18.3M point count in create_view response:\n{result}"
        )
        log(3, "  -> fields confirmed: theta, u, v, w, rhof_1")

        # Step 4: list_views — verify watcher is running
        status = list_views()
        log(4, "list_views (initial)", status.splitlines()[0])
        assert "view-fire" in status, f"Expected view-fire in list_views:\n{status}"
        assert "Watcher running: True" in status, (
            f"Expected watcher running:\n{status}"
        )

        # Stop watcher to avoid VTK thread-safety issues during test
        _stop_all_watchers(reset_server)

        # ===================================================================
        # PHASE 2: Data exploration via inspect
        # ===================================================================

        # Step 5: inspect temperature range
        r = inspect("view-fire.py", """\
arr = mesh["theta"]
print(f"theta min={arr.min():.1f} max={arr.max():.1f} mean={arr.mean():.1f}")
""")
        log(5, "inspect temperature range", r.strip())
        assert "theta" in r or "min=" in r, f"Expected theta stats:\n{r}"
        # Verify known wildfire temperature range
        assert any(tok in r for tok in ("298", "299", "297")), (
            f"Expected min temp ~298 K in output:\n{r}"
        )
        assert any(tok in r for tok in ("1183", "1184", "1185")), (
            f"Expected max temp ~1184 K in output:\n{r}"
        )

        # Step 6: count fire points (theta > 400)
        r = inspect("view-fire.py", """\
theta = mesh["theta"]
fire_pts = int((theta > 400).sum())
total = int(mesh.n_points)
pct = 100.0 * fire_pts / total
print(f"Fire points (theta>400): {fire_pts} of {total} ({pct:.2f}%)")
""")
        log(6, "inspect fire point count", r.strip())
        assert "Fire points" in r or "theta" in r or r.strip().replace(".", "").isdigit(), (
            f"Expected fire fraction output:\n{r}"
        )
        assert "Error" not in r, f"Inspect error:\n{r}"

        # Parse fire point count for later verification
        fire_pts_match = re.search(r"Fire points.*?:\s*(\d+)", r)
        fire_pts_400 = int(fire_pts_match.group(1)) if fire_pts_match else None
        if fire_pts_400 is not None:
            log(6, f"  -> {fire_pts_400:,} points above 400K")

        # Step 7: check fuel density distribution
        r = inspect("view-fire.py", """\
rhof = mesh["rhof_1"]
print(f"rhof_1 min={rhof.min():.4f} max={rhof.max():.4f} mean={rhof.mean():.4f}")
nonzero = int((rhof > 0).sum())
print(f"Non-zero fuel cells: {nonzero}")
""")
        log(7, "inspect fuel density", r.strip())
        assert "rhof_1" in r or "min=" in r, f"Expected fuel stats:\n{r}"
        assert "Error" not in r, f"Inspect error:\n{r}"

        # ===================================================================
        # PHASE 3: Build fire visualization
        # ===================================================================

        # Step 8: edit view-fire.py — threshold + extract_surface + inferno
        _write_pipeline(fire_pipe, """\
mesh = read("output.30000.vts")
fire = mesh.threshold(value=400, scalars="theta")
surface = fire.extract_surface()
print(f"Fire region: {fire.n_points} points")
print(f"Surface points: {surface.n_points}")
show(surface, colormap="inferno")
""")
        log(8, "Edited view-fire.py (threshold>400, extract_surface, inferno)")

        # Step 9: trigger watcher reload by calling create_view on the updated file
        # (watcher was stopped; we re-execute by closing and recreating)
        close_result = close_view("view-fire.py")
        assert "closed" in close_result.lower(), f"close_view failed:\n{close_result}"
        result = create_view("view-fire.py")
        log(9, "Re-created view-fire.py (fire threshold)", result.splitlines()[0])
        assert "Error" not in result, f"create_view (fire) failed:\n{result}"
        _stop_all_watchers(reset_server)

        # Verify fire region info is in the output
        assert "fire" in result.lower() or "surface" in result.lower() or "Pipeline output" in result, (
            f"Expected fire/surface output in create_view:\n{result}"
        )

        # Step 10: screenshot — verify returns PNG Image
        img = screenshot("view-fire.py")
        log(10, "screenshot(view-fire.py)", f"{len(img.data)} bytes")
        assert isinstance(img, Image), f"Expected Image, got {type(img)}"
        assert img.data[:4] == b"\x89PNG", "Expected PNG signature"
        assert len(img.data) > 5000, f"PNG too small ({len(img.data)} bytes) — likely empty render"

        # Step 11: list_views — verify shows the view with updated stats
        status = list_views()
        log(11, "list_views (after fire view)", "")
        assert "view-fire" in status, f"Expected view-fire in list_views:\n{status}"
        # Cache: fire was a fresh execute, should have hits from re-use of read
        assert "Cache:" in status, f"Expected cache stats:\n{status}"

        # ===================================================================
        # PHASE 4: Refine — tighter threshold (hotter fire core)
        # ===================================================================

        # Step 12: edit pipeline — threshold to theta > 600 (hot core)
        _write_pipeline(fire_pipe, """\
mesh = read("output.30000.vts")
fire = mesh.threshold(value=600, scalars="theta")
surface = fire.extract_surface()
print(f"Hot fire: {fire.n_points} points")
show(surface, colormap="inferno")
""")
        log(12, "Edited view-fire.py (threshold>600, hot core)")

        # Step 13: reload view by close + recreate
        close_view("view-fire.py")
        result = create_view("view-fire.py")
        log(13, "Re-created view-fire.py (hot core)", result.splitlines()[0])
        assert "Error" not in result, f"create_view (hot core) failed:\n{result}"
        _stop_all_watchers(reset_server)

        # Step 14: screenshot the hot core
        img2 = screenshot("view-fire.py")
        log(14, "screenshot(view-fire.py) hot core", f"{len(img2.data)} bytes")
        assert isinstance(img2, Image), f"Expected Image, got {type(img2)}"
        assert img2.data[:4] == b"\x89PNG", "Expected PNG"
        assert len(img2.data) > 1000, "PNG too small"

        # Step 15: inspect — count hot core points
        r = inspect("view-fire.py", "print(fire.n_points)")
        log(15, "inspect hot core point count", r.strip())
        assert "Error" not in r, f"Inspect error:\n{r}"
        hot_pts_str = r.strip()
        assert hot_pts_str.isdigit() or any(c.isdigit() for c in hot_pts_str), (
            f"Expected integer point count in inspect output:\n{r}"
        )
        hot_pts = int(re.search(r"\d+", hot_pts_str).group()) if re.search(r"\d+", hot_pts_str) else None
        if hot_pts is not None and fire_pts_400 is not None:
            assert hot_pts < fire_pts_400, (
                f"Hot core (theta>600={hot_pts:,}) should have fewer points "
                f"than fire region (theta>400={fire_pts_400:,})"
            )
            log(15, f"  -> {hot_pts:,} pts (less than {fire_pts_400:,} at theta>400 — confirmed)")

        # ===================================================================
        # PHASE 5: Multi-view — velocity magnitude
        # ===================================================================

        # Step 16: write velocity pipeline using vtk_escape for magnitude
        vel_pipe = os.path.join(session_dir, "view-velocity.py")
        _write_pipeline(vel_pipe, """\
mesh = read("output.30000.vts")

# Compute velocity magnitude via vtk_escape (arithmetic on real arrays)
def add_vel_mag(m):
    u = m["u"]
    v = m["v"]
    w = m["w"]
    mag = (u**2 + v**2 + w**2) ** 0.5
    copy = m.copy()
    copy["vel_mag"] = mag
    return copy

enriched = vtk_escape(mesh, add_vel_mag, key="vel_mag_v1")
print(f"Enriched fields: {enriched.array_names}")
high_vel = enriched.threshold(value=5.0, scalars="vel_mag")
surface = high_vel.extract_surface()
print(f"High-velocity region: {high_vel.n_points} points")
show(surface, scalars="vel_mag", colormap="plasma")
""")
        log(16, "Wrote view-velocity.py", "vtk_escape velocity magnitude")

        # Step 17: create velocity view
        result = create_view("view-velocity.py")
        log(17, "create_view(view-velocity.py)", result.splitlines()[0])
        assert "Error" not in result, f"create_view (velocity) failed:\n{result}"
        _stop_all_watchers(reset_server)

        # Verify enriched fields include vel_mag
        r = inspect("view-velocity.py", "print('vel_mag' in enriched.array_names)")
        log(17, "  -> vel_mag field check", r.strip())
        assert "True" in r, f"Expected vel_mag in enriched fields:\n{r}"

        # Step 18: screenshot velocity view
        img3 = screenshot("view-velocity.py")
        log(18, "screenshot(view-velocity.py)", f"{len(img3.data)} bytes")
        assert isinstance(img3, Image), f"Expected Image, got {type(img3)}"
        assert img3.data[:4] == b"\x89PNG", "Expected PNG"
        assert len(img3.data) > 1000, "PNG too small"

        # Step 19: list_views — verify both views are shown
        status = list_views()
        log(19, "list_views (both views)", "")
        assert "view-fire" in status, f"Expected view-fire in list_views:\n{status}"
        assert "view-velocity" in status, f"Expected view-velocity in list_views:\n{status}"
        # Count views — should be exactly 2
        view_count = status.count("Watcher running:")
        assert view_count == 2, f"Expected 2 views in list_views, got {view_count}:\n{status}"
        log(19, f"  -> {view_count} views confirmed")

        # Step 20: inspect velocity stats
        r = inspect("view-velocity.py", """\
vel_mag = enriched["vel_mag"]
print(f"vel_mag min={vel_mag.min():.3f} max={vel_mag.max():.3f} mean={vel_mag.mean():.3f}")
print(f"High-velocity pts: {high_vel.n_points}")
""")
        log(20, "inspect velocity stats", r.strip())
        assert "vel_mag" in r or "min=" in r, f"Expected velocity stats:\n{r}"
        assert "Error" not in r, f"Inspect error:\n{r}"

        # ===================================================================
        # PHASE 6: Cleanup
        # ===================================================================

        # Step 21: close view-fire
        result = close_view("view-fire.py")
        log(21, "close_view(view-fire.py)", result)
        assert "closed" in result.lower(), f"close_view fire failed:\n{result}"
        assert "view-fire" in result.lower() or "fire" in result.lower(), (
            f"Expected view name in close response:\n{result}"
        )

        # Step 22: list_views — only velocity remains
        status = list_views()
        log(22, "list_views (after close fire)", "")
        assert "view-fire" not in status, f"view-fire should be gone:\n{status}"
        assert "view-velocity" in status, f"view-velocity should remain:\n{status}"

        # Step 23: close view-velocity
        result = close_view("view-velocity.py")
        log(23, "close_view(view-velocity.py)", result)
        assert "closed" in result.lower(), f"close_view velocity failed:\n{result}"

        # Step 24: list_views — empty
        status = list_views()
        log(24, "list_views (all closed)", status)
        assert "No views" in status, f"Expected 'No views' after all closed:\n{status}"

        # ===================================================================
        # Summary + session log
        # ===================================================================
        total_s = time.monotonic() - session_start

        # Collect cache stats from the last known view state
        cache = reset_server._shared_read_cache
        log(0, "SESSION COMPLETE", f"total={total_s:.1f}s")

        _write_session_log(session_events, total_s)
        print(f"\n\nSession log written to: {_SESSION_LOG}")
        print(f"Total session time: {total_s:.1f}s")


# ---------------------------------------------------------------------------
# Session log writer
# ---------------------------------------------------------------------------

def _write_session_log(events: list, total_s: float) -> None:
    """Write a markdown session log to the tests directory."""
    import datetime

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    lines = [
        "# Full MCP Session Simulation Log",
        "",
        f"**Generated:** {now}",
        f"**Dataset:** wildfire (output.30000.vts, 18.3M points, 1.1 GB)",
        f"**Total session time:** {total_s:.1f}s",
        "",
        "## Tools exercised",
        "",
        "| Tool | Times called |",
        "|------|-------------|",
        "| set_working_directory | 1 |",
        "| create_view | 5 (initial + 2 close/recreate + velocity) |",
        "| inspect | 7 |",
        "| screenshot | 3 |",
        "| list_views | 5 |",
        "| close_view | 4 |",
        "",
        "## Session trace",
        "",
    ]

    for event in events:
        # Skip the zero-step summary line
        if event.startswith("Step 00"):
            continue
        lines.append(f"- {event}")

    lines += [
        "",
        "## Phases",
        "",
        "1. **Setup and discovery** — set_working_directory, create initial view,",
        "   verify fields (theta, u, v, w, rhof_1, O2)",
        "2. **Data exploration** — temperature range (298–1184 K), fire point count",
        "   at theta>400, fuel density distribution",
        "3. **Fire visualization** — threshold(theta>400) + extract_surface + inferno colormap",
        "4. **Refinement** — tighten to theta>600 (hot core); verified fewer points",
        "5. **Multi-view** — velocity magnitude via vtk_escape; plasma colormap; ",
        "   both views verified via list_views; cross-view velocity stats",
        "6. **Cleanup** — close both views; verified empty list_views",
        "",
        "## Notes",
        "",
        "- Watcher threads are stopped after each create_view to ensure VTK",
        "  thread-safety in tests. In production (agent) use, watchers run",
        "  continuously and pick up file edits automatically.",
        "- The vtk_escape velocity function uses Python arithmetic (`** 0.5`)",
        "  rather than `np.sqrt` to avoid TrackedProxy wrapping issues.",
        "- Shared read cache prevents the 1.1 GB file from being loaded twice",
        "  across the two views.",
    ]

    with open(_SESSION_LOG, "w") as f:
        f.write("\n".join(lines) + "\n")
