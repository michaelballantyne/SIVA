"""Standalone test for TrameViewer — no browser needed.

This script:
1. Creates a TrameViewer on port 8080.
2. Creates a PyVista plotter with a sphere mesh.
3. Registers it as a "test" view.
4. Starts the server in a background thread.
5. Verifies the server is running.
6. Optionally blocks for manual testing (pass --block to keep it running).

Usage:
    xvfb-run -a python mcp_server/test_trame_standalone.py
    xvfb-run -a python mcp_server/test_trame_standalone.py --block

When --block is passed the script keeps the server running and prints the
URL so you can open it in a browser.

NOTES:
- xvfb-run is required in headless environments because PyVista's off-screen
  rendering still needs an X server for OpenGL context creation.
- The server runs on a background daemon thread so it exits when the script
  exits (unless --block is passed).
"""

import argparse
import sys
import time
import os

# Resolve the project root so we can import trame_viewer.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _SCRIPT_DIR)

import pyvista as pv
from trame_viewer import TrameViewer


def main():
    parser = argparse.ArgumentParser(description="Standalone Trame viewer test")
    parser.add_argument("--port", type=int, default=8080, help="Port for Trame server")
    parser.add_argument(
        "--block",
        action="store_true",
        help="Keep server running for manual browser testing",
    )
    args = parser.parse_args()

    # --- Create viewer ---
    viewer = TrameViewer(port=args.port, offscreen=True)

    # --- Create a simple test plotter ---
    plotter = pv.Plotter(off_screen=True)
    mesh = pv.Sphere()
    plotter.add_mesh(mesh, scalars=mesh.points[:, 2], colormap="viridis")
    plotter.reset_camera()
    plotter.render()

    # --- Register view ---
    viewer.add_view("sphere", plotter)
    print(f"Registered view 'sphere' with {mesh.n_points} points")

    # --- Add a second view to test tab switching ---
    plotter2 = pv.Plotter(off_screen=True)
    cube = pv.Cube()
    plotter2.add_mesh(cube, color="orange")
    plotter2.reset_camera()
    plotter2.render()
    viewer.add_view("cube", plotter2)
    print(f"Registered view 'cube' with {cube.n_points} points")

    # --- Start server ---
    if args.block:
        print(f"\nOpen {viewer.url} in your browser")
        print("Press Ctrl+C to stop.\n")
        viewer.start(block=True)
    else:
        # Non-blocking: start in background thread, verify it starts.
        viewer.start(block=False)

        # Give the server time to bind the port.
        time.sleep(2.0)

        if viewer.is_running():
            print(f"Trame server started successfully at {viewer.url}")
            print(f"Views registered: {list(viewer.plotters.keys())}")
            print(f"Active view: {viewer._active_view}")
        else:
            print("ERROR: Trame server thread did not start!", file=sys.stderr)
            sys.exit(1)

        # Test update_view (simulating reconciler notification).
        print("Testing update_view('sphere')...")
        viewer.update_view("sphere")
        print("  update_view OK (no exception)")

        # Test remove_view.
        print("Testing remove_view('cube')...")
        viewer.remove_view("cube")
        print(f"  Views after remove: {list(viewer.plotters.keys())}")

        # Clean up.
        plotter.close()
        plotter2.close()
        viewer.stop()

        print("\nAll checks passed.")


if __name__ == "__main__":
    main()
