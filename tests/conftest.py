"""Pytest configuration and shared fixtures for VisLang tests."""

import os
import subprocess
import time
import signal
import pytest


# Path to the wildfire dataset used by integration tests.
_WILDFIRE_DATA = "output.30000.vts"

# Global Xvfb process handle
_xvfb_proc = None


def pytest_sessionstart(session):
    """Start a virtual X display (Xvfb) if no DISPLAY is set.

    VTK requires an X11 display for rendering even in offscreen mode.  In CI
    and headless environments there's no display, so we start Xvfb and set
    DISPLAY so VTK can find a GLX context.
    """
    global _xvfb_proc
    if not os.environ.get("DISPLAY"):
        try:
            _xvfb_proc = subprocess.Popen(
                ["Xvfb", ":99", "-screen", "0", "1024x768x24"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            # Give Xvfb a moment to start
            time.sleep(0.5)
            os.environ["DISPLAY"] = ":99"
        except FileNotFoundError:
            pass  # Xvfb not available — tests may fail if display required


def pytest_sessionfinish(session, exitstatus):
    """Shut down Xvfb after the test session."""
    global _xvfb_proc
    if _xvfb_proc is not None:
        _xvfb_proc.terminate()
        try:
            _xvfb_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _xvfb_proc.kill()
        _xvfb_proc = None
        # Clean up DISPLAY env var
        os.environ.pop("DISPLAY", None)


def pytest_collection_modifyitems(config, items):
    """Skip integration tests when the required dataset is not present.

    test_integration.py was designed to be run as a standalone script with
    the wildfire dataset symlinked into the working directory.  When run via
    pytest (e.g. in CI) without the dataset, skip those tests so the rest of
    the suite can run cleanly.
    """
    dataset_available = os.path.exists(_WILDFIRE_DATA)

    skip_no_dataset = pytest.mark.skip(
        reason=f"Integration tests require '{_WILDFIRE_DATA}' in the working "
               "directory. Run from a session folder with the dataset symlinked."
    )

    for item in items:
        if "test_integration" in item.nodeid:
            if not dataset_available:
                item.add_marker(skip_no_dataset)
