import pytest
import tempfile
import os


def test_set_working_directory():
    from mcp_server.server import set_working_directory, _working_directory, _views
    # Reset state
    import mcp_server.server as srv
    srv._working_directory = None
    srv._views = {}

    with tempfile.TemporaryDirectory() as d:
        result = set_working_directory(d)
        assert "Working directory set to" in result
        assert srv._working_directory == d


def test_set_working_directory_invalid():
    import mcp_server.server as srv
    from mcp_server.server import set_working_directory
    srv._working_directory = None
    srv._views = {}
    result = set_working_directory("/nonexistent/path")
    assert "Error" in result


def test_set_working_directory_after_views():
    import mcp_server.server as srv
    from mcp_server.server import set_working_directory
    srv._working_directory = "/tmp"
    srv._views = {"test": "dummy"}
    result = set_working_directory("/tmp")
    assert "Error" in result
    srv._views = {}
