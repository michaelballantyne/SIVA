"""Tests for list_views and close_view MCP tools."""

import os
import time


class TestListViews:
    """Tests for the list_views MCP tool."""

    def test_list_views_empty(self, reset_server):
        """list_views returns a helpful message when no views exist."""
        from mcp_server.server import list_views

        result = list_views()
        assert "No views" in result
        assert "create_view" in result

    def test_list_views_one(self, view_dir, reset_server):
        """list_views shows the single active view with cache stats."""
        from mcp_server.server import list_views

        result = list_views()
        assert "Active views" in result
        assert "view-main" in result
        assert "view-main.py" in result
        # Should show hit/miss counts.
        assert "hit" in result
        assert "miss" in result
        # No errors for a clean pipeline.
        assert "no errors" in result.lower()

    def test_list_views_multiple(self, tmp_vtk_dir, reset_server):
        """list_views shows all active views."""
        from mcp_server.server import set_working_directory, create_view, list_views

        # Create two pipeline files.
        for name in ("view-alpha.py", "view-beta.py"):
            path = os.path.join(tmp_vtk_dir, name)
            with open(path, "w") as fh:
                fh.write('mesh = read("test.vtk")\nshow(mesh)\n')

        set_working_directory(tmp_vtk_dir)
        r1 = create_view("view-alpha.py")
        r2 = create_view("view-beta.py")
        assert "Error" not in r1, f"create_view alpha failed: {r1}"
        assert "Error" not in r2, f"create_view beta failed: {r2}"

        result = list_views()
        assert "view-alpha" in result
        assert "view-beta" in result
        # Both should appear as separate lines.
        assert result.count("view-alpha") >= 1
        assert result.count("view-beta") >= 1


    def test_list_views_shows_change_summary_after_reload(self, tmp_vtk_dir, reset_server):
        """After watcher re-executes a modified pipeline, list_views shows a change summary."""
        from mcp_server.server import set_working_directory, create_view, list_views

        # Write initial pipeline and create the view.
        pipeline_path = os.path.join(tmp_vtk_dir, "view-watch.py")
        with open(pipeline_path, "w") as f:
            f.write(
                'mesh = read("test.vtk")\n'
                'hot = mesh.threshold(value=200.0, scalars="T")\n'
                'show(hot, colormap="viridis")\n'
            )
        set_working_directory(tmp_vtk_dir)
        result = create_view("view-watch.py")
        assert "Error" not in result, f"create_view failed: {result}"

        # At this point no watcher reload has happened yet — no change summary.
        status_before = list_views()
        assert "Last change:" not in status_before, (
            f"Expected no change summary before first watcher reload:\n{status_before}"
        )

        # Edit the pipeline to change the threshold value.
        with open(pipeline_path, "w") as f:
            f.write(
                'mesh = read("test.vtk")\n'
                'hot = mesh.threshold(value=700.0, scalars="T")\n'
                'print(f"hot points: {hot.n_points}")\n'
                'show(hot, colormap="inferno")\n'
            )

        # Wait for the file watcher to pick up the change (up to 3 seconds).
        deadline = time.monotonic() + 3.0
        status_after = ""
        while time.monotonic() < deadline:
            status_after = list_views()
            if "Last change:" in status_after:
                break
            time.sleep(0.1)

        assert "Last change:" in status_after, (
            f"Expected 'Last change:' in list_views output after watcher reload:\n{status_after}"
        )
        # The summary should mention cache stats (cached/recomputed).
        assert "cached" in status_after or "recomputed" in status_after, (
            f"Expected cache info in change summary:\n{status_after}"
        )

    def test_list_views_shows_error_in_change_summary(self, tmp_vtk_dir, reset_server):
        """When a reload fails, list_views shows the error in last_change_summary."""
        from mcp_server.server import set_working_directory, create_view, list_views

        pipeline_path = os.path.join(tmp_vtk_dir, "view-errwatch.py")
        with open(pipeline_path, "w") as f:
            f.write('mesh = read("test.vtk")\nshow(mesh)\n')
        set_working_directory(tmp_vtk_dir)
        result = create_view("view-errwatch.py")
        assert "Error" not in result, f"create_view failed: {result}"

        # Overwrite with a bad pipeline (undefined name).
        with open(pipeline_path, "w") as f:
            f.write('mesh = read("test.vtk")\nbad = undefined_name + 1\nshow(mesh)\n')

        # Wait for watcher to report the error in the change summary.
        deadline = time.monotonic() + 3.0
        status = ""
        while time.monotonic() < deadline:
            status = list_views()
            if "Pipeline error:" in status:
                break
            time.sleep(0.1)

        assert "Pipeline error:" in status, (
            f"Expected 'Pipeline error:' in change summary after bad reload:\n{status}"
        )
        assert "NameError" in status or "undefined_name" in status, (
            f"Expected error details in change summary:\n{status}"
        )


class TestCloseView:
    """Tests for the close_view MCP tool."""

    def test_close_view(self, view_dir, reset_server):
        """close_view removes the view and confirms closure."""
        from mcp_server.server import close_view

        result = close_view("view-main.py")
        assert "view-main" in result
        assert "closed" in result.lower()
        assert "view-main" not in reset_server._views

    def test_close_view_not_found(self, reset_server):
        """close_view returns an error if the view doesn't exist."""
        from mcp_server.server import close_view

        result = close_view("nonexistent.py")
        assert result.startswith("Error")
        assert "nonexistent" in result or "no view" in result.lower()

    def test_close_view_stops_watcher(self, view_dir, reset_server):
        """close_view stops the file watcher."""
        vs = reset_server._views["view-main"]
        watcher = vs.watcher
        assert watcher is not None
        assert watcher.is_alive()

        from mcp_server.server import close_view
        close_view("view-main.py")

        # Watcher should no longer be alive after close.
        assert not watcher.is_alive()

    def test_close_view_list_views_after_close(self, view_dir, reset_server):
        """After close_view, list_views no longer shows the closed view."""
        from mcp_server.server import close_view, list_views

        close_view("view-main.py")
        result = list_views()
        assert "view-main" not in result
        # Should show the empty-state message.
        assert "No views" in result or "create_view" in result


