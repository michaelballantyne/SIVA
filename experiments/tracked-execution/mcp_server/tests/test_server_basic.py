"""Tests for the set_working_directory MCP tool."""

import os
import tempfile

import pytest


def test_set_working_directory(reset_server):
    from mcp_server.server import set_working_directory

    with tempfile.TemporaryDirectory() as d:
        result = set_working_directory(d)
        assert "Working directory set to" in result
        assert reset_server._working_directory == d


def test_set_working_directory_invalid(reset_server):
    from mcp_server.server import set_working_directory

    result = set_working_directory("/nonexistent/path")
    assert "Error" in result


def test_set_working_directory_after_views(reset_server):
    from mcp_server.server import set_working_directory

    reset_server._working_directory = "/tmp"
    reset_server._views = {"test": "dummy"}
    result = set_working_directory("/tmp")
    assert "Error" in result
    # Restore clean state so the teardown fixture works correctly.
    reset_server._views = {}
