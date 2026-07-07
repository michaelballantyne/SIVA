"""Pytest configuration and shared fixtures for SIVA tests."""

import ctypes
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

# Keeps the ctypes callback for the custom X error handler alive for the
# lifetime of the process — see _install_nonfatal_x_error_handler().
_x_error_handler_ref = None


def pytest_sessionstart(session):
    """Session-level setup: start Xvfb if needed, generate test data."""
    _start_xvfb_if_needed()
    _install_nonfatal_x_error_handler()
    _ensure_synthetic_data()


def pytest_sessionfinish(session, exitstatus):
    """Shut down Xvfb after the test session.

    Finalizes any surviving VTK render windows first. Offscreen/interactive
    render windows created by tests aren't always torn down deterministically
    before the session ends, and some linger until the garbage collector
    reclaims them — which can happen well after Xvfb would otherwise be
    stopped. Finalizing them here, while the display is still up, releases
    their GL contexts while the connection is known-good rather than leaving
    that to whenever the interpreter happens to collect them.
    """
    _finalize_render_windows()
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


def _finalize_render_windows():
    """Explicitly Finalize any live vtkRenderWindow instances.

    Tests create renderers (offscreen and interactive) that aren't always
    torn down deterministically before the session ends; some linger until
    the garbage collector reclaims them, which can happen well after
    ``_stop_xvfb()`` runs. Finalizing them here, while the X display is
    still up, releases their GL contexts cleanly instead of leaving that to
    whenever the interpreter happens to collect them.
    """
    import gc

    import vtk

    gc.collect()
    for obj in gc.get_objects():
        if isinstance(obj, vtk.vtkRenderWindow):
            try:
                obj.Finalize()
            except Exception:
                pass


def _install_nonfatal_x_error_handler():
    """Install a process-wide X error handler that never aborts the process.

    Xlib's built-in default error handler (the one in effect whenever nothing
    else has called ``XSetErrorHandler``) prints the "X Error of failed
    request: ..." block and then calls the C-level ``exit(1)`` — for *any*
    protocol error, not just fatal I/O errors. VTK installs its own handler
    while a render window is actively current, but during teardown (window
    destructors, GC of a leftover render window at interpreter shutdown) that
    guard isn't always active, so a late/duplicate GLX call (e.g.
    ``glXMakeCurrent`` on an already-torn-down context) can trip the raw
    Xlib default and hard-``exit(1)`` the whole pytest process — flipping an
    otherwise all-green run to a CI failure.

    Installing a handler that always returns 0 (Xlib ignores the return
    value for error handlers, but never calling C's ``exit()`` is what
    matters) makes any such teardown-time X error a harmless no-op. Because
    ``XSetErrorHandler`` sets a single process-wide handler, and VTK
    typically swaps its own handler in/out around save/restore of "whatever
    was previously installed", installing ours first means VTK restores
    *this* nonfatal handler afterward instead of Xlib's fatal built-in one.
    """
    global _x_error_handler_ref
    try:
        libx11 = ctypes.CDLL("libX11.so.6")
    except OSError:
        return  # No Xlib available (e.g. non-Linux) — nothing to install.

    handler_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)

    def _ignore_x_error(display, error_event):
        return 0

    _x_error_handler_ref = handler_type(_ignore_x_error)
    try:
        libx11.XSetErrorHandler(_x_error_handler_ref)
    except Exception:
        pass


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

@pytest.fixture(autouse=True)
def _isolate_test_cwd(tmp_path, monkeypatch):
    """Redirect every test's working directory to a fresh temporary directory.

    This prevents tests that call server tools (which write ``view-*.py`` and
    ``.siva/`` relative to the current directory) from dirtying the repo tree.
    Dataset paths in conftest are absolute (built from REPO_ROOT), so moving
    away from the repo root is safe for data discovery.

    Tests that manage their own cwd (e.g. via ``os.chdir`` in setUp) are safe:
    monkeypatch restores the original cwd after each test regardless of any
    chdir calls made during the test.
    """
    monkeypatch.chdir(tmp_path)


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
