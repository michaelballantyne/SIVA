"""Pytest configuration and shared fixtures for VisLang tests."""

import os
import subprocess
import sys
import time
import numpy as np
import pytest


# Root of the repository.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Path to the wildfire dataset used by integration tests.
_WILDFIRE_DATA = os.path.join(
    REPO_ROOT, "datasets", "wildfire", "data", "output.30000.vts",
)

# Global Xvfb process handle
_xvfb_proc = None


def pytest_sessionstart(session):
    """Session-level setup: start Xvfb if needed, generate test data."""
    _start_xvfb_if_needed()
    _ensure_synthetic_data()


def pytest_sessionfinish(session, exitstatus):
    """Shut down Xvfb after the test session."""
    _stop_xvfb()


def pytest_collection_modifyitems(config, items):
    """Skip tests that require datasets not present locally."""
    dataset_available = os.path.exists(_WILDFIRE_DATA)

    skip_no_dataset = pytest.mark.skip(
        reason=f"Integration tests require '{_WILDFIRE_DATA}' in the working "
               "directory. Run from a session folder with the dataset symlinked."
    )

    for item in items:
        if "test_integration" in item.nodeid:
            if not dataset_available:
                item.add_marker(skip_no_dataset)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _start_xvfb_if_needed():
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
            time.sleep(0.5)
            os.environ["DISPLAY"] = ":99"
        except FileNotFoundError:
            pass  # Xvfb not available — tests may fail if display required


def _stop_xvfb():
    global _xvfb_proc
    if _xvfb_proc is not None:
        _xvfb_proc.terminate()
        try:
            _xvfb_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _xvfb_proc.kill()
        _xvfb_proc = None
        os.environ.pop("DISPLAY", None)


def _ensure_synthetic_data():
    """Generate the synthetic test dataset if it doesn't exist yet."""
    vti_path = os.path.join(REPO_ROOT, "datasets", "synthetic", "data", "output.vti")
    if not os.path.exists(vti_path):
        gen_script = os.path.join(REPO_ROOT, "datasets", "synthetic", "generate.py")
        if os.path.exists(gen_script):
            subprocess.run(
                [sys.executable, gen_script],
                cwd=REPO_ROOT,
                check=True,
                stdout=subprocess.DEVNULL,
            )


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_vti_path():
    """Path to the synthetic test dataset; auto-generated if absent."""
    _ensure_synthetic_data()
    path = os.path.join(REPO_ROOT, "datasets", "synthetic", "data", "output.vti")
    if not os.path.exists(path):
        pytest.skip("Synthetic dataset not present — run datasets/synthetic/generate.py")
    return path


# ---------------------------------------------------------------------------
# Shared helpers (not fixtures — take arguments, importable by test modules)
# ---------------------------------------------------------------------------

def make_image_data(dims=(10, 10, 10), field_name="temperature",
                    field_range=(0.0, 100.0)):
    """Create a vtkImageData with one scalar field in a known range.

    Useful for unit tests that need a small, self-contained VTK dataset
    without writing a file to disk.

    Args:
        dims: Tuple of (nx, ny, nz) dimensions.
        field_name: Name of the scalar array to add.
        field_range: (min, max) range for the linearly-spaced values.

    Returns:
        A ``vtkImageData`` with one active point scalar array.
    """
    import vtk
    from vtk.util.numpy_support import numpy_to_vtk

    img = vtk.vtkImageData()
    img.SetDimensions(*dims)
    img.SetOrigin(0.0, 0.0, 0.0)
    img.SetSpacing(1.0, 1.0, 1.0)
    n = img.GetNumberOfPoints()
    vals = np.linspace(field_range[0], field_range[1], n)
    arr = numpy_to_vtk(vals.astype(np.float64))
    arr.SetName(field_name)
    img.GetPointData().AddArray(arr)
    img.GetPointData().SetActiveScalars(field_name)
    return img
